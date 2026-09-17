"""Markdown parsing: frontmatter, custom ::: blocks, CommonMark rendering."""

import html
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_cjk_friendly import cjk_friendly

# フロントマター解析は依存ゼロの frontmatter.py が正。ここでは
# 従来の import 元(build.markdown.parse_frontmatter)を保つため再輸出する。
from .frontmatter import parse_frontmatter  # noqa: F401

# CommonMark + tables (used for both article bodies and inline `:::highlight`)
# 日本語 (CJK) と CommonMark の相性問題 (softbreak の不要な空白、
# 全角約物に隣接した **強調** の不成立) は mdit-py-cjk-friendly が
# パーサレベルで解決する。
md = (MarkdownIt("commonmark", {"html": True})
      .enable("table")
      .use(cjk_friendly))


# ---------------------------------------------------------------------------
# Mermaid: convert fenced ```mermaid blocks into <div class="mermaid"> so the
# Mermaid runtime can render them. Runs on the rendered HTML (after CommonMark)
# because doing it pre-render breaks on the blank lines that Mermaid DSL allows
# inside a single block.
# ---------------------------------------------------------------------------

_MERMAID_HTML_RE = re.compile(
    r'<pre><code class="language-mermaid">(.*?)</code></pre>',
    re.DOTALL,
)


def process_mermaid_blocks(html_text):
    """Replace markdown-rendered ```mermaid code blocks with <div class="mermaid">.

    markdown-it produces <pre><code class="language-mermaid">…</code></pre>
    with the diagram body HTML-escaped. Mermaid expects raw DSL text inside
    a div, so we unescape and re-wrap."""
    def _replace(match):
        diagram = html.unescape(match.group(1)).rstrip()
        return f'<div class="mermaid">\n{diagram}\n</div>'
    return _MERMAID_HTML_RE.sub(_replace, html_text)





def strip_leading_title(body):
    """Drop the leading `# Title` line if present.

    Blog posts (and some articles) duplicate their frontmatter `title:` as
    the first H1 of the body; the template already renders `{{ title }}` in
    the page hero, so leaving the H1 in the body produces two titles on the
    page. No-op when the body doesn't start with `# `.
    """
    if not body.startswith("# "):
        return body
    _, _, rest = body.partition("\n")
    return rest.lstrip("\n")


def translation_exists(md_path, lang):
    """Check whether the opposite-language sibling source file exists
    (`.md` or `.adoc` — see `sibling_exists`).

    Per-folder layout: each article lives in its own directory containing
    `ja.{md,adoc}` and `en.{md,adoc}` side by side.
    """
    md_path = Path(md_path)
    other_lang = "en" if lang == "ja" else "ja"
    return sibling_exists(md_path.parent, other_lang)


# ---------------------------------------------------------------------------
# AsciiDoc: `ja.adoc`/`en.adoc` is an alternative to `ja.md`/`en.md` in the
# same per-folder layout, for articles that outgrow CommonMark (tables,
# footnotes, quote attribution — the things the `:::` custom blocks below
# were invented to fake). Frontmatter parsing is unchanged (it just looks
# for a leading `---` block regardless of what follows); only body
# rendering branches on the file extension. pyasciidoc is an optional
# dependency — it's only imported when a `.adoc` file is actually built,
# with a clear error if missing (same convention as aiseed-migration-kit's
# `amig convert`: don't force every user to install it, but don't fail
# silently either).
# ---------------------------------------------------------------------------

SOURCE_EXTS = (".md", ".adoc")

# 両形式が同居するフォルダへの警告は1回だけ出す(collect_* が同じ
# フォルダを何度も走査するため)
_warned_ambiguous = set()


def resolve_source(article_dir, lang):
    """Return the `<lang>.md` or `<lang>.adoc` file in `article_dir`, or
    `None` if neither exists.

    If BOTH exist the source is ambiguous; we refuse to guess which one is
    authoritative, warn once on stderr, and return `None` — the article is
    skipped consistently everywhere (build, indexes, --list, hreflang)
    instead of publishing a possibly-stale version. Aborting the whole
    build here would be worse: one mid-conversion folder would kill
    --list, unrelated single-article builds, and serve.py startup.
    """
    article_dir = Path(article_dir)
    found = [
        p for ext in SOURCE_EXTS
        if (p := article_dir / f"{lang}{ext}").exists()
    ]
    if len(found) > 1:
        key = (article_dir, lang)
        if key not in _warned_ambiguous:
            _warned_ambiguous.add(key)
            names = " と ".join(p.name for p in found)
            print(
                f"警告: {article_dir} に {names} が両方あります。"
                "どちらが正か推測できないため、この記事はスキップします"
                "(片方だけにしてください)",
                file=sys.stderr,
            )
        return None
    return found[0] if found else None


def sibling_exists(article_dir, lang):
    """Whether a `<lang>.md` or `<lang>.adoc` source file exists."""
    return resolve_source(article_dir, lang) is not None


_ADOC_TITLE_RE = re.compile(r"^=[ \t]+\S.*(?:\n|$)")


def _strip_leading_adoc_title(body):
    """Drop a leading `= Title` line if present (same reasoning as
    `strip_leading_title` for Markdown's `# Title`)."""
    m = _ADOC_TITLE_RE.match(body)
    if not m:
        return body
    return body[m.end():].lstrip("\n")


# ---------------------------------------------------------------------------
# AsciiDoc 出力の後処理: 旧 ::: カスタムブロックと同じ見た目に揃える。
# (mermaid の後処理 process_mermaid_blocks と同じ「レンダリング後の HTML を
# 正規表現で整形する」方式。)
# ---------------------------------------------------------------------------

_CHAIN_DIV_RE = re.compile(
    r'(<div class="chain-diagram">)(.*?)(</div>)', re.DOTALL
)


def process_chain_blocks(html_text):
    """`[.chain-diagram]` ロールの中身を旧 :::chain の出力形式に揃える。

    pyasciidoc は role を <div class="chain-diagram"><p>…</p></div> にする。
    旧パイプラインは <p> なしで、行内改行を <br>、段落間を <br><br>、
    `→` を <span class="arrow">&rarr;</span> にしていた。CSS(.chain-diagram)
    はその形を前提にしているため、ここで同じ形へ変換する。"""
    def _replace(match):
        inner = match.group(2).strip()
        inner = re.sub(r"</p>\s*<p>", "<br><br>", inner)
        inner = inner.replace("<p>", "").replace("</p>", "")
        inner = inner.replace("→", '<span class="arrow">&rarr;</span>')
        # 行内の改行は AsciiDoc のハード改行(` +` → <br />)が担うので、
        # ここでは <br> への変換はしない(二重改行になるため)
        return f"{match.group(1)}\n{inner}\n{match.group(3)}"
    return _CHAIN_DIV_RE.sub(_replace, html_text)


def _add_table_class(html_text):
    """pyasciidoc の素の <table> に旧 :::compare と同じクラスを付ける。
    旧 Markdown パイプラインで表になるのは事実上 :::compare だけなので、
    .adoc の表はすべて comparison-table の見た目で揃える。"""
    return html_text.replace("<table>", '<table class="comparison-table fade-in">')


# .adoc 用レンダラ(遅延構築・使い回し)。pyasciidoc の asciidoc プラグインに
# pywashi の form プラグインを合成する——本文中の [.form] + JSON が
# <div class="fr-form"><script type="application/json">…</script></div> になり、
# ページ側の form-render.js が対話的フォームを描く(FormRescue)。
# form プラグインは pyasciidoc の総称ロール(ad_role_block)より前に自分を
# 置くよう設計されているので、.use(asciidoc) の後に .use(form) すればよい。
_md_adoc_cache = None


def _adoc_md():
    global _md_adoc_cache
    if _md_adoc_cache is None:
        try:
            from pyasciidoc import asciidoc
        except ImportError:
            raise SystemExit(
                "pyasciidoc がインストールされていません。"
                "`pip install pyasciidoc` を実行してください"
                "(.adoc 記事のビルドに必要です)。"
            ) from None
        md_ = MarkdownIt("commonmark", {"html": False}).use(asciidoc)
        try:
            from pywashi.form import form as form_plugin
            md_.use(form_plugin)
        except ImportError:
            # pywashi 無しでもビルドは通る([.form] は総称ロールの div になる
            # だけ)。フォームを使うサイトでは `pip install pywashi`。
            pass
        _md_adoc_cache = md_
    return _md_adoc_cache


def render_body(body, source_path):
    """Render an article body to HTML, dispatching on the source file's
    extension. `.md` keeps the existing custom-block + CommonMark pipeline;
    `.adoc` goes through pyasciidoc. Mermaid fence post-processing applies
    to both — pyasciidoc passes ```mermaid fences through unchanged, so the
    same regex-based unwrap works regardless of which renderer produced the
    surrounding HTML.
    """
    source_path = Path(source_path)
    if source_path.suffix == ".adoc":
        html_out = _adoc_md().render(_strip_leading_adoc_title(body))
        html_out = process_chain_blocks(html_out)
        html_out = _add_table_class(html_out)
    else:
        body = strip_leading_title(body)
        body = process_custom_blocks(body)
        html_out = md.render(body)
    return process_mermaid_blocks(html_out)


# ---------------------------------------------------------------------------
# Custom Markdown blocks (:::chain, :::highlight, :::quote, :::compare)
# ---------------------------------------------------------------------------

def process_custom_blocks(body):
    """Convert custom ::: blocks to HTML."""
    lines = body.split("\n")
    result = []
    in_block = None
    block_content = []

    for line in lines:
        stripped = line.strip()

        # Block openers
        if stripped.startswith(":::chain"):
            if in_block:
                result.append(close_block(in_block, block_content))
                block_content = []
            in_block = "chain"
            continue
        elif stripped.startswith(":::highlight"):
            if in_block:
                result.append(close_block(in_block, block_content))
                block_content = []
            in_block = "highlight"
            continue
        elif stripped.startswith(":::quote"):
            if in_block:
                result.append(close_block(in_block, block_content))
                block_content = []
            in_block = "quote"
            continue
        elif stripped.startswith(":::compare"):
            if in_block:
                result.append(close_block(in_block, block_content))
                block_content = []
            in_block = "compare"
            continue
        elif stripped == ":::":
            if in_block:
                result.append(close_block(in_block, block_content))
                block_content = []
                in_block = None
            continue

        if in_block:
            block_content.append(line)
        else:
            result.append(line)

    if in_block:
        result.append(close_block(in_block, block_content))

    return "\n".join(result)


def close_block(block_type, lines):
    """Close a custom block and return HTML."""
    content = "\n".join(lines).strip()

    if block_type == "chain":
        content = content.replace("→", '<span class="arrow">&rarr;</span>')
        content = content.replace("\n\n", "<br><br>")
        content = content.replace("\n", "<br>\n")
        content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
        return f'<div class="chain-diagram">\n{content}\n</div>'

    elif block_type == "highlight":
        html = md.render(content)
        return f'<div class="highlight-box">\n{html}\n</div>'

    elif block_type == "quote":
        content = content.replace("\n", "<br>\n")
        return f'<div class="quote-block">\n<p class="quote-text">\n{content}\n</p>\n</div>'

    elif block_type == "compare":
        return build_table(content)

    return content


def build_table(content):
    """Build comparison table from pipe-delimited text."""
    lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
    if not lines:
        return ""

    def split_row(line):
        # Split on '|', strip whitespace, drop the leading/trailing
        # empties that come from the table's outer pipe characters.
        # Keep empty cells in the middle (e.g. an empty top-left header
        # in a labeled comparison table).
        parts = [c.strip() for c in line.split("|")]
        if parts and parts[0] == "":
            parts = parts[1:]
        if parts and parts[-1] == "":
            parts = parts[:-1]
        return parts

    html = '<table class="comparison-table fade-in">\n'
    for i, line in enumerate(lines):
        cells = split_row(line)
        if i == 0:
            html += "<tr>\n"
            for c in cells:
                html += f"  <th>{md.renderInline(c)}</th>\n"
            html += "</tr>\n"
        elif cells and all(set(c) <= {"-", ":"} for c in cells if c):
            # Skip Markdown table separator rows: `| --- | --- |`,
            # `|---|---|`, `| :--- | :---: | ---: |` etc.
            # (Allow empty cells in the row for the empty-row guard,
            # but require at least one non-empty cell that's all
            # dashes/colons.)
            if any(c for c in cells):
                continue
            html += "<tr>\n"
            for c in cells:
                html += f"  <td>{md.renderInline(c)}</td>\n"
            html += "</tr>\n"
        else:
            html += "<tr>\n"
            for c in cells:
                html += f"  <td>{md.renderInline(c)}</td>\n"
            html += "</tr>\n"
    html += "</table>"
    return html
