"""門 — Matter の橋(Node)や上の層から core を叩く小さな HTTP(標準ライブラリだけ)。

GET  /state            いまの状態
POST /lock  /unlock    上位からの施錠・解錠(JSON: {"by": "..."} )。同じ機械(ループバック)の相手だけ。ほかは 403 で拒んで記録に残す
POST /auth             読取機からの認証結果 {"subject","route","valid","duress"}
POST /event            {"fire": true} | {"mains": false} | {"door_open": true} | {"manual": "機械キー"} | {"tamper": "..."}
POST /time             {"now": 1700000000}
POST /credential       発行者の署名つき資格情報 {"credential": {"body","sig"}, "route": "遠隔"} → {"result", ...状態}
POST /trust            管理者の署名つきの信頼の一覧の変更 {"request": {"body","sig"}} → {"result": "受理" | "拒否"}
GET  /trust            信頼の一覧(公開鍵と範囲。秘密は無い)
GET  /audit?since=N    監査の差分
GET  /verify           連鎖の検査
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .controller import Controller


def is_loopback(addr: str) -> bool:
    """同じ機械の相手か。IPv4 を写した IPv6(::ffff:127.0.0.1)も同じ機械として見る。読めない住所は外とする。"""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


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
            b = json.loads(self.rfile.read(n) or b"{}") if n else {}
            return b if isinstance(b, dict) else {}

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
                if u.path == "/trust":
                    return self._json(200, ctl.trust)
            self._json(404, {"error": "no such path"})

        def do_POST(self):
            u = urlparse(self.path)
            b = self._body()
            with lock:
                ctl.tick(time.time()) if ctl.synced else None
                if u.path in ("/lock", "/unlock") and not is_loopback(self.client_address[0]):
                    # Matter の橋のための口。外の網には開かない(網を通した解錠は /credential の署名つき資格情報だけ)
                    ctl.refuse_remote(u.path[1:], self.client_address[0])
                    return self._json(403, {"error": "forbidden", "result": "拒否"})
                if u.path == "/lock":
                    return self._json(200, {"result": ctl.lock_request(b.get("by", "上位"))})
                if u.path == "/unlock":
                    return self._json(200, {"result": ctl.unlock_request(b.get("by", "上位"))})
                if u.path == "/auth":
                    r = ctl.on_auth(b.get("subject", "?"), b.get("route", "系統1"), valid=bool(b.get("valid", True)),
                                    reason=b.get("reason", ""), duress=bool(b.get("duress", False)))
                    return self._json(200, {"result": r, **ctl.state()})
                if u.path == "/credential":
                    r = ctl.present(b.get("credential"), b.get("route") or "遠隔")
                    return self._json(200, {"result": r, **ctl.state()})
                if u.path == "/trust":
                    return self._json(200, {"result": ctl.change_trust(b.get("request"))})
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


def start_workers(ctl: Controller, lock: threading.Lock, exporter=None, interval: float = 0.5) -> None:
    """時計と伝票の書き出しを、別々のスレッドで回す。

    時計は判断の排他(lock)を取って自動施錠を進める。書き出し(exporter)は排他を持たずに回すので、
    箱が網の共有フォルダで固まっても、判断、自動施錠、命令の受け付けは止まらない。
    """

    def ticker():
        while True:
            time.sleep(interval)
            with lock:
                if ctl.synced:
                    ctl.tick(time.time())

    def shipper():
        while True:
            time.sleep(interval)
            try:
                exporter()
            except Exception:  # 箱の不調は次の回に持ち越す。判断には響かせない
                pass

    threading.Thread(target=ticker, daemon=True).start()
    if exporter:
        threading.Thread(target=shipper, daemon=True).start()


def serve(ctl: Controller, host: str = "127.0.0.1", port: int = 8797, exporter=None) -> None:
    """HTTP の口を開く。exporter を渡すと、伝票の書き出しを判断と別に回し続ける。"""
    lock = threading.Lock()
    srv = ThreadingHTTPServer((host, port), make_handler(ctl, lock))
    start_workers(ctl, lock, exporter)
    srv.serve_forever()
