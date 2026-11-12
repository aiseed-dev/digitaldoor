#!/usr/bin/env python3
"""「確かめの始め方」を、サイトの頁にする。

    .venv/bin/python tools/build_check.py      # docs/確かめの始め方.md → html/check/index.html

読む物: docs/確かめの始め方.md(正。直すときはこちらを直して、この道具を走らせ直す)。
作る物: html/check/index.html(/check/ で公開する頁)。

- Markdown の変換は tools/build_manual.py の md_to_html(markdown_it、生の HTML は通さない)を使う。
- 一つめの見出し(#)を頁の題にし、章の見出し(##)に id を付けて、頁の頭に目次を出す。
- https://door.aiseed.dev/ で始まるリンクは、サイトの中のリンク(/ から)に直す。
- 表、コードの囲み、引用は、狭い画面(幅 375)では横に送れる枠に入れ、頁の外へはみ出させない。
- 頁の枠(head、CSP、ヘッダー、フッター)は html/consult/index.html と揃える。CSS の版は html/index.html から読む。
"""
from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build.site_nav import footer_html, nav_html  # noqa: E402  上の並びと奥付の正
from build_manual import md_to_html, wrap_tables  # noqa: E402

SRC = SITE / "docs" / "確かめの始め方.md"
OUT = SITE / "html" / "check" / "index.html"
SITE_URL = "https://door.aiseed.dev"
DESCRIPTION = ("会社の人が、Claude を使って自社のシステムを確かめ始めるための手順。"
               "Anthropic の Cyber Verification Program の Defense Access の申し込み、運用の決まり、"
               "AI に渡す物と渡さない物、業者への通知、最初の一つの確かめ方。")

STYLE = """        .check h2 { scroll-margin-top: 1rem; }
        .check .toc { background: var(--paper-deep); border: 1px solid var(--rule); border-radius: 4px; padding: 0.8rem 1.2rem; margin: 1.5rem 0 2rem; }
        .check .toc p { margin: 0 0 0.3rem; font-weight: 700; }
        .check .toc ol { margin: 0; padding-left: 1.2rem; list-style: none; }
        .check .toc li { margin: 0.2rem 0; line-height: 1.7; }
        .check .table-wrap table { min-width: 32rem; }
        .check td, .check th { line-height: 1.7; }
        .check .table-wrap td:first-child, .check .table-wrap th:first-child { min-width: 6em; }
        .check pre { max-width: 100%; }
        @media (max-width: 600px) {
            .check th, .check td { padding: 0.45rem 0.4rem; font-size: 0.9rem; }
            .check pre { font-size: 0.8rem; padding: 0.8rem; }
        }"""


def add_ids_and_toc(body: str) -> tuple[str, str, str]:
    """本文から題(h1)を取り出し、h2 に id を付け、目次の HTML を返す。"""
    title = "確かめの始め方"
    m = re.search(r"<h1>(.*?)</h1>\n?", body)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1))
        body = body[:m.start()] + body[m.end():]
    items = []
    count = 0

    def repl(mm: re.Match) -> str:
        nonlocal count
        text = mm.group(1)
        plain = re.sub(r"<[^>]+>", "", text)
        num = re.match(r"(\d+)\.", plain)
        hid = f"s{num.group(1)}" if num else ("sources" if plain.strip() == "出典" else f"h{count}")
        count += 1
        items.append(f'<li><a href="#{hid}">{text}</a></li>')
        return f'<h2 id="{hid}">{text}</h2>'

    body = re.sub(r"<h2>(.*?)</h2>", repl, body)
    toc = '<nav class="toc" aria-label="目次">\n<p>目次</p>\n<ol>\n' + "\n".join(items) + "\n</ol>\n</nav>"
    return title, body, toc


def build() -> Path:
    md = SRC.read_text(encoding="utf-8")
    body = md_to_html(md)
    body = body.replace(f'href="{SITE_URL}/', 'href="/')
    body = wrap_tables(body)
    title, body, toc = add_ids_and_toc(body)
    css_v = re.search(r'style\.css\?v=([0-9a-f]+)', (SITE / "html/index.html").read_text(encoding="utf-8"))
    css = "/css/style.css" + (f"?v={css_v.group(1)}" if css_v else "")
    page = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>確かめの始め方 | デジタルドア</title>
    <meta name="description" content="{escape(DESCRIPTION)}">
    <link rel="canonical" href="{SITE_URL}/check/">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'">
    <link rel="icon" href="/favicon.ico" sizes="any">
    <link rel="stylesheet" href="{css}">
    <style>
{STYLE}
    </style>
</head>
<body>
    <header class="site-header">
        <a href="/" class="brand">デジタルドア</a>
        {nav_html()}
    </header>
    <main class="check article-body">
<h1>{title}</h1>
<p class="index-subtitle">このページは <a href="https://github.com/aiseed-dev/digitaldoor/blob/main/docs/確かめの始め方.md">docs/確かめの始め方.md</a> から作っています。</p>
{toc}
{body}
    </main>
    {footer_html()}
</body>
</html>
'''
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    return OUT


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    out = build()
    print(f"Built check: {out.relative_to(SITE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
