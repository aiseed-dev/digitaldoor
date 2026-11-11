#!/usr/bin/env python3
"""関連ニュースを広めに集めて、AI に一行で整えさせ、news/items.json に足す。載せる物は人が tools/pick_news.py で選ぶ。

    .venv/bin/python tools/collect_news.py            # 取り口を全部読み、新しい物を足す
    .venv/bin/python tools/collect_news.py --no-ai    # AI を使わず、題をそのまま足す(後で --retry で整える)
    .venv/bin/python tools/collect_news.py --retry    # 整っていない物だけ AI に回す
    .venv/bin/python tools/collect_news.py --max 40   # 一回で AI に回す上限
    .venv/bin/python tools/collect_news.py --days 30  # この日数より古い物は拾わない(既定 30)

取り口は news/sources.json、絞り込みの語は news/keywords.json、結果は news/items.json。
AI は Claude(ANTHROPIC_API_KEY か `ant auth login` の資格情報)。無ければ題のまま置く。

AI で整えに回した物のうち、整えた件数、失敗した件数と理由の要約を、最後に出す。
集めた物は、整えに失敗しても news/items.json に書く(題のまま。後で --retry で整えられる)。

終了コード:
  0  ふつう。資格情報が無くて題のまま置いたときも 0
  1  資格情報があるのに、整えに回した物が一件も整わなかった(API キーの誤り、網の不通、
     SDK と引数の食い違いなど)。毎朝の仕事が成功に見えたまま raw が積もるのを防ぐ
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
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


def make_client():
    """Claude の client を作る。資格情報は SDK が環境変数や `ant auth login` の置き場から探す。"""
    import anthropic

    return anthropic.Anthropic()


def has_credentials(client) -> bool:
    """client が資格情報を見つけたか。

    anthropic の SDK(1.x)の Anthropic() は、API キー(api_key)、トークン(auth_token)、
    資格情報の提供元(credentials。`ant auth login` の置き場など)を、作るときに解決して持つ。
    どれも無ければ、要求を送るときに失敗する。
    """
    return bool(getattr(client, "api_key", None) or getattr(client, "auth_token", None)
                or getattr(client, "credentials", None))


def refine(batch: list[dict], client=None) -> dict[str, dict]:
    """一束を AI に渡し、id → 整えた物 を返す。"""
    if client is None:
        client = make_client()
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


@dataclass
class RefineResult:
    """refine_all の結果。件数はニュースの件数(束の数ではない)。"""
    asked: int = 0            # 整えに回した件数
    done: int = 0             # 整えた件数(status が ai になった物)
    failed: int = 0           # 整えに失敗した件数
    skipped: int = 0          # 前の失敗で止めたため、回さなかった件数
    no_credentials: bool = False
    reasons: Counter = field(default_factory=Counter)  # 理由 → 件数

    def fail(self, reason: str, n: int) -> None:
        self.failed += n
        self.reasons[reason] += n

    @property
    def all_failed(self) -> bool:
        """資格情報があるのに、回した物が一件も整わなかったか。"""
        return not self.no_credentials and self.asked > 0 and self.done == 0

    def summary(self) -> list[str]:
        if self.asked == 0:
            return ["AI で整える物はありません"]
        if self.no_credentials:
            return [f"AI の資格情報がありません。{self.asked} 件を題のまま残します(後で --retry で整えられます)"]
        lines = [f"AI で整えた物 {self.done} 件(回した物 {self.asked} 件、失敗 {self.failed} 件"
                 + (f"、前の失敗で止めて回さなかった物 {self.skipped} 件" if self.skipped else "") + ")"]
        for reason, n in self.reasons.most_common():
            lines.append(f"  失敗の理由: {reason}({n} 件)")
        return lines


def _reason(e: Exception) -> str:
    msg = " ".join(str(e).split())
    if len(msg) > 160:
        msg = msg[:160] + "…"
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def _is_api_error(e: Exception) -> bool:
    """API や網の失敗か(SDK が既に再試行した後の物。続けても通らないので止める)。"""
    try:
        import anthropic
    except ImportError:
        return False
    return isinstance(e, anthropic.APIError)


def refine_all(items: list[dict], limit: int, batch_size: int = 15, client=None, pause: float = 1.0) -> RefineResult:
    """status が raw の物を、上から limit 件まで、batch_size 件ずつ AI で整える。

    資格情報が無ければ何もしない(no_credentials)。API や網の失敗(anthropic.APIError)は、
    SDK が再試行した後の物なので、そこで止め、残りは回さない。断り(refusal)や返事の形の
    誤りは、その束だけを失敗として数え、次の束に進む。
    """
    todo = [i for i in items if i.get("status") == "raw"][:limit]
    res = RefineResult(asked=len(todo))
    if not todo:
        return res
    if client is None:
        try:
            client = make_client()
        except Exception as e:  # anthropic が入っていない、資格情報の置き場が壊れている等
            if isinstance(e, ImportError) and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                res.no_credentials = True
                return res
            res.fail(_reason(e), len(todo))
            return res
    if not has_credentials(client):
        res.no_credentials = True
        return res
    for k in range(0, len(todo), batch_size):
        batch = todo[k:k + batch_size]
        try:
            out = refine(batch, client)
        except Exception as e:
            res.fail(_reason(e), len(batch))
            if _is_api_error(e):
                res.skipped = len(todo) - k - len(batch)
                break
            continue
        for i in batch:
            o = out.get(i["id"])
            try:
                fields = {"relevant": o["relevant"], "title": o["title"], "summary": o["summary"],
                          "category": o["category"], "kind": o["kind"]}
            except (KeyError, TypeError):
                res.fail("AI の返事にその件が無い、または項目が欠けている", 1)
                continue
            i.update(fields, status="ai")
            res.done += 1
        if pause and k + batch_size < len(todo):
            time.sleep(pause)
    return res


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
    res = None if args.no_ai else refine_all(items, args.max)
    items.sort(key=lambda i: (i.get("published") or i.get("collected")), reverse=True)
    ITEMS.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"合計 {len(items)} 件 → {ITEMS}")
    if res is not None:
        for line in res.summary():
            print(line)
        if res.all_failed:
            print(f"エラー: 資格情報があるのに、AI で一件も整えられませんでした(回した物 {res.asked} 件)。"
                  "集めた物は題のまま書きました", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
