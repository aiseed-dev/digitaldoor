"""疑似の PocketBase(fake_pb.py)そのものの確かめ(番号なし)。

疑似が窓口仕様書 3.2〜3.4 の決まりと、3、4 章の応答の形のとおりに答える事を見る。疑似は試しと手の確かめの土台なので、
疑似の誤りで道具の試しが通ってしまわないようにする。相手は 127.0.0.1 だけ。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import pytest

from fake_pb import challenge_of

HERE = Path(__file__).resolve().parent


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def call(pb, method, path, body=None, token=None, headers=None):
    """疑似に一つ要求を出し、(数, 本文の JSON か None, ヘッダ) を返す。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(pb.url + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=10) as r:
            raw, status, h = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw, status, h = e.read(), e.code, dict(e.headers)
    return status, (json.loads(raw) if raw else None), h


def login(pb, ident, pw, coll="kikai"):
    s, b, _ = call(pb, "POST", f"/api/collections/{coll}/auth-with-password", {"identity": ident, "password": pw})
    assert s == 200, b
    return b["token"]


@pytest.fixture
def world(pb):
    """二人の本人(u1、u2)と、二つの kikai(shoukai、asa)の札。u1、u2 の問い合わせと写しを一つずつ置く。"""
    u1, u2 = pb.add_user(), pb.add_user()
    w = {"pb": pb, "u1": u1, "u2": u2,
         "t1": pb.token_for("users", u1["id"]), "t2": pb.token_for("users", u2["id"]),
         "ts": login(pb, *pb.add_kikai("shoukai")), "ta": login(pb, *pb.add_kikai("asa"))}
    for u in (u1, u2):
        w["q_" + u["id"]] = pb.add_record("toiawase", {"owner": u["id"], "kind": "質問", "subject": "件名",
                                                       "body": "本文", "reply": True, "status": "新しい"})
        w["s_" + u["id"]] = pb.add_record("shoukai", {"owner": u["id"], "issuer": "3f2a9c0d1e4b5a67",
                                                      "made_at": "2026-12-10T12:00",
                                                      "sheet": {"形式": "digitalkey-shoukai/1"}})
    return w


def test_health_and_cors(pb):
    """仕様書 14 章: /api/health は 200。応答に Access-Control-Allow-Origin: *。OPTIONS は 204。"""
    s, b, h = call(pb, "GET", "/api/health")
    assert s == 200 and b["code"] == 200
    assert h.get("Access-Control-Allow-Origin") == "*"
    s, b, h = call(pb, "OPTIONS", "/api/collections/toiawase/records",
                   headers={"Access-Control-Request-Headers": "authorization,content-type"})
    assert s == 204 and h.get("Access-Control-Allow-Origin") == "*"
    assert "PATCH" in h.get("Access-Control-Allow-Methods", "")


def test_toiawase_rules(world):
    """仕様書 3.3: 本人は自分の分だけ(一覧で絞られ、ほかの人の分は 404)。asa は全部読める。shoukai は一覧が空、見るは 404。
    客(札なし)は一覧が空。"""
    pb, u1, u2 = world["pb"], world["u1"], world["u2"]
    q2 = world["q_" + u2["id"]]
    path = "/api/collections/toiawase/records"
    s, b, _ = call(pb, "GET", path, token=world["t1"])
    assert s == 200 and [x["owner"] for x in b["items"]] == [u1["id"]]
    assert set(b) == {"page", "perPage", "totalItems", "totalPages", "items"}
    assert call(pb, "GET", f"{path}/{q2['id']}", token=world["t1"])[0] == 404
    s, b, _ = call(pb, "GET", path, token=world["ta"])
    assert len(b["items"]) == 2
    s, b, _ = call(pb, "GET", path, token=world["ts"])
    assert s == 200 and b["items"] == []
    assert call(pb, "GET", f"{path}/{q2['id']}", token=world["ts"])[0] == 404
    s, b, _ = call(pb, "GET", path)
    assert s == 200 and b["items"] == []


def test_toiawase_create_update_delete(world):
    """仕様書 3.3: 作れるのは本人で owner が自分、status が 新しい の時だけ(ほかは 400)。直すは null(403)。
    消せるのは本人の分だけ(ほかの人の分は 404)。"""
    pb, u1, u2 = world["pb"], world["u1"], world["u2"]
    path = "/api/collections/toiawase/records"
    ok = {"owner": u1["id"], "kind": "教室", "subject": "件名", "body": "本文", "reply": False, "status": "新しい"}
    s, b, _ = call(pb, "POST", path, ok, token=world["t1"])
    assert s == 200 and b["collectionName"] == "toiawase" and re.fullmatch(r"[a-z0-9]{15}", b["id"])
    assert b["created"].endswith("Z")
    assert call(pb, "POST", path, {**ok, "status": "返事済み"}, token=world["t1"])[0] == 400
    assert call(pb, "POST", path, {k: v for k, v in ok.items() if k != "status"}, token=world["t1"])[0] == 400
    assert call(pb, "POST", path, {**ok, "owner": u2["id"]}, token=world["t1"])[0] == 400
    assert call(pb, "POST", path, ok, token=world["ta"])[0] == 400
    assert call(pb, "POST", path, {**ok, "kind": "雑談"}, token=world["t1"])[0] == 400
    assert call(pb, "POST", path, {**ok, "subject": "長" * 201}, token=world["t1"])[0] == 400
    assert call(pb, "PATCH", f"{path}/{b['id']}", {"status": "閉じた"}, token=world["t1"])[0] == 403
    q2 = world["q_" + u2["id"]]
    assert call(pb, "DELETE", f"{path}/{q2['id']}", token=world["t1"])[0] == 404
    assert call(pb, "DELETE", f"{path}/{q2['id']}", token=world["ta"])[0] == 404
    assert call(pb, "DELETE", f"{path}/{b['id']}", token=world["t1"])[0] == 204


def test_shoukai_rules(world):
    """仕様書 3.4: 本人は自分の写しだけを見る。shoukai の kikai は一覧、見る、作る、直す、消すができる。asa は読めず書けない。
    owner は一人に一枚(一意の索引で 400)。"""
    pb, u1, u2 = world["pb"], world["u1"], world["u2"]
    path = "/api/collections/shoukai/records"
    s, b, _ = call(pb, "GET", path, token=world["t1"])
    assert [x["owner"] for x in b["items"]] == [u1["id"]]
    s, b, _ = call(pb, "GET", path, token=world["ts"])
    assert len(b["items"]) == 2
    s, b, _ = call(pb, "GET", path, token=world["ta"])
    assert b["items"] == []
    s2 = world["s_" + u2["id"]]
    assert call(pb, "GET", f"{path}/{s2['id']}", token=world["t1"])[0] == 404
    assert call(pb, "GET", f"{path}/{s2['id']}", token=world["ta"])[0] == 404
    new = {"owner": u1["id"], "issuer": "3f2a9c0d1e4b5a67", "made_at": "2026-12-11T09:00",
           "sheet": {"形式": "digitalkey-shoukai/1"}}
    assert call(pb, "POST", path, new, token=world["ts"])[0] == 400          # 一人に一枚
    u3 = pb.add_user()
    assert call(pb, "POST", path, {**new, "owner": u3["id"]}, token=world["ta"])[0] == 400
    assert call(pb, "POST", path, {**new, "owner": u3["id"]}, token=world["t1"])[0] == 400
    assert call(pb, "POST", path, {**new, "owner": "nouser"}, token=world["ts"])[0] == 400
    assert call(pb, "POST", path, {**new, "owner": u3["id"], "issuer": "XYZ"}, token=world["ts"])[0] == 400
    assert call(pb, "POST", path, {**new, "owner": u3["id"], "made_at": "2026-12-11"}, token=world["ts"])[0] == 400
    assert call(pb, "POST", path, {**new, "owner": u3["id"]}, token=world["ts"])[0] == 200
    assert call(pb, "PATCH", f"{path}/{s2['id']}", {"made_at": "2026-12-12T00:00"}, token=world["ta"])[0] == 404
    assert call(pb, "PATCH", f"{path}/{s2['id']}", {"made_at": "2026-12-12T00:00"}, token=world["ts"])[0] == 200
    assert call(pb, "DELETE", f"{path}/{s2['id']}", token=world["t2"])[0] == 404
    assert call(pb, "DELETE", f"{path}/{s2['id']}", token=world["ts"])[0] == 204


def test_kikai_collection_locked(world):
    """仕様書 3.2: kikai の一覧と見るは null(superuser だけ)なので 403。"""
    pb = world["pb"]
    assert call(pb, "GET", "/api/collections/kikai/records", token=world["ts"])[0] == 403


def test_list_params(world):
    """仕様書 4.5、8.4、9 章: perPage の上限 1000、skipTotal で totalItems と totalPages が -1、fields で絞る、
    filter の status="新しい"、sort の -created。"""
    pb = world["pb"]
    path = "/api/collections/toiawase/records"
    q = urlencode({"perPage": 5000, "skipTotal": 1, "fields": "id,created,kind,subject",
                   "filter": 'status="新しい"', "sort": "-created"})
    s, b, _ = call(pb, "GET", f"{path}?{q}", token=world["ta"])
    assert s == 200 and b["perPage"] == 1000 and b["totalItems"] == -1 and b["totalPages"] == -1
    assert all(set(x) == {"id", "created", "kind", "subject"} for x in b["items"])
    s, b, _ = call(pb, "GET", f"{path}?" + urlencode({"filter": "status ~ 'x'"}), token=world["ta"])
    assert s == 400


def test_password_auth(pb):
    """仕様書 8.4 の 1: kikai の auth-with-password は {token, record}。違うパスワードは 400。"""
    ident, pw = pb.add_kikai("asa")
    s, b, _ = call(pb, "POST", "/api/collections/kikai/auth-with-password", {"identity": ident, "password": pw})
    assert s == 200 and set(b) == {"token", "record"} and b["record"]["yaku"] == "asa"
    s, b, _ = call(pb, "POST", "/api/collections/kikai/auth-with-password", {"identity": ident, "password": "x"})
    assert s == 400 and b["status"] == 400


def test_bearer_prefix(world):
    """仕様書 4.4: Authorization は Bearer の前置きを付けても付けなくても受ける。"""
    s, b, _ = call(world["pb"], "GET", "/api/collections/toiawase/records", token="Bearer " + world["t1"])
    assert len(b["items"]) == 1


def _oauth(pb, redirect="http://127.0.0.1:9/contact/", deny=False, person=None):
    s, m, _ = call(pb, "GET", "/api/collections/users/auth-methods?fields=mfa,otp,password,oauth2")
    assert s == 200 and set(m) == {"mfa", "otp", "password", "oauth2"}
    prov = m["oauth2"]["providers"][0]
    assert prov["name"] == "google" and prov["authURL"].endswith("redirect_uri=")
    assert prov["codeChallenge"] == challenge_of(prov["codeVerifier"])
    url = prov["authURL"] + quote(redirect, safe="")
    if deny:
        url += "&deny=1"
    if person:
        url += "&as=" + person
    parts = urlsplit(url)
    s, _, h = call(pb, "GET", parts.path + "?" + parts.query)
    assert s == 302
    back = parse_qs(urlsplit(h["Location"]).query)
    assert h["Location"].startswith(redirect)
    return prov, back


def test_oauth2_flow(pb):
    """仕様書 4.1、4.2: auth-methods の google の authURL は疑似の頁を指し、その頁は redirect_uri へ code と state を付けて戻す。
    auth-with-oauth2 は code と codeVerifier の組を確かめて、架空の users のレコードと札を返す。code は一度だけ。"""
    redirect = "http://127.0.0.1:9/contact/"
    prov, back = _oauth(pb, redirect)
    assert back["state"] == [prov["state"]] and "code" in back
    body = {"provider": "google", "code": back["code"][0], "codeVerifier": prov["codeVerifier"],
            "redirectURL": redirect}
    s, b, _ = call(pb, "POST", "/api/collections/users/auth-with-oauth2", body)
    assert s == 200 and set(b) == {"token", "record", "meta"} and b["meta"]["isNew"] is True
    assert b["record"]["collectionName"] == "users" and b["record"]["email"].endswith("@example.invalid")
    assert call(pb, "POST", "/api/collections/users/auth-with-oauth2", body)[0] == 400        # 二度目
    s, r, _ = call(pb, "POST", "/api/collections/users/auth-refresh", token=b["token"])
    assert s == 200 and r["record"]["id"] == b["record"]["id"]
    prov2, back2 = _oauth(pb, redirect)
    bad = {"provider": "google", "code": back2["code"][0], "codeVerifier": prov["codeVerifier"],
           "redirectURL": redirect}
    assert call(pb, "POST", "/api/collections/users/auth-with-oauth2", bad)[0] == 400        # 組が違う
    good = {**bad, "codeVerifier": prov2["codeVerifier"]}
    s, b2, _ = call(pb, "POST", "/api/collections/users/auth-with-oauth2", good)
    assert s == 200 and b2["record"]["id"] == b["record"]["id"] and b2["meta"]["isNew"] is False


def test_oauth2_deny(pb):
    """疑似の頁に deny=1 を付けると、error を付けて戻す。"""
    _, back = _oauth(pb, deny=True)
    assert back["error"] == ["access_denied"] and "code" not in back


def test_standalone_prints_credentials_once():
    """`python tests/madoguchi/fake_pb.py --port 0` で単独で立ち、試しの kikai 二つの識別とパスワードを標準出力に出す。
    パスワードは起動のたびに変わる(コードに固定の値を書かない)。"""
    outs = []
    for _ in range(2):
        p = subprocess.Popen([sys.executable, str(HERE / "fake_pb.py"), "--port", "0"], stdout=subprocess.PIPE,
                             text=True, encoding="utf-8")
        try:
            lines = [p.stdout.readline() for _ in range(9)]
        finally:
            p.terminate()
            p.wait(timeout=10)
        outs.append("".join(lines))
    for out in outs:
        assert re.search(r"http://127\.0\.0\.1:\d+", out)
        assert out.count("MADOGUCHI_PASSWORD=") == 2
        assert re.search(r"MADOGUCHI_IDENTITY=shoukai-", out) and re.search(r"MADOGUCHI_IDENTITY=asa-", out)
    pw = [re.findall(r"MADOGUCHI_PASSWORD=(\S+)", o) for o in outs]
    assert pw[0] != pw[1]


@pytest.mark.parametrize("yaku", ["shoukai", "asa"])
def test_password_auth_record_has_yaku(pb, yaku):
    """仕様書 3.2、8.4: auth-with-password の応答の record は、そのコレクションの項目(kikai の yaku を含む)を持つ。"""
    ident, pw = pb.add_kikai(yaku)
    s, b, _ = call(pb, "POST", "/api/collections/kikai/auth-with-password", {"identity": ident, "password": pw})
    assert s == 200
    assert b["record"]["collectionName"] == "kikai" and b["record"]["yaku"] == yaku
    assert b["record"]["email"] == ident
