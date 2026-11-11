"""在庫表（inventory.toml）を読み、期限と欄の抜けを見る。K-14、K-04、K-05、K-13。"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("id", "kind", "where", "holder", "role")
WANTED = ("expires", "rotate_days", "lost", "leaked")
KINDS = ("password", "token", "key", "cert", "passphrase", "other")


class Refused(Exception):
    """在庫表に値の欄がある、または読めない。終了コード 2。メッセージに値は入れない。"""


@dataclass
class Finding:
    id: str
    problem: str  # expired | soon | missing | invalid
    detail: str


@dataclass
class Result:
    count: int = 0
    findings: list[Finding] = field(default_factory=list)

    def by(self, problem: str) -> list[Finding]:
        return [f for f in self.findings if f.problem == problem]

    @property
    def failed(self) -> bool:
        return any(f.problem in ("expired", "missing", "invalid") for f in self.findings)


def load(path: Path) -> list[dict]:
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise Refused(f"在庫表を読めない: {path} ({type(e).__name__})") from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        # TOMLDecodeError のメッセージは位置だけを含む。値の断片は出さない
        m = re.search(r"line \d+, column \d+", str(e))
        where = m.group(0) if m else type(e).__name__
        raise Refused(f"在庫表が TOML として読めない: {path} ({where})") from None
    secrets = data.get("secret", [])
    if not isinstance(secrets, list):
        raise Refused("在庫表の secret は [[secret]] の並びで書く")
    for i, s in enumerate(secrets, 1):
        if not isinstance(s, dict):
            raise Refused(f"在庫表の {i} 件目が表になっていない")
        if any(str(k).lower() == "value" for k in s):
            name = s.get("id", f"#{i}")
            raise Refused(
                f"在庫表の {name} に value の欄がある。値は書かない（K-13）。欄を消してから走らせる"
            )
    return secrets


def _as_date(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        try:
            return dt.date.fromisoformat(v)
        except ValueError:
            return None
    return None


def check(secrets: list[dict], days: int = 30, today: dt.date | None = None) -> Result:
    today = today or dt.date.today()
    res = Result(count=len(secrets))
    seen: set[str] = set()
    for i, s in enumerate(secrets, 1):
        sid = str(s.get("id") or f"#{i}")
        if sid in seen:
            res.findings.append(Finding(sid, "invalid", "id が重複"))
        seen.add(sid)
        for k in REQUIRED:
            if s.get(k) in (None, ""):
                res.findings.append(Finding(sid, "missing", f"{k} が無い（必須）"))
        if s.get("kind") not in (None, "") and s.get("kind") not in KINDS:
            res.findings.append(Finding(sid, "invalid", "kind が " + " | ".join(KINDS) + " のどれでもない"))
        for k in WANTED:
            if s.get(k) in (None, ""):
                res.findings.append(Finding(sid, "missing", f"{k} が無い"))
        if s.get("expires") not in (None, ""):
            exp = _as_date(s["expires"])
            if exp is None:
                res.findings.append(Finding(sid, "invalid", "expires が日付として読めない"))
            elif exp < today:
                res.findings.append(Finding(sid, "expired", f"{exp.isoformat()} に切れた"))
            elif exp <= today + dt.timedelta(days=days):
                res.findings.append(
                    Finding(sid, "soon", f"{exp.isoformat()} に切れる（{(exp - today).days} 日後）")
                )
    return res


LABEL = {"expired": "期限切れ", "soon": "期限が近い", "missing": "抜け", "invalid": "形の誤り"}


def table(res: Result, days: int) -> str:
    lines = [f"在庫 {res.count} 件。期限の近さは {days} 日で見た。"]
    if not res.findings:
        lines.append("抜けも期限切れも無い。")
        return "\n".join(lines)
    lines += ["", "| id | 何 | 中身 |", "|---|---|---|"]
    order = ("expired", "missing", "invalid", "soon")
    for p in order:
        for f in res.by(p):
            lines.append(f"| {f.id} | {LABEL[p]} | {f.detail} |")
    return "\n".join(lines)
