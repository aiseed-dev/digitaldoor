#!/usr/bin/env python3
"""
Markdown → HTML article/blog builder for aiseed.dev

Per-folder layout (single article = single folder):
    articles/insights/01-climate-mistake/{ja,en}.md
    articles/blog/015-japan-windows-disaster-risk/{ja,en}.md + assets
    articles/claude-debian/00-prologue/{ja,en}.md

Usage:
    python3 tools/build_article.py articles/insights/09-healthcare-fiscal/ja.md
    python3 tools/build_article.py articles/blog/001-grid-attack-naphtha/ja.md
    python3 tools/build_article.py --all          # Build all articles + blog
    python3 tools/build_article.py --list         # List available articles

    # Operate on a different site directory (layout: articles/, html/):
    python3 tools/build_article.py --site /path/to/other-site --all
    AISEED_SITE=/path/to/other-site python3 tools/build_article.py --all

Dependencies: jinja2, markdown-it-py, pillow (pyasciidoc, optional — only
needed to build `.adoc` articles; see build.markdown.render_body)

Implementation is split across tools/build/:
    config.py         — site paths, Jinja2 env, configure_site()
    markdown.py       — frontmatter + custom ::: blocks + CommonMark
    images.py         — image copying + OGP image generation
    template_vars.py  — *_vars() builders for article/index pages

Templates live in tools/templates/; a site may override them by placing its
own tools/templates/ under --site.
"""

import re
import sys
from pathlib import Path

from build import config
from build import series as series_expansion
from build.config import render
from build.images import (
    OGP_FILENAME,
    OGP_SIZE,
    copy_images,
    generate_ogp_image,
)
from build.markdown import (
    SOURCE_EXTS,
    parse_frontmatter,
    render_body,
    resolve_source,
    sibling_exists,
    translation_exists,
)
from build.template_vars import (
    BOOK_SUBSERIES,
    custom_index_vars,
    aiways_index_vars,
    blog_index_vars,
    book_index_vars,
    book_series_title,
    fable_index_vars,
    farming_index_vars,
    index_vars,
)

BOOK_SLUG_PREFIX = "claude-debian-"


def _iter_article_files(series_dir, lang, recurse=False):
    """Yield `<lang>.md`/`<lang>.adoc` files from each `NN-slug/` subfolder
    of `series_dir`, sorted by folder name (which leads with the chapter
    number).

    When `recurse=True`, also descends one level into non-digit-prefixed
    subdirectories (e.g. `part-1-collapse/`) to find chapter folders inside.
    This supports a part-grouped layout while keeping the slug-based URL
    routing unchanged.

    Returns an empty iterator if the series directory does not exist.
    """
    if not series_dir.exists():
        return
    for sub in sorted(series_dir.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name[:1].isdigit():
            f = resolve_source(sub, lang)
            if f:
                yield f
            continue
        if recurse:
            for chapter_sub in sorted(sub.iterdir()):
                if not chapter_sub.is_dir():
                    continue
                if not chapter_sub.name[:1].isdigit():
                    continue
                f = resolve_source(chapter_sub, lang)
                if f:
                    yield f


def _detect_lang(md_path):
    """ja.md/ja.adoc → 'ja',  en.md/en.adoc → 'en'."""
    return "en" if Path(md_path).stem == "en" else "ja"


# ---------------------------------------------------------------------------
# Shared chapter-page rendering (unified essay design)
#
# Every article/chapter page on the site — insights, blog, claude-debian,
# ai-native-ways, phosphorus-and-farming, fable — renders through the same
# template pair tools/templates/chapter.html (JA) / chapter.en.html (EN).
# The helpers below build the template's variable surface so each series
# only has to provide its own naming, URLs, and navigation.
# ---------------------------------------------------------------------------

def _chapter_template(lang: str) -> str:
    return "chapter.en.html" if lang == "en" else "chapter.html"


def _numbered_chapter_label(number: str, lang: str) -> str:
    """'00' → '序章' / 'Prologue'. Otherwise '第N章' / 'Chapter N'."""
    n = number.lstrip("0") or "0"
    if n == "0":
        return "序章" if lang == "ja" else "Prologue"
    return f"第{n}章" if lang == "ja" else f"Chapter {n}"


def _series_chapter_toc_html(entries, current_slug, lang, summary):
    """Render the collapsible per-series contents block for a chapter page.

    entries: list of (slug, href, label, title). The current chapter is
    rendered as plain text; other chapters are links.
    """
    if not entries:
        return ""
    is_en = lang == "en"
    aria = "Series chapters" if is_en else "シリーズ章一覧"
    items = []
    for slug, href, label, title in entries:
        display_title = _strip_chapter_prefix(title, label)
        if slug == current_slug:
            items.append(
                f'      <li class="current" aria-current="page">'
                f'<span class="toc-num">{label}</span>'
                f'<span class="toc-title">{display_title}</span></li>'
            )
        else:
            items.append(
                f'      <li><a href="{href}">'
                f'<span class="toc-num">{label}</span>'
                f'<span class="toc-title">{display_title}</span></a></li>'
            )
    return (
        f'<nav class="series-toc" aria-label="{aria}">\n'
        f'  <details>\n'
        f'    <summary>{summary}</summary>\n'
        f'    <ol>\n' + "\n".join(items) + "\n    </ol>\n"
        f'  </details>\n'
        f'</nav>'
    )


def _chapter_nav_html(prev_item, next_item, fallback, lang):
    """Prev/next navigation rendered under the article body.

    prev_item / next_item: (href, title) or None. fallback: (href, label)
    shown in the "next" slot at the end of a series.
    """
    is_en = lang == "en"
    prev_prefix = "Prev: " if is_en else "前: "
    next_prefix = "Next: " if is_en else "次: "
    left = (
        f'<a href="{prev_item[0]}" rel="prev">&larr; {prev_prefix}{prev_item[1]}</a>'
        if prev_item else "<span></span>"
    )
    if next_item:
        right = f'<a href="{next_item[0]}" rel="next">{next_prefix}{next_item[1]} &rarr;</a>'
    elif fallback:
        right = f'<a href="{fallback[0]}">{fallback[1]} &rarr;</a>'
    else:
        right = ""
    return f'<nav class="chapter-nav">\n  {left}\n  {right}\n</nav>'


def _chapter_vars(meta, content_html, *, series_name, series_index_url,
                  chapter_label, canonical_url, hreflang_ja, hreflang_en,
                  og_image, other_lang_url, other_lang_label,
                  chapter_toc_html="", nav_html="", masthead_kind="Essay",
                  hero_meta=None, masthead_right=None, colophon_label=None):
    """Assemble the variable dict consumed by chapter.html / chapter.en.html."""
    number = meta.get("number", "").strip('"')
    date_str = meta.get("date", "")
    year = date_str.split(".")[0] if "." in date_str else (date_str[:4] if date_str else "")
    if hero_meta is None:
        hero_meta = (
            f"{chapter_label} № {number} · {year}" if number
            else " · ".join(x for x in (chapter_label, year) if x)
        )
    if masthead_right is None:
        masthead_right = (
            f"{chapter_label} / {masthead_kind}" if chapter_label else masthead_kind
        )
    if colophon_label is None:
        colophon_label = (
            f"{series_name} · {chapter_label}" if chapter_label else series_name
        )
    return {
        "title": meta.get("title", ""),
        "title_html": meta.get("title_html", ""),
        "subtitle": meta.get("subtitle", ""),
        "description": meta.get("description", ""),
        "date": date_str,
        "year": year,
        "number": number,
        "series": series_name,
        "series_index_url": series_index_url,
        "chapter_label": chapter_label,
        "masthead_right": masthead_right,
        "hero_meta": hero_meta,
        "colophon_label": colophon_label,
        "content_html": content_html,
        "canonical_url": canonical_url,
        "hreflang_ja": hreflang_ja,
        "hreflang_en": hreflang_en,
        "og_image": og_image,
        "other_lang_url": other_lang_url,
        "other_lang_label": other_lang_label,
        "chapter_toc_html": chapter_toc_html,
        "nav_html": nav_html,
        # [.form] を含むページだけ form.css / form-render.js / Turnstile を
        # 読み込む(pywashi の form プラグインが .fr-form を出力する)
        "has_form": 'class="fr-form"' in content_html,
        "asset_version": config.asset_version(),
    }


def _book_stem(slug, subseries=""):
    """URL stem for a book chapter: slug without the `claude-debian-` prefix
    (or the `claude-debian-<subseries>-` prefix for sub-series chapters)."""
    if subseries:
        prefix = f"{BOOK_SLUG_PREFIX}{subseries}-"
        if slug.startswith(prefix):
            return slug[len(prefix):]
    if slug.startswith(BOOK_SLUG_PREFIX):
        return slug[len(BOOK_SLUG_PREFIX):]
    return slug


def _book_subseries_of(md_or_dir) -> str:
    """Return the book sub-series key for a chapter path (e.g. 'server'),
    or '' for parent-series chapters.

    Accepts either the ja/en source file (.md or .adoc) or its parent
    (chapter) directory.
    """
    p = Path(md_or_dir)
    chapter_dir = p.parent if p.is_file() or p.suffix in SOURCE_EXTS else p
    if chapter_dir.parent.name in BOOK_SUBSERIES:
        return chapter_dir.parent.name
    return ""


def _book_url_base(lang: str, subseries: str = "") -> str:
    """URL prefix for book pages, e.g. '/claude-debian' or
    '/en/claude-debian/server'."""
    base = "/en/claude-debian" if lang == "en" else "/claude-debian"
    return f"{base}/{subseries}" if subseries else base


def _book_chapter_toc_html(lang, current_slug, subseries=""):
    """Series contents for a book chapter page (scoped to its sub-series)."""
    is_en = lang == "en"
    base = _book_url_base(lang, subseries)
    entries = []
    for c in collect_book_chapters(lang, subseries):
        slug = c.get("slug", "")
        label = _numbered_chapter_label(c.get("number", "").strip('"'), lang)
        entries.append((slug, f"{base}/{_book_stem(slug, subseries)}/", label, c.get("title", "")))
    series_name = book_series_title(lang, subseries)
    summary = f"Contents — {series_name}" if is_en else f"目次 — {series_name}"
    return _series_chapter_toc_html(entries, current_slug, lang, summary)


# ---------------------------------------------------------------------------
# Build functions — articles
# ---------------------------------------------------------------------------

def _insights_chapter_toc_html(lang, current_slug):
    """Series contents for an insights chapter page. Chapters are numbered
    within their part, so the label uses the compound `部-章` form (e.g. '1-03')."""
    is_en = lang == "en"
    base = "/en/insights" if is_en else "/insights"
    entries = []
    for c in collect_articles(lang):
        slug = c.get("slug", "")
        number = c.get("number", "").strip('"')
        part = c.get("part", "").strip('"')
        label = f"{part}-{number}" if part and part != "9" else number
        entries.append((slug, f"{base}/{slug}/", label, c.get("title", "")))
    summary = "Contents — Structural Analysis" if is_en else "目次 — 構造分析シリーズ"
    return _series_chapter_toc_html(entries, current_slug, lang, summary)


def build_article(md_path):
    """Build a single insights article from Markdown file."""
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False

    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug' in front matter")
        return False

    # Resolve output directory up front so OGP generation can target it.
    lang = meta.get("lang") or _detect_lang(md_path)
    meta["lang"] = lang
    is_en = lang == "en"
    slug = meta["slug"]
    if is_en:
        out_dir = config.SITE_ROOT / "html" / "en" / "insights" / slug
    else:
        out_dir = config.OUTPUT_BASE / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # Context for OGP image resolution
    meta["_source_dir"] = str(md_path.parent)
    meta["_out_dir"] = str(out_dir)

    # Body rendering dispatches on md_path's extension (.md → CommonMark +
    # custom blocks, .adoc → pyasciidoc); both drop a leading duplicate
    # title and unwrap ```mermaid fences the same way.
    body_html = render_body(body, md_path)

    insights_base = "/en/insights" if is_en else "/insights"
    series_name = "Structural Analysis" if is_en else "構造分析シリーズ"
    chapter_label = _numbered_chapter_label(meta.get("number", "").strip('"'), lang)

    prev_slug = meta.get("prev_slug", "")
    next_slug = meta.get("next_slug", "")
    nav_html = _chapter_nav_html(
        (f"{insights_base}/{prev_slug}/", meta.get("prev_title", "")) if prev_slug else None,
        (f"{insights_base}/{next_slug}/", meta.get("next_title", "")) if next_slug else None,
        (f"{insights_base}/", "Insights Top" if is_en else "Insights トップ"),
        lang,
    )

    canonical_url = f"{config.SITE_URL}{insights_base}/{slug}/"
    has_ja = sibling_exists(md_path.parent, "ja")
    has_en = sibling_exists(md_path.parent, "en")
    has_other = translation_exists(md_path, lang)
    other_lang_url = (
        f"{config.SITE_URL}/insights/{slug}/" if is_en
        else f"{config.SITE_URL}/en/insights/{slug}/"
    )

    from build.images import resolve_og_image
    og_image = resolve_og_image(meta, out_dir, f"{config.SITE_URL}{insights_base}/{slug}")

    variables = _chapter_vars(
        meta, body_html,
        series_name=series_name,
        series_index_url=f"{insights_base}/",
        chapter_label=chapter_label,
        canonical_url=canonical_url,
        hreflang_ja=f"{config.SITE_URL}/insights/{slug}/" if has_ja else "",
        hreflang_en=f"{config.SITE_URL}/en/insights/{slug}/" if has_en else "",
        og_image=og_image,
        other_lang_url=other_lang_url if has_other else "",
        other_lang_label="日本語" if is_en else "EN",
        chapter_toc_html=_insights_chapter_toc_html(lang, slug),
        nav_html=nav_html,
        masthead_kind="Analysis",
    )
    html = render(_chapter_template(lang), variables)

    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")

    # Copy assets from the article folder (lang-aware: JA build skips
    # `en-`-prefixed assets so EN-only PDFs don't leak in).
    copy_images(md_path.parent, out_dir, lang=lang)

    print(f"Built: {out_file}")
    return True


def collect_articles(lang="ja"):
    """Collect and sort article metadata for a given language."""
    articles = []
    for f in _iter_article_files(config.INSIGHTS_DIR, lang, recurse=True):
        text = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if meta.get("lang", "ja") == lang or (lang == "ja" and "lang" not in meta):
            articles.append(meta)
    # Sort by (part, number). Part is "1"/"2"/"3" or empty; empty parts sort
    # last (for any flat-layout articles still present).
    articles.sort(key=lambda m: (
        m.get("part", "9").strip('"') or "9",
        m.get("number", "99").strip('"'),
    ))
    return articles


def build_index(lang="ja"):
    """Build the insights index page from article metadata.

    Articles carrying a `part` field are grouped under that part's heading;
    articles without a `part` (legacy flat layout) appear in a tail section.
    """
    is_en = lang == "en"
    articles = collect_articles(lang)
    insights_base = "/en/insights" if is_en else "/insights"

    # Group by part (preserves intra-part order from collect_articles' sort)
    from collections import OrderedDict
    grouped = OrderedDict()
    for a in articles:
        part = a.get("part", "").strip('"')
        grouped.setdefault(part, []).append(a)

    def _part_heading(part_key, sample):
        """Render the heading for a part section, or empty for flat fallback."""
        if not part_key or part_key == "9":
            return ""
        part_title = sample.get("part_title", "").strip('"')
        if not part_title:
            return ""
        roman = {"1": "第一部", "2": "第二部", "3": "第三部"}.get(part_key, f"第{part_key}部")
        if is_en:
            roman_en = {"1": "Part I", "2": "Part II", "3": "Part III"}.get(part_key, f"Part {part_key}")
            return f'<h2 class="part-heading" style="margin-top:2rem;border-top:1px solid #ddd;padding-top:1rem;">{roman_en} — {part_title}</h2>\n'
        return f'<h2 class="part-heading" style="margin-top:2rem;border-top:1px solid #ddd;padding-top:1rem;">{roman} {part_title}</h2>\n'

    # Build article list HTML
    article_list = ""
    for part_key, part_articles in grouped.items():
        sample = part_articles[0] if part_articles else {}
        article_list += _part_heading(part_key, sample)
        for a in part_articles:
            slug = a.get("slug", "")
            number = a.get("number", "").strip('"')
            title = a.get("title", "")
            subtitle = a.get("subtitle", "")
            description = a.get("description", "")
            article_list += f'''
                <a href="{insights_base}/{slug}/" style="text-decoration: none; color: inherit;">
                    <div class="activity-item fade-in">
                        <div class="activity-number">{number}</div>
                        <div class="activity-content">
                            <h3>{title} — {subtitle}</h3>
                            <p>{description}</p>
                        </div>
                    </div>
                </a>
'''

    variables = index_vars(lang, article_list)
    html = render("index.html", variables)

    # Write output
    if is_en:
        out_file = config.SITE_ROOT / "html" / "en" / "insights" / "index.html"
    else:
        out_file = config.SITE_ROOT / "html" / "insights" / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built index: {out_file}")
    return True


# ---------------------------------------------------------------------------
# Build functions — blog
# ---------------------------------------------------------------------------

def _blog_label() -> str:
    """site.json の builder.series で blog.adoc に付けた label(既定 "Blog")。"""
    for d in series_expansion.site_series_defs(config.SITE_ROOT):
        if d.get("file") == "blog.adoc" and d.get("label"):
            return d["label"]
    return "Blog"

def build_blog_post(md_path):
    """Build a single blog post from Markdown file."""
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False

    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug' in front matter")
        return False

    lang = meta.get("lang") or _detect_lang(md_path)
    meta["lang"] = lang
    is_en = lang == "en"
    slug = meta["slug"]
    if is_en:
        out_dir = config.SITE_ROOT / "html" / "en" / "blog" / slug
    else:
        out_dir = config.BLOG_OUTPUT_BASE / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # Context for OGP image resolution
    meta["_source_dir"] = str(md_path.parent)
    meta["_out_dir"] = str(out_dir)

    # Prev/next navigation — posts are ordered by numeric filename prefix.
    # collect_blog_posts() returns them newest-first (descending file number),
    # so posts[i-1] is newer and posts[i+1] is older than posts[i]. We treat
    # "prev" as older (smaller number) and "next" as newer, matching how
    # articles frontmatter labels the series order.
    posts = collect_blog_posts(lang)
    idx = next(
        (i for i, p in enumerate(posts) if p.get("slug") == slug),
        None,
    )
    next_post = posts[idx - 1] if idx is not None and idx > 0 else None
    prev_post = posts[idx + 1] if idx is not None and idx < len(posts) - 1 else None
    file_number = posts[idx].get("_file_number", 0) if idx is not None else 0

    body_html = render_body(body, md_path)

    blog_base = "/en/blog" if is_en else "/blog"
    category = meta.get("category", _blog_label())
    post_no = f"№ {file_number:03d}" if file_number else ""

    nav_html = _chapter_nav_html(
        (f'{blog_base}/{prev_post.get("slug", "")}/', prev_post.get("title", "")) if prev_post else None,
        (f'{blog_base}/{next_post.get("slug", "")}/', next_post.get("title", "")) if next_post else None,
        (f"{blog_base}/", "Blog Top" if is_en else "Blog トップ"),
        lang,
    )

    date_str = meta.get("date", "")
    has_ja = sibling_exists(md_path.parent, "ja")
    has_en = sibling_exists(md_path.parent, "en")
    has_other = translation_exists(md_path, lang)

    from build.images import resolve_og_image
    og_image = resolve_og_image(meta, out_dir, f"{config.SITE_URL}{blog_base}/{slug}")

    variables = _chapter_vars(
        meta, body_html,
        series_name=_blog_label(),
        series_index_url=f"{blog_base}/",
        chapter_label=post_no,
        canonical_url=f"{config.SITE_URL}{blog_base}/{slug}/",
        hreflang_ja=f"{config.SITE_URL}/blog/{slug}/" if has_ja else "",
        hreflang_en=f"{config.SITE_URL}/en/blog/{slug}/" if has_en else "",
        og_image=og_image,
        other_lang_url=(
            (f"{config.SITE_URL}/blog/{slug}/" if is_en else f"{config.SITE_URL}/en/blog/{slug}/")
            if has_other else ""
        ),
        other_lang_label="日本語" if is_en else "EN",
        nav_html=nav_html,
        masthead_right=f"{post_no} / {_blog_label()}" if post_no else _blog_label(),
        hero_meta=" · ".join(x for x in (category, date_str) if x),
        colophon_label=f"{_blog_label()} {post_no}".strip(),
    )
    html = render(_chapter_template(lang), variables)

    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")

    # Copy assets from the article folder (lang-aware).
    copy_images(md_path.parent, out_dir, lang=lang)

    print(f"Built blog: {out_file}")
    return True


def collect_blog_posts(lang="ja"):
    """Collect and sort blog post metadata for a given language."""
    posts = []
    for f in _iter_article_files(config.BLOG_DIR, lang):
        text = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if meta.get("lang", "ja") == lang or (lang == "ja" and "lang" not in meta):
            # Folder name leads with the post number, e.g. "015-japan-…".
            num_match = re.match(r"(\d+)", f.parent.name)
            meta["_file_number"] = int(num_match.group(1)) if num_match else 0
            posts.append(meta)
    # Sort by file number descending (newest first; 006 before 005...)
    posts.sort(key=lambda p: p.get("_file_number", 0), reverse=True)
    return posts


def build_blog_index(lang="ja"):
    """Build the blog index page from post metadata."""
    is_en = lang == "en"
    posts = collect_blog_posts(lang)
    blog_base = "/en/blog" if is_en else "/blog"

    post_list = ""
    for p in posts:
        slug = p.get("slug", "")
        title = p.get("title", "")
        date = p.get("date", "")
        description = p.get("description", "")
        post_list += f'''
                <a href="{blog_base}/{slug}/" style="text-decoration: none; color: inherit;">
                    <div class="activity-item">
                        <div class="activity-number" style="font-size: 0.7rem;">{date}</div>
                        <div class="activity-content">
                            <h3>{title}</h3>
                            <p>{description}</p>
                        </div>
                    </div>
                </a>
'''

    variables = blog_index_vars(lang, post_list)
    variables["page_title"] = _blog_label()
    variables["structural_analysis_label"] = _blog_label()
    html = render("index.html", variables)

    if is_en:
        out_file = config.SITE_ROOT / "html" / "en" / "blog" / "index.html"
    else:
        out_file = config.BLOG_OUTPUT_BASE / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built blog index: {out_file}")
    return True


# ---------------------------------------------------------------------------
# Build functions — book (long-form serial)
#
# Source:  articles/claude-debian/NN-slug.md
# Output:  html/claude-debian/{stem}/index.html   (stem = slug sans prefix)
# ---------------------------------------------------------------------------

def build_book_chapter(md_path):
    """Build a single book chapter from a Markdown file.

    JA source: articles/claude-debian/NN-slug.md    → html/claude-debian/{stem}/
    EN source: articles/claude-debian/en-NN-slug.md → html/en/claude-debian/{stem}/
    """
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False

    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug' in front matter")
        return False

    lang = meta.get("lang") or _detect_lang(md_path)
    meta["lang"] = lang

    subseries = _book_subseries_of(md_path)
    meta["_subseries"] = subseries

    stem = _book_stem(meta["slug"], subseries)
    if lang == "en":
        out_base = config.SITE_ROOT / "html" / "en" / "claude-debian"
    else:
        out_base = config.BOOK_OUTPUT_BASE
    out_dir = (out_base / subseries / stem) if subseries else (out_base / stem)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta["_source_dir"] = str(md_path.parent)
    meta["_out_dir"] = str(out_dir)

    body_html = render_body(body, md_path)

    is_en = lang == "en"
    book_base = _book_url_base(lang, subseries)
    series_name = book_series_title(lang, subseries)
    chapter_label = _numbered_chapter_label(meta.get("number", "").strip('"'), lang)

    prev_slug = meta.get("prev_slug", "")
    next_slug = meta.get("next_slug", "")
    nav_html = _chapter_nav_html(
        (f"{book_base}/{_book_stem(prev_slug, subseries)}/", meta.get("prev_title", "")) if prev_slug else None,
        (f"{book_base}/{_book_stem(next_slug, subseries)}/", meta.get("next_title", "")) if next_slug else None,
        (f"{book_base}/", f"{series_name} TOC" if is_en else f"{series_name} 目次"),
        lang,
    )

    has_ja = sibling_exists(md_path.parent, "ja")
    has_en = sibling_exists(md_path.parent, "en")
    has_other = translation_exists(md_path, lang)
    other_base = _book_url_base("ja" if is_en else "en", subseries)

    from build.images import resolve_og_image
    og_image = resolve_og_image(meta, out_dir, f"{config.SITE_URL}{book_base}/{stem}")

    variables = _chapter_vars(
        meta, body_html,
        series_name=series_name,
        series_index_url=f"{book_base}/",
        chapter_label=chapter_label,
        canonical_url=f"{config.SITE_URL}{book_base}/{stem}/",
        hreflang_ja=f"{config.SITE_URL}{_book_url_base('ja', subseries)}/{stem}/" if has_ja else "",
        hreflang_en=f"{config.SITE_URL}{_book_url_base('en', subseries)}/{stem}/" if has_en else "",
        og_image=og_image,
        other_lang_url=f"{config.SITE_URL}{other_base}/{stem}/" if has_other else "",
        other_lang_label="日本語" if is_en else "EN",
        chapter_toc_html=_book_chapter_toc_html(lang, meta["slug"], subseries),
        nav_html=nav_html,
        masthead_kind="Book",
    )
    html = render(_chapter_template(lang), variables)

    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")

    # Copy chapter assets into out_dir (lang-aware: JA build skips
    # `en-`-prefixed assets).
    copy_images(md_path.parent, out_dir, lang=lang)

    print(f"Built book: {out_file}")
    return True


def collect_book_chapters(lang="ja", subseries=""):
    """Collect book chapter metadata for a language, in folder-name order.

    With `subseries=""` (default), returns the parent-series chapters
    (`articles/claude-debian/NN-slug/`). With a sub-series key (e.g.
    `"server"`), returns that sub-series' chapters only.
    """
    series_dir = config.BOOK_DIR / subseries if subseries else config.BOOK_DIR
    chapters = []
    for f in _iter_article_files(series_dir, lang):
        text = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if meta.get("lang", "ja") != lang and not (lang == "ja" and "lang" not in meta):
            continue
        num_match = re.match(r"(\d+)", f.parent.name)
        meta["_file_number"] = int(num_match.group(1)) if num_match else 0
        chapters.append(meta)
    chapters.sort(key=lambda m: m.get("_file_number", 0))
    return chapters


def _render_book_chapter_list(chapters, book_base, subseries=""):
    """Render chapter cards for a book index page (parent or sub-series)."""
    out = ""
    for c in chapters:
        slug = c.get("slug", "")
        stem = _book_stem(slug, subseries)
        number = c.get("number", "").strip('"')
        title = c.get("title", "")
        subtitle = c.get("subtitle", "")
        description = c.get("description", "")
        out += f'''
                <a href="{book_base}/{stem}/" style="text-decoration: none; color: inherit;">
                    <div class="activity-item fade-in">
                        <div class="activity-number">{number}</div>
                        <div class="activity-content">
                            <h3>{title}{(" — " + subtitle) if subtitle else ""}</h3>
                            <p>{description}</p>
                        </div>
                    </div>
                </a>
'''
    return out


def _book_subseries_hero_html(lang):
    """Render hero cards announcing every active book sub-series, to prepend
    to the parent book index. Returns '' if no sub-series have chapters."""
    is_en = lang == "en"
    parts = []
    for key, cfg in BOOK_SUBSERIES.items():
        if not collect_book_chapters(lang, key):
            continue
        base = "/en/claude-debian" if is_en else "/claude-debian"
        href = f"{base}/{key}/"
        name = cfg["name_en"] if is_en else cfg["name_ja"]
        subtitle = cfg["subtitle_en"] if is_en else cfg["subtitle_ja"]
        description = cfg["description_en"] if is_en else cfg["description_ja"]
        label = (
            "Sub-series — open all chapters →"
            if is_en
            else "サブシリーズを開く →"
        )
        marker = "Sub-series" if is_en else "サブシリーズ"
        parts.append(f'''
                <a href="{href}" style="text-decoration: none; color: inherit; display: block; margin-bottom: 1.5em;">
                    <div class="activity-item fade-in" style="border-left: 4px solid #d4a574; background: rgba(212,165,116,0.08);">
                        <div class="activity-number" style="font-size: 0.65em; line-height: 1.2; padding: 0.4em 0; white-space: nowrap;">▶<br>{marker}</div>
                        <div class="activity-content">
                            <h3>{name} — {subtitle}</h3>
                            <p>{description}<br><strong>{label}</strong></p>
                        </div>
                    </div>
                </a>
''')
    return "".join(parts)


def build_book_index(lang="ja"):
    """Build the book table-of-contents page for a language.

    The parent index lists parent-series chapters only; sub-series are
    announced as a hero card at the top and keep their own index page.
    """
    chapters = collect_book_chapters(lang)
    if not chapters:
        return False

    is_en = lang == "en"
    book_base = "/en/claude-debian" if is_en else "/claude-debian"
    # "Translation exists" if the opposite language has any chapters built.
    has_translation = bool(collect_book_chapters("en" if not is_en else "ja"))

    chapter_list = (
        _book_subseries_hero_html(lang)
        + _render_book_chapter_list(chapters, book_base)
    )

    variables = book_index_vars(lang, chapter_list, has_translation=has_translation)
    # CTA buttons lead into the series instead of the generic site links.
    first_stem = _book_stem(chapters[0].get("slug", ""))
    variables["cta_btn1_text"] = "Start from the Prologue" if is_en else "序章から読み始める"
    variables["cta_btn1_link"] = f"{book_base}/{first_stem}/"
    for sub_key, sub_cfg in BOOK_SUBSERIES.items():
        if collect_book_chapters(lang, sub_key):
            sub_name = sub_cfg["name_en"] if is_en else sub_cfg["name_ja"]
            variables["cta_btn2_text"] = f"{sub_name} →" if is_en else f"{sub_name}へ →"
            variables["cta_btn2_link"] = f"{book_base}/{sub_key}/"
            break
    html = render("index.html", variables)

    if is_en:
        out_file = config.SITE_ROOT / "html" / "en" / "claude-debian" / "index.html"
    else:
        out_file = config.BOOK_OUTPUT_BASE / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built book index: {out_file}")
    return True


def build_book_subseries_index(subseries, lang="ja"):
    """Build the index page for one book sub-series
    (e.g. /claude-debian/server/). Lists the sub-series' own chapters and
    links back to the parent index."""
    if subseries not in BOOK_SUBSERIES:
        return False
    chapters = collect_book_chapters(lang, subseries)
    if not chapters:
        return False

    is_en = lang == "en"
    book_base = ("/en/claude-debian" if is_en else "/claude-debian") + f"/{subseries}"
    has_translation = bool(
        collect_book_chapters("en" if not is_en else "ja", subseries)
    )

    # Back-link to the parent series index, above the chapter cards (the
    # shared index template has no dedicated intro slot).
    parent_base = "/en/claude-debian" if is_en else "/claude-debian"
    back_label = (
        "← Back to Learning Debian with Claude"
        if is_en else "← Claudeと一緒に学ぶDebian 目次へ戻る"
    )
    back_html = (
        f'\n                <p style="margin-bottom: 1.5em;">'
        f'<a href="{parent_base}/" style="color: inherit;">{back_label}</a></p>\n'
    )
    chapter_list = back_html + _render_book_chapter_list(chapters, book_base, subseries)

    variables = book_index_vars(
        lang, chapter_list, has_translation=has_translation, subseries=subseries
    )
    # CTA buttons: into chapter 1, and back to the parent series.
    first_stem = _book_stem(chapters[0].get("slug", ""), subseries)
    variables["cta_btn1_text"] = "Start with Chapter 1" if is_en else "第1章から読み始める"
    variables["cta_btn1_link"] = f"{book_base}/{first_stem}/"
    variables["cta_btn2_text"] = (
        "Main (Desktop) Series" if is_en else "本編（デスクトップ編）へ"
    )
    variables["cta_btn2_link"] = parent_base + "/"
    html = render("index.html", variables)

    if is_en:
        out_file = config.SITE_ROOT / "html" / "en" / "claude-debian" / subseries / "index.html"
    else:
        out_file = config.BOOK_OUTPUT_BASE / subseries / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built book sub-series index ({subseries}): {out_file}")
    return True


# ---------------------------------------------------------------------------
# Build functions — ai-native-ways (independent template)
#
# Source:  articles/ai-native-ways/NN-slug/{ja,en}.md
# Output:  html/ai-native-ways/{slug}/index.html  (JA)
#          html/en/ai-native-ways/{slug}/index.html  (EN)
#
# Chapters render through the shared chapter template like every series;
# example-N/ pages keep the series-local template-example.html.
# ---------------------------------------------------------------------------

AIWAYS_SERIES_NAME_JA = "AIネイティブな仕事の作法"
AIWAYS_SERIES_NAME_EN = "AI-Native Ways of Working"


# Sub-series registry. Each sub-series lives in `articles/ai-native-ways/<key>/`,
# numbers its chapters from 01, has its own series-index page at
# `/ai-native-ways/<key>/`, and is announced from the parent index as a hero
# card.
AIWAYS_SUBSERIES = {
    "software": {
        "name_ja": "ソフトウェア開発編",
        "name_en": "Software",
        "subtitle_ja": "ソフトウェア工学から、リベラルアーツへ ── 技術職の基盤転換",
        "subtitle_en": "From software engineering to the liberal arts — the foundational shift of the technical profession",
        "description_ja": (
            "AIがコードを書く能力で人間トップクラスに到達した。コーダーの仕事は"
            "なくなり、判断中心のビルダーが代わりに立つ。技術職の基盤学問は、"
            "ソフトウェア工学からリベラルアーツへ。導入・自立・転換の三編で、"
            "数年で完了する構造転換を追う。"
        ),
        "description_en": (
            "AI has reached human-top-class capability in writing code. "
            "Coders' work disappears; judgment-centered builders take their "
            "place. The foundational discipline of the technical profession "
            "shifts from software engineering to the liberal arts. "
            "A three-part case — Introduction, Independence, Shift — for an "
            "irreversible structural shift completing in years."
        ),
        # The sub-series is presented in three 編 (parts). Each chapter declares
        # its `part` ("1"/"2"/"3") in front matter and restarts numbering at 1
        # within its part; index grouping and breadcrumbs use the `part` value.
        "parts": [
            {"key": "1", "short_ja": "導入編", "short_en": "Introduction",
             "name_ja": "導入編 ── なぜ作り、どう始めるか",
             "name_en": "Introduction — why build, and how to start"},
            {"key": "2", "short_ja": "自立編", "short_en": "Independence",
             "name_ja": "自立編 ── Microsoft 365・Copilot・WordPress・基幹システム・GitHub から自立する",
             "name_en": "Independence — from Microsoft 365, Copilot, WordPress, core systems, and GitHub"},
            {"key": "3", "short_ja": "転換編", "short_en": "Shift",
             "name_ja": "転換編 ── 産業構造の帰結",
             "name_en": "Shift — the industry consequence"},
        ],
    },
}


def _aiways_subseries_of(md_or_dir) -> str:
    """Return the sub-series key for a chapter path (e.g. 'software'), or ''
    for parent-series chapters.

    Accepts either the ja/en source file (.md or .adoc) or its parent
    (chapter) directory.
    """
    p = Path(md_or_dir)
    chapter_dir = p.parent if p.is_file() or p.suffix in SOURCE_EXTS else p
    parent = chapter_dir.parent
    if parent.name in AIWAYS_SUBSERIES:
        return parent.name
    return ""


def _aiways_url_base(lang: str, subseries: str = "") -> str:
    """URL prefix for chapter / index pages.

    Parent: '/ai-native-ways' (or '/en/ai-native-ways').
    Sub-series: '/ai-native-ways/software' (or '/en/ai-native-ways/software').
    """
    base = "/en/ai-native-ways" if lang == "en" else "/ai-native-ways"
    return f"{base}/{subseries}" if subseries else base


def _aiways_output_base(lang: str, subseries: str = "") -> Path:
    """Output directory for chapter / index pages (mirrors `_aiways_url_base`)."""
    if lang == "en":
        base = config.SITE_ROOT / "html" / "en" / "ai-native-ways"
    else:
        base = config.AIWAYS_OUTPUT_BASE
    return base / subseries if subseries else base


def _aiways_part(subseries: str, part_key: str):
    """Return the parts-config entry for a chapter's `part` value, or None."""
    if not subseries or not part_key:
        return None
    for p in AIWAYS_SUBSERIES.get(subseries, {}).get("parts", []) or []:
        if str(p.get("key")) == str(part_key):
            return p
    return None


def _aiways_part_short(subseries: str, meta: dict, lang: str) -> str:
    """Short 編 name ('導入編' / 'Introduction') for a chapter, or ''."""
    p = _aiways_part(subseries, str(meta.get("part", "")).strip('"'))
    if not p:
        return ""
    return p.get("short_en", "") if lang == "en" else p.get("short_ja", "")


def _aiways_chapter_label(number: str, lang: str, subseries: str = "",
                          part_short: str = "", part: str = "") -> str:
    """Parent series: '00' → '序章' / 'Prologue', otherwise '第N章' / 'Chapter N'.

    Sub-series chapters renumber within each 編 (part), so the label uses the
    compound `部-章` form `{part}-{number}` (e.g. '2-10'); when `part_short` is
    given it is prefixed, giving '編名 部-章' (e.g. '自立編 2-10' /
    'Independence 2-10'). The breadcrumb is rendered as `series · chapter_label`,
    giving 'ソフトウェア開発編 · 自立編 2-10'.
    """
    n = number.lstrip("0") or "0"
    if subseries:
        base = f"{part}-{number}" if part else (f"第{n}章" if lang == "ja" else f"Chapter {n}")
        return f"{part_short} {base}" if part_short else base
    if n == "0":
        return "序章" if lang == "ja" else "Prologue"
    return f"第{n}章" if lang == "ja" else f"Chapter {n}"


def _iter_aiways_subseries_files(subseries: str, lang: str):
    """Yield `<lang>.md`/`<lang>.adoc` files from each `NN-slug/` subfolder
    of a sub-series."""
    sub_dir = config.AIWAYS_DIR / subseries
    if not sub_dir.exists():
        return
    for sub in sorted(sub_dir.iterdir()):
        if not sub.is_dir():
            continue
        if not sub.name[:1].isdigit():
            continue
        f = resolve_source(sub, lang)
        if f:
            yield f


def _aiways_chapter_examples_html(chapter_dir, chapter_slug: str, lang: str, subseries: str = "") -> str:
    """Render a callout at the end of a chapter listing its example-N/ pages.

    For EN chapters, if an example only ships JA content, link to the JA URL
    so the source / outputs are still reachable.
    """
    examples = sorted(
        sub for sub in Path(chapter_dir).iterdir()
        if sub.is_dir() and re.match(r"example-\d+$", sub.name)
    )
    if not examples:
        return ""

    is_en = lang == "en"
    heading = "実例" if not is_en else "Examples"
    intro = (
        "再現可能なソース・コマンド・実測結果を、別ページにまとめてある。"
        if not is_en
        else "Runnable source, commands, and measured results — see the dedicated example page(s)."
    )

    ja_base = _aiways_url_base("ja", subseries)
    en_base = _aiways_url_base("en", subseries)

    items = []
    for ex in examples:
        en_readme = ex / "README.en.md"
        # If EN README exists, link to /en/.../example-N/. Otherwise (or for JA)
        # link to the JA URL.
        if is_en and not en_readme.exists():
            href = f"{ja_base}/{chapter_slug}/{ex.name}/"
            ja_only_marker = ' <small>(JA)</small>'
        else:
            base = en_base if is_en else ja_base
            href = f"{base}/{chapter_slug}/{ex.name}/"
            ja_only_marker = ''

        readme = en_readme if (is_en and en_readme.exists()) else (ex / "README.md")
        title = ""
        if readme.exists():
            text = readme.read_text(encoding="utf-8")
            m = re.search(r"^# (.+)$", text, re.MULTILINE)
            if m:
                title = m.group(1).strip()
        if not title:
            title = _example_label(ex.name, lang)
        items.append(f'<li><a href="{href}">{title}</a>{ja_only_marker}</li>')

    return (
        f'\n<h2>{heading}</h2>\n'
        f'<p>{intro}</p>\n'
        f'<ul>\n' + "\n".join(items) + "\n</ul>\n"
    )


_CHAPTER_PREFIX_SEP_RE = re.compile(r'^[\s—–―\-:·、。─-╿]+')


def _strip_chapter_prefix(title: str, label: str) -> str:
    """Strip a leading chapter label (e.g. '第2章', 'Chapter 2', '序章',
    'Prologue') and any following separator/punctuation, so the TOC row
    doesn't repeat what the chapter-number column already shows."""
    if not label:
        return title
    t = title
    if t.startswith(label):
        t = t[len(label):]
    return _CHAPTER_PREFIX_SEP_RE.sub('', t)


def _aiways_chapter_toc_html(lang: str, subseries: str, current_slug: str) -> str:
    """Render a collapsible series chapter-list for an ai-native-ways chapter page.

    Scoped to the chapter's (sub)series. The current chapter is rendered as
    plain text; other chapters are links.
    """
    chapters = collect_aiways_chapters(lang, subseries)
    base = _aiways_url_base(lang, subseries)
    is_en = lang == "en"
    if subseries:
        cfg = AIWAYS_SUBSERIES[subseries]
        sub_name = cfg["name_en"] if is_en else cfg["name_ja"]
        summary = f"Contents — {sub_name}" if is_en else f"目次 — {sub_name}"
    else:
        summary = "Contents — all chapters" if is_en else "目次 — 全章"
    entries = []
    for c in chapters:
        slug = c.get("slug", "")
        label = _aiways_chapter_label(
            c.get("number", "").strip('"'), lang, subseries,
            _aiways_part_short(subseries, c, lang), c.get("part", "").strip('"'),
        )
        entries.append((slug, f"{base}/{slug}/", label, c.get("title", "")))
    return _series_chapter_toc_html(entries, current_slug, lang, summary)


def build_aiways_chapter(md_path):
    """Build a single ai-native-ways chapter using the shared chapter template."""
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False

    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug' in front matter")
        return False

    lang = meta.get("lang") or _detect_lang(md_path)
    meta["lang"] = lang

    subseries = _aiways_subseries_of(md_path)

    slug = meta["slug"]
    out_dir = _aiways_output_base(lang, subseries) / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    meta["_source_dir"] = str(md_path.parent)
    meta["_out_dir"] = str(out_dir)

    body_html = render_body(body, md_path)

    # Append a "実例 / Examples" callout listing example-N/ folders for this chapter.
    examples_html = _aiways_chapter_examples_html(md_path.parent, meta["slug"], lang, subseries)
    if examples_html:
        body_html = body_html + examples_html

    if subseries:
        cfg = AIWAYS_SUBSERIES[subseries]
        series_name = (
            f"{AIWAYS_SERIES_NAME_EN} — {cfg['name_en']}"
            if lang == "en"
            else f"{AIWAYS_SERIES_NAME_JA} — {cfg['name_ja']}"
        )
    else:
        series_name = AIWAYS_SERIES_NAME_EN if lang == "en" else AIWAYS_SERIES_NAME_JA

    series_index_url = _aiways_url_base(lang, subseries) + "/"
    ja_url_base = _aiways_url_base("ja", subseries)
    en_url_base = _aiways_url_base("en", subseries)

    canonical_base = en_url_base if lang == "en" else ja_url_base
    canonical_url = f"{config.SITE_URL}{canonical_base}/{slug}/"
    hreflang_ja = f"{config.SITE_URL}{ja_url_base}/{slug}/"
    hreflang_en = f"{config.SITE_URL}{en_url_base}/{slug}/"

    other_lang_url = (hreflang_ja if lang == "en" else hreflang_en)
    other_lang_label = "日本語" if lang == "en" else "EN"

    has_other = sibling_exists(md_path.parent, "ja" if lang == "en" else "en")

    # Resolve OG image: prefer an article-folder hero_image, else default
    from build.images import resolve_og_image
    og_image = resolve_og_image(meta, out_dir, public_base_url=canonical_url.rstrip("/"))

    chapter_label = _aiways_chapter_label(
        meta.get("number", "").strip('"'), lang, subseries,
        _aiways_part_short(subseries, meta, lang), meta.get("part", "").strip('"'),
    )
    url_base = _aiways_url_base(lang, subseries)
    prev_slug = meta.get("prev_slug", "")
    next_slug = meta.get("next_slug", "")
    nav_html = _chapter_nav_html(
        (f"{url_base}/{prev_slug}/", meta.get("prev_title", "")) if prev_slug else None,
        (f"{url_base}/{next_slug}/", meta.get("next_title", "")) if next_slug else None,
        (series_index_url, "Contents" if lang == "en" else "目次"),
        lang,
    )

    variables = _chapter_vars(
        meta, body_html,
        series_name=series_name,
        series_index_url=series_index_url,
        chapter_label=chapter_label,
        canonical_url=canonical_url,
        hreflang_ja=hreflang_ja if sibling_exists(md_path.parent, "ja") else "",
        hreflang_en=hreflang_en if sibling_exists(md_path.parent, "en") else "",
        og_image=og_image,
        other_lang_url=other_lang_url if has_other else "",
        other_lang_label=other_lang_label,
        chapter_toc_html=_aiways_chapter_toc_html(lang, subseries, slug),
        nav_html=nav_html,
        masthead_kind="Essay",
    )
    html = render(_chapter_template(lang), variables)

    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")

    # Copy assets from the article folder (lang-aware).
    copy_images(md_path.parent, out_dir, lang=lang)

    print(f"Built ai-native-ways: {out_file}")
    return True


# ---------------------------------------------------------------------------
# Build functions — phosphorus-and-farming
#
# Source:  articles/phosphorus-and-farming/NN-slug/{ja,en}.md
# Output:  html/phosphorus-and-farming/{slug}/index.html       (JA)
#          html/en/phosphorus-and-farming/{slug}/index.html    (EN)
#
# The existing static /natural-farming/ landing page is independent and
# untouched (different URL prefix entirely).
# ---------------------------------------------------------------------------

FARMING_SERIES_NAME_JA = "リン資源枯渇と自然農法シリーズ"
FARMING_SERIES_NAME_EN = "Phosphorus Depletion and Natural Farming Series"


def _farming_chapter_toc_html(lang: str, current_slug: str) -> str:
    """Render a collapsible series chapter-list for a phosphorus-and-farming
    chapter page. The current chapter is plain text; others are links."""
    base = "/en/phosphorus-and-farming" if lang == "en" else "/phosphorus-and-farming"
    is_en = lang == "en"
    summary = "Contents — all chapters" if is_en else "目次 — 全章"
    entries = []
    for c in collect_farming_chapters(lang):
        slug = c.get("slug", "")
        label = _numbered_chapter_label(c.get("number", "").strip('"'), lang)
        entries.append((slug, f"{base}/{slug}/", label, c.get("title", "")))
    return _series_chapter_toc_html(entries, current_slug, lang, summary)


def build_farming_chapter(md_path):
    """Build a single phosphorus-and-farming chapter using the shared chapter template."""
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False

    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug' in front matter")
        return False

    lang = meta.get("lang") or _detect_lang(md_path)
    meta["lang"] = lang

    slug = meta["slug"]
    if lang == "en":
        out_dir = config.SITE_ROOT / "html" / "en" / "phosphorus-and-farming" / slug
    else:
        out_dir = config.FARMING_OUTPUT_BASE / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    meta["_source_dir"] = str(md_path.parent)
    meta["_out_dir"] = str(out_dir)

    body_html = render_body(body, md_path)

    series_name = FARMING_SERIES_NAME_EN if lang == "en" else FARMING_SERIES_NAME_JA
    series_index_url = "/en/phosphorus-and-farming/" if lang == "en" else "/phosphorus-and-farming/"

    canonical_url = f"{config.SITE_URL}/{'en/' if lang == 'en' else ''}phosphorus-and-farming/{slug}/"
    hreflang_ja = f"{config.SITE_URL}/phosphorus-and-farming/{slug}/"
    hreflang_en = f"{config.SITE_URL}/en/phosphorus-and-farming/{slug}/"

    other_lang_url = (hreflang_ja if lang == "en" else hreflang_en)
    other_lang_label = "日本語" if lang == "en" else "EN"
    has_other = sibling_exists(md_path.parent, "ja" if lang == "en" else "en")

    from build.images import resolve_og_image
    og_image = resolve_og_image(meta, out_dir, public_base_url=canonical_url.rstrip("/"))

    farming_base = "/en/phosphorus-and-farming" if lang == "en" else "/phosphorus-and-farming"
    prev_slug = meta.get("prev_slug", "")
    next_slug = meta.get("next_slug", "")
    nav_html = _chapter_nav_html(
        (f"{farming_base}/{prev_slug}/", meta.get("prev_title", "")) if prev_slug else None,
        (f"{farming_base}/{next_slug}/", meta.get("next_title", "")) if next_slug else None,
        (series_index_url, "Contents" if lang == "en" else "目次"),
        lang,
    )

    variables = _chapter_vars(
        meta, body_html,
        series_name=series_name,
        series_index_url=series_index_url,
        chapter_label=_numbered_chapter_label(meta.get("number", "").strip('"'), lang),
        canonical_url=canonical_url,
        hreflang_ja=hreflang_ja if sibling_exists(md_path.parent, "ja") else "",
        hreflang_en=hreflang_en if sibling_exists(md_path.parent, "en") else "",
        og_image=og_image,
        other_lang_url=other_lang_url if has_other else "",
        other_lang_label=other_lang_label,
        chapter_toc_html=_farming_chapter_toc_html(lang, slug),
        nav_html=nav_html,
        masthead_kind="Essay",
    )
    html = render(_chapter_template(lang), variables)

    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")

    copy_images(md_path.parent, out_dir, lang=lang)

    print(f"Built phosphorus-and-farming: {out_file}")
    return True


def collect_farming_chapters(lang="ja"):
    """Collect phosphorus-and-farming chapter metadata for a language."""
    chapters = []
    for f in _iter_article_files(config.FARMING_DIR, lang):
        text = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if meta.get("lang", "ja") != lang and not (lang == "ja" and "lang" not in meta):
            continue
        num_match = re.match(r"(\d+)", f.parent.name)
        meta["_file_number"] = int(num_match.group(1)) if num_match else 0
        chapters.append(meta)
    chapters.sort(key=lambda m: m.get("_file_number", 0))
    return chapters


def build_farming_index(lang="ja"):
    """Build the phosphorus-and-farming TOC page using the shared index.html template."""
    chapters = collect_farming_chapters(lang)
    if not chapters:
        return False

    is_en = lang == "en"
    farming_base = "/en/phosphorus-and-farming" if is_en else "/phosphorus-and-farming"
    has_translation = bool(collect_farming_chapters("en" if not is_en else "ja"))

    chapter_list = ""
    for c in chapters:
        slug = c.get("slug", "")
        number = c.get("number", "").strip('"')
        title = c.get("title", "")
        subtitle = c.get("subtitle", "")
        description = c.get("description", "")
        chapter_list += f'''
                <a href="{farming_base}/{slug}/" style="text-decoration: none; color: inherit;">
                    <div class="activity-item fade-in">
                        <div class="activity-number">{number}</div>
                        <div class="activity-content">
                            <h3>{title}{(" — " + subtitle) if subtitle else ""}</h3>
                            <p>{description}</p>
                        </div>
                    </div>
                </a>
'''

    variables = farming_index_vars(lang, chapter_list, has_translation=has_translation)
    html = render("index.html", variables)

    if is_en:
        out_file = config.SITE_ROOT / "html" / "en" / "phosphorus-and-farming" / "index.html"
    else:
        out_file = config.FARMING_OUTPUT_BASE / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built phosphorus-and-farming index: {out_file}")
    return True


# ---------------------------------------------------------------------------
# Build functions — fable (serialized column, shared chapter template)
#
# Source:  articles/fable/NN-slug/{ja,en}.md
# Output:  html/fable/{slug}/index.html       (JA)
#          html/en/fable/{slug}/index.html    (EN)
#
# 連載「Fable 5 が帰ってきた」— design doc: docs/plan/fable/plan.md.
# Chapters are 回 (installments), not 章: labels read 第N回. A chapter can
# override its label via `chapter_label` in front matter (used by the
# 番外編 outlook column).
# ---------------------------------------------------------------------------

FABLE_SERIES_NAME_JA = "Fable 5 が帰ってきた"
FABLE_SERIES_NAME_EN = "Fable 5 Is Back"


def _fable_url_base(lang: str) -> str:
    return "/en/fable" if lang == "en" else "/fable"


def _fable_chapter_label(meta: dict, lang: str) -> str:
    """'第N回' / 'Part N'; front matter `chapter_label` overrides (番外編 etc.)."""
    override = meta.get("chapter_label", "").strip()
    if override:
        return override
    n = meta.get("number", "").strip('"').lstrip("0") or "0"
    return f"第{n}回" if lang == "ja" else f"Part {n}"


def _fable_chapter_toc_html(lang: str, current_slug: str) -> str:
    """Series contents for a fable chapter page."""
    base = _fable_url_base(lang)
    is_en = lang == "en"
    summary = "Contents — all installments" if is_en else "目次 — 全回"
    entries = []
    for c in collect_fable_chapters(lang):
        slug = c.get("slug", "")
        entries.append((slug, f"{base}/{slug}/", _fable_chapter_label(c, lang), c.get("title", "")))
    return _series_chapter_toc_html(entries, current_slug, lang, summary)


def build_fable_chapter(md_path):
    """Build a single fable installment using the shared chapter template."""
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False

    text = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug' in front matter")
        return False

    lang = meta.get("lang") or _detect_lang(md_path)
    meta["lang"] = lang
    is_en = lang == "en"

    slug = meta["slug"]
    if is_en:
        out_dir = config.SITE_ROOT / "html" / "en" / "fable" / slug
    else:
        out_dir = config.FABLE_OUTPUT_BASE / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    meta["_source_dir"] = str(md_path.parent)
    meta["_out_dir"] = str(out_dir)

    body_html = render_body(body, md_path)

    series_name = FABLE_SERIES_NAME_EN if is_en else FABLE_SERIES_NAME_JA
    fable_base = _fable_url_base(lang)
    series_index_url = f"{fable_base}/"

    canonical_url = f"{config.SITE_URL}{fable_base}/{slug}/"
    hreflang_ja = f"{config.SITE_URL}/fable/{slug}/"
    hreflang_en = f"{config.SITE_URL}/en/fable/{slug}/"

    other_lang_url = (hreflang_ja if is_en else hreflang_en)
    has_other = sibling_exists(md_path.parent, "ja" if is_en else "en")

    from build.images import resolve_og_image
    og_image = resolve_og_image(meta, out_dir, public_base_url=canonical_url.rstrip("/"))

    prev_slug = meta.get("prev_slug", "")
    next_slug = meta.get("next_slug", "")
    nav_html = _chapter_nav_html(
        (f"{fable_base}/{prev_slug}/", meta.get("prev_title", "")) if prev_slug else None,
        (f"{fable_base}/{next_slug}/", meta.get("next_title", "")) if next_slug else None,
        (series_index_url, "Contents" if is_en else "目次"),
        lang,
    )

    variables = _chapter_vars(
        meta, body_html,
        series_name=series_name,
        series_index_url=series_index_url,
        chapter_label=_fable_chapter_label(meta, lang),
        canonical_url=canonical_url,
        hreflang_ja=hreflang_ja if sibling_exists(md_path.parent, "ja") else "",
        hreflang_en=hreflang_en if sibling_exists(md_path.parent, "en") else "",
        og_image=og_image,
        other_lang_url=other_lang_url if has_other else "",
        other_lang_label="日本語" if is_en else "EN",
        chapter_toc_html=_fable_chapter_toc_html(lang, slug),
        nav_html=nav_html,
        masthead_kind="Series",
    )
    html = render(_chapter_template(lang), variables)

    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")

    copy_images(md_path.parent, out_dir, lang=lang)

    print(f"Built fable: {out_file}")
    return True


def collect_fable_chapters(lang="ja"):
    """Collect fable installment metadata for a language, in folder order."""
    chapters = []
    for f in _iter_article_files(config.FABLE_DIR, lang):
        text = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if meta.get("lang", "ja") != lang and not (lang == "ja" and "lang" not in meta):
            continue
        num_match = re.match(r"(\d+)", f.parent.name)
        meta["_file_number"] = int(num_match.group(1)) if num_match else 0
        chapters.append(meta)
    chapters.sort(key=lambda m: m.get("_file_number", 0))
    return chapters


def build_fable_index(lang="ja"):
    """Build the fable TOC page using the shared index.html template."""
    chapters = collect_fable_chapters(lang)
    if not chapters:
        return False

    is_en = lang == "en"
    fable_base = _fable_url_base(lang)
    has_translation = bool(collect_fable_chapters("en" if not is_en else "ja"))

    chapter_list = ""
    for c in chapters:
        slug = c.get("slug", "")
        number = c.get("number", "").strip('"')
        title = c.get("title", "")
        subtitle = c.get("subtitle", "")
        description = c.get("description", "")
        chapter_list += f'''
                <a href="{fable_base}/{slug}/" style="text-decoration: none; color: inherit;">
                    <div class="activity-item fade-in">
                        <div class="activity-number">{number}</div>
                        <div class="activity-content">
                            <h3>{title}{(" — " + subtitle) if subtitle else ""}</h3>
                            <p>{description}</p>
                        </div>
                    </div>
                </a>
'''

    variables = fable_index_vars(lang, chapter_list, has_translation=has_translation)
    html = render("index.html", variables)

    if is_en:
        out_file = config.SITE_ROOT / "html" / "en" / "fable" / "index.html"
    else:
        out_file = config.FABLE_OUTPUT_BASE / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built fable index: {out_file}")
    return True


# ---------------------------------------------------------------------------
# Build functions — ai-native-ways examples (per-chapter "example-N" pages)
#
# Source:  articles/ai-native-ways/NN-slug/example-N/{README.md, results.md, ...}
# Output:  html/ai-native-ways/<slug>/example-N/index.html
#
# Each example folder is rendered as a stand-alone technical doc page using
# template-example.html. README.md and results.md (if present) are concatenated
# as the page body. Source files (Makefile, *.py, etc.) are copied alongside as
# downloadable raw assets, and a "files in this example" section lists them.
# ---------------------------------------------------------------------------

# Files to copy into the html output dir as downloadable assets.
_EXAMPLE_INCLUDE_EXT = {
    ".py", ".sh", ".mk", ".css", ".js", ".html", ".md", ".mmd",
    ".json", ".yaml", ".yml", ".csv", ".tsv", ".txt", ".toml",
    ".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp",
    ".pdf", ".epub", ".docx", ".xlsx", ".log",
}
_EXAMPLE_INCLUDE_NAMES = {"Makefile", "Dockerfile", "Procfile", "requirements.txt"}
_EXAMPLE_SKIP_NAMES = {".stamp", ".gitignore", ".DS_Store"}
_EXAMPLE_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", ".git"}


def _iter_example_files(example_dir):
    """Yield (Path, rel_path) for every file in example_dir (recursive) that
    should be copied into the published html output."""
    base = Path(example_dir)
    for p in sorted(base.rglob("*")):
        if any(part in _EXAMPLE_SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file():
            continue
        if p.name in _EXAMPLE_SKIP_NAMES:
            continue
        if p.name in _EXAMPLE_INCLUDE_NAMES:
            yield p, p.relative_to(base)
            continue
        if p.suffix.lower() in _EXAMPLE_INCLUDE_EXT:
            yield p, p.relative_to(base)


def _humanize_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


def _example_files_html(example_dir):
    """Render the 'files in this example' section. Groups by top-level folder
    so out/, src/, docx/ etc. read cleanly."""
    files = list(_iter_example_files(example_dir))
    if not files:
        return ""

    groups = {}
    for src, rel in files:
        parts = rel.parts
        head = "" if len(parts) == 1 else parts[0]
        groups.setdefault(head, []).append((src, rel))

    parts_html = []
    group_order = sorted(groups.keys(), key=lambda k: (k == "", k))
    for head in group_order:
        if head:
            parts_html.append(f'<h3>{head}/</h3>')
        items = []
        for src, rel in groups[head]:
            href = "/".join(rel.parts)
            display = "/".join(rel.parts)
            size = _humanize_size(src.stat().st_size)
            items.append(
                f'<li><span class="filename"><a href="{href}">{display}</a></span>'
                f'<span class="filesize">{size}</span></li>'
            )
        parts_html.append("<ul>" + "".join(items) + "</ul>")
    return "\n".join(parts_html)


def _example_label(folder_name: str, lang: str) -> str:
    """'example-1' → '実例 1' / 'Example 1'."""
    m = re.match(r"example-(\d+)", folder_name)
    n = m.group(1) if m else folder_name
    return f"実例 {n}" if lang == "ja" else f"Example {n}"


def _example_chapter_meta(example_dir):
    """Read frontmatter from the parent chapter's ja/en source
    (.md or .adoc — frontmatter format is the same for both)."""
    chapter_dir = Path(example_dir).parent
    meta_by_lang = {}
    for lang in ("ja", "en"):
        f = resolve_source(chapter_dir, lang)
        if f is None:
            continue
        text = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if "lang" not in meta and lang == "en":
            meta["lang"] = "en"
        meta_by_lang[lang] = meta
    return meta_by_lang


def build_aiways_example(example_dir, lang="ja"):
    """Build a single example page at /ai-native-ways/<chapter-slug>/<example-N>/."""
    from jinja2 import Template

    example_dir = Path(example_dir)
    if not example_dir.is_dir():
        return False

    chapter_meta = _example_chapter_meta(example_dir)
    meta = chapter_meta.get(lang)
    if meta is None:
        return False

    # Pick README/results: language-specific variants if present, otherwise
    # fall back to the JA originals (no EN translation yet for examples — the
    # EN page links back to the same source folder so the artifacts are still
    # accessible).
    readme_lang = example_dir / f"README.{lang}.md"
    results_lang = example_dir / f"results.{lang}.md"
    readme = readme_lang if readme_lang.exists() else (example_dir / "README.md")
    results = results_lang if results_lang.exists() else (example_dir / "results.md")

    # If we are building EN but only JA content exists, skip EN — the EN chapter
    # page will link to the JA example URL instead.
    if lang == "en" and not readme_lang.exists():
        return False

    if not readme.exists():
        return False

    body_md = readme.read_text(encoding="utf-8")
    if results.exists():
        body_md = body_md.rstrip() + "\n\n---\n\n" + results.read_text(encoding="utf-8")

    content_html = render_body(body_md, readme)

    chapter_slug = meta["slug"]
    example_name = example_dir.name  # 'example-1'
    is_en = lang == "en"
    subseries = _aiways_subseries_of(example_dir.parent)
    aiways_base = _aiways_url_base(lang, subseries)
    series_index_url = aiways_base + "/"
    chapter_url = f"{series_index_url}{chapter_slug}/"

    out_dir = _aiways_output_base(lang, subseries) / chapter_slug / example_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Copy raw asset files alongside index.html.
    for src, rel in _iter_example_files(example_dir):
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            dst.write_bytes(src.read_bytes())

    # Title: pull the first H1 from README.md, else fall back to "実例 N".
    h1_match = re.search(r"^# (.+)$", readme.read_text(encoding="utf-8"), re.MULTILINE)
    example_label = _example_label(example_name, lang)
    if h1_match:
        example_title = h1_match.group(1).strip()
        # Strip leading "実例 N — " / "Example N — " if present, since the label is shown separately.
        example_title = re.sub(r"^(実例\s*\d+|Example\s*\d+)\s*[—\-:：]\s*", "", example_title)
    else:
        example_title = example_label

    chapter_title = meta.get("title", "")
    chapter_label = _aiways_chapter_label(meta.get("number", "").strip('"'), lang, subseries, _aiways_part_short(subseries, meta, lang), meta.get("part", "").strip('"'))
    if subseries:
        cfg = AIWAYS_SUBSERIES[subseries]
        series_name = (
            f"{AIWAYS_SERIES_NAME_EN} — {cfg['name_en']}"
            if is_en
            else f"{AIWAYS_SERIES_NAME_JA} — {cfg['name_ja']}"
        )
    else:
        series_name = AIWAYS_SERIES_NAME_EN if is_en else AIWAYS_SERIES_NAME_JA

    canonical_url = f"{config.SITE_URL}{aiways_base}/{chapter_slug}/{example_name}/"

    files_html = _example_files_html(example_dir)
    files_heading = "ファイル一覧" if not is_en else "Files"
    back_label = (
        f"{chapter_label}「{chapter_title}」に戻る"
        if not is_en
        else f"Back to {chapter_label}: {chapter_title}"
    )

    variables = {
        "lang": lang,
        "example_title": example_title,
        "example_label": example_label,
        "subtitle": "",
        "description": (
            f"{chapter_title} の {example_label}: 実行可能なソースと実測結果。"
            if not is_en
            else f"{example_label} for {chapter_title}: runnable source and measured results."
        ),
        "canonical_url": canonical_url,
        "og_image": f"{config.SITE_URL}/aiseed-og-default.png",
        "series": series_name,
        "series_index_url": series_index_url,
        "chapter_label": chapter_label,
        "chapter_title": chapter_title,
        "chapter_url": chapter_url,
        "content_html": content_html,
        "files_html": files_html,
        "files_heading": files_heading,
        "back_label": back_label,
    }

    tpl_path = config.AIWAYS_DIR / "template-example.html"
    template_text = tpl_path.read_text(encoding="utf-8")
    html = Template(template_text).render(**variables)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"Built ai-native-ways example: {out_dir / 'index.html'}")
    return True


def collect_aiways_examples(chapter_dir):
    """Yield example-N/ subfolders of a chapter, sorted by N."""
    if not Path(chapter_dir).is_dir():
        return
    for sub in sorted(Path(chapter_dir).iterdir()):
        if sub.is_dir() and re.match(r"example-\d+$", sub.name):
            yield sub


def collect_aiways_chapters(lang="ja", subseries: str = ""):
    """Collect ai-native-ways chapter metadata for a language.

    With `subseries=""` (default), returns the parent-series chapters
    (`articles/ai-native-ways/NN-slug/`). With a sub-series key (e.g.
    `"software"`), returns that sub-series' chapters only.
    """
    if subseries:
        files = _iter_aiways_subseries_files(subseries, lang)
    else:
        files = _iter_article_files(config.AIWAYS_DIR, lang)

    chapters = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        if meta.get("lang", "ja") != lang and not (lang == "ja" and "lang" not in meta):
            continue
        num_match = re.match(r"(\d+)", f.parent.name)
        meta["_file_number"] = int(num_match.group(1)) if num_match else 0
        chapters.append(meta)
    chapters.sort(key=lambda m: m.get("_file_number", 0))
    return chapters


def _aiways_subseries_hero_html(lang: str) -> str:
    """Render hero cards announcing every active sub-series, to prepend to
    the parent series index. Returns '' if no sub-series have published chapters."""
    is_en = lang == "en"
    parts = []
    for key, cfg in AIWAYS_SUBSERIES.items():
        if not collect_aiways_chapters(lang, key):
            continue
        href = _aiways_url_base(lang, key) + "/"
        name = cfg["name_en"] if is_en else cfg["name_ja"]
        subtitle = cfg["subtitle_en"] if is_en else cfg["subtitle_ja"]
        description = cfg["description_en"] if is_en else cfg["description_ja"]
        label = (
            "Sub-series — open all chapters →"
            if is_en
            else "サブシリーズを開く →"
        )
        marker = "Sub-series" if is_en else "サブシリーズ"
        parts.append(f'''
                <a href="{href}" style="text-decoration: none; color: inherit; display: block; margin-bottom: 1.5em;">
                    <div class="activity-item fade-in" style="border-left: 4px solid #d4a574; background: rgba(212,165,116,0.08);">
                        <div class="activity-number" style="font-size: 0.65em; line-height: 1.2; padding: 0.4em 0; white-space: nowrap;">▶<br>{marker}</div>
                        <div class="activity-content">
                            <h3>{name} — {subtitle}</h3>
                            <p>{description}<br><strong>{label}</strong></p>
                        </div>
                    </div>
                </a>
''')
    return "".join(parts)


def _render_aiways_chapter_list(chapters, aiways_base: str, parts=None, lang="ja") -> str:
    """Render chapter cards for an aiways index page (parent or sub-series).

    If `parts` is given (a list of {upto, name_ja, name_en}), insert a part
    heading before the first chapter whose number falls into each part.
    """
    out = ""
    shown_parts = set()
    for c in chapters:
        slug = c.get("slug", "")
        number = c.get("number", "").strip('"')
        if parts:
            pk = str(c.get("part", "")).strip('"')
            if pk and pk not in shown_parts:
                shown_parts.add(pk)
                part = next((p for p in parts if str(p.get("key")) == pk), None)
                if part is not None:
                    pname = part["name_en" if lang == "en" else "name_ja"]
                    out += (
                        '\n                <h2 class="aiways-part" style="'
                        "margin:2.6rem 0 0.6rem;padding-bottom:0.3rem;"
                        "border-bottom:1px solid var(--rule,#d4cdb8);"
                        "color:var(--accent,#c8442a);font-size:1.05rem;"
                        'letter-spacing:0.04em;">' + pname + "</h2>\n"
                    )
        title = c.get("title", "")
        subtitle = c.get("subtitle", "")
        description = c.get("description", "")
        out += f'''
                <a href="{aiways_base}/{slug}/" style="text-decoration: none; color: inherit;">
                    <div class="activity-item fade-in">
                        <div class="activity-number">{number}</div>
                        <div class="activity-content">
                            <h3>{title}{(" — " + subtitle) if subtitle else ""}</h3>
                            <p>{description}</p>
                        </div>
                    </div>
                </a>
'''
    return out


def build_aiways_index(lang="ja"):
    """Build the ai-native-ways table-of-contents page using the shared
    index.html template (matches the look of the insights / claude-debian
    series indexes, while individual chapters keep their custom typography).

    The parent index lists parent-series chapters only. Sub-series are
    announced as a hero card at the top; their chapters are reachable via the
    dedicated sub-series index page.
    """
    chapters = collect_aiways_chapters(lang)
    if not chapters:
        return False

    is_en = lang == "en"
    aiways_base = _aiways_url_base(lang)
    has_translation = bool(collect_aiways_chapters("en" if not is_en else "ja"))

    chapter_list = (
        _aiways_subseries_hero_html(lang)
        + _render_aiways_chapter_list(chapters, aiways_base)
    )

    variables = aiways_index_vars(lang, chapter_list, has_translation=has_translation)
    html = render("index.html", variables)

    out_file = _aiways_output_base(lang) / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built ai-native-ways index: {out_file}")
    return True


def build_aiways_subseries_index(subseries: str, lang: str = "ja"):
    """Build the index page for one sub-series (e.g. /ai-native-ways/software/).

    Lists the sub-series' own chapters and links back to the parent index.
    """
    cfg = AIWAYS_SUBSERIES.get(subseries)
    if not cfg:
        return False
    chapters = collect_aiways_chapters(lang, subseries)
    if not chapters:
        return False

    is_en = lang == "en"
    aiways_base = _aiways_url_base(lang, subseries)
    has_translation = bool(collect_aiways_chapters("en" if not is_en else "ja", subseries))

    name = cfg["name_en"] if is_en else cfg["name_ja"]
    subtitle = cfg["subtitle_en"] if is_en else cfg["subtitle_ja"]
    description = cfg["description_en"] if is_en else cfg["description_ja"]
    series_title = (
        f"{AIWAYS_SERIES_NAME_EN} — {name}" if is_en
        else f"{AIWAYS_SERIES_NAME_JA} — {name}"
    )

    chapter_list = _render_aiways_chapter_list(
        chapters, aiways_base, parts=cfg.get("parts"), lang=lang
    )

    variables = aiways_index_vars(lang, chapter_list, has_translation=has_translation)
    # Override identity for the sub-series.
    variables["page_title"] = series_title
    variables["page_subtitle"] = subtitle
    variables["structural_analysis_label"] = series_title
    variables["meta_description"] = description
    variables["canonical_url"] = f"{config.SITE_URL}{aiways_base}/"
    variables["hreflang_ja"] = f"{config.SITE_URL}{_aiways_url_base('ja', subseries)}/"
    variables["hreflang_en"] = (
        f"{config.SITE_URL}{_aiways_url_base('en', subseries)}/" if has_translation else ""
    )
    variables["other_lang_link"] = (
        _aiways_url_base("ja" if is_en else "en", subseries) + "/"
    )
    # Override asset / image paths: the sub-series index sits one directory
    # deeper than the parent index, so all relative `../` paths need one
    # more `../` to reach the site root.
    variables["css_path"] = "../../../css/style.css" if is_en else "../../css/style.css"
    variables["js_path"] = "../../../js/main.js" if is_en else "../../js/main.js"
    variables["img_path"] = (
        "../../../images/IMG_3285.jpg" if is_en else "../../images/IMG_3285.jpg"
    )
    # Back-link to the parent series index (rendered into intro_html so we
    # don't need a template change).
    parent_href = _aiways_url_base(lang) + "/"
    back_label = (
        f"← Back to {AIWAYS_SERIES_NAME_EN}" if is_en
        else f"← {AIWAYS_SERIES_NAME_JA} 目次へ"
    )
    variables["intro_html"] = (
        f'<a href="{parent_href}" style="color: inherit;">{back_label}</a><br>\n'
        f'                    {description}'
    )
    # Sub-series specific CTAs.
    variables["cta_title"] = (
        "Start with Chapter 1" if is_en else "第1章から読み始める"
    )
    variables["cta_html"] = (
        f'<a href="{aiways_base}/{chapters[0].get("slug", "")}/">'
        f'{chapters[0].get("title", "")}</a>'
    )
    variables["series_description"] = description

    html = render("index.html", variables)
    out_file = _aiways_output_base(lang, subseries) / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")
    print(f"Built ai-native-ways sub-series index ({subseries}): {out_file}")
    return True


# ---------------------------------------------------------------------------
# Static pages: cache-bust style.css / main.js references
# ---------------------------------------------------------------------------

STATIC_PAGES_FOR_ASSETS = [
    "index.html",
    "404.html",
    "license/index.html",
    "en/index.html",
    "about/index.html",
    "en/about/index.html",
    "natural-farming/index.html",
    "en/natural-farming/index.html",
    "light-farming/index.html",
    "en/light-farming/index.html",
    "privacy/index.html",
    "en/privacy/index.html",
]

_CSS_REF_RE = re.compile(r'(href="[^"]*style\.css)(?:\?v=[^"]*)?(")')
_JS_REF_RE = re.compile(r'(src="[^"]*main\.js)(?:\?v=[^"]*)?(")')


def update_static_page_asset_versions():
    """Stamp the current asset version onto style.css / main.js references
    in hand-edited top-level pages. Without this, browsers keep serving the
    cached CSS/JS after a deploy until the file expires."""
    version = config.asset_version()
    replacement_css = rf'\1?v={version}\2'
    replacement_js = rf'\1?v={version}\2'
    html_root = config.SITE_ROOT / "html"
    updated = 0
    for rel in STATIC_PAGES_FOR_ASSETS:
        f = html_root / rel
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        new_text = _CSS_REF_RE.sub(replacement_css, text)
        new_text = _JS_REF_RE.sub(replacement_js, new_text)
        if new_text != text:
            f.write_text(new_text, encoding="utf-8")
            updated += 1
    if updated:
        print(f"Stamped asset version {version} onto {updated} static pages")


# ---------------------------------------------------------------------------
# Home page: latest blog posts section
# ---------------------------------------------------------------------------

HOME_LATEST_COUNT = 5
HOME_MARKER_START = "<!-- LATEST_BLOG_POSTS_START -->"
HOME_MARKER_END = "<!-- LATEST_BLOG_POSTS_END -->"


def update_home_latest_posts(lang="ja", count=HOME_LATEST_COUNT):
    """Replace the 'Latest Blog Posts' block on the home page with the N
    newest posts, sourced from the blog markdown frontmatter."""
    is_en = lang == "en"
    posts = collect_blog_posts(lang)[:count]
    if not posts:
        return False

    blog_base = "/en/blog" if is_en else "/blog"
    cards = []
    for p in posts:
        slug = p.get("slug", "")
        title = p.get("title", "")
        date = p.get("date", "")
        description = p.get("description", "")
        cards.append(
            f'                <a href="{blog_base}/{slug}/" class="article-link">\n'
            f'                    <div class="activity-item">\n'
            f'                        <div class="activity-number" style="font-size: 0.7rem;">{date}</div>\n'
            f'                        <div class="activity-content">\n'
            f'                            <h3>{title}</h3>\n'
            f'                            <p>{description}</p>\n'
            f'                        </div>\n'
            f'                    </div>\n'
            f'                </a>'
        )

    home = config.SITE_ROOT / "html" / ("en/index.html" if is_en else "index.html")
    if not home.exists():
        print(f"Warning: home page not found: {home}")
        return False

    text = home.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(HOME_MARKER_START) + r".*?" + re.escape(HOME_MARKER_END),
        re.DOTALL,
    )
    if not pattern.search(text):
        print(f"Warning: markers not found in {home}")
        return False

    block = (
        HOME_MARKER_START
        + "\n\n"
        + "\n\n".join(cards)
        + "\n\n                "
        + HOME_MARKER_END
    )
    new_text = pattern.sub(block, text)
    home.write_text(new_text, encoding="utf-8")
    print(f"Updated home latest posts: {home}")
    return True


# ---------------------------------------------------------------------------
# Sitemap & robots.txt
# ---------------------------------------------------------------------------

def build_sitemap():
    """Generate sitemap.xml from all articles."""
    from datetime import date

    def norm_date(d):
        """Convert YYYY.MM.DD or YYYY-MM-DD to ISO YYYY-MM-DD."""
        if not d:
            return date.today().isoformat()
        return str(d).replace(".", "-")

    ja_articles = collect_articles("ja")
    en_articles = collect_articles("en")
    ja_posts = collect_blog_posts("ja")
    en_posts = collect_blog_posts("en")
    ja_chapters = collect_book_chapters("ja")
    en_chapters = collect_book_chapters("en")
    ja_aiways = collect_aiways_chapters("ja")
    en_aiways = collect_aiways_chapters("en")
    aiways_sub_metas = []
    for sub_key in AIWAYS_SUBSERIES:
        aiways_sub_metas.extend(collect_aiways_chapters("ja", sub_key))
        aiways_sub_metas.extend(collect_aiways_chapters("en", sub_key))
    ja_farming = collect_farming_chapters("ja")
    en_farming = collect_farming_chapters("en")
    ja_fable = collect_fable_chapters("ja")
    en_fable = collect_fable_chapters("en")

    all_dates = [
        norm_date(m.get("date"))
        for m in (*ja_articles, *en_articles, *ja_posts, *en_posts,
                  *ja_chapters, *en_chapters, *ja_aiways, *en_aiways,
                  *aiways_sub_metas, *ja_farming, *en_farming,
                  *ja_fable, *en_fable)
        if m.get("date")
    ]
    latest = max(all_dates) if all_dates else date.today().isoformat()

    site_url = config.SITE_URL

    urls = []
    # Homepages
    urls.append((f"{site_url}/", latest, "1.0"))
    urls.append((f"{site_url}/en/", latest, "0.8"))

    # Index pages
    urls.append((f"{site_url}/insights/", latest, "0.9"))
    urls.append((f"{site_url}/en/insights/", latest, "0.8"))

    # JP articles
    for meta in ja_articles:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/insights/{slug}/", norm_date(meta.get("date")), "0.7"))

    # EN articles
    for meta in en_articles:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/en/insights/{slug}/", norm_date(meta.get("date")), "0.6"))

    # Blog index pages
    urls.append((f"{site_url}/blog/", latest, "0.8"))
    urls.append((f"{site_url}/en/blog/", latest, "0.7"))

    # JP blog posts
    for meta in ja_posts:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/blog/{slug}/", norm_date(meta.get("date")), "0.7"))

    # EN blog posts
    for meta in en_posts:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/en/blog/{slug}/", norm_date(meta.get("date")), "0.6"))

    # Book: Claudeと一緒に学ぶDebian / Learning Debian with Claude
    if ja_chapters:
        urls.append((f"{site_url}/claude-debian/", latest, "0.8"))
        for meta in ja_chapters:
            slug = meta.get("slug", "")
            if not slug:
                continue
            stem = _book_stem(slug)
            urls.append((f"{site_url}/claude-debian/{stem}/", norm_date(meta.get("date")), "0.6"))
    if en_chapters:
        urls.append((f"{site_url}/en/claude-debian/", latest, "0.7"))
        for meta in en_chapters:
            slug = meta.get("slug", "")
            if not slug:
                continue
            stem = _book_stem(slug)
            urls.append((f"{site_url}/en/claude-debian/{stem}/", norm_date(meta.get("date")), "0.5"))

    # Book sub-series (e.g. /claude-debian/server/)
    for sub_key in BOOK_SUBSERIES:
        ja_sub = collect_book_chapters("ja", sub_key)
        en_sub = collect_book_chapters("en", sub_key)
        if ja_sub:
            urls.append((f"{site_url}/claude-debian/{sub_key}/", latest, "0.8"))
            for meta in ja_sub:
                slug = meta.get("slug", "")
                if not slug:
                    continue
                stem = _book_stem(slug, sub_key)
                urls.append((
                    f"{site_url}/claude-debian/{sub_key}/{stem}/",
                    norm_date(meta.get("date")), "0.6",
                ))
        if en_sub:
            urls.append((f"{site_url}/en/claude-debian/{sub_key}/", latest, "0.7"))
            for meta in en_sub:
                slug = meta.get("slug", "")
                if not slug:
                    continue
                stem = _book_stem(slug, sub_key)
                urls.append((
                    f"{site_url}/en/claude-debian/{sub_key}/{stem}/",
                    norm_date(meta.get("date")), "0.5",
                ))

    # AI-Native Ways of Working
    if ja_aiways:
        urls.append((f"{site_url}/ai-native-ways/", latest, "0.8"))
        for meta in ja_aiways:
            slug = meta.get("slug", "")
            if not slug:
                continue
            urls.append((f"{site_url}/ai-native-ways/{slug}/", norm_date(meta.get("date")), "0.6"))
    if en_aiways:
        urls.append((f"{site_url}/en/ai-native-ways/", latest, "0.7"))
        for meta in en_aiways:
            slug = meta.get("slug", "")
            if not slug:
                continue
            urls.append((f"{site_url}/en/ai-native-ways/{slug}/", norm_date(meta.get("date")), "0.5"))

    # AI-Native Ways — sub-series (e.g. /ai-native-ways/software/)
    for sub_key in AIWAYS_SUBSERIES:
        ja_sub = collect_aiways_chapters("ja", sub_key)
        en_sub = collect_aiways_chapters("en", sub_key)
        if ja_sub:
            urls.append((f"{site_url}/ai-native-ways/{sub_key}/", latest, "0.8"))
            for meta in ja_sub:
                slug = meta.get("slug", "")
                if not slug:
                    continue
                urls.append((
                    f"{site_url}/ai-native-ways/{sub_key}/{slug}/",
                    norm_date(meta.get("date")), "0.6",
                ))
        if en_sub:
            urls.append((f"{site_url}/en/ai-native-ways/{sub_key}/", latest, "0.7"))
            for meta in en_sub:
                slug = meta.get("slug", "")
                if not slug:
                    continue
                urls.append((
                    f"{site_url}/en/ai-native-ways/{sub_key}/{slug}/",
                    norm_date(meta.get("date")), "0.5",
                ))

    # Natural Farming landing page (static)
    urls.append((f"{site_url}/natural-farming/", latest, "0.8"))
    urls.append((f"{site_url}/en/natural-farming/", latest, "0.7"))
    # Phosphorus and Natural Farming series chapters
    if ja_farming:
        urls.append((f"{site_url}/phosphorus-and-farming/", latest, "0.8"))
    if en_farming:
        urls.append((f"{site_url}/en/phosphorus-and-farming/", latest, "0.7"))
    for meta in ja_farming:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/phosphorus-and-farming/{slug}/", norm_date(meta.get("date")), "0.6"))
    for meta in en_farming:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/en/phosphorus-and-farming/{slug}/", norm_date(meta.get("date")), "0.5"))
    # Fable 5 が帰ってきた — serialized column
    if ja_fable:
        urls.append((f"{site_url}/fable/", latest, "0.8"))
    if en_fable:
        urls.append((f"{site_url}/en/fable/", latest, "0.7"))
    for meta in ja_fable:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/fable/{slug}/", norm_date(meta.get("date")), "0.6"))
    for meta in en_fable:
        slug = meta.get("slug", "")
        if not slug:
            continue
        urls.append((f"{site_url}/en/fable/{slug}/", norm_date(meta.get("date")), "0.5"))
    # サイト独自シリーズ(site.json の builder.series)
    for sdef in series_expansion.custom_series_defs(config.SITE_ROOT):
        base = sdef["url_base"].strip("/")
        for lang, prefix, prio_index, prio_page in (
                ("ja", "", "0.8", "0.6"), ("en", "/en", "0.7", "0.5")):
            metas = collect_custom_chapters(sdef, lang)
            if not metas:
                continue
            urls.append((f"{site_url}{prefix}/{base}/", latest, prio_index))
            for meta in metas:
                slug = meta.get("slug", "")
                if slug:
                    urls.append((f"{site_url}{prefix}/{base}/{slug}/",
                                 norm_date(meta.get("date")), prio_page))

    urls.append((f"{site_url}/about/", latest, "0.6"))
    urls.append((f"{site_url}/en/about/", latest, "0.5"))
    urls.append((f"{site_url}/light-farming/", latest, "0.8"))
    urls.append((f"{site_url}/en/light-farming/", latest, "0.7"))
    urls.append((f"{site_url}/privacy/", latest, "0.3"))
    urls.append((f"{site_url}/en/privacy/", latest, "0.2"))

    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    # 実在するページだけを載せる(雛形が想定する固定ページが無いサイトのため)
    _html_root = config.SITE_ROOT / "html"
    urls = [u for u in urls
            if (_html_root / u[0][len(site_url):].strip("/") / "index.html").exists()]
    xml_lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for loc, lastmod, priority in urls:
        xml_lines.append("  <url>")
        xml_lines.append(f"    <loc>{loc}</loc>")
        xml_lines.append(f"    <lastmod>{lastmod}</lastmod>")
        xml_lines.append(f"    <priority>{priority}</priority>")
        xml_lines.append("  </url>")
    xml_lines.append("</urlset>")

    out = config.SITE_ROOT / "html" / "sitemap.xml"
    out.write_text("\n".join(xml_lines) + "\n", encoding="utf-8")
    print(f"Built: {out}")


def build_robots():
    """Generate robots.txt."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /tools/\n"
        "Disallow: /articles/\n"
        "\n"
        f"Sitemap: {config.SITE_URL}/sitemap.xml\n"
    )
    out = config.SITE_ROOT / "html" / "robots.txt"
    out.write_text(content, encoding="utf-8")
    print(f"Built: {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def regenerate_ogp(md_path):
    """Regenerate only the og-image.jpg for a given article/blog markdown file."""
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False
    meta, _ = parse_frontmatter(md_path.read_text(encoding="utf-8"))
    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug'")
        return False
    hero = meta.get("hero_image", "").strip()
    if not hero:
        print(f"Skip: {md_path} has no hero_image")
        return False

    is_blog = md_path.parent.parent.name == "blog"
    is_en = (meta.get("lang") or _detect_lang(md_path)) == "en"
    if is_blog:
        base = config.SITE_ROOT / "html" / "en" / "blog" if is_en else config.BLOG_OUTPUT_BASE
    else:
        base = config.SITE_ROOT / "html" / "en" / "insights" if is_en else config.OUTPUT_BASE
    out_dir = base / meta["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)

    src = md_path.parent / hero
    out = out_dir / OGP_FILENAME
    if generate_ogp_image(src, out):
        print(f"OGP: {out} ({OGP_SIZE[0]}x{OGP_SIZE[1]}) from {src.name}")
        return True
    print(f"Error: source image {src} not found")
    return False


def _build_series(subdir):
    """1シリーズ分(展開済みツリーの subdir、例 "blog"/"claude-debian/server")
    を記事+索引までビルドする。シリーズファイル(articles/<series>.adoc)を
    引数に指定したとき、および serve.py の差分ビルドから使う。"""
    root, _, sub_key = subdir.partition("/")

    def _files(series_dir, recurse=False):
        return list(_iter_article_files(series_dir, "ja", recurse=recurse)) \
             + list(_iter_article_files(series_dir, "en", recurse=recurse))

    if root == "insights":
        for f in _files(config.INSIGHTS_DIR, recurse=True):
            build_article(f)
        build_index("ja")
        build_index("en")
    elif root == "blog":
        for f in _files(config.BLOG_DIR):
            build_blog_post(f)
        build_blog_index("ja")
        build_blog_index("en")
        update_home_latest_posts("ja")
        update_home_latest_posts("en")
    elif root == "claude-debian":
        series_dir = config.BOOK_DIR / sub_key if sub_key else config.BOOK_DIR
        for f in _files(series_dir):
            build_book_chapter(f)
        if sub_key:
            for lang in ("ja", "en"):
                if collect_book_chapters(lang, sub_key):
                    build_book_subseries_index(sub_key, lang)
        else:
            for lang in ("ja", "en"):
                if collect_book_chapters(lang):
                    build_book_index(lang)
    elif root == "ai-native-ways":
        series_dir = config.AIWAYS_DIR / sub_key if sub_key else config.AIWAYS_DIR
        for f in _files(series_dir):
            build_aiways_chapter(f)
        if sub_key:
            for lang in ("ja", "en"):
                if collect_aiways_chapters(lang, sub_key):
                    build_aiways_subseries_index(sub_key, lang)
        else:
            for lang in ("ja", "en"):
                if collect_aiways_chapters(lang):
                    build_aiways_index(lang)
        for chapter_sub in (sorted(series_dir.iterdir())
                            if series_dir.exists() else []):
            if not (chapter_sub.is_dir() and chapter_sub.name[:1].isdigit()):
                continue
            for ex in collect_aiways_examples(chapter_sub):
                for ex_lang in ("ja", "en"):
                    if resolve_source(chapter_sub, ex_lang) is not None:
                        build_aiways_example(ex, ex_lang)
    elif root == "phosphorus-and-farming":
        for f in _files(config.FARMING_DIR):
            build_farming_chapter(f)
        for lang in ("ja", "en"):
            if collect_farming_chapters(lang):
                build_farming_index(lang)
    elif root == "fable":
        for f in _files(config.FABLE_DIR):
            build_fable_chapter(f)
        for lang in ("ja", "en"):
            if collect_fable_chapters(lang):
                build_fable_index(lang)
    else:
        print(f"未知のシリーズです: {subdir}", file=sys.stderr)
        return False
    return True


def copy_form_assets():
    """pywashi の form-render.js / form.css を html/ に配る([.form] 描画用)。
    pywashi が無ければ何もしない(フォームを使わないサイトでは不要)。"""
    try:
        from importlib.resources import files
        assets = files("pywashi") / "form_assets"
    except (ImportError, ModuleNotFoundError):
        return
    for name, sub in (("form-render.js", "js"), ("form.css", "css")):
        src = assets / name
        if src.is_file():
            dest = config.SITE_ROOT / "html" / sub / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())


# ---------------------------------------------------------------------------
# サイト独自シリーズ(site.json の builder.series で宣言したもの)
#
# 組み込みの6シリーズと違い、サイトごとに名前・URL・ラベルを決められる。
# 「固定ページ」「お知らせ」など、そのサイトにしかない区分のためのもの。
# 記事ページと索引ページの作りは fable(いちばん素直な連載)に倣う。
# ---------------------------------------------------------------------------


def _custom_dir(sdef, lang="ja"):
    """展開済みツリー上の、このシリーズのソースディレクトリ。"""
    return config.SITE_ROOT / ".build" / "articles" / sdef["url_base"].strip("/")


def _custom_url_base(sdef, lang):
    base = "/" + sdef["url_base"].strip("/")
    return ("/en" + base) if lang == "en" else base


def collect_custom_chapters(sdef, lang="ja"):
    chapters = []
    for f in _iter_article_files(_custom_dir(sdef), lang):
        meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
        num_match = re.match(r"(\d+)", f.parent.name)
        meta["_file_number"] = int(num_match.group(1)) if num_match else 0
        chapters.append(meta)
    chapters.sort(key=lambda m: m.get("_file_number", 0))
    return chapters


def build_custom_chapter(md_path, sdef):
    """サイト独自シリーズの記事1本をビルドする。"""
    md_path = Path(md_path)
    if not md_path.exists():
        print(f"Error: {md_path} not found")
        return False
    meta, body = parse_frontmatter(md_path.read_text(encoding="utf-8"))
    if "slug" not in meta:
        print(f"Error: {md_path} missing 'slug' in front matter")
        return False

    lang = meta.get("lang") or _detect_lang(md_path)
    meta["lang"] = lang
    is_en = lang == "en"
    slug = meta["slug"]
    base = sdef["url_base"].strip("/")
    url_base = _custom_url_base(sdef, lang)

    out_dir = config.SITE_ROOT / "html" / ("en/" + base if is_en else base) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    meta["_source_dir"] = str(md_path.parent)
    meta["_out_dir"] = str(out_dir)

    body_html = render_body(body, md_path)
    series_name = sdef.get("label", base)
    series_index_url = f"{url_base}/"
    canonical_url = f"{config.SITE_URL}{url_base}/{slug}/"
    hreflang_ja = f"{config.SITE_URL}/{base}/{slug}/"
    hreflang_en = f"{config.SITE_URL}/en/{base}/{slug}/"
    has_other = sibling_exists(md_path.parent, "ja" if is_en else "en")

    from build.images import resolve_og_image
    og_image = resolve_og_image(meta, out_dir,
                                public_base_url=canonical_url.rstrip("/"))

    prev_slug = meta.get("prev_slug", "")
    next_slug = meta.get("next_slug", "")
    nav_html = _chapter_nav_html(
        (f"{url_base}/{prev_slug}/", meta.get("prev_title", "")) if prev_slug else None,
        (f"{url_base}/{next_slug}/", meta.get("next_title", "")) if next_slug else None,
        (series_index_url, "Contents" if is_en else "目次"),
        lang,
    )
    variables = _chapter_vars(
        meta, body_html,
        series_name=series_name,
        series_index_url=series_index_url,
        chapter_label=series_name,
        canonical_url=canonical_url,
        hreflang_ja=hreflang_ja if sibling_exists(md_path.parent, "ja") else "",
        hreflang_en=hreflang_en if sibling_exists(md_path.parent, "en") else "",
        og_image=og_image,
        other_lang_url=(hreflang_ja if is_en else hreflang_en) if has_other else "",
        other_lang_label="日本語" if is_en else "EN",
        nav_html=nav_html,
        masthead_kind=series_name,
        masthead_right=(f"No. {_num} / {series_name}" if (_num := meta.get("number", "").strip(chr(34))) else series_name),
        colophon_label=f"{series_name} {meta.get('number', '').strip(chr(34))}".strip(),
    )
    (out_dir / "index.html").write_text(
        render(_chapter_template(lang), variables), encoding="utf-8")
    copy_images(md_path.parent, out_dir, lang=lang)
    print(f"Built {base}: {out_dir / 'index.html'}")
    return True


def build_custom_index(sdef, lang="ja"):
    """サイト独自シリーズの索引ページ。"""
    chapters = collect_custom_chapters(sdef, lang)
    if not chapters:
        return False
    base = sdef["url_base"].strip("/")
    url_base = _custom_url_base(sdef, lang)
    other = "en" if lang == "ja" else "ja"

    items = ""
    for c in chapters:
        items += f'''
                <a href="{url_base}/{c.get("slug", "")}/" style="text-decoration: none; color: inherit;">
                    <div class="activity-item fade-in">
                        <div class="activity-number">{c.get("number", "").strip(chr(34))}</div>
                        <div class="activity-content">
                            <h3>{c.get("title", "")}{(' <span class="item-sub">— ' + c["subtitle"] + "</span>") if c.get("subtitle") else ""}</h3>
                            <p>{c.get("description", "")}</p>
                        </div>
                    </div>
                </a>
'''
    variables = custom_index_vars(
        lang, items,
        title=sdef.get("label", base),
        subtitle=sdef.get("subtitle", ""),
        description=sdef.get("description", ""),
        url_base=base,
        has_translation=bool(collect_custom_chapters(sdef, other)),
    )
    out_file = (config.SITE_ROOT / "html"
                / ("en/" + base if lang == "en" else base) / "index.html")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(render("index.html", variables), encoding="utf-8")
    print(f"Built {base} index: {out_file}")
    return True


def build_custom_series(sdef):
    """1シリーズ分(記事+索引・日英)。ビルドした記事数を返す。"""
    n = 0
    for lang in ("ja", "en"):
        for f in _iter_article_files(_custom_dir(sdef), lang):
            if build_custom_chapter(f, sdef):
                n += 1
        build_custom_index(sdef, lang)
    return n


def main():
    site, args = config.resolve_site(sys.argv[1:])
    config.configure_site(site)
    series_expansion.expand_all()
    copy_form_assets()

    if not args:
        print("Usage: python3 tools/build_article.py [--site <dir>] <article.md>")
        print("       python3 tools/build_article.py [--site <dir>] --all")
        print("       python3 tools/build_article.py [--site <dir>] --list")
        print("       python3 tools/build_article.py [--site <dir>] --ogp <article.md>")
        sys.exit(1)

    arg = args[0]

    if arg == "--ogp":
        if len(args) < 2:
            print("Usage: python3 tools/build_article.py [--site <dir>] --ogp <article.md>")
            sys.exit(1)
        regenerate_ogp(args[1])
        return

    if arg == "--list":
        for f in list(_iter_article_files(config.INSIGHTS_DIR, "ja", recurse=True)):
            meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
            num = meta.get("number", "??")
            title = meta.get("title", "untitled")
            print(f"  [{num}] {f.parent.name}/{f.name} → {title}")
        return

    if arg == "--all":
        files = list(_iter_article_files(config.INSIGHTS_DIR, "ja", recurse=True)) \
              + list(_iter_article_files(config.INSIGHTS_DIR, "en", recurse=True))
        ok = 0
        if files:
            for f in files:
                if build_article(f):
                    ok += 1
            build_index("ja")
            build_index("en")
        else:
            # insights を持たないサイト(記事シリーズだけのサイト)でも先へ進む
            print(f"No insights articles in {config.INSIGHTS_DIR} (skipped)")

        # Build blog posts
        blog_ok = 0
        blog_files = list(_iter_article_files(config.BLOG_DIR, "ja")) \
                   + list(_iter_article_files(config.BLOG_DIR, "en"))
        for f in blog_files:
            if build_blog_post(f):
                blog_ok += 1
        if blog_files:
            build_blog_index("ja")
            update_home_latest_posts("ja")
            # 英語の記事が一本も無いサイトでは /en/ を作らない
            if any(True for _ in _iter_article_files(config.BLOG_DIR, "en")):
                build_blog_index("en")
                if (config.SITE_ROOT / "html" / "en" / "index.html").exists():
                    update_home_latest_posts("en")

        # Build book chapters (claude-debian) — JA + EN, including every
        # sub-series.
        book_ok = 0
        book_files = list(_iter_article_files(config.BOOK_DIR, "ja")) \
                   + list(_iter_article_files(config.BOOK_DIR, "en"))
        for sub_key in BOOK_SUBSERIES:
            book_files += list(_iter_article_files(config.BOOK_DIR / sub_key, "ja"))
            book_files += list(_iter_article_files(config.BOOK_DIR / sub_key, "en"))
        for f in book_files:
            if build_book_chapter(f):
                book_ok += 1
        if collect_book_chapters("ja"):
            build_book_index("ja")
        if collect_book_chapters("en"):
            build_book_index("en")
        for sub_key in BOOK_SUBSERIES:
            if collect_book_chapters("ja", sub_key):
                build_book_subseries_index(sub_key, "ja")
            if collect_book_chapters("en", sub_key):
                build_book_subseries_index(sub_key, "en")

        # Build ai-native-ways essays — JA + EN, including every sub-series.
        aiways_ok = 0
        aiways_files = list(_iter_article_files(config.AIWAYS_DIR, "ja")) \
                     + list(_iter_article_files(config.AIWAYS_DIR, "en"))
        for sub_key in AIWAYS_SUBSERIES:
            aiways_files += list(_iter_aiways_subseries_files(sub_key, "ja"))
            aiways_files += list(_iter_aiways_subseries_files(sub_key, "en"))
        for f in aiways_files:
            if build_aiways_chapter(f):
                aiways_ok += 1
        if collect_aiways_chapters("ja"):
            build_aiways_index("ja")
        if collect_aiways_chapters("en"):
            build_aiways_index("en")
        for sub_key in AIWAYS_SUBSERIES:
            if collect_aiways_chapters("ja", sub_key):
                build_aiways_subseries_index(sub_key, "ja")
            if collect_aiways_chapters("en", sub_key):
                build_aiways_subseries_index(sub_key, "en")

        # Build phosphorus-and-farming chapters — JA + EN
        farming_ok = 0
        farming_files = list(_iter_article_files(config.FARMING_DIR, "ja")) \
                      + list(_iter_article_files(config.FARMING_DIR, "en"))
        for f in farming_files:
            if build_farming_chapter(f):
                farming_ok += 1
        if collect_farming_chapters("ja"):
            build_farming_index("ja")
        if collect_farming_chapters("en"):
            build_farming_index("en")

        # Build fable installments — JA + EN
        fable_ok = 0
        fable_files = list(_iter_article_files(config.FABLE_DIR, "ja")) \
                    + list(_iter_article_files(config.FABLE_DIR, "en"))
        for f in fable_files:
            if build_fable_chapter(f):
                fable_ok += 1
        if collect_fable_chapters("ja"):
            build_fable_index("ja")
        if collect_fable_chapters("en"):
            build_fable_index("en")

        # Build ai-native-ways examples — every chapter's example-N/ folders,
        # in parent series and every sub-series.
        examples_ok = examples_total = 0
        # シリーズのディレクトリが無いサイト(雛形から作った新しいサイト等)
        # でも通るように、存在確認してから走査する
        chapter_dirs = [
            sub for sub in sorted(config.AIWAYS_DIR.iterdir())
            if sub.is_dir() and sub.name[:1].isdigit()
        ] if config.AIWAYS_DIR.exists() else []
        for sub_key in AIWAYS_SUBSERIES:
            sub_dir = config.AIWAYS_DIR / sub_key
            if not sub_dir.exists():
                continue
            chapter_dirs += [
                sub for sub in sorted(sub_dir.iterdir())
                if sub.is_dir() and sub.name[:1].isdigit()
            ]
        for chapter_sub in chapter_dirs:
            for ex in collect_aiways_examples(chapter_sub):
                for ex_lang in ("ja", "en"):
                    if resolve_source(chapter_sub, ex_lang) is None:
                        continue
                    examples_total += 1
                    if build_aiways_example(ex, ex_lang):
                        examples_ok += 1

        # サイト独自シリーズ(site.json の builder.series で宣言したもの)
        custom_ok = 0
        for sdef in series_expansion.custom_series_defs(config.SITE_ROOT):
            custom_ok += build_custom_series(sdef)

        update_static_page_asset_versions()
        build_sitemap()
        build_robots()
        print(
            f"\nBuilt {ok}/{len(files)} articles + {blog_ok}/{len(blog_files)} blog posts"
            f" + {book_ok}/{len(book_files)} book chapters"
            f" + {aiways_ok}/{len(aiways_files)} ai-native-ways"
            f" + {examples_ok}/{examples_total} ai-native-ways examples"
            f" + {farming_ok}/{len(farming_files)} phosphorus-and-farming chapters"
            f" + {fable_ok}/{len(fable_files)} fable installments"
            f" + {custom_ok} custom-series articles"
            f" + indexes + sitemap.xml + robots.txt."
        )
        return

    # シリーズファイル(articles/blog.adoc 等)を指定されたら、そのシリーズを
    # まるごとビルドする(展開は main 冒頭の expand_all() で済んでいる)
    p_series = Path(arg)
    if p_series.suffix == ".adoc":
        if p_series.name in series_expansion.SERIES_MAP:
            _build_series(series_expansion.SERIES_MAP[p_series.name])
            return
        for sdef in series_expansion.custom_series_defs(config.SITE_ROOT):
            if sdef["file"] == p_series.name:
                build_custom_series(sdef)
                return

    # Auto-detect book / blog / aiways / article from the path. Per-folder
    # layout means the series name is the grandparent dir of
    # `<series>/<NN-slug>/<lang>.md`.
    p = Path(arg).resolve()
    series = p.parent.parent.name if p.is_file() else ""
    if series == "claude-debian" or "claude-debian/" in arg:
        build_book_chapter(arg)
    elif series == "blog" or "/blog/" in arg or arg.startswith("blog/"):
        build_blog_post(arg)
    elif series == "ai-native-ways" or "ai-native-ways/" in arg:
        build_aiways_chapter(arg)
    elif series == "phosphorus-and-farming" or "phosphorus-and-farming/" in arg:
        build_farming_chapter(arg)
    elif series == "fable" or "/fable/" in arg or arg.startswith("fable/"):
        build_fable_chapter(arg)
    else:
        build_article(arg)


if __name__ == "__main__":
    main()
