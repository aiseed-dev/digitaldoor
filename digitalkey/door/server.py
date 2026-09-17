"""門 — Matter の橋(Node)や上の層から core を叩く小さな HTTP(標準ライブラリだけ)。

GET  /state            いまの状態
POST /lock  /unlock    上位からの施錠・解錠(JSON: {"by": "..."} )
POST /auth             読取機からの認証結果 {"subject","route","valid","duress"}
POST /event            {"fire": true} | {"mains": false} | {"door_open": true} | {"manual": "機械キー"} | {"tamper": "..."}
POST /time             {"now": 1700000000}
GET  /audit?since=N    監査の差分
GET  /verify           連鎖の検査
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .controller import Controller


def make_handler(ctl: Controller, lock: threading.Lock):
    class H(BaseHTTPRequestHandler):
        def _json(self, code: int, obj) -> None:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def log_message(self, *a):  # 静かに
            pass

        def do_GET(self):
            u = urlparse(self.path)
            with lock:
                if u.path == "/state":
                    return self._json(200, ctl.state())
                if u.path == "/audit":
                    since = int(parse_qs(u.query).get("since", ["0"])[0])
                    return self._json(200, [asdict(e) for e in ctl.audit.since(since)])
                if u.path == "/verify":
                    ok, n, why = ctl.audit.verify()
                    return self._json(200, {"ok": ok, "count": n, "why": why})
            self._json(404, {"error": "no such path"})

        def do_POST(self):
            u = urlparse(self.path)
            b = self._body()
            with lock:
                ctl.tick(time.time()) if ctl.synced else None
                if u.path == "/lock":
                    return self._json(200, {"result": ctl.lock_request(b.get("by", "上位"))})
                if u.path == "/unlock":
                    return self._json(200, {"result": ctl.unlock_request(b.get("by", "上位"))})
                if u.path == "/auth":
                    r = ctl.on_auth(b.get("subject", "?"), b.get("route", "系統1"), valid=bool(b.get("valid", True)),
                                    reason=b.get("reason", ""), duress=bool(b.get("duress", False)))
                    return self._json(200, {"result": r, **ctl.state()})
                if u.path == "/event":
                    if "fire" in b:
                        ctl.contacts._fire = bool(b["fire"]) if hasattr(ctl.contacts, "_fire") else None
                        ctl.on_fire(bool(b["fire"]))
                    if "mains" in b:
                        if hasattr(ctl.contacts, "_mains"):
                            ctl.contacts._mains = bool(b["mains"])
                        ctl.on_mains(bool(b["mains"]))
                    if "door_open" in b:
                        if hasattr(ctl.contacts, "_door_open"):
                            ctl.contacts._door_open = bool(b["door_open"])
                        ctl.on_door(bool(b["door_open"]))
                    if "manual" in b:
                        ctl.on_manual_release(str(b["manual"]))
                    if "tamper" in b:
                        ctl.on_tamper(str(b["tamper"]))
                    return self._json(200, ctl.state())
                if u.path == "/time":
                    ctl.set_time(float(b.get("now", time.time())), True)
                    return self._json(200, ctl.state())
            self._json(404, {"error": "no such path"})

    return H


def serve(ctl: Controller, host: str = "127.0.0.1", port: int = 8797) -> None:
    lock = threading.Lock()
    srv = ThreadingHTTPServer((host, port), make_handler(ctl, lock))

    def ticker():
        while True:
            time.sleep(0.5)
            with lock:
                if ctl.synced:
                    ctl.tick(time.time())

    threading.Thread(target=ticker, daemon=True).start()
    srv.serve_forever()
