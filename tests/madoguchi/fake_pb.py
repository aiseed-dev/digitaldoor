"""疑似の PocketBase(試しと、窓口の頁を手で確かめるための物。標準ライブラリだけ)。

窓口仕様書(docs/窓口仕様書.md)の 3、4、8、9 章が使う口だけを持ちます。応答の形は、仕様書が確かめた
PocketBase 0.40.4 の形(3、4 章と 14 章の出典)に合わせています。

    GET    /api/health
    GET    /api/collections/users/auth-methods            (?fields=... で上の鍵を絞る)
    POST   /api/collections/<users|kikai>/auth-with-password
    POST   /api/collections/users/auth-with-oauth2
    POST   /api/collections/<users|kikai>/auth-refresh    (仕様書 4.4。頁を読み直した時に使う)
    GET    /api/collections/<c>/records                   (page、perPage、sort、filter、fields、skipTotal)
    GET    /api/collections/<c>/records/<id>
    POST   /api/collections/<c>/records
    PATCH  /api/collections/<c>/records/<id>
    DELETE /api/collections/<c>/records/<id>
    GET    /fake-google                                    (疑似の提供者の頁。すぐに redirect_uri へ戻す)
    OPTIONS どの道も                                       (CORS の下調べに 204)

本物と違う所:

- **決まりを手で書いています。** 仕様書 3.2〜3.4 の決まり(本人、`kikai` の役 `shoukai`、`kikai` の役 `asa`)を、
  式を解釈せずに Python の条件として `_allowed` に書いています。式の書き方の誤りは、この疑似では見つかりません
  (本物の決まりは仕様書 11.4 の手の確かめで見ます)。superuser は居ません(`null` の決まりの操作は、いつも 403)。
- **札は、この疑似の中だけで通じる乱数の文字列です。** JWT ではなく、寿命も `tokenKey` もありません。
  `Authorization` のヘッダで受け、`Bearer ` の前置きは外して読みます。知らない札は、本物と同じく客(guest)として扱います。
- **realtime(`/api/realtime`、`/api/oauth2-redirect`)がありません。** `apple` の形(仕様書 4.3)は試せません。
  `auth-methods` が返す提供者は `google` の一つだけです。
- **絞り込み(filter)は `項目 = "値"` を `&&` でつないだ形だけを読みます。** ほかの形は 400 です。並べ替え(sort)は
  項目の名を `,` でつなぎ、`-` で逆順にする形だけです。展開(expand)はありません。
- **項目の確かめは、仕様書 3.3、3.4 の制約(要る、選べる値、長さ、形、関係の相手、一意の索引)だけです。** 応答の
  `data` の中の誤りの言葉は本物に似せた物で、同じではありません。
- **回数の制限(rate limit)、MFA、OTP、ファイル、メールはありません。** `users` の新しい登録は、疑似の提供者を
  通った時だけです(パスワードでの登録の口はありません)。
- **時刻。** `created` と `updated` は、本物と同じく UTC の `YYYY-MM-DD HH:MM:SS.mmmZ` です。試しは `add_record` で決めた
  値を入れられます。

試しの中での使い方:

    with FakePB() as pb:
        ident, pw = pb.add_kikai("shoukai")
        uid = pb.add_user()["id"]
        ... pb.url に向けて道具を動かす ...
        pb.requests            # 受けた要求(方法、道、問い、ヘッダ、本文)の列
        pb.records("shoukai")  # 今のレコード

手で頁を確かめる時:

    python tests/madoguchi/fake_pb.py --port 8090 [--deny]

試しの `kikai` のレコードを二つ(役 `shoukai` と `asa`)作り、その識別とパスワードを、起動の時に一度だけ標準出力に出します
(どちらも起動のたびに乱数で作ります)。疑似の提供者の頁は、`?deny=1` を付けると(`--deny` で立てると、いつも)
`error=access_denied` を付けて戻します。`?as=<名>` を付けると、別の架空の人としてサインインします。
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
import secrets
import string
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

ID_CHARS = string.ascii_lowercase + string.digits

COLLECTION_IDS = {
    "users": "_pb_users_auth_",
    "kikai": "pbc_kikai000000",
    "toiawase": "pbc_toiawase000",
    "shoukai": "pbc_shoukai0000",
}
AUTH_COLLECTIONS = ("users", "kikai")
BASE_COLLECTIONS = ("toiawase", "shoukai")

TOIAWASE_KINDS = ["質問", "相談", "教室", "そのほか"]
TOIAWASE_STATUS = ["新しい", "返事済み", "閉じた"]
MADE_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
ISSUER = re.compile(r"^[0-9a-f]{16}$")
FILTER_PART = re.compile(r'^\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)\s*(=|!=)\s*"((?:[^"\\]|\\.)*)"\s*\)?\s*$')

PER_PAGE_DEFAULT = 30
PER_PAGE_MAX = 1000


def new_id() -> str:
    """PocketBase の既定の id の形([a-z0-9]{15})。"""
    return "".join(secrets.choice(ID_CHARS) for _ in range(15))


def pb_time(d: datetime | None = None) -> str:
    """PocketBase の autodate の形(UTC、`YYYY-MM-DD HH:MM:SS.mmmZ`)。"""
    d = (d or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return d.strftime("%Y-%m-%d %H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def challenge_of(verifier: str) -> str:
    """PKCE の S256。"""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def err(status: int, message: str, data: dict | None = None) -> tuple[int, dict]:
    return status, {"status": status, "message": message, "data": data or {}}


NOT_FOUND = "The requested resource wasn't found."
SUPERUSERS_ONLY = "Only superusers can perform this action."


@dataclass
class Req:
    """受けた要求の控え。"""
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes
    auth: tuple[str, str] | None = None     # (コレクション, レコードの id)。客なら None

    def q(self, name: str, default: str | None = None) -> str | None:
        v = self.query.get(name)
        return v[0] if v else default

    @property
    def json(self):
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass
class _Code:
    challenge: str
    redirect: str
    person: str
    used: bool = False


@dataclass
class FakePB:
    """疑似の PocketBase。`start()` で `host` の空いた口(`port=0`)に立てる。"""
    host: str = "127.0.0.1"
    port: int = 0
    deny: bool = False
    quiet: bool = True
    requests: list[Req] = field(default_factory=list)
    overrides: list = field(default_factory=list)   # fn(req) -> (status, body) | None。決まりの前に当てる

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, dict]] = {c: {} for c in COLLECTION_IDS}
        self._secret: dict[tuple[str, str], str] = {}      # (コレクション, id) → パスワード
        self._tokens: dict[str, tuple[str, str]] = {}
        self._states: dict[str, str] = {}                  # state → codeVerifier
        self._codes: dict[str, _Code] = {}
        self._people: dict[str, str] = {}                  # 疑似の提供者の人 → users の id
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---- 立てる、止める ----

    def start(self) -> "FakePB":
        handler = type("Handler", (_Handler,), {"pb": self})
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "FakePB":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ---- 試しが直に使う口 ----

    def add_user(self, *, email: str | None = None, name: str = "", id: str | None = None,
                 password: str | None = None) -> dict:
        """架空の users のレコードを足す。"""
        rid = id or new_id()
        now = pb_time()
        rec = {"id": rid, "email": email or f"user-{rid}@example.invalid", "emailVisibility": False,
               "verified": True, "name": name, "avatar": "", "created": now, "updated": now}
        with self._lock:
            self._data["users"][rid] = rec
            if password is not None:
                self._secret[("users", rid)] = password
        return copy.deepcopy(rec)

    def add_kikai(self, yaku: str, *, email: str | None = None, password: str | None = None) -> tuple[str, str]:
        """kikai のレコードを足す。識別(email)とパスワードは、渡さなければ乱数で作る。(識別, パスワード) を返す。"""
        if yaku not in ("shoukai", "asa"):
            raise ValueError(yaku)
        rid = new_id()
        email = email or f"{yaku}-{secrets.token_hex(4)}@kikai.invalid"
        password = password or secrets.token_urlsafe(18)
        now = pb_time()
        with self._lock:
            self._data["kikai"][rid] = {"id": rid, "email": email, "emailVisibility": False, "verified": False,
                                        "yaku": yaku, "created": now, "updated": now}
            self._secret[("kikai", rid)] = password
        return email, password

    def add_record(self, collection: str, data: dict, *, id: str | None = None, created: str | None = None,
                   updated: str | None = None) -> dict:
        """決まりと確かめを通さずにレコードを置く(試しの下ごしらえ)。"""
        rid = id or new_id()
        now = pb_time()
        rec = {"id": rid, **copy.deepcopy(data), "created": created or now, "updated": updated or created or now}
        with self._lock:
            self._data[collection][rid] = rec
        return copy.deepcopy(rec)

    def records(self, collection: str) -> list[dict]:
        """今のレコード(置いた順)の写し。"""
        with self._lock:
            return [copy.deepcopy(r) for r in self._data[collection].values()]

    def token_for(self, collection: str, rid: str) -> str:
        """そのレコードの札を作る(試しが本人や機械として呼ぶ時)。"""
        tok = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens[tok] = (collection, rid)
        return tok

    @property
    def tokens(self) -> list[str]:
        """出した札の全部(出力に出ていないことを確かめるため)。"""
        with self._lock:
            return list(self._tokens)

    def record_requests(self, *, method: str | None = None, collection: str | None = None) -> list[Req]:
        """受けた要求のうち、レコードの口(/api/collections/<c>/records)への物。"""
        out = []
        for r in list(self.requests):
            m = re.match(r"^/api/collections/([^/]+)/records(?:/|$)", r.path)
            if not m or (collection and m.group(1) != collection) or (method and r.method != method):
                continue
            out.append(r)
        return out

    # ---- 中 ----

    def _serialize(self, collection: str, rec: dict, auth: tuple[str, str] | None = None) -> dict:
        out = {"collectionId": COLLECTION_IDS[collection], "collectionName": collection}
        out.update(copy.deepcopy(rec))
        if collection in AUTH_COLLECTIONS and not (auth == (collection, rec["id"]) or rec.get("emailVisibility")):
            out.pop("email", None)
        return out

    def _auth_of(self, headers: dict[str, str]) -> tuple[str, str] | None:
        h = headers.get("authorization", "").strip()
        if h.lower().startswith("bearer "):
            h = h[7:].strip()
        if not h:
            return None
        with self._lock:
            who = self._tokens.get(h)
            if who and who[1] in self._data[who[0]]:
                return who
        return None

    def _auth_record(self, auth: tuple[str, str] | None) -> dict | None:
        if auth is None:
            return None
        return self._data[auth[0]].get(auth[1])

    def _allowed(self, collection: str, op: str, auth: tuple[str, str] | None, rec: dict | None = None,
                 body: dict | None = None) -> bool | None:
        """仕様書 3.1〜3.4 の決まりを手で書いた物。None は決まりが null(superuser だけ)。"""
        who = auth[0] if auth else None
        me = self._auth_record(auth) or {}
        is_user = who == "users"
        yaku = me.get("yaku") if who == "kikai" else None
        if collection == "users":
            if op in ("list", "view", "update", "delete"):
                return is_user and rec is not None and rec["id"] == auth[1]
            return None
        if collection == "kikai":
            return None
        if collection == "toiawase":
            if op in ("list", "view"):
                return (is_user and rec is not None and rec.get("owner") == auth[1]) or yaku == "asa"
            if op == "create":
                body = body or {}
                return is_user and body.get("owner") == auth[1] and body.get("status") == "新しい"
            if op == "update":
                return None
            if op == "delete":
                return is_user and rec is not None and rec.get("owner") == auth[1]
        if collection == "shoukai":
            if op in ("list", "view"):
                return (is_user and rec is not None and rec.get("owner") == auth[1]) or yaku == "shoukai"
            return yaku == "shoukai"
        return None

    def _validate(self, collection: str, rec: dict) -> dict:
        """仕様書 3.3、3.4 の項目の制約。誤りを {項目: {code, message}} で返す。"""
        bad: dict[str, dict] = {}

        def blank(name):
            bad[name] = {"code": "validation_required", "message": "Cannot be blank."}

        def owner_ok():
            o = rec.get("owner")
            if not o:
                blank("owner")
            elif not isinstance(o, str) or o not in self._data["users"]:
                bad["owner"] = {"code": "validation_missing_rel_records",
                                "message": "Failed to find all relation records with the provided ids."}

        if collection == "toiawase":
            owner_ok()
            if not rec.get("kind"):
                blank("kind")
            elif rec["kind"] not in TOIAWASE_KINDS:
                bad["kind"] = {"code": "validation_invalid_value", "message": "Invalid value."}
            for name, top in (("subject", 200), ("body", 5000)):
                v = rec.get(name)
                if not v:
                    blank(name)
                elif not isinstance(v, str) or len(v) > top:
                    bad[name] = {"code": "validation_max_text_constraint", "message": f"Must be less than {top}."}
            if "reply" in rec and not isinstance(rec["reply"], bool):
                bad["reply"] = {"code": "validation_invalid_value", "message": "Invalid value."}
            if not rec.get("status"):
                blank("status")
            elif rec["status"] not in TOIAWASE_STATUS:
                bad["status"] = {"code": "validation_invalid_value", "message": "Invalid value."}
        elif collection == "shoukai":
            owner_ok()
            if "owner" not in bad:
                for other in self._data["shoukai"].values():
                    if other["id"] != rec.get("id") and other.get("owner") == rec.get("owner"):
                        bad["owner"] = {"code": "validation_not_unique", "message": "Value must be unique."}
            if rec.get("sheet") in (None, "", [], {}):
                blank("sheet")
            for name, pat in (("made_at", MADE_AT), ("issuer", ISSUER)):
                v = rec.get(name)
                if not v:
                    blank(name)
                elif not isinstance(v, str) or len(v) > 16 or not pat.match(v):
                    bad[name] = {"code": "validation_invalid_format", "message": "Invalid value format."}
        return bad

    def _new_token(self, collection: str, rid: str) -> str:
        return self.token_for(collection, rid)

    # ---- 口ごとの働き ----

    def handle(self, req: Req, host_header: str) -> tuple[int, dict | None, dict[str, str]]:
        """一つの要求に答える。(数, 本文, 足すヘッダ)。"""
        with self._lock:
            self.requests.append(req)
        for fn in list(self.overrides):
            got = fn(req)
            if got is not None:
                return got[0], got[1], {}
        path = req.path
        if req.method == "GET" and path == "/api/health":
            return 200, {"message": "API is healthy.", "code": 200, "data": {}}, {}
        if req.method == "GET" and path == "/fake-google":
            return self._fake_google(req)
        m = re.match(r"^/api/collections/([^/]+)/(auth-methods|auth-with-password|auth-with-oauth2|auth-refresh)$", path)
        if m:
            coll, what = m.groups()
            if coll not in AUTH_COLLECTIONS:
                return (*err(404, NOT_FOUND), {})
            if what == "auth-methods" and req.method == "GET" and coll == "users":
                return 200, self._auth_methods(req, host_header), {}
            if what == "auth-with-password" and req.method == "POST":
                return (*self._auth_with_password(coll, req), {})
            if what == "auth-with-oauth2" and req.method == "POST" and coll == "users":
                return (*self._auth_with_oauth2(req), {})
            if what == "auth-refresh" and req.method == "POST":
                return (*self._auth_refresh(coll, req), {})
            return (*err(404, NOT_FOUND), {})
        m = re.match(r"^/api/collections/([^/]+)/records(?:/([^/]+))?$", path)
        if m:
            coll, rid = m.groups()
            if coll not in COLLECTION_IDS:
                return (*err(404, "Missing collection context."), {})
            with self._lock:
                if rid is None and req.method == "GET":
                    return (*self._list(coll, req), {})
                if rid is None and req.method == "POST":
                    return (*self._create(coll, req), {})
                if rid is not None and req.method == "GET":
                    return (*self._view(coll, rid, req), {})
                if rid is not None and req.method == "PATCH":
                    return (*self._update(coll, rid, req), {})
                if rid is not None and req.method == "DELETE":
                    return (*self._delete(coll, rid, req), {})
        return (*err(404, NOT_FOUND), {})

    def _auth_methods(self, req: Req, host_header: str) -> dict:
        state = secrets.token_urlsafe(22)
        verifier = secrets.token_urlsafe(43)
        cc = challenge_of(verifier)
        with self._lock:
            self._states[state] = verifier
        base = f"http://{host_header or f'{self.host}:{self.port}'}"
        q = urlencode({"client_id": "fake-client", "code_challenge": cc, "code_challenge_method": "S256",
                       "response_type": "code", "scope": "openid email profile", "state": state})
        provider = {"name": "google", "displayName": "Google", "state": state,
                    "authURL": f"{base}/fake-google?{q}&redirect_uri=",
                    "codeVerifier": verifier, "codeChallenge": cc, "codeChallengeMethod": "S256"}
        full = {"password": {"enabled": False, "identityFields": ["email"]},
                "oauth2": {"enabled": True, "providers": [provider]},
                "mfa": {"enabled": False, "duration": 0},
                "otp": {"enabled": False, "duration": 0},
                # fields を付けない時だけ返る古い鍵
                "authProviders": [provider], "usernamePassword": False, "emailPassword": False}
        fields = req.q("fields")
        if fields:
            keep = [f.strip() for f in fields.split(",") if f.strip()]
            return {k: v for k, v in full.items() if k in keep}
        return full

    def _fake_google(self, req: Req) -> tuple[int, dict | None, dict[str, str]]:
        state = req.q("state", "")
        redirect = req.q("redirect_uri", "")
        if not redirect or not redirect.startswith(("http://", "https://")):
            return (*err(400, "redirect_uri がありません"), {})
        with self._lock:
            known = state in self._states
            self._states.pop(state, None)
        sep = "&" if "?" in redirect else "?"
        if self.deny or req.q("deny") == "1":
            return 302, None, {"Location": redirect + sep + urlencode({"error": "access_denied", "state": state})}
        if not known:
            return (*err(400, "知らない state です"), {})
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = _Code(challenge=req.q("code_challenge", ""), redirect=redirect,
                                      person=req.q("as", "default") or "default")
        extra = {"code": code, "state": state, "scope": "email profile openid", "authuser": "0", "prompt": "none"}
        return 302, None, {"Location": redirect + sep + urlencode(extra)}

    def _auth_with_password(self, coll: str, req: Req) -> tuple[int, dict]:
        body = req.json if isinstance(req.json, dict) else {}
        ident, password = body.get("identity"), body.get("password")
        with self._lock:
            for rid, rec in self._data[coll].items():
                if rec.get("email") == ident and self._secret.get((coll, rid)) == password and password:
                    tok = self._new_token(coll, rid)
                    return 200, {"token": tok, "record": self._serialize(coll, rec, (coll, rid))}
        return err(400, "Failed to authenticate.")

    def _auth_with_oauth2(self, req: Req) -> tuple[int, dict]:
        body = req.json if isinstance(req.json, dict) else {}
        with self._lock:
            c = self._codes.get(body.get("code") or "")
            ok = (body.get("provider") == "google" and c is not None and not c.used
                  and isinstance(body.get("codeVerifier"), str)
                  and challenge_of(body["codeVerifier"]) == c.challenge
                  and body.get("redirectURL") == c.redirect)
            if not ok:
                return err(400, "Failed to authenticate.")
            c.used = True
            rid = self._people.get(c.person)
            is_new = rid is None or rid not in self._data["users"]
            if is_new:
                rec = self.add_user(email=f"fake-{c.person}@example.invalid", name=f"架空の人 {c.person}")
                rid = rec["id"]
                self._people[c.person] = rid
            rec = self._data["users"][rid]
            tok = self._new_token("users", rid)
            meta = {"id": f"fake-google-{c.person}", "name": rec["name"], "email": rec["email"],
                    "username": "", "avatarURL": "", "isNew": is_new, "accessToken": "", "refreshToken": ""}
            return 200, {"token": tok, "record": self._serialize("users", rec, ("users", rid)), "meta": meta}

    def _auth_refresh(self, coll: str, req: Req) -> tuple[int, dict]:
        if req.auth is None or req.auth[0] != coll:
            return err(401, "The request requires valid record authorization token.")
        rec = self._data[coll][req.auth[1]]
        return 200, {"token": self._new_token(coll, rec["id"]), "record": self._serialize(coll, rec, req.auth)}

    def _list(self, coll: str, req: Req) -> tuple[int, dict]:
        if self._allowed(coll, "list", req.auth) is None and coll in AUTH_COLLECTIONS and coll != "users":
            return err(403, SUPERUSERS_ONLY)
        try:
            page = int(req.q("page", "1") or "1")
            per = int(req.q("perPage", str(PER_PAGE_DEFAULT)) or PER_PAGE_DEFAULT)
        except ValueError:
            return err(400, "Invalid pagination parameters.")
        page = max(page, 1)
        per = PER_PAGE_DEFAULT if per <= 0 else min(per, PER_PAGE_MAX)
        items = [r for r in self._data[coll].values() if self._allowed(coll, "list", req.auth, r)]
        flt = req.q("filter")
        if flt:
            conds = []
            for part in flt.split("&&"):
                m = FILTER_PART.match(part)
                if not m:
                    return err(400, "Invalid filter parameters.")
                name, op, raw = m.groups()
                conds.append((name, op, raw.replace('\\"', '"').replace("\\\\", "\\")))
            items = [r for r in items if all((str(r.get(n, "")) == v) == (op == "=") for n, op, v in conds)]
        sort = req.q("sort")
        if sort:
            known = set().union(*(set(r) for r in self._data[coll].values())) | {"id", "created", "updated"}
            for key in reversed([s.strip() for s in sort.split(",") if s.strip()]):
                rev = key.startswith("-")
                name = key.lstrip("-+")
                if name not in known:
                    return err(400, "Invalid sort parameters.")
                items.sort(key=lambda r: str(r.get(name, "")), reverse=rev)
        total = len(items)
        chunk = items[(page - 1) * per: page * per]
        out = [self._serialize(coll, r, req.auth) for r in chunk]
        fields = req.q("fields")
        if fields and fields.strip() != "*":
            keep = [f.strip() for f in fields.split(",") if f.strip()]
            out = [{k: v for k, v in r.items() if k in keep} for r in out]
        skip = (req.q("skipTotal") or "").lower() in ("1", "true")
        return 200, {"page": page, "perPage": per,
                     "totalItems": -1 if skip else total,
                     "totalPages": -1 if skip else (total + per - 1) // per,
                     "items": out}

    def _view(self, coll: str, rid: str, req: Req) -> tuple[int, dict]:
        rec = self._data[coll].get(rid)
        if self._allowed(coll, "view", req.auth, rec) is None:
            return err(403, SUPERUSERS_ONLY)
        if rec is None or not self._allowed(coll, "view", req.auth, rec):
            return err(404, NOT_FOUND)
        return 200, self._serialize(coll, rec, req.auth)

    def _create(self, coll: str, req: Req) -> tuple[int, dict]:
        if coll in AUTH_COLLECTIONS:
            return err(403, SUPERUSERS_ONLY)
        body = req.json
        if not isinstance(body, dict):
            return err(400, "Failed to load the submitted data due to invalid formatting.")
        if not self._allowed(coll, "create", req.auth, None, body):
            return err(400, "Failed to create record.")
        rid = new_id()
        fields = _fields_of(coll)
        rec = {"id": rid, **{k: copy.deepcopy(v) for k, v in body.items() if k in fields}}
        bad = self._validate(coll, rec)
        if bad:
            return err(400, "Failed to create record.", bad)
        now = pb_time()
        rec["created"] = rec["updated"] = now
        self._data[coll][rid] = rec
        return 200, self._serialize(coll, rec, req.auth)

    def _update(self, coll: str, rid: str, req: Req) -> tuple[int, dict]:
        rec = self._data[coll].get(rid)
        if self._allowed(coll, "update", req.auth, rec) is None:
            return err(403, SUPERUSERS_ONLY)
        if rec is None or not self._allowed(coll, "update", req.auth, rec):
            return err(404, NOT_FOUND)
        body = req.json
        if not isinstance(body, dict):
            return err(400, "Failed to load the submitted data due to invalid formatting.")
        fields = _fields_of(coll)
        new = {**copy.deepcopy(rec), **{k: copy.deepcopy(v) for k, v in body.items() if k in fields}}
        bad = self._validate(coll, new)
        if bad:
            return err(400, "Failed to update record.", bad)
        new["updated"] = pb_time()
        self._data[coll][rid] = new
        return 200, self._serialize(coll, new, req.auth)

    def _delete(self, coll: str, rid: str, req: Req) -> tuple[int, dict | None]:
        rec = self._data[coll].get(rid)
        if self._allowed(coll, "delete", req.auth, rec) is None:
            return err(403, SUPERUSERS_ONLY)
        if rec is None or not self._allowed(coll, "delete", req.auth, rec):
            return err(404, NOT_FOUND)
        del self._data[coll][rid]
        return 204, None


def _fields_of(coll: str) -> set[str]:
    """作る・直す時に受ける項目(仕様書 3.3、3.4。id、created、updated はこちらで決める)。"""
    return {"toiawase": {"owner", "kind", "subject", "body", "reply", "status"},
            "shoukai": {"owner", "sheet", "made_at", "issuer"}}.get(coll, set())


class _Handler(BaseHTTPRequestHandler):
    pb: FakePB
    protocol_version = "HTTP/1.1"
    server_version = "FakePocketBase/0.40.4"

    def log_message(self, fmt, *args):
        if not self.pb.quiet:
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         self.headers.get("Access-Control-Request-Headers") or "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _do(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n > 0 else b""
        parts = urlsplit(self.path)
        headers = {k.lower(): v for k, v in self.headers.items()}
        req = Req(self.command, parts.path, parse_qs(parts.query, keep_blank_values=True), headers, body)
        req.auth = self.pb._auth_of(headers)
        try:
            status, payload, extra = self.pb.handle(req, self.headers.get("Host", ""))
        except Exception:   # 疑似の誤りは 500 にする
            status, payload, extra = 500, {"status": 500, "message": "fake error", "data": {}}, {}
        data = b"" if payload is None or status == 204 else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        for k, v in extra.items():
            self.send_header(k, v)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = _do


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="疑似の PocketBase(試しと、窓口の頁を手で確かめるための物)")
    ap.add_argument("--port", type=int, default=8090, help="待ち受ける口(0 は空いた口)")
    ap.add_argument("--host", default="127.0.0.1", help="待ち受ける所(既定 127.0.0.1)")
    ap.add_argument("--deny", action="store_true", help="疑似の提供者の頁が、いつも error を付けて戻す")
    ap.add_argument("--log", action="store_true", help="受けた要求の方法と道を標準エラーに出す")
    args = ap.parse_args(argv)
    pb = FakePB(host=args.host, port=args.port, deny=args.deny, quiet=not args.log)
    shoukai = pb.add_kikai("shoukai")
    asa = pb.add_kikai("asa")
    pb.start()
    print(f"疑似の PocketBase: {pb.url}")
    print("試しの kikai(役 shoukai。置きに行く道具の env のファイルに書く):")
    print(f"  MADOGUCHI_URL={pb.url}")
    print(f"  MADOGUCHI_IDENTITY={shoukai[0]}")
    print(f"  MADOGUCHI_PASSWORD={shoukai[1]}")
    print("試しの kikai(役 asa。毎朝の一覧の env のファイルに書く):")
    print(f"  MADOGUCHI_URL={pb.url}")
    print(f"  MADOGUCHI_IDENTITY={asa[0]}")
    print(f"  MADOGUCHI_PASSWORD={asa[1]}")
    print("止める時は Ctrl-C。")
    sys.stdout.flush()
    try:
        pb._thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        pb.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
