"""Markdown parsing: frontmatter, custom ::: blocks, CommonMark rendering."""

import html
import re
import sys
import unicodedata
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


# ---------------------------------------------------------------------------
# AsciiDoc の表を、部品(pyasciidoc)に渡す前に「一行=一行」の形へ整える。
#
# AsciiDoc の決まりでは、表の最初の(空でない)行のセルの数 N が列の数に
# なり、以後のセルは行をまたいで順に詰められ、N 個ごとに一行になる。
# 部品は `|` で始まる一行を表の一行として扱うので、セルを一行に一つずつ
# 書いた表は崩れる。ここで N 個ごとに `| a | b | c` の一行へまとめ直す。
# 区切りの見分け方(`|===`、```/~~~ の囲み、`----`、`////`)と、セルの
# 分け方(`\|` はセルの中のパイプ)は部品の _table_rule /
# _find_closing_line / _split_table_row と同じにしてある。
# ---------------------------------------------------------------------------

_ADOC_TABLE_DELIM_RE = re.compile(r"^\|===[ \t]*$")
_ADOC_FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")
_ADOC_LISTING_DELIM_RE = re.compile(r"^-{4,}[ \t]*$")
_ADOC_BLOCK_COMMENT_RE = re.compile(r"^/{4,}[ \t]*$")
_ADOC_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _split_adoc_row(line):
    """部品の _split_table_row と同じ分け方。ただし `\\|` は戻さず、
    書いたままの字で返す(組み直した行を部品がもう一度分けるため)。"""
    cells = _ADOC_CELL_SPLIT_RE.split(line)
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    return [c.strip() for c in cells]


def _fence_closes(text, marker):
    stripped = text.strip()
    return bool(stripped) and set(stripped) <= {marker[0]} and len(stripped) >= len(marker)


def _find_table_close(lines, start):
    """部品の _find_closing_line と同じく、囲み(```/~~~)と ////
    の中の `|===` を飛ばして、表の閉じの行の番号を返す(無ければ len)。"""
    fence = ""
    in_comment = False
    i = start
    while i < len(lines):
        text = lines[i].lstrip()
        if fence:
            if _fence_closes(text, fence):
                fence = ""
        elif in_comment:
            if _ADOC_BLOCK_COMMENT_RE.match(text):
                in_comment = False
        elif (m := _ADOC_FENCE_OPEN_RE.match(text)):
            fence = m.group(1)
        elif _ADOC_BLOCK_COMMENT_RE.match(text):
            in_comment = True
        elif _ADOC_TABLE_DELIM_RE.match(text):
            return i
        i += 1
    return len(lines)


def _is_wide(ch):
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _join_continuation(text, cont):
    """続きの行を一行につなぐ。部品は改行を softbreak として扱い、両側が
    全角の字なら消し(cjk_friendly)、そうでなければ空白一つに見せる。
    同じ見え方になるよう、両側が全角なら何も挟まず、ほかは空白でつなぐ。
    続きの行の中の `|` は、部品がセルの区切りと読まないよう `\\|` にする。"""
    out = text
    for c in cont:
        c = _ADOC_CELL_SPLIT_RE.sub(r"\\|", c)
        sep = "" if out and c and _is_wide(out[-1]) and _is_wide(c[0]) else " "
        out = f"{out}{sep}{c}" if out else c
    return out


def _reflow_table(inner, warn):
    """表の中身の行(区切りの行を除く)を、N 列ごとの一行に組み直す。

    すでに全部の行が N 個のセルなら、そのまま返す(今までの表は変わらない)。
    `|` で始まらない行は直前のセルの続き。続きのセルが組み直した行の
    最後に来るときは、続きの行をそのまま後ろに置く(部品の扱いと同じ)。
    行の途中に来るときは、一行に収めるため _join_continuation でつなぐ。"""
    row_cells = []   # 行ごとのセル(書いた順)。続きの行は各セルの第2要素へ
    for line in inner:
        text = line.lstrip()
        if not text.strip():
            continue
        if text.startswith("|"):
            row_cells.append([[c, []] for c in _split_adoc_row(text)])
        elif row_cells:
            row_cells[-1][-1][1].append(text.strip())
        # 最初の行より前の、`|` で始まらない行は部品と同じく捨てる
    if not row_cells:
        return inner
    n = len(row_cells[0])
    if all(len(r) == n for r in row_cells):
        return inner

    cells = [c for r in row_cells for c in r]
    rem = len(cells) % n
    if rem:
        warn(len(cells), n)
        cells += [["", []] for _ in range(n - rem)]

    out = []
    for k in range(0, len(cells), n):
        row = cells[k:k + n]
        texts = []
        for j, (text, cont) in enumerate(row):
            if cont and j < n - 1:
                text = _join_continuation(text, cont)
            texts.append(text)
        out.append("| " + " | ".join(texts))
        out.extend(row[-1][1])
    return out


def normalize_adoc_tables(text, label=""):
    """`|===` の表を、部品が読める「一行=表の一行」の形に整えて返す。

    `label` は警告に出す名前(記事の ID など)。セルの数が列の数で
    割り切れない表は最後の行を空のセルで埋め、標準エラーに一行書く。"""
    lines = text.split("\n")
    out = []
    fence = ""
    in_listing = False
    in_comment = False
    table_no = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.lstrip()
        indent = len(line) - len(s)
        if fence:
            if _fence_closes(s, fence):
                fence = ""
        elif in_listing:
            if _ADOC_LISTING_DELIM_RE.match(s):
                in_listing = False
        elif in_comment:
            if _ADOC_BLOCK_COMMENT_RE.match(s):
                in_comment = False
        elif indent < 4 and (m := _ADOC_FENCE_OPEN_RE.match(s)):
            fence = m.group(1)
        elif _ADOC_LISTING_DELIM_RE.match(s):
            in_listing = True
        elif _ADOC_BLOCK_COMMENT_RE.match(s):
            in_comment = True
        elif indent < 4 and _ADOC_TABLE_DELIM_RE.match(s):
            table_no += 1
            close = _find_table_close(lines, i + 1)
            no = table_no

            def warn(count, n, no=no):
                where = f"{label} の" if label else ""
                print(
                    f"警告: {where}{no} 番目の表は、セルの数 {count} が列の数 {n} で"
                    "割り切れません。最後の行を空のセルで埋めました",
                    file=sys.stderr,
                )

            out.append(line)
            out.extend(_reflow_table(lines[i + 1:close], warn))
            if close < len(lines):
                out.append(lines[close])
            i = close + 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


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
        body = normalize_adoc_tables(
            _strip_leading_adoc_title(body), label=f"{source_path.parent.name}/{source_path.name}")
        html_out = _adoc_md().render(body)
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
