"""相手に出した token の台帳（`~/.config/aiseed/tokens.json`）を見る。K-05、K-14。

台帳はアプリ（seed など）が書く。持つのはハッシュだけで、kagi はハッシュも出さない。
見るのは、誰に、どのアプリの、どの範囲を、いつまで出したか。期限切れのまま取り消していない物と、期限の近い物を出す。
形は seed の `seed token`（seed の設計書 §5.9）と同じだが、kagi は seed のコードを使わない（JSON を読むだけ）。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from kagi.inventory import Refused

DEFAULT = "~/.config/aiseed/tokens.json"
LABEL = {
    "live": "生きている",
    "soon": "期限が近い",
    "expired": "期限切れ（取り消していない）",
    "revoked": "取り消した",
}


def load(path: Path) -> list[dict]:
    path = Path(path).expanduser()
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        raise Refused(f"token の台帳が無い: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise Refused(f"token の台帳を読めない: {path} ({type(e).__name__})") from None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, list) or not all(isinstance(t, dict) for t in tokens):
        raise Refused('token の台帳は {"tokens": [...]} の形')
    for t in tokens:
        if any(k in t for k in ("token", "code", "value")):
            raise Refused("token の台帳に token そのものの欄がある。置くのはハッシュだけ（K-13）")
    return tokens


def rows(tokens: list[dict], days: int = 30, today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    out = []
    for t in tokens:
        try:
            exp = dt.date.fromisoformat(str(t.get("expires")))
        except ValueError:
            exp = None
        if t.get("revoked"):
            st = "revoked"
        elif exp is None or exp < today:
            st = "expired"
        elif exp <= today + dt.timedelta(days=days):
            st = "soon"
        else:
            st = "live"
        out.append(
            {
                "name": str(t.get("name", "")),
                "app": str(t.get("app", "")),
                "scope": ",".join(str(x) for x in t.get("scope", [])),
                "state": st,
                "expires": str(t.get("expires", "書いていない")),
            }
        )
    return out


def fixes(rs: list[dict]) -> int:
    return sum(1 for r in rs if r["state"] == "expired")


def table(rs: list[dict], path: Path | str, days: int = 30) -> str:
    live = sum(1 for r in rs if r["state"] in ("live", "soon"))
    lines = [f"出した token {len(rs)} 件（生きている {live}）。期限の近さは {days} 日で見た。台帳: {path}"]
    if rs:
        lines += ["", "| 誰に | アプリ | 範囲 | 今 | 期限 |", "|---|---|---|---|---|"]
        order = {"expired": 0, "soon": 1, "live": 2, "revoked": 3}
        for r in sorted(rs, key=lambda r: order[r["state"]]):
            lines.append(
                f"| {r['name']} | {r['app']} | {r['scope']} | {LABEL[r['state']]} | {r['expires']} |"
            )
    if fixes(rs):
        lines += [
            "",
            "期限切れのまま残っている物は、取り消すか出し直す（そのアプリの命令で。seed なら seed token revoke）。",
        ]
    return "\n".join(lines)
