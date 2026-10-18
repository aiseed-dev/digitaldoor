"""発行者の署名つき資格情報の HTTP の口(POST /credential、POST /trust、GET /trust)の試験。

HTTP の口は POST のたびに時計を実時間へ進めるので、この試験の資格情報の期限は実時間のまわりに取る。
"""
import json
import threading
import time
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


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


@pytest.fixture
def world():
    """(ctl, base, admin)。信頼の一覧は空で、管理者の鍵だけ入れた扉。"""
    admin = Ed25519Signer.generate()
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=999), name=DOOR,
                     admin_key=admin.public_key())
    ctl.set_time(int(time.time()), True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, threading.Lock()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield ctl, f"http://127.0.0.1:{srv.server_port}", admin
    finally:
        srv.shutdown()


def cred(signer, cid, *, once=False, doors=(DOOR,)):
    t = int(time.time())
    return credentials.issue(signer, cred_id=cid, subject="resident-101", doors=list(doors),
                             not_before=t - 3600, not_after=t + 3600, once=once)


def add_req(admin, issuer, req_id, doors=(DOOR,)):
    return credentials.trust_change(admin, door=DOOR, action="add", issuer_public_key=issuer.public_key(),
                                    doors=list(doors), req_id=req_id)


def test_http_trust_add_then_credential_unlocks():
    """R-AC-12 R-AC-09: POST /trust で発行者を足し、GET /trust に公開鍵と範囲が出て、POST /credential の資格情報で解錠する。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=999), name=DOOR,
                     admin_key=admin.public_key())
    ctl.set_time(int(time.time()), True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, threading.Lock()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert call(base, "GET", "/trust") == {}
        assert call(base, "POST", "/trust", {"request": add_req(admin, a, "r-1")})["result"] == "受理"
        listed = call(base, "GET", "/trust")
        iid = key_id(a.public_key())
        assert listed[iid]["public_key"] == a.public_key().hex() and listed[iid]["doors"] == [DOOR]
        assert call(base, "GET", "/state")["locked"] is True
        r = call(base, "POST", "/credential", {"credential": cred(a, "c-1"), "route": "遠隔"})
        assert r["result"] == "解錠"
        assert call(base, "GET", "/state")["locked"] is False
        rows = call(base, "GET", "/audit?since=0")
        ok = [x for x in rows if x["kind"] == "認証" and f"issuer={iid}" in x["detail"] and "id=c-1" in x["detail"]]
        assert ok and ok[-1]["route"] == "遠隔"
        assert any(x["kind"] == "設定" and x["event"] == "信頼する発行者" for x in rows)
        assert call(base, "GET", "/verify")["ok"]
    finally:
        srv.shutdown()


def test_http_get_trust_has_no_secret(world):
    """R-AC-12: GET /trust は公開鍵と範囲だけを返し、秘密の鍵は出ない。"""
    ctl, base, admin = world
    a = Ed25519Signer.generate()
    call(base, "POST", "/trust", {"request": add_req(admin, a, "r-1")})
    text = json.dumps(call(base, "GET", "/trust"))
    for secret in (a.private_bytes().hex(), admin.private_bytes().hex()):
        assert secret not in text


def test_http_credential_from_untrusted_vendor_is_rejected_and_recorded(world):
    """R-AC-10: 信頼していない業者の鍵の資格情報で遠隔の解錠を求めると、結果は「拒否」で錠は動かず、記録に理由が残る。"""
    ctl, base, admin = world
    vendor = Ed25519Signer.generate()
    r = call(base, "POST", "/credential", {"credential": cred(vendor, "v-1"), "route": "遠隔"})
    assert r["result"] == "拒否"
    assert call(base, "GET", "/state")["locked"] is True
    rows = call(base, "GET", "/audit?since=0")
    assert [x for x in rows if x["kind"] == "認証"][-1]["result"] == "知らない発行者"


def test_http_bad_signature_and_range_and_once(world):
    """R-AC-09 R-AC-10: HTTP の口でも、署名の不一致、範囲の外、一回限りの二度目を、仕様の理由の言葉で拒む。"""
    ctl, base, admin = world
    a = Ed25519Signer.generate()
    call(base, "POST", "/trust", {"request": add_req(admin, a, "r-1")})

    def reason():
        return [x for x in call(base, "GET", "/audit?since=0") if x["kind"] == "認証"][-1]["result"]

    c = cred(a, "c-tamper")
    body = json.loads(c["body"])
    body["subject"] = "resident-102"
    tampered = {"body": json.dumps(body, ensure_ascii=False), "sig": c["sig"]}
    assert call(base, "POST", "/credential", {"credential": tampered, "route": "遠隔"})["result"] == "拒否"
    assert reason() == "署名が合わない"

    elsewhere = cred(a, "c-elsewhere", doors=["door-2"])
    assert call(base, "POST", "/credential", {"credential": elsewhere, "route": "遠隔"})["result"] == "拒否"
    assert reason() == "範囲の外"

    once = cred(a, "c-once", once=True)
    assert call(base, "POST", "/credential", {"credential": once, "route": "遠隔"})["result"] == "解錠"
    assert call(base, "POST", "/credential", {"credential": once, "route": "遠隔"})["result"] == "拒否"
    assert reason() == "使用済み"
    assert call(base, "GET", "/verify")["ok"]


def test_http_unsynced_clock_rejects_credential():
    """R-AC-02: 時計が未同期の扉は、HTTP の口からの資格情報も「時刻が未同期」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=999), name=DOOR,
                     trust={key_id(a.public_key()): {"public_key": a.public_key().hex(), "doors": [DOOR]}})
    ctl.set_time(int(time.time()), False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, threading.Lock()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert call(base, "POST", "/credential", {"credential": cred(a, "c-1"), "route": "遠隔"})["result"] == "拒否"
        rows = call(base, "GET", "/audit?since=0")
        assert [x for x in rows if x["kind"] == "認証"][-1]["result"] == "時刻が未同期"
    finally:
        srv.shutdown()


def test_http_bad_trust_requests_change_nothing(world):
    """R-AC-12: POST /trust で、別の鍵の署名、扉の名前の違う要求、同じ id の二度目は「拒否」で、一覧は変わらず、拒否が設定の記録に残る。"""
    ctl, base, admin = world
    other, a, b = (Ed25519Signer.generate() for _ in range(3))
    assert call(base, "POST", "/trust", {"request": add_req(other, a, "r-1")})["result"] == "拒否"
    wrong_door = credentials.trust_change(admin, door="door-2", action="add", issuer_public_key=a.public_key(),
                                          doors=[DOOR], req_id="r-2")
    assert call(base, "POST", "/trust", {"request": wrong_door})["result"] == "拒否"
    assert call(base, "GET", "/trust") == {}

    ok = add_req(admin, b, "r-3")
    assert call(base, "POST", "/trust", {"request": ok})["result"] == "受理"
    before = call(base, "GET", "/trust")
    assert call(base, "POST", "/trust", {"request": ok})["result"] == "拒否"
    assert call(base, "GET", "/trust") == before

    rows = call(base, "GET", "/audit?since=0")
    rejected = [x for x in rows if x["kind"] == "設定" and ("拒否" in x["event"] or "拒否" in x["result"])]
    assert len(rejected) == 3
    assert call(base, "GET", "/verify")["ok"]


def test_http_records_only_grow_through_credential_paths(world):
    """R-CT-14: 資格情報と信頼の口を通しても、記録は減らず、前の記録は変わらない。"""
    ctl, base, admin = world
    a = Ed25519Signer.generate()
    seen = []
    steps = [("POST", "/trust", {"request": add_req(admin, a, "r-1")}),
             ("POST", "/credential", {"credential": cred(a, "c-1", once=True), "route": "遠隔"}),
             ("POST", "/credential", {"credential": cred(a, "c-1", once=True), "route": "遠隔"}),
             ("POST", "/credential", {"credential": {"body": "{", "sig": "00"}, "route": "遠隔"}),
             ("GET", "/trust", None)]
    for method, p, body in steps:
        call(base, method, p, body)
        rows = call(base, "GET", "/audit?since=0")
        assert rows[:len(seen)] == seen and len(rows) >= len(seen)
        seen = rows
