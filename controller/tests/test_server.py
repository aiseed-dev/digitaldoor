import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from http.server import ThreadingHTTPServer

import pytest

from digitaldoor_controller.audit import Audit, HmacSigner
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller
from digitaldoor_controller.server import make_handler, start_workers


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_http_gate():
    """R-CT-15 R-CT-19 R-CT-20: HTTP のインターフェースで命令を受け、リーダーの判定結果を受け、状態と記録を照会する。"""
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=999))
    ctl.set_time(1.0, True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, threading.Lock()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert call(base, "GET", "/state")["locked"] is True
        assert call(base, "POST", "/unlock", {"by": "Matter"})["result"] == "解錠"
        assert call(base, "GET", "/state")["locked"] is False
        assert call(base, "POST", "/lock", {"by": "Matter"})["result"] == "施錠"
        assert call(base, "POST", "/event", {"fire": True})["mode"] == "火災"
        assert call(base, "POST", "/lock", {"by": "Matter"})["result"] == "拒否"
        assert call(base, "POST", "/auth", {"subject": "A", "valid": False, "reason": "失効"})["result"] == "拒否"
        rows = call(base, "GET", "/audit?since=0")
        assert rows[-1]["event"] == "拒否" and call(base, "GET", "/verify")["ok"]
    finally:
        srv.shutdown()


def test_records_only_grow(tmp_path):
    """R-CT-14: 一通りの操作と HTTP のインターフェースの全部を通しても、記録は減らず、前の記録は変わらない。消すインターフェースは無い。"""
    path = tmp_path / "audit.jsonl"
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32), path), Config(autolock_s=999))
    ctl.set_time(1.0, True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctl, threading.Lock()))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    steps = [("POST", "/unlock", {"by": "Matter"}), ("POST", "/lock", {}), ("POST", "/auth", {"subject": "A"}),
             ("POST", "/event", {"door_open": True}), ("POST", "/event", {"door_open": False}),
             ("POST", "/event", {"manual": "機械キー"}), ("POST", "/event", {"tamper": "蓋"}),
             ("POST", "/event", {"fire": True}), ("POST", "/event", {"fire": False}),
             ("POST", "/event", {"mains": False}), ("POST", "/event", {"mains": True}),
             ("POST", "/time", {"now": 2.0}), ("GET", "/state", None), ("GET", "/audit?since=0", None),
             ("GET", "/verify", None)]
    try:
        seen = []
        for method, p, body in steps:
            call(base, method, p, body)
            rows = call(base, "GET", "/audit?since=0")
            assert rows[:len(seen)] == seen and len(rows) >= len(seen)
            seen = rows
        for method in ("DELETE", "PUT", "PATCH"):
            with pytest.raises(urllib.error.HTTPError):
                call(base, method, "/audit")
        assert call(base, "GET", "/audit?since=0") == seen
    finally:
        srv.shutdown()
    assert [asdict(e) for e in Audit(HmacSigner(b"k" * 32), path).entries] == seen


def test_stuck_box_does_not_stop_decisions():
    """R-CT-17: 箱への書き出しが応答しなくても、自動施錠と命令の受け付けは止まらない。"""
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=0.2))
    ctl.set_time(time.time(), True)
    lock, stuck, release = threading.Lock(), threading.Event(), threading.Event()

    def exporter():
        stuck.set()
        release.wait(10)          # ネットワークの共有フォルダが固まったつもり
        return 0

    start_workers(ctl, lock, exporter=exporter, interval=0.05)
    try:
        assert stuck.wait(2)
        assert lock.acquire(timeout=1), "書き出しが判断の排他を握っている"
        ctl.on_auth("A")
        lock.release()
        deadline = time.time() + 2
        while time.time() < deadline and not ctl.locked:
            time.sleep(0.05)
        assert ctl.locked
    finally:
        release.set()
