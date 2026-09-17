#!/usr/bin/env python3
"""RSS(feed.xml)を作る。build_article.py --all の後に走らせる。

    python3 tools/build_feed.py            # <site>/html/feed.xml
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build.frontmatter import parse_frontmatter  # noqa: E402

SITE = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))


def main() -> int:
    cfg = json.loads((SITE / "site.json").read_text(encoding="utf-8"))
    site_url = cfg["site_url"].rstrip("/")
    name = cfg["site_name"]["ja"] if isinstance(cfg["site_name"], dict) else cfg["site_name"]
    items = []
    for sdef in cfg["builder"]["series"]:
        base = sdef["url_base"].strip("/")
        for f in sorted((SITE / ".build" / "articles" / base).glob("*/ja.adoc")):
            meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
            d = datetime.strptime(str(meta["date"]).replace(".", "-"), "%Y-%m-%d").replace(tzinfo=JST)
            items.append((d, f"{site_url}/{base}/{meta['slug']}/", meta.get("title", ""), meta.get("description", ""), sdef.get("label", base)))
    items.sort(key=lambda t: t[0], reverse=True)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>',
           f"<title>{escape(name)}</title>", f"<link>{site_url}/</link>",
           "<description>扉と鍵の話を、層に分けて書く</description>", "<language>ja</language>",
           f"<lastBuildDate>{format_datetime(datetime.now(JST))}</lastBuildDate>"]
    for d, url, title, desc, label in items:
        out += ["<item>", f"<title>{escape(title)}</title>", f"<link>{url}</link>", f"<guid>{url}</guid>",
                f"<category>{escape(label)}</category>", f"<pubDate>{format_datetime(d)}</pubDate>",
                f"<description>{escape(desc)}</description>", "</item>"]
    out += ["</channel></rss>", ""]
    dest = SITE / "html" / "feed.xml"
    dest.write_text("\n".join(out), encoding="utf-8")
    print(f"Built feed: {dest} ({len(items)} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
