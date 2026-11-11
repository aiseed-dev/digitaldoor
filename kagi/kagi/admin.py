"""seed のサーバーの「人の出入り」を、係の机から管理する（設計書 §3.4 の机の画面の四つ目）。

この部品は seed のサーバー（https://github.com/aiseed-dev/seed ）の管理の口を前提にしている。
digitaldoor には 2026-11-11 に写した。切り離すかは運営者が決める（kagi/README.md）。

seed のサーバーは運営者の鍵で Claude を呼ぶので、誰がその口を使えるかを、欄と決まりのはっきりした
フォームの形で管理する。

サーバーの管理の口（`/api/admin/*`。要求の頭 `x-seed-invite` に管理者の token）を `urllib` で呼ぶ。
kagi は seed を import しない。要る言葉と決まりは、ここに書く。

- 管理者の token は作業の場所の `seed-admin.json`（600）にだけ置く。画面・記録・例外の文には出さない。
- 通すのは https だけ（手元で確かめる `http://127.0.0.1`・`localhost` は通す）。転送（redirect）は辿らない。
- 画面の中身を作る所は Flet を使わない関数。サーバーを呼ぶ所は `transport` を差し替えられる。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from kagi import audit, inventory
from kagi.inventory import Refused

CONN_FILE = "seed-admin.json"
ADMIN_TOKEN_ID = "seed-admin-token"
SCOPES = ("member", "buyer", "admin")
SCOPE_LABEL = {
    "member": "仲間（写真の読み取り、公開）",
    "buyer": "買い手（一言を送るだけ）",
    "admin": "管理者（この画面）",
}
STATE_LABEL = {"live": "使えます", "expired": "期限切れ", "revoked": "取り消し済み"}
DAYS_DEFAULT = {"member": 90, "buyer": 90, "admin": 30}
DAYS_MAX = 365
NAME_MAX = 64
EXPIRING_DAYS = 14  # seed のサーバーが「もうすぐ切れる」と数える日数と同じ
DENIED_MANY = 20  # 今日、断った要求がこの回数以上なら「気を付ける事」に出す
AUDIT_LINES = 40
TIMEOUT = 20
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

# サーバーが外のサービスを呼ぶ鍵。overview の keys の名 → 画面の名と、在庫表の行を見分ける語
SERVER_KEYS = {
    "claude": ("Claude（Anthropic）", ("anthropic", "claude")),
    "plantnet": ("Pl@ntNet", ("plantnet", "pl@ntnet")),
}

ACT = {
    "read": "写真を読み取った",
    "talk": "Claude と話した",
    "identify": "草の名を確かめた",
    "lookup": "土地を調べた",
    "publish": "公開した",
    "withdraw": "取り下げた",
    "connect": "一言を送った",
    "export": "記録を持ち帰った",
    "delete_me": "自分の箱を消した",
    "admin.overview": "管理: 状態を見た",
    "admin.tokens": "管理: token を見た",
    "admin.token.add": "管理: token を出した",
    "admin.token.revoke": "管理: token を取り消した",
    "admin.token_revoke_all": "管理: token を全部取り消した",
    "admin.pause": "管理: 受け付けを止めた",
    "admin.resume": "管理: 受け付けを再開した",
    "admin.audit": "管理: 操作の記録を見た",
    "admin.records": "管理: 公開の一覧を見た",
    "admin.withdraw": "管理: 公開を取り下げた",
    "admin.connections": "管理: 寄せられた一言を見た",
    "admin.connection_remove": "管理: 寄せられた一言を消した",
    "admin.pending": "管理: 載せる前の記録を見た",
    "admin.pending_photo": "管理: 載せる前の写真を見た",
    "admin.approve": "管理: 記録を載せた",
    "admin.return": "管理: 記録を戻した",
    "publish.state": "公開の今を見た",
}
REASON_MAX = 200  # 戻す理由（サーバーと同じ上限。HTML と連絡先らしい物はサーバーが断る）


class AdminError(Exception):
    """サーバーに繋がらない、断られた。文に token は入れない。"""


# ---- 繋ぐ先 ----


def check_url(url: str) -> str:
    """https だけを通す（手元の番地は http も通す）。末尾の / を外して返す。"""
    u = (url or "").strip().rstrip("/")
    if not u:
        raise Refused("サーバーの URL が空")
    p = urllib.parse.urlsplit(u)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise Refused("サーバーの URL は https://… の形で書く")
    if p.username or p.password or p.query or p.fragment:
        raise Refused("サーバーの URL に名前・合言葉・? や # を入れない")
    if p.scheme == "http" and p.hostname not in LOCAL_HOSTS:
        raise Refused("http の URL は通さない（token が平文で流れるため）。https://… にする")
    return u


class Conn:
    """繋ぐ先。token は repr にも str にも出さない。"""

    __slots__ = ("_token", "url", "warning")

    def __init__(self, url: str, token: str, warning: str | None = None):
        self.url = url
        self._token = token
        self.warning = warning

    def __repr__(self) -> str:
        return f"Conn(url={self.url!r})"

    __str__ = __repr__

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or self.url


def conn_path(d: Path) -> Path:
    return Path(d) / CONN_FILE


def save_conn(d: Path, url: str, token: str) -> Path:
    """URL と管理者の token を、持ち主だけが読めるファイル（600）に置く。"""
    url = check_url(url)
    token = (token or "").strip()
    if not token:
        raise Refused("管理者の token が空")
    if any(c.isspace() or ord(c) < 32 for c in token) or len(token) > 512:
        raise Refused("管理者の token の形が違う（空白や改行を含む、長すぎる）")
    path = conn_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json.dumps({"url": url, "token": token}).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return path


def load_conn(d: Path) -> Conn | None:
    """置いた繋ぐ先。無ければ None。読めない時は Refused（文に値は入れない）。"""
    path = conn_path(d)
    if not path.exists():
        return None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        data = json.loads(path.read_text("utf-8"))
        url, token = str(data["url"]), str(data["token"])
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as e:
        raise Refused(f"繋ぐ先のファイルを読めない: {path} ({type(e).__name__})") from None
    warn = None if mode == 0o600 else f"繋ぐ先のファイルの許可が {mode:03o}（600 にする: chmod 600 {path}）"
    return Conn(check_url(url), token, warn)


def forget_conn(d: Path) -> bool:
    path = conn_path(d)
    if path.exists():
        path.unlink()
        return True
    return False


# ---- サーバーを呼ぶ ----


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 辿らない（token を別の先へ運ばない）


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    """既定の通信。返すのは（状態の番号、中身）。"""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except OSError:
            data = b""
        return e.code, data
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None)
        why = type(reason).__name__ if isinstance(reason, BaseException) else type(e).__name__
        raise AdminError(f"サーバーに繋がらない ({why})") from None


class Client:
    """管理の口。`transport(method, url, headers, body) -> (status, bytes)` を差し替えられる。"""

    def __init__(self, conn: Conn, transport=None):
        self.conn = conn
        self.transport = transport or urllib_transport

    def __repr__(self) -> str:
        return f"Client({self.conn!r})"

    def call(self, method: str, path: str, body: dict | None = None):
        headers = {"x-seed-invite": self.conn._token, "accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        try:
            status, raw = self.transport(method, self.conn.url + path, headers, data)
        except AdminError:
            raise
        except Exception as e:  # noqa: BLE001 — 差し替えた通信の失敗も一文に
            raise AdminError(f"サーバーに繋がらない ({type(e).__name__})") from None
        try:
            out = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            out = None
        if status == 403:
            raise AdminError(
                "サーバーに断られた（403）。管理者の token が違うか、取り消されたか、期限が切れた"
            )
        if status >= 400:
            detail = ""
            if isinstance(out, dict):
                d = out.get("detail")
                detail = "、".join(str(x) for x in d) if isinstance(d, list) else str(out.get("error", ""))
            raise AdminError(f"サーバーに断られた（{status}）" + (f": {detail}" if detail else ""))
        if not isinstance(out, dict):
            raise AdminError(f"サーバーの答えが読めない（{status}）")
        return out

    def overview(self) -> dict:
        return self.call("GET", "/api/admin/overview")

    def tokens(self) -> list[dict]:
        return list(self.call("GET", "/api/admin/tokens").get("tokens") or [])

    def token_add(self, name: str, scope: str, days: int, note: str = "") -> str:
        out = self.call(
            "POST", "/api/admin/tokens", {"name": name, "scope": scope, "days": days, "note": note}
        )
        code = out.get("token")
        if not isinstance(code, str) or not code:
            raise AdminError("サーバーの答えに token が無い")
        return code

    def revoke(self, name: str) -> int:
        return int(self.call("POST", "/api/admin/tokens/revoke", {"name": name}).get("revoked", 0))

    def revoke_all(self) -> int:
        return int(self.call("POST", "/api/admin/tokens/revoke_all").get("revoked", 0))

    def pause(self, paused: bool) -> bool:
        return bool(self.call("POST", "/api/admin/pause", {"paused": bool(paused)}).get("paused"))

    def audit(self, n: int = AUDIT_LINES) -> dict:
        return self.call("GET", f"/api/admin/audit?limit={int(n)}")

    def records(self) -> list[dict]:
        return list(self.call("GET", "/api/admin/records").get("records") or [])

    def connections(self, rid: str) -> list[dict]:
        q = urllib.parse.quote(rid, safe="")
        return list(self.call("GET", f"/api/admin/records/{q}/connections").get("connections") or [])

    def withdraw(self, rid: str) -> None:
        self.call("DELETE", f"/api/admin/records/{urllib.parse.quote(rid, safe='')}")

    def connection_remove(self, rid: str, name: str) -> None:
        q, n = urllib.parse.quote(rid, safe=""), urllib.parse.quote(name, safe="")
        self.call("DELETE", f"/api/admin/records/{q}/connections/{n}")

    def pending(self) -> list[dict]:
        return list(self.call("GET", "/api/admin/pending").get("pending") or [])

    def pending_photo(self, rid: str, name: str) -> bytes:
        """載せる前の写真（JPEG の中身）。"""
        q, n = urllib.parse.quote(rid, safe=""), urllib.parse.quote(name, safe="")
        headers = {"x-seed-invite": self.conn._token, "accept": "image/jpeg"}
        try:
            status, raw = self.transport(
                "GET", f"{self.conn.url}/api/admin/pending/{q}/photos/{n}", headers, None
            )
        except AdminError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AdminError(f"サーバーに繋がらない ({type(e).__name__})") from None
        if status >= 400 or not raw.startswith(b"\xff\xd8\xff"):
            raise AdminError(f"写真を読めない（{status}）: {name}")
        return raw

    def approve(self, rid: str) -> None:
        self.call("POST", f"/api/admin/pending/{urllib.parse.quote(rid, safe='')}/approve")

    def return_pending(self, rid: str, reason: str) -> None:
        self.call("POST", f"/api/admin/pending/{urllib.parse.quote(rid, safe='')}/return", {"reason": reason})


def load_all(client: Client) -> dict:
    """画面に要る物を一度に読む。"""
    return {
        "overview": client.overview(),
        "tokens": client.tokens(),
        "audit": client.audit(AUDIT_LINES),
        "records": client.records(),
        "pending": _pending(client),
    }


def _pending(client: Client) -> list[dict] | None:
    """載せる前の記録。預かりの無い古いサーバー（404）なら None（画面に出さない）。"""
    try:
        return client.pending()
    except AdminError as e:
        if "（404）" in str(e):
            return None
        raise


# ---- 画面の中身（Flet を使わない） ----


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def state_rows(o: dict) -> list[list[str]]:
    """今の状態（名、値）。overview に無い値の行は「分からない」か、出さない。"""
    o = _d(o)
    keys, tk, left = _d(o.get("keys")), _d(o.get("tokens")), _d(o.get("remaining"))

    def have(k: str) -> str:
        if k not in keys:
            return "分からない"
        return "在る" if keys.get(k) else "無い"

    def yes(k: str, a: str, b: str) -> str:
        return "分からない" if k not in o else (a if o.get(k) else b)

    audit_word = {"on": "書いています", "off": "書いていません", "error": "書けなくなっています"}
    rows = [
        ["サーバー", f"動いています（版 {o.get('version', '?')}）"],
        ["受け付け", yes("paused", "止めています", "受け付けています")],
        ["Claude を呼べるか", yes("read", "呼べます", "呼べません")],
        ["外の鍵", f"Claude {have('claude')}、Pl@ntNet {have('plantnet')}"],
        ["操作の記録", audit_word.get(str(o.get("audit")), "分からない")],
    ]
    today = []
    if _int(left.get("reads")) is not None:
        today.append(f"読み取り {left['reads']} 回")
    if _int(left.get("talks")) is not None:
        today.append(f"話す {left['talks']} 回")
    if isinstance(left.get("usd"), int | float) and not isinstance(left.get("usd"), bool):
        today.append(f"使える額 {left['usd']} ドル")
    if today:
        rows.append(["今日の残り", "、".join(today)])
    if "records" in o or "connections" in o:
        rows.append(["公開", f"記録 {o.get('records', 0)} 件、寄せられた一言 {o.get('connections', 0)} 件"])
    people = []
    if _int(tk.get("live")) is not None:
        people.append(f"token {tk['live']} 人")
    if _int(o.get("invites")) is not None:
        people.append(f"招待コード {o['invites']} 人")
    if people:
        rows.append(["入れる人", "、".join(people)])
    if _int(o.get("denied_today")) is not None:
        rows.append(["今日断った要求", f"{o['denied_today']} 回"])
    return rows


def attention(o: dict, audit_: dict | None = None) -> list[tuple[str, list[str]]]:
    """気を付ける事。（何が起きているかの文、サーバーで打つコマンドの行）の並び。無ければ空。"""
    o, au = _d(o), _d(audit_)
    out: list[tuple[str, list[str]]] = []

    def warn(text: str, *cmds: str) -> None:
        out.append((text, list(cmds)))

    if o.get("paused"):
        warn("受け付けを止めています。戻すには下の「受け付けを再開する」を押します。")
    if "read" in o and not o.get("read"):
        warn(
            "写真を読み取れず、話す口も使えません。Claude の鍵が入っていません。サーバーで次を打ちます。"
            "二行目で鍵を貼り、Enter の後に Ctrl-D で終えます（鍵がもう在る時は作り直しません）。",
            "sudo install -d -o root -g root -m 700 /etc/credstore",
            "sudo test -e /etc/credstore/seed.anthropic || sudo sh -c 'umask 077; cat > /etc/credstore/seed.anthropic'",
            "sudo systemctl restart seed",
        )
    if o.get("audit") == "off":
        warn(
            "操作の記録を書いていません。署名の鍵が在りません。サーバーで次を打ちます。"
            "鍵がもう在る時は作り直しません（作り直すと、前の記録を確かめられなくなるため）。",
            "sudo test -e /etc/seed/log-key || "
            "sudo sh -c 'umask 077; head -c 32 /dev/urandom > /etc/seed/log-key'",
            "sudo systemctl restart seed",
        )
    elif o.get("audit") == "error":
        warn(
            "操作の記録に書けなくなっています。置き場の空きと持ち主を見ます。",
            "df -h /var/lib/seed",
            "ls -ld /var/lib/seed/audit",
        )
    if au.get("verified") is False:
        warn(
            "操作の記録が書き換えられています。誰かが機械の中に入った印です。"
            "表からの被害を増やさないために、下の「受け付けを止める」を押します。"
            "次に、サーバーで知らない入り方が無いかを見ます。",
            "last -n 20",
            "sudo journalctl -u ssh --since -2d",
        )
    denied = _int(o.get("denied_today"))
    if denied is not None and denied >= DENIED_MANY:
        warn(
            f"今日、断った要求が {denied} 回あります。誰かが試しているかもしれません。"
            "下の「操作の記録」で相手を見ます。続くなら「受け付けを止める」を押します。"
        )
    tk = _d(o.get("tokens"))
    if _int(tk.get("expired")):
        warn(
            f"期限の切れた token が {tk['expired']} 件あります（もう入れません）。要る人には、下で出し直します。"
        )
    if _int(tk.get("expiring")):
        warn(
            f"あと {EXPIRING_DAYS} 日以内に切れる token が {tk['expiring']} 件あります。"
            "要る人には、切れる前に出し直します。"
        )
    reads = _int(_d(o.get("remaining")).get("reads"))
    if reads is not None and reads <= 0:
        warn("今日の写真の読み取りの上限に達しました。明日戻ります。")
    return out


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def token_rows(tokens: list[dict], today: dt.date | None = None) -> list[dict]:
    """token の一覧。生きている物が先。もうすぐ切れる物に印（soon）。"""
    today = today or dt.date.today()
    order = {"live": 0, "expired": 1, "revoked": 2}
    rows = []
    for t in sorted(
        (t for t in tokens if isinstance(t, dict)),
        key=lambda x: (order.get(x.get("state"), 9), str(x.get("name", ""))),
    ):
        state = str(t.get("state", "expired"))
        exp = _date(t.get("expires"))
        soon = state == "live" and exp is not None and exp <= today + dt.timedelta(days=EXPIRING_DAYS)
        scopes = t.get("scope") if isinstance(t.get("scope"), list) else [t.get("scope")]
        label = STATE_LABEL.get(state, state)
        if soon:
            label += f"（あと {(exp - today).days} 日で切れる）"
        rows.append(
            {
                "name": str(t.get("name", "")),
                "scope": "、".join(SCOPE_LABEL.get(str(s), str(s)) for s in scopes if s),
                "state": label,
                "period": f"{t.get('created', '')} 〜 {t.get('expires', '')}",
                "note": str(t.get("note", "") or ""),
                "live": state == "live",
                "soon": soon,
            }
        )
    return rows


def record_rows(records: list[dict]) -> list[dict]:
    """公開された記録（名前、場所、公開した人、日、寄せられた一言の数、頁）。"""
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "id": str(r.get("id", "")),
                "head": " · ".join(str(x) for x in (r.get("name"), r.get("place"), r.get("since")) if x),
                "meta": f"公開した人 {r.get('owner') or '分からない人'}　公開した日 {r.get('published') or '?'}"
                f"　寄せられた一言 {r.get('connections', 0)} 件",
                "url": str(r.get("url", "") or ""),
            }
        )
    return out


def pending_rows(pending: list[dict]) -> list[dict]:
    """載せる前の記録（呼び名、届いた時、全文、写真の名）。古い物が先（サーバーの順）。"""
    out = []
    for r in pending:
        if not isinstance(r, dict):
            continue
        photos = r.get("photos") if isinstance(r.get("photos"), list) else []
        when = str(r.get("received", "") or "")[:16].replace("T", " ")
        out.append(
            {
                "id": str(r.get("id", "")),
                "meta": f"送った人 {r.get('name') or '分からない人'}　届いた時 {when or '?'}　写真 {len(photos)} 枚",
                "markdown": str(r.get("markdown", "") or ""),
                "photos": [str(n) for n in photos],
            }
        )
    return out


def check_reason(reason: str) -> str:
    """戻す理由の欄。前後の空白を外し、200 字まで、改行は空白に。"""
    r = " ".join((reason or "").split())
    if len(r) > REASON_MAX:
        raise Refused(f"戻す理由は {REASON_MAX} 字まで")
    return r


def connection_rows(conns: list[dict]) -> list[dict]:
    return [
        {
            "name": str(c.get("name", "")),
            "head": f"{c.get('date', '')}　{c.get('from', '')}",
            "word": str(c.get("word", "")),
        }
        for c in conns
        if isinstance(c, dict)
    ]


def audit_view(a: dict) -> dict:
    """サーバーの操作の記録（時、誰、した事、どうなった）と、確かめの結果。"""
    a = _d(a)
    v = a.get("verified")
    if v is True:
        head = "確かめた: 書き換えは見つからない"
    elif v is False:
        head = "書き換えが見つかった。記録の連なりが切れている（気を付ける事を見る）"
    else:
        head = "確かめられなかった（サーバーに署名の鍵が無いか、記録がまだ無い）"
    rows = []
    for ln in (a.get("lines") or [])[:AUDIT_LINES]:
        if not isinstance(ln, dict):
            continue
        action = str(ln.get("action", ""))
        who = str(ln.get("actor", ""))
        rows.append(
            [
                str(ln.get("ts", ""))[:16].replace("T", " "),
                "知らない人" if who in ("?", "") else who,
                ACT.get(action, action),
                str(ln.get("detail", "")),
            ]
        )
    return {
        "verified": v,
        "head": head,
        "tampered": v is False,
        "columns": ["時", "誰", "した事", "どうなった"],
        "rows": rows,
    }


# ---- 人に渡す token の欄 ----


def default_days(scope: str) -> int:
    return DAYS_DEFAULT.get(scope, 90)


def check_token_form(name: str, scope: str, days: str) -> tuple[dict, dict | None]:
    """欄の決まり。（欄ごとの駄目な理由、通る時の値）。理由が一つでもあれば値は None。"""
    errors: dict[str, str] = {}
    n = (name or "").strip()
    if not n:
        errors["name"] = "渡す相手の名前を書く（必須）"
    elif len(n) > NAME_MAX:
        errors["name"] = f"名前は {NAME_MAX} 字まで"
    elif any(ord(c) < 32 for c in n):
        errors["name"] = "名前に改行や制御の文字を入れない"
    if scope not in SCOPES:
        errors["scope"] = "できる事を、仲間・買い手・管理者から一つ選ぶ"
    s = (days or "").strip()
    n_days = None
    if not s:
        errors["days"] = "期限を日数で書く（1〜365）"
    elif not (s.isascii() and s.isdigit()):
        errors["days"] = "期限は日数を数字で書く（1〜365）"
    else:
        n_days = int(s)
        if not 1 <= n_days <= DAYS_MAX:
            errors["days"] = f"期限は 1〜{DAYS_MAX} 日"
    if errors:
        return errors, None
    return {}, {"name": n, "scope": scope, "days": n_days}


def second_press(st: dict, key) -> bool:
    """二度押し。一度目は印を付けて False、同じ物の二度目で True（印を外す）。"""
    if st.get("confirm") == key:
        st["confirm"] = None
        return True
    st["confirm"] = key
    return False


# ---- 操作（サーバーを呼び、kagi の記録にも一行） ----


def note_audit(
    d: Path, action: str, detail: str, env: dict | None = None, actor: str | None = None
) -> str | None:
    """kagi の記録に一行。書けた時は None、書けない時はその事の一文。"""
    env = os.environ if env is None else env
    try:
        key = audit.find_key(env)
        audit.add(Path(d) / "audit.log", key, actor or env.get("USER", "?"), action, detail)
    except audit.NoKey:
        return "kagi の記録には書かなかった（署名の鍵が無い。skills/kagi/02-記録.md）"
    except (ValueError, OSError) as e:
        why = str(e) if isinstance(e, ValueError) else type(e).__name__
        return f"kagi の記録に書けなかった（{why}）"
    return None


def issue_token(d: Path, client: Client, name: str, scope: str, days: str, note: str = "", env=None):
    """欄を確かめて token を出す。（token、記録の一文）。欄が駄目なら Refused（理由は欄ごと）。"""
    errors, v = check_token_form(name, scope, days)
    if v is None:
        raise Refused("、".join(errors.values()))
    note = (note or "").strip()
    code = client.token_add(v["name"], v["scope"], v["days"], note)
    msg = note_audit(d, "seed.token.add", f"{client.conn.host}: {v['name']} {v['scope']} {v['days']} 日", env)
    return code, msg


def revoke_token(d: Path, client: Client, name: str, env=None) -> tuple[int, str | None]:
    n = client.revoke(name)
    return n, note_audit(d, "seed.token.revoke", f"{client.conn.host}: {name}", env)


def revoke_all_tokens(d: Path, client: Client, env=None) -> tuple[int, str | None]:
    n = client.revoke_all()
    return n, note_audit(d, "seed.token.revoke_all", f"{client.conn.host}: {n} 件（自分の分は残す）", env)


def set_paused(d: Path, client: Client, paused: bool, env=None) -> tuple[bool, str | None]:
    now = client.pause(paused)
    action = "seed.pause" if paused else "seed.resume"
    return now, note_audit(
        d, action, f"{client.conn.host}: 受け付けを{'止めた' if paused else '再開した'}", env
    )


def withdraw_record(d: Path, client: Client, rid: str, env=None) -> str | None:
    client.withdraw(rid)
    return note_audit(d, "seed.withdraw", f"{client.conn.host}: 公開 {rid} を取り下げた", env)


def remove_connection(d: Path, client: Client, rid: str, name: str, env=None) -> str | None:
    client.connection_remove(rid, name)
    return note_audit(
        d, "seed.connection_remove", f"{client.conn.host}: 公開 {rid} の一言 {name} を消した", env
    )


def approve_record(d: Path, client: Client, rid: str, env=None) -> str | None:
    client.approve(rid)
    return note_audit(d, "seed.approve", f"{client.conn.host}: 記録 {rid} を載せた", env)


def return_record(d: Path, client: Client, rid: str, reason: str, env=None) -> str | None:
    """戻す。理由は公開した人に届く。kagi の記録には id だけ（理由は書かない）。"""
    client.return_pending(rid, check_reason(reason))
    return note_audit(d, "seed.return", f"{client.conn.host}: 記録 {rid} を戻した", env)


# ---- 在庫表 ----

_VALUE_PATTERNS = [
    re.compile(p)
    for p in (
        r"sk-[A-Za-z0-9_-]{8,}",
        r"sk_(live|test)_",
        r"AKIA[0-9A-Z]{16}",
        r"ghp_[A-Za-z0-9]{20,}",
        r"glpat-",
        r"xox[abps]-",
        r"-----BEGIN ",
        r"\b[0-9a-fA-F]{32,}\b",
    )
]
_RUN = re.compile(r"[A-Za-z0-9_+=-]{20,}")


def looks_like_value(text: str) -> bool:
    """鍵や token の値らしいか（決まった頭、長い 16 進、大文字・小文字・数字の混じる長い並び）。"""
    s = text or ""
    if any(p.search(s) for p in _VALUE_PATTERNS):
        return True
    for m in _RUN.finditer(s):
        w = m.group(0)
        if re.search(r"[A-Z]", w) and re.search(r"[a-z]", w) and re.search(r"[0-9]", w):
            return True
    return False


SECRET_FIELDS = (
    ("id", "名"),
    ("where", "置き場"),
    ("holder", "持つ人"),
    ("role", "役"),
    ("expires", "期限"),
    ("lost", "なくした時"),
    ("leaked", "漏れた時"),
)


def check_secret_form(fields: dict, existing: list[dict] | None = None) -> dict:
    """在庫表の一件の欄の決まり。欄ごとの駄目な理由（無ければ空）。"""
    errors: dict[str, str] = {}
    for k, label in SECRET_FIELDS:
        v = str(fields.get(k, "") or "").strip()
        if looks_like_value(v):
            errors[k] = f"{label} に値らしい物がある。値は書かない（K-13）。消してから"
        elif k in ("id", "where", "holder", "role") and not v:
            errors[k] = f"{label} を書く（必須）"
        elif any(ord(c) < 32 for c in v):
            errors[k] = f"{label} に改行を入れない"
    sid = str(fields.get("id", "") or "").strip()
    if "id" not in errors and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]*", sid):
        errors["id"] = "名は英数字と . _ @ - で書く"
    elif "id" not in errors and any(str(s.get("id")) == sid for s in existing or []):
        errors["id"] = f"{sid} はもう在庫表に在る"
    exp = str(fields.get("expires", "") or "").strip()
    if exp and "expires" not in errors:
        try:
            dt.date.fromisoformat(exp)
        except ValueError:
            errors["expires"] = "期限は 2027-11-06 の形で書く"
    return errors


def add_secret(d: Path, fields: dict, kind: str = "token", made: dt.date | None = None) -> str:
    """在庫表に [[secret]] を一件足す。値の欄は無い。足した形を読めるか確かめてから書く。id を返す。"""
    path = Path(d) / "inventory.toml"
    try:
        old = path.read_text("utf-8") if path.exists() else ""
        existing = inventory.load(path) if path.exists() else []
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(f"在庫表を読めない: {path} ({type(e).__name__})") from None
    errors = check_secret_form(fields, existing)
    if errors:
        raise Refused("、".join(errors.values()))
    if kind not in inventory.KINDS:
        raise Refused("kind が違う")
    lines = ["[[secret]]", f"id = {json.dumps(fields['id'].strip())}", f"kind = {json.dumps(kind)}"]
    for k in ("where", "holder", "role"):
        lines.append(f"{k} = {json.dumps(str(fields[k]).strip(), ensure_ascii=False)}")
    lines.append(f"made = {(made or dt.date.today()).isoformat()}")
    exp = str(fields.get("expires", "") or "").strip()
    if exp:
        lines.append(f"expires = {exp}")
    for k in ("lost", "leaked"):
        v = str(fields.get(k, "") or "").strip()
        if v:
            lines.append(f"{k} = {json.dumps(v, ensure_ascii=False)}")
    new = old.rstrip("\n") + ("\n\n" if old.strip() else "") + "\n".join(lines) + "\n"
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError:
        raise Refused("足した形が TOML として読めない。書かなかった") from None
    path.write_text(new, "utf-8")
    return fields["id"].strip()


def has_admin_token_row(d: Path) -> bool:
    """在庫表に、管理者の token の一件が在るか（id が seed-admin-token か、置き場に seed-admin.json）。"""
    try:
        secrets = inventory.load(Path(d) / "inventory.toml")
    except Refused:
        return False
    return any(str(s.get("id")) == ADMIN_TOKEN_ID or CONN_FILE in str(s.get("where", "")) for s in secrets)


def admin_token_prefill(d: Path, conn: Conn | None) -> dict:
    """在庫表に足す時の欄の初めの値（値は入れない）。"""
    host = conn.host if conn else "seed のサーバー"
    return {
        "id": ADMIN_TOKEN_ID,
        "where": f"pc:{conn_path(d)}（600）",
        "holder": os.environ.get("USER", ""),
        "role": f"kagi の机から {host} の管理の口を使う（範囲 admin）",
        "expires": "",
        "lost": "サーバーで新しい管理者の token を出し、kagi の「人の出入り」で入れ替える",
        "leaked": "サーバーで、その token を取り消す → 操作の記録で範囲を見る → 新しい token を出して入れ替える → 書く",
    }


def _rows_for(secrets: list[dict], words: tuple[str, ...]) -> list[dict]:
    out = []
    for s in secrets:
        text = f"{s.get('id', '')} {s.get('where', '')}".lower()
        if any(w in text for w in words):
            out.append(s)
    return out


def server_keys_view(d: Path, overview: dict | None, days: int = 30, today: dt.date | None = None) -> dict:
    """サーバーが外のサービスを呼ぶ鍵と、在庫表の行の突き合わせ。値は無い（在るか無いかだけ）。

    在庫表の行は、id か置き場に、鍵ごとの語（anthropic・claude、plantnet）を含む物をその鍵の一件と見る。
    """
    today = today or dt.date.today()
    v = {
        "columns": ["鍵", "サーバーに", "名", "置き場", "持つ人", "役", "期限", "なくした時", "漏れた時"],
        "rows": [],
        "fixes": [],
        "notes": [],
        "error": None,
        "seen": overview is not None,
    }
    try:
        secrets = inventory.load(Path(d) / "inventory.toml")
    except Refused as e:
        v["error"] = str(e)
        secrets = []
    res = inventory.check(secrets, days, today)
    by_id: dict[str, list] = {}
    for f in res.findings:
        by_id.setdefault(f.id, []).append(f)
    keys = _d(_d(overview).get("keys")) if overview is not None else {}
    for name, (label, words) in SERVER_KEYS.items():
        on_server = keys.get(name) if name in keys else None
        server = (
            "分からない（「人の出入り」で読む）" if on_server is None else ("在る" if on_server else "無い")
        )
        rows = _rows_for(secrets, words)
        if not rows:
            v["rows"].append([label, server, "（在庫表に無い）", "", "", "", "", "", ""])
            if on_server:
                v["fixes"].append(
                    f"{label}: サーバーに鍵が在るのに、在庫表に一件も無い。在庫表に一件足す（K-14）"
                )
            continue
        if on_server is False:
            v["notes"].append(
                f"{label}: 在庫表に在るが、サーバーには無い（置く前か、外した後なら、それで合っている）"
            )
        for s in rows:
            sid = str(s.get("id", ""))
            v["rows"].append(
                [
                    label,
                    server,
                    sid,
                    str(s.get("where", "")),
                    str(s.get("holder", "")),
                    str(s.get("role", "")),
                    str(s.get("expires", "書いていない")),
                    str(s.get("lost", "")),
                    str(s.get("leaked", "")),
                ]
            )
            for f in by_id.get(sid, []):
                if f.problem == "expired":
                    v["fixes"].append(f"{label}: {sid} の期限が切れた（{f.detail}）")
                elif f.problem == "soon":
                    v["notes"].append(f"{label}: {sid} の期限が近い（{f.detail}）")
    return v
