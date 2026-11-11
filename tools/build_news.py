#!/usr/bin/env python3
"""news/YYYY-MM-DD.adoc から html/news/index.html を作り、トップの新しいニュースを入れ替える。

    python3 tools/build_news.py

ニュースは aiai と同じ作り方で、1 日 1 つの adoc を AI の CLI が書き(news/tsukuru.py)、人が読んで直して commit する。
ここが載せるのは、news/ にある日付の名前の adoc だけ(SKILL.md と 好み.adoc は読まない)。
1 件は `== 見出し` の節で、:分野:、:国・地域:、:種別:、本文、`出典:` の行(資料の名前、日付、URL、確かめた日)を持つ。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SITE / "tools"))

from build.site_nav import footer_html, nav_html  # noqa: E402  上の並びと奥付の正

JST = timezone(timedelta(hours=9))
START, END = "<!-- LATEST_NEWS_START -->", "<!-- LATEST_NEWS_END -->"
DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.adoc$")
URL = re.compile(r"https?://[^\s、,()（）<>]+")


def read_day(text: str) -> tuple[str, str, list[dict]]:
    """一日の adoc を (題, 日付, [件]) にする。件は {"heading", "attrs", "body", "sources"}。"""
    title = (re.search(r"^= (.+)$", text, re.M) or [None, ""])[1].strip()
    date = (re.search(r"^:日付: (\S+)", text, re.M) or [None, ""])[1].strip()
    entries = []
    for part in re.split(r"^== ", text, flags=re.M)[1:]:
        lines = part.splitlines()
        heading, attrs, body, sources = lines[0].strip(), {}, [], []
        para: list[str] = []
        for line in lines[1:]:
            m = re.match(r"^:([^:]+): (.*)$", line)
            if m and not body and not para:
                attrs[m[1].strip()] = m[2].strip()
            elif line.startswith("出典:"):
                sources.append(line[len("出典:"):].strip().removesuffix("+").strip())
            elif line.strip():
                para.append(line.strip())
            elif para:
                body.append(" ".join(para))
                para = []
        if para:
            body.append(" ".join(para))
        entries.append({"heading": heading, "attrs": attrs, "body": body, "sources": sources})
    return title, date, entries


def load_days(folder: Path = SITE / "news") -> list[tuple[str, list[dict]]]:
    """[(日付, [件])]。新しい日が先。"""
    out = []
    for p in sorted(folder.iterdir() if folder.exists() else [], reverse=True):
        if DAY_FILE.match(p.name):
            _, date, entries = read_day(p.read_text(encoding="utf-8"))
            if entries:
                out.append((date or p.stem, entries))
    return out


def link_source(text: str) -> str:
    """出典の行の URL だけを、リンクにする(ほかは文字のまま)。"""
    out, pos = [], 0
    for m in URL.finditer(text):
        out.append(escape(text[pos:m.start()]))
        url = m[0]
        out.append(f'<a href="{escape(url)}" rel="noopener">{escape(url)}</a>')
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


def anchor(date: str, n: int) -> str:
    return f"{date}-{n}"


def entry_html(date: str, n: int, e: dict, *, full: bool = True) -> str:
    a = e["attrs"]
    tags = " ".join(f'<span class="news-tag">{escape(a[k])}</span>' for k in ("種別", "分野") if a.get(k))
    where = escape(a.get("国・地域", ""))
    head = escape(e["heading"])
    meta = f'<div class="activity-number">{escape(date.replace("-", "."))}{" · " + where if where else ""} {tags}</div>'
    if not full:
        return f'''
<div class="activity-item news-item">
  {meta}
  <h3><a href="/news/#{anchor(date, n)}">{head}</a></h3>
</div>'''
    body = "".join(f"<p>{escape(p)}</p>" for p in e["body"])
    srcs = "".join(f"<li>{link_source(s)}</li>" for s in e["sources"])
    return f'''
<div class="activity-item news-item" id="{anchor(date, n)}">
  {meta}
  <h3>{head}</h3>
  {body}
  {f'<ul class="news-sources">{srcs}</ul>' if srcs else ""}
</div>'''


def main() -> int:
    days = load_days()
    cfg = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    site_url = cfg["site_url"].rstrip("/")
    css_v = re.search(r'style\.css\?v=([0-9a-f]+)', (SITE / "html/index.html").read_text(encoding="utf-8"))
    css = "/css/style.css" + (f"?v={css_v.group(1)}" if css_v else "")
    body = "".join(f'<h2 class="news-day">{escape(d.replace("-", "."))}</h2>'
                   + "".join(entry_html(d, n, e) for n, e in enumerate(entries, 1))
                   for d, entries in days) or "<p>まだ記事がありません。</p>"
    now = datetime.now(JST).strftime("%Y.%m.%d %H:%M")
    page = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ニュース | デジタルドア</title>
    <meta name="description" content="扉と鍵に関するニュース。AI が元の記事を開いて確かめて書き、人が読んで直した物を、出典と確かめた日を付けて載せています。">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'">
    <link rel="canonical" href="{site_url}/news/">
    <link rel="icon" href="/favicon.ico" sizes="any">
    <link rel="alternate" type="application/rss+xml" title="デジタルドア" href="/feed.xml">
    <link rel="stylesheet" href="{css}">
</head>
<body>
    <header class="site-header">
        <a href="/" class="brand">デジタルドア</a>
        {nav_html()}
    </header>
    <main class="index">
        <h1>ニュース</h1>
        <p class="index-subtitle">扉と鍵に関するニュースです。AI が手引きに沿って元の記事を開いて確かめて書き、人が読んで直した物を載せています。種別の「発表」は当事者が自分で出した物、「報道」は記者が書いた物、「解説」は意見や分析です。詳しくは出典の元の資料をご覧ください。作り方は <a href="https://github.com/aiseed-dev/digitaldoor/tree/main/news">news/</a> にあります。最終更新 {now}</p>
        <section class="article-list">{body}
        </section>
    </main>
    {footer_html()}
</body>
</html>
'''
    out = SITE / "html/news/index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    # トップの「ニュース」: 新しい 5 件の見出し
    home = SITE / "html/index.html"
    s = home.read_text(encoding="utf-8")
    if START in s and END in s:
        flat = [(d, n, e) for d, entries in days for n, e in enumerate(entries, 1)][:5]
        latest = "".join(entry_html(d, n, e, full=False) for d, n, e in flat) or "<p>まだ記事がありません。</p>"
        s = s[: s.index(START) + len(START)] + latest + "\n            " + s[s.index(END):]
        home.write_text(s, encoding="utf-8")
    n = sum(len(e) for _, e in days)
    print(f"Built news: {out} ({len(days)} 日、{n} 件)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
