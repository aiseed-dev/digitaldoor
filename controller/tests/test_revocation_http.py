"""失効の一覧の HTTP の口(扉コントローラ仕様書 5.2c、試験の対 44)の試験。

POST /revocation、GET /revocation、POST /credential の任意の欄 revocation、OpenAPI の定義(版 1.3.0)。
HTTP の口は POST のたびに扉の時計を `clock.now()` へ進めるので、止まった時計(FixedClock)を渡して時刻を固定する。
一覧は仕様の形をここで組み立てて署名する(実装の口に頼らない)。
"""
import contextlib
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from digitaldoor_controller import credentials
from digitaldoor_controller.audit import Audit, Ed25519Signer, HmacSigner
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller
from digitaldoor_controller.server import make_handler
from digitaldoor_controller.slips import key_id

DOOR = "door-1"
T0 = 1_794_700_800
DAY = 86_400
EVENT = "失効の一覧"


class FixedClock:
    """止まった扉の時計(server.Clock と同じ口)。"""

    offset_s = 0.0

    def __init__(self, now):
        self.t = now

    def now(self):
        return self.t


def make_list(signer, *, generation, issued_at=T0, revoked=()):
    body = json.dumps({"format": "digitaldoor-revocation/1", "issuer": key_id(signer.public_key()),
                       "generation": generation, "issued_at": issued_at, "revoked": list(revoked)},
                      ensure_ascii=False)
    return {"body": body, "sig": signer.sign(body.encode("utf-8"))}


def cred(signer, cid):
    return credentials.issue(signer, cred_id=cid, subject=f"resident-{cid}", doors=[DOOR],
                             not_before=T0 - DAY, not_after=T0 + 30 * DAY)


def request(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, json.loads(r.read())
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


def call(base, method, path, body=None):
    status, _, out = request(base, method, path, body)
    assert status == 200, (method, path, status, out)
    return out


@contextlib.contextmanager
def door(trust_signers=(), *, host="127.0.0.1", max_age=None):
    trust = {key_id(s.public_key()): {"public_key": s.public_key().hex(), "doors": [DOOR]} for s in trust_signers}
    config = Config(autolock_s=0) if max_age is None else Config(autolock_s=0, revocation_max_age_s=max_age)
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), config, name=DOOR, trust=trust)
    ctl.set_time(T0, True)
    srv = ThreadingHTTPServer((host, 0), make_handler(ctl, threading.Lock(), clock=FixedClock(T0)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield ctl, srv, f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def list_rows(base):
    return [x for x in call(base, "GET", "/audit?since=0") if x["kind"] == "設定" and x["event"] == EVENT]


def last_auth_reason(base):
    return [x for x in call(base, "GET", "/audit?since=0") if x["kind"] == "認証"][-1]["result"]


def test_http_post_and_get_revocation_like_pairs_33_to_35():
    """R-AC-22: 試験の対 44。POST /revocation は 受理 / 同じ(記録は増えない)/ 古い世代の拒否 を返し、GET /revocation が状態を返し、失効した資格情報は拒む。"""
    a = Ed25519Signer.generate()
    ia = key_id(a.public_key())
    with door([a]) as (ctl, _, base):
        assert call(base, "GET", "/revocation") == {}
        g7 = make_list(a, generation=7, revoked=["c-0012", "c-0031"])
        assert call(base, "POST", "/revocation", {"list": g7}) == {"result": "受理"}
        rows = list_rows(base)
        assert len(rows) == 1 and rows[0]["result"] == "受理" and rows[0]["subject"] == ia
        assert rows[0]["detail"] == f'issuer={ia} generation=7 issued_at={T0} revoked=["c-0012", "c-0031"]'
        n = len(call(base, "GET", "/audit?since=0"))
        assert call(base, "POST", "/revocation", {"list": g7}) == {"result": "同じ"}
        assert len(call(base, "GET", "/audit?since=0")) == n
        assert call(base, "POST", "/revocation", {"list": make_list(a, generation=6, revoked=["c-0012"])}) \
            == {"result": "拒否"}
        assert list_rows(base)[-1]["result"] == "拒否" and list_rows(base)[-1]["detail"] == "古い世代"
        assert call(base, "GET", "/revocation") == {
            ia: {"generation": 7, "issued_at": T0, "revoked": ["c-0012", "c-0031"]}}
        r = call(base, "POST", "/credential", {"credential": cred(a, "c-0012")})
        assert r["result"] == "拒否" and "revocation" not in r
        assert last_auth_reason(base) == "失効済み"
        assert call(base, "GET", "/verify")["ok"]


def test_http_credential_carries_revocation_and_response_has_its_result():
    """R-AC-22: 試験の対 44。POST /credential の revocation に一覧を入れると present に渡り、応答に "revocation": 一覧の結果 が入る。"""
    a = Ed25519Signer.generate()
    ia = key_id(a.public_key())
    with door([a]) as (ctl, _, base):
        call(base, "POST", "/revocation", {"list": make_list(a, generation=7, revoked=["c-0012", "c-0031"])})
        g8 = make_list(a, generation=8, issued_at=T0 + DAY, revoked=["c-0012", "c-0031"])
        r = call(base, "POST", "/credential", {"credential": cred(a, "c-0040"), "revocation": g8})
        assert r["result"] == "解錠" and r["revocation"] == "受理"
        assert call(base, "GET", "/revocation") == {
            ia: {"generation": 8, "issued_at": T0 + DAY, "revoked": ["c-0012", "c-0031"]}}
        r = call(base, "POST", "/credential", {"credential": cred(a, "c-0031"), "route": "系統1", "revocation": g8})
        assert r["result"] == "拒否" and r["revocation"] == "同じ"
        assert last_auth_reason(base) == "失効済み"


def test_http_credential_with_rejected_revocation_still_checks_credential():
    """R-AC-22: 試験の対 44。一緒に運んだ一覧が拒否でも資格情報は確かめられ、応答の revocation は「拒否」。"""
    a, x = Ed25519Signer.generate(), Ed25519Signer.generate()
    with door([a]) as (ctl, _, base):
        r = call(base, "POST", "/credential", {"credential": cred(a, "c-0040"),
                                               "revocation": make_list(x, generation=1)})
        assert r["result"] == "解錠" and r["revocation"] == "拒否"
        assert list_rows(base)[-1]["detail"] == "知らない発行者"
        assert call(base, "GET", "/revocation") == {}


def test_http_credential_without_revocation_has_no_revocation_field():
    """R-AC-22: 試験の対 44。revocation を入れない POST /credential の応答には revocation の欄が無い。"""
    a = Ed25519Signer.generate()
    with door([a]) as (ctl, _, base):
        r = call(base, "POST", "/credential", {"credential": cred(a, "c-0040")})
        assert r["result"] == "解錠" and "revocation" not in r


@pytest.mark.parametrize("body", [{}, {"lst": {"body": "{}", "sig": "00"}}, {"list": None}])
def test_http_revocation_without_list_is_recorded_as_bad_form(body):
    """R-AC-22: 試験の対 44。list の無い(か一覧でない)POST /revocation は「拒否」で、形式が違う の拒否が記録に残る。"""
    with door([Ed25519Signer.generate()]) as (ctl, _, base):
        assert call(base, "POST", "/revocation", body) == {"result": "拒否"}
        rows = list_rows(base)
        assert len(rows) == 1 and rows[0]["result"] == "拒否" and rows[0]["detail"] == "形式が違う"
        assert rows[0]["subject"] == ""


def test_http_get_revocation_has_no_secret():
    """R-AC-22: 試験の対 44。GET /revocation は発行者ごとの世代、時刻、失効の id だけで、秘密は出ない。"""
    a = Ed25519Signer.generate()
    with door([a]) as (ctl, _, base):
        call(base, "POST", "/revocation", {"list": make_list(a, generation=2, revoked=["c-1"])})
        got = call(base, "GET", "/revocation")
        assert all(set(v) == {"generation", "issued_at", "revoked"} for v in got.values())
        assert a.private_bytes().hex() not in json.dumps(got)


def test_http_stale_rule_through_http():
    """R-AC-14: 試験の対 44。revocation_max_age_s を入れた扉は、HTTP の口でも一覧の無い発行者の長い資格情報を「失効の知らせが古い」で拒む。"""
    a = Ed25519Signer.generate()
    with door([a], max_age=3600) as (ctl, _, base):
        assert call(base, "POST", "/credential", {"credential": cred(a, "c-0040")})["result"] == "拒否"
        assert last_auth_reason(base) == "失効の知らせが古い"
        r = call(base, "POST", "/credential", {"credential": cred(a, "c-0040"),
                                               "revocation": make_list(a, generation=1, issued_at=T0)})
        assert r["result"] == "解錠" and r["revocation"] == "受理"


def test_openapi_has_revocation_paths_and_version_1_3_0():
    """R-DV-11 R-AC-22: 試験の対 44。定義に /revocation の GET と POST、/credential の欄 revocation があり、版は 1.3.0 で見出しも同じ。"""
    with door() as (ctl, _, base):
        status, headers, doc = request(base, "GET", "/openapi.json")
        assert status == 200
        assert doc["info"]["version"] == "1.3.0" and headers["API-Version"] == "1.3.0"
        paths = doc["paths"]
        assert {"get", "post"} <= set(paths["/revocation"])
        props = paths["/credential"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "revocation" in props and "credential" in props
        for method in ("GET", "POST"):
            st, h, _ = request(base, method, "/revocation", {} if method == "POST" else None)
            assert st == 200 and h["API-Version"] == "1.3.0"


def _lan_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))     # 送りはしない。経路から自分の番地を引くだけ
        addr = s.getsockname()[0]
        s.close()
    except OSError:
        return None
    return None if addr.startswith("127.") or addr == "0.0.0.0" else addr


def test_http_revocation_is_accepted_from_other_machines():
    """R-AC-22: 試験の対 44。/revocation は署名で守る口で、相手の住所は問わない(同じ機械でない相手からも受理する)。"""
    lan = _lan_address()
    if lan is None:
        pytest.skip("ループバックでない番地が無い")
    a = Ed25519Signer.generate()
    with door([a], host="0.0.0.0") as (ctl, srv, base):
        remote = f"http://{lan}:{srv.server_port}"
        try:
            status, _, out = request(remote, "POST", "/revocation", {"list": make_list(a, generation=1)})
        except OSError:
            pytest.skip("自分の LAN の番地へつなげない")
        assert status == 200 and out == {"result": "受理"}
        status, _, out = request(remote, "GET", "/revocation")
        assert status == 200 and key_id(a.public_key()) in out
