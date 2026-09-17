import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from digitalkey.door.audit import Audit, HmacSigner
from digitalkey.door.contacts import SimContacts
from digitalkey.door.controller import Config, Controller
from digitalkey.door.server import make_handler


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_http_gate():
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
