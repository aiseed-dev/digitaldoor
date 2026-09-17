#!/usr/bin/env python3
"""関連ニュースを広めに集めて、AI に一行で整えさせ、news/items.json に足す。載せる物は人が tools/pick_news.py で選ぶ。

    python3 tools/collect_news.py            # 取り口を全部読み、新しい物を足す
    python3 tools/collect_news.py --no-ai    # AI を使わず、題をそのまま足す(後で --retry で整える)
    python3 tools/collect_news.py --retry    # 整っていない物だけ AI に回す
    python3 tools/collect_news.py --max 40   # 一回で AI に回す上限
    python3 tools/collect_news.py --days 30  # この日数より古い物は拾わない(既定 30)

取り口は news/sources.json、絞り込みの語は news/keywords.json、結果は news/items.json。
AI は Claude(ANTHROPIC_API_KEY か `ant auth login` の資格情報)。無ければ題のまま置く。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
NEWS = SITE / "news"
ITEMS = NEWS / "items.json"
ATOM = "{http://www.w3.org/2005/Atom}"
UA = "Mozilla/5.0 (compatible; digitaldoor-news/0.1; +https://door.aiseed.dev/)"
MODEL = "claude-opus-5"

CATEGORIES = ["規格", "読取機", "錠", "扉", "鍵の発行", "事件", "企業", "その他"]
KINDS = ["発表", "報道", "噂", "解説"]


# ---------------------------------------------------------------- 取る
def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _text(el, *names) -> str:
    for n in names:
        t = el.findtext(n)
        if t:
            return t.strip()
    return ""


def parse_feed(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    out = []
    for it in root.findall(".//item"):  # RSS 2.0
        link = _text(it, "link") or (it.find("guid").text if it.find("guid") is not None else "")
        src = it.find("source")
        out.append({
            "title": _text(it, "title"), "link": link.strip(),
            "summary": _text(it, "description"),
            "published": _text(it, "pubDate"),
            "publisher": src.text.strip() if src is not None and src.text else "",
        })
    for it in root.findall(f".//{ATOM}entry"):  # Atom
        link_el = it.find(f"{ATOM}link")
        out.append({
            "title": _text(it, f"{ATOM}title"), "link": (link_el.get("href") if link_el is not None else "").strip(),
            "summary": _text(it, f"{ATOM}summary", f"{ATOM}content"),
            "published": _text(it, f"{ATOM}published", f"{ATOM}updated"), "publisher": "",
        })
    return [o for o in out if o["link"] and o["title"]]


def norm_date(s: str) -> str:
    if not s:
        return ""
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def strip_html(s: str) -> str:
    import re
    from html import unescape
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def item_id(link: str) -> str:
    return hashlib.sha1(link.encode()).hexdigest()[:16]


def collect(sources: list[dict], include: list[str], known: set[str], days: int = 30) -> list[dict]:
    new = []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    for s in sources:
        try:
            entries = parse_feed(fetch(s["url"]))
        except Exception as e:  # 取り口が一つ落ちても他は続ける
            print(f"  取れない: {s['name']}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        n = 0
        for e in entries:
            iid = item_id(e["link"])
            if iid in known:
                continue
            text = (e["title"] + " " + strip_html(e["summary"])).lower()
            if s.get("filter") and not any(k.lower() in text for k in include):
                continue
            pub = norm_date(e["published"])
            if pub and datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp() < cutoff:
                continue
            known.add(iid)
            new.append({
                "id": iid, "link": e["link"], "source": s["name"], "publisher": e["publisher"],
                "lang": s.get("lang", ""), "published": norm_date(e["published"]),
                "title_raw": e["title"], "summary_raw": strip_html(e["summary"])[:400],
                "collected": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "raw", "picked": False,
            })
            n += 1
        print(f"  {s['name']}: {len(entries)} 件のうち新しい物 {n}")
    return new


# ---------------------------------------------------------------- 整える(AI)
SYSTEM = """あなたはデジタルドア(扉、電子錠、読取機=ドアホン、扉のコントローラ、鍵の発行、規格 Aliro と Matter)の情報サイトの編集者です。
渡されたニュースの題と要約を読み、一件ずつ次を返してください。
- relevant: デジタルドアの読者に関係がありそうか(見立て。載せるかどうかは人が決める)。
- title: 日本語の題。原文の意味を保ち、30 字前後。
- summary: 日本語で一行(60〜90 字)。誰が何をしたかを、事実として書けることだけ書く。原文に無いことを足さない。
- category: 規格 / 読取機 / 錠 / 扉 / 鍵の発行 / 事件 / 企業 / その他 のどれか一つ。
- kind: 発表(当事者の発表) / 報道(記者が確かめて書いた物) / 噂(未確認の情報) / 解説(意見や解説) のどれか一つ。当事者の発表と、報道されただけの物を混ぜない。
会社名と製品名は原文のまま。個人名は書かない。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevant": {"type": "boolean"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "kind": {"type": "string", "enum": KINDS},
                },
                "required": ["id", "relevant", "title", "summary", "category", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def refine(batch: list[dict]) -> dict[str, dict]:
    """一束を AI に渡し、id → 整えた物 を返す。"""
    import anthropic

    client = anthropic.Anthropic()
    payload = [{"id": i["id"], "source": i["source"], "publisher": i["publisher"], "lang": i["lang"],
                "published": i["published"], "title": i["title_raw"], "summary": i["summary_raw"]} for i in batch]
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        messages=[{"role": "user", "content": "次のニュースを整えてください。\n\n" + json.dumps(payload, ensure_ascii=False, indent=1)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("AI が断った: " + str(getattr(response, "stop_details", "")))
    text = next(b.text for b in response.content if b.type == "text")
    return {o["id"]: o for o in json.loads(text)["items"]}


def refine_all(items: list[dict], limit: int, batch_size: int = 15) -> int:
    todo = [i for i in items if i.get("status") == "raw"][:limit]
    done = 0
    for k in range(0, len(todo), batch_size):
        batch = todo[k:k + batch_size]
        try:
            out = refine(batch)
        except Exception as e:
            print(f"  AI で整えられなかった({type(e).__name__}: {e})。題のまま残す", file=sys.stderr)
            break
        for i in batch:
            o = out.get(i["id"])
            if not o:
                continue
            i.update({"relevant": o["relevant"], "title": o["title"], "summary": o["summary"],
                      "category": o["category"], "kind": o["kind"], "status": "ai"})
            done += 1
        time.sleep(1)
    return done


# ---------------------------------------------------------------- 主
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-ai", action="store_true", help="AI を使わない")
    ap.add_argument("--retry", action="store_true", help="集めずに、整っていない物だけ AI に回す")
    ap.add_argument("--max", type=int, default=40, help="一回で AI に回す上限")
    ap.add_argument("--days", type=int, default=30, help="この日数より古い物は拾わない")
    args = ap.parse_args(argv)

    items = json.loads(ITEMS.read_text(encoding="utf-8")) if ITEMS.exists() else []
    known = {i["id"] for i in items}
    if not args.retry:
        cfg = json.loads((NEWS / "sources.json").read_text(encoding="utf-8"))
        include = json.loads((NEWS / "keywords.json").read_text(encoding="utf-8"))["include"]
        new = collect(cfg["sources"], include, known, args.days)
        items.extend(new)
        print(f"新しい物 {len(new)} 件")
    if not args.no_ai:
        n = refine_all(items, args.max)
        print(f"AI で整えた物 {n} 件")
    items.sort(key=lambda i: (i.get("published") or i.get("collected")), reverse=True)
    ITEMS.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"合計 {len(items)} 件 → {ITEMS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
