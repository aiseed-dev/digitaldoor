"""報告 — 台帳から作る定型の文書。

WWOOFジャパンへの月次報告、住宅宿泊事業法第14条の定期報告(2ヶ月ごと: 宿泊日数・宿泊者数・延べ宿泊者数・国籍別の内訳)。
出力は AsciiDoc。組版は pywashi など別の道具で。
"""
from __future__ import annotations

import calendar
from collections import Counter
from datetime import date, timedelta

from .ledger import Ledger


def _d(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def wwoof_monthly(ledger: Ledger, year: int, month: int) -> str:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    rows = []
    for d in ledger.select("滞在_WWOOF"):
        a, b = _d(d["到着日"]), _d(d["出発日"]) if d["出発日"] else None
        if a is None or a > last or (b is not None and b < first):
            continue
        rows.append(d)
    lines = [f"= WWOOF 月次報告 {year}年{month}月", ":報告元: entrance", "",
             f"受け入れ {len(rows)} 名。", "",
             "|===", "| 氏名 | 国籍 | 在留資格 | 到着日 | 出発日 | 傷害保険 | 手伝い時間/日 | 手伝いの内容", ""]
    for d in rows:
        lines.append(f"| {d['氏名']} | {d['国籍']} | {d['在留資格']} | {d['到着日']} | {d['出発日'] or '滞在中'} "
                     f"| {d['傷害保険']} | {d['手伝い時間']} | {d['手伝いの内容']}")
    lines.append("|===")
    return "\n".join(lines) + "\n"


def minpaku_teiki(ledger: Ledger, start: date, end: date) -> str:
    """期間 [start, end] の定期報告。宿泊日数は「誰かが泊まった夜」の数、延べは人×夜。"""
    nights: set[date] = set()
    nobe = 0
    guests = 0
    by_nat: Counter[str] = Counter()
    for d in ledger.select("宿泊_民泊"):
        a, b = _d(d["到着日"]), _d(d["出発日"])
        if a is None or b is None:
            continue
        stayed = 0
        day = a
        while day < b:
            if start <= day <= end:
                nights.add(day)
                stayed += 1
            day += timedelta(days=1)
        if stayed:
            guests += 1
            nobe += stayed
            by_nat[d["国籍"] or "不明"] += 1
    lines = [f"= 住宅宿泊事業 定期報告 {start.isoformat()} 〜 {end.isoformat()}", ":報告元: entrance", "",
             f"届出住宅に人を宿泊させた日数:: {len(nights)}",
             f"宿泊者数:: {guests}",
             f"延べ宿泊者数:: {nobe}", "",
             "国籍別の宿泊者数の内訳", "", "|===", "| 国籍 | 人数", ""]
    for nat, n in sorted(by_nat.items()):
        lines.append(f"| {nat} | {n}")
    lines.append("|===")
    return "\n".join(lines) + "\n"
