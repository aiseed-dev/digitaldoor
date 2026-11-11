"""門 — Matter の橋(Node)や上の層から core を叩く小さな HTTP(標準ライブラリだけ)。

GET  /state            いまの状態
POST /lock  /unlock    上位からの施錠・解錠(JSON: {"by": "..."} )。同じ機械(ループバック)の相手だけ。ほかは 403 で拒んで記録に残す
POST /auth             読取機からの認証結果 {"subject","route","valid","duress"}
POST /event            {"fire": true} | {"mains": false} | {"door_open": true} | {"manual": "機械キー"} | {"tamper": "..."}
POST /time             {"now": 1700000000}
POST /credential       発行者の署名つき資格情報 {"credential": {"body","sig"}, "route": "遠隔", "revocation": {"body","sig"}}
                       → {"result", ...状態}。revocation(任意)は一緒に運んだ失効の一覧で、入れたときは応答に "revocation": 一覧の結果
POST /trust            管理者の署名つきの信頼の一覧の変更 {"request": {"body","sig"}} → {"result": "受理" | "拒否"}
GET  /trust            信頼の一覧(公開鍵と範囲。秘密は無い)
POST /revocation       発行者の署名つきの失効の一覧 {"list": {"body","sig"}} → {"result": "受理" | "同じ" | "拒否"}
GET  /revocation       発行者ごとの失効の状態 {issuer: {"generation", "issued_at", "revoked"}}(秘密は無い)
GET  /audit?since=N    監査の差分
GET  /verify           連鎖の検査
GET  /openapi.json     この口の全部の定義(OpenAPI 3.1)。応答のすべてに API-Version の見出しが付く
GET  /events?since=N   出来事の配信(WebSocket)。監査の記録を連番の順に一件ずつ送る
GET  /fault  POST /fault  故障注入(開発のときだけ。同じ機械の相手だけ)
                       {"lock": "応答しない"|"途中で切断"|"正常", "clock_offset_s": 秒, "events": "切断"}

/credential、/trust、/revocation は署名で守る口で、相手の住所は問わない(扉コントローラ仕様書 5.2a、5.2c)。
開発の口(定義、配信、故障注入)は扉コントローラ仕様書 5.5 による。
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import events
from .controller import Controller
from .openapi import API_VERSION, document

FAULT_LOCK = ("正常", "応答しない", "途中で切断")
FAULT_EVENTS = ("切断",)


class Clock:
    """扉の時計。開発のときは故障注入で実際の時刻からずらせる(仕様書 5.5)。"""

    def __init__(self, offset_s: float = 0.0) -> None:
        self.offset_s = offset_s

    def now(self) -> float:
        return time.time() + self.offset_s


def is_loopback(addr: str) -> bool:
    """同じ機械の相手か。IPv4 を写した IPv6(::ffff:127.0.0.1)も同じ機械として見る。読めない住所は外とする。"""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def make_handler(ctl: Controller, lock: threading.Lock, *, dev: bool = False, clock: Clock | None = None):
    """dev=True で故障注入(/fault)を開く。接点が模擬(故障の印を持つ)ときだけ開くこと。"""
    if dev and not hasattr(ctl.contacts, "fault_lock"):
        raise ValueError("故障注入は、故障の印を持つ模擬の接点の扉だけで開けます")
    clock = clock or Clock()
    hub = events.Hub()
    spec = document()

    class H(BaseHTTPRequestHandler):
        def end_headers(self) -> None:
            self.send_header("API-Version", API_VERSION)
            super().end_headers()

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

        def _faults(self) -> dict:
            return {"lock": ctl.contacts.fault_lock, "clock_offset_s": clock.offset_s,
                    "failures": ctl.contacts.failures, "actual_bolt_out": ctl.contacts.actual_bolt_out}

        def _set_fault(self, b: dict) -> None:
            """無い項目か許されない値が一つでもあれば 400 で、何も変えない。"""
            bad = set(b) - {"lock", "clock_offset_s", "events"}
            off = b.get("clock_offset_s", clock.offset_s)
            if (bad or not b or ("lock" in b and b["lock"] not in FAULT_LOCK)
                    or isinstance(off, bool) or not isinstance(off, (int, float)) or off != off
                    or abs(off) == float("inf") or ("events" in b and b["events"] not in FAULT_EVENTS)):
                return self._json(400, {"error": "無い項目か許されない値です", "accepts": {
                    "lock": list(FAULT_LOCK), "clock_offset_s": "秒(数)", "events": list(FAULT_EVENTS)}})
            if "lock" in b:
                ctl.contacts.fault_lock = b["lock"]
            clock.offset_s = float(off)
            ctl.note_fault(json.dumps(b, ensure_ascii=False, sort_keys=True))
            out = self._faults()
            if "events" in b:
                out["cut"] = hub.cut_all()
            return self._json(200, out)

        def _events(self, query: str) -> None:
            if self.headers.get("Upgrade", "").lower() != "websocket" or not self.headers.get("Sec-WebSocket-Key"):
                return self._json(426, {"error": "WebSocket で接続してください。差分だけなら /audit?since=N"})
            try:
                since = int(parse_qs(query).get("since", ["0"])[0])
            except ValueError:
                return self._json(400, {"error": "since は整数です"})
            if not hub.enter(self.connection):
                return self._json(503, {"error": "配信を受ける相手が上限に達しています"})
            try:
                # WebSocket の切り替えは HTTP/1.1 の応答で返す(この口のほかの応答は HTTP/1.0 のまま)
                self.wfile.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                                  f"Sec-WebSocket-Accept: {events.accept_key(self.headers['Sec-WebSocket-Key'])}\r\n"
                                  f"API-Version: {API_VERSION}\r\n\r\n").encode("ascii"))
                events.stream(self.connection, ctl.audit, lock, since)
            except (OSError, ConnectionError, ValueError):
                pass  # 受け手が切れた。判断には響かせない
            finally:
                hub.leave(self.connection)
                self.close_connection = True

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/openapi.json":
                return self._json(200, spec)
            if u.path == "/events":
                return self._events(u.query)
            with lock:
                if u.path == "/fault" and dev:
                    return self._json(200, self._faults())
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
                if u.path == "/revocation":
                    return self._json(200, ctl.revocations)
            self._json(404, {"error": "no such path"})

        def do_POST(self):
            u = urlparse(self.path)
            b = self._body()
            with lock:
                ctl.tick(clock.now()) if ctl.synced else None
                if u.path == "/fault" and dev:
                    if not is_loopback(self.client_address[0]):
                        ctl.note_fault(f"同じ機械以外からの要求 {self.client_address[0]}", result="拒否", route="網")
                        return self._json(403, {"error": "forbidden", "result": "拒否"})
                    return self._set_fault(b)
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
                    # 一緒に運んだ一覧は present と同じく先に受ける。結果を応答に入れるため、ここで受けてから渡す
                    carried = b.get("revocation")
                    rev = ctl.accept_revocation(carried) if carried is not None else None
                    r = ctl.present(b.get("credential"), b.get("route") or "遠隔")
                    out = {"result": r, **ctl.state()}
                    if rev is not None:
                        out["revocation"] = rev
                    return self._json(200, out)
                if u.path == "/revocation":
                    return self._json(200, {"result": ctl.accept_revocation(b.get("list"))})
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


def start_workers(ctl: Controller, lock: threading.Lock, exporter=None, interval: float = 0.5,
                  clock: Clock | None = None) -> None:
    """時計と伝票の書き出しを、別々のスレッドで回す。

    時計は判断の排他(lock)を取って自動施錠を進める。書き出し(exporter)は排他を持たずに回すので、
    箱が網の共有フォルダで固まっても、判断、自動施錠、命令の受け付けは止まらない。
    """

    def ticker():
        while True:
            time.sleep(interval)
            with lock:
                if ctl.synced:
                    ctl.tick(clock.now())

    def shipper():
        while True:
            time.sleep(interval)
            try:
                exporter()
            except Exception:  # 箱の不調は次の回に持ち越す。判断には響かせない
                pass

    clock = clock or Clock()
    threading.Thread(target=ticker, daemon=True).start()
    if exporter:
        threading.Thread(target=shipper, daemon=True).start()


def serve(ctl: Controller, host: str = "127.0.0.1", port: int = 8797, exporter=None, dev: bool = False) -> None:
    """HTTP の口を開く。exporter を渡すと、伝票の書き出しを判断と別に回し続ける。

    dev=True で故障注入を開く。接点が模擬(SimContacts)のときだけ渡す。本物の錠の扉では開かない。
    """
    lock, clock = threading.Lock(), Clock()
    srv = ThreadingHTTPServer((host, port), make_handler(ctl, lock, dev=dev, clock=clock))
    srv.daemon_threads = True
    start_workers(ctl, lock, exporter, clock=clock)
    srv.serve_forever()
