#!/usr/bin/env python3
"""news/items.json から html/news/index.html を作り、トップの新しいニュースを入れ替える。

    python3 tools/build_news.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from html import escape
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
START, END = "<!-- LATEST_NEWS_START -->", "<!-- LATEST_NEWS_END -->"


def shown(items: list[dict], limit: int = 200) -> list[dict]:
    """出す物: 人が選んだ物だけ。新しい順に limit 件。"""
    return [i for i in items if i.get("picked")][:limit]


def day(i: dict) -> str:
    s = i.get("published") or i.get("collected") or ""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST).strftime("%Y.%m.%d")
    except Exception:
        return ""


def entry(i: dict) -> str:
    title = i.get("title") or i["title_raw"]
    summary = i.get("summary") or ""
    tags = " ".join(f'<span class="news-tag">{escape(t)}</span>' for t in (i.get("kind"), i.get("category")) if t)
    who = escape(i.get("publisher") or i.get("source") or "")
    return f'''
<div class="activity-item news-item">
  <div class="activity-number">{day(i)} · {who} {tags}</div>
  <h3><a href="{escape(i["link"])}" rel="noopener">{escape(title)}</a></h3>
  {f"<p>{escape(summary)}</p>" if summary else ""}
</div>'''


def main() -> int:
    items = json.loads((SITE / "news/items.json").read_text(encoding="utf-8")) if (SITE / "news/items.json").exists() else []
    items = shown(items)
    cfg = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    site_url = cfg["site_url"].rstrip("/")
    css_v = re.search(r'style\.css\?v=([0-9a-f]+)', (SITE / "html/index.html").read_text(encoding="utf-8"))
    css = "/css/style.css" + (f"?v={css_v.group(1)}" if css_v else "")
    body = "".join(entry(i) for i in items) or "<p>まだ記事がありません。</p>"
    now = datetime.now(JST).strftime("%Y.%m.%d %H:%M")
    page = f'''<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ニュース | デジタルドア</title>
    <meta name="description" content="扉と鍵に関するニュース。AI が集めて一行に要約し、人が選んで掲載します。発表・報道・噂・解説の種別付き。">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'">
    <link rel="canonical" href="{site_url}/news/">
    <link rel="icon" href="/favicon.ico" sizes="any">
    <link rel="alternate" type="application/rss+xml" title="デジタルドア" href="/feed.xml">
    <link rel="stylesheet" href="{css}">
</head>
<body>
    <header class="site-header">
        <a href="/" class="brand">デジタルドア</a>
        <nav><a href="/guide/">解説</a><a href="/blog/">ブログ</a><a href="/news/">ニュース</a><a href="/#about">このサイトについて</a></nav>
    </header>
    <main class="index">
        <h1>ニュース</h1>
        <p class="index-subtitle">扉と鍵に関するニュースです。AI が集めて一行に要約し、載せる記事は人が選んでいます。発表・報道・噂・解説の種別は目安です。詳しくは元の記事をお読みください。最終更新 {now}</p>
        <section class="article-list">{body}
        </section>
    </main>
    <footer class="site-footer"><p>デジタルドア — aiseed · <a href="/license/">ライセンス</a> · <a href="/feed.xml">RSS</a></p></footer>
</body>
</html>
'''
    out = SITE / "html/news/index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    # トップの「ニュース」
    home = SITE / "html/index.html"
    s = home.read_text(encoding="utf-8")
    if START in s and END in s:
        latest = "".join(entry(i) for i in items[:5]) or "<p>まだ記事がありません。</p>"
        s = s[: s.index(START) + len(START)] + latest + "\n            " + s[s.index(END):]
        home.write_text(s, encoding="utf-8")
    print(f"Built news: {out} ({len(items)} picked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
