"""開発の口(仕様書 5.5)の試験。仕様書 第 8 章の表の 26〜30。

期待値は仕様書から決めている。WebSocket は依存を増やさないよう、RFC 6455 の小さな相手をここに書く。
"""
import base64
import contextlib
import hashlib
import json
import os
import socket
import struct
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
from digitaldoor_controller.server import Clock, make_handler, start_workers

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
FAULT_KEYS = {"lock", "clock_offset_s", "failures"}


# ---- 道具 ----

def new_ctl(autolock_s=999.0, now=None):
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=autolock_s))
    ctl.set_time(time.time() if now is None else now, True)
    return ctl


@contextlib.contextmanager
def serve(ctl, lock=None, *, dev=True, clock=None, host="127.0.0.1"):
    lock = lock or threading.Lock()
    srv = ThreadingHTTPServer((host, 0), make_handler(ctl, lock, dev=dev, clock=clock))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv, f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def request(base, method, path, body=None, timeout=5):
    """(状態の番号, 見出し, 中身) を返す。4xx/5xx でも例外にしない。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


def call(base, method, path, body=None):
    status, _, raw = request(base, method, path, body)
    assert status == 200, (method, path, status, raw)
    return json.loads(raw)


def audit_rows(base):
    return call(base, "GET", "/audit?since=0")


def fault_records(rows):
    return [r for r in rows if r["kind"] == "試験" and r["event"] == "故障注入"]


def record_text(row):
    return " ".join(str(v) for v in row.values())


def wait_until(pred, timeout=3.0, step=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step)
    return pred()


# ---- RFC 6455 の小さな相手 ----

class WSRejected(Exception):
    def __init__(self, status, head):
        super().__init__(f"HTTP {status}")
        self.status, self.head = status, head


class WS:
    """読むのは サーバーからの文(マスク無し)、送るのは マスク付きの ping と close だけ。"""

    def __init__(self, port, since=0, *, rcvbuf=None, timeout=5.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if rcvbuf:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.sock.settimeout(timeout)
        self.sock.connect(("127.0.0.1", port))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /events?since={since} HTTP/1.1\r\n"
                           f"Host: 127.0.0.1:{port}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buf += chunk
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split()[1]) if lines and len(lines[0].split()) > 1 else 0
        self.headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            self.headers[k.strip().lower()] = v.strip()
        if status != 101:
            self.sock.close()
            raise WSRejected(status, self.headers)
        expect = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        assert self.headers.get("sec-websocket-accept") == expect
        assert self.headers.get("upgrade", "").lower() == "websocket"

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("切れた")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def frame(self):
        """(opcode, 中身) を一つ読む。続きの断片はつなげる。"""
        op, payload = None, b""
        while True:
            b0, b1 = self._read(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            assert not b1 & 0x80, "サーバーからの文はマスクしない(RFC 6455 5.1)"
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            data = self._read(n)
            if opcode >= 0x8:          # 制御の文は断片の間にも来る
                return opcode, data
            if opcode != 0:
                op = opcode
            payload += data
            if fin:
                return op, payload

    def send(self, opcode, payload=b""):
        mask = os.urandom(4)
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def records_until(self, last_seq, timeout=5.0):
        """連番が last_seq に届くまで、テキストの文を記録として集める。"""
        got = []
        deadline = time.time() + timeout
        while not got or got[-1]["seq"] < last_seq:
            self.sock.settimeout(max(0.01, deadline - time.time()))
            op, data = self.frame()
            if op == 0x1:
                got.append(json.loads(data.decode("utf-8")))
        return got

    def quiet(self, wait=0.3):
        """wait 秒の間に届いた記録を返す(届かなければ空)。"""
        got = []
        self.sock.settimeout(wait)
        try:
            while True:
                op, data = self.frame()
                if op == 0x1:
                    got.append(json.loads(data.decode("utf-8")))
        except (socket.timeout, TimeoutError):
            return got

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()


def lan_address():
    """この機械の、ループバックでない IPv4 の番地。無ければ None。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))     # 送りはしない。経路から自分の番地を引くだけ
        addr = s.getsockname()[0]
        s.close()
    except OSError:
        return None
    return None if addr.startswith("127.") or addr == "0.0.0.0" else addr


# ---- 26 定義と版 ----

def test_openapi_documented_paths_are_served_and_versioned():
    """R-DV-11: 定義にある口はどれも 404 を返さず、定義に無い口は 404。応答のすべてに定義と同じ版の API-Version が付く。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (_, base):
        status, headers, raw = request(base, "GET", "/openapi.json")
        assert status == 200
        doc = json.loads(raw)
        assert str(doc["openapi"]).startswith("3.1")
        version = doc["info"]["version"]
        assert version and headers["API-Version"] == version

        paths = doc["paths"]
        assert "/openapi.json" in paths and "/events" in paths and "/fault" in paths
        assert paths["/fault"].get("x-dev-only") is True or all(
            op.get("x-dev-only") is True for m, op in paths["/fault"].items() if m in ("get", "post"))
        assert {"get", "post"} <= set(paths["/fault"])

        hit = 0
        for path, item in paths.items():
            concrete = path
            while "{" in concrete:
                a, b = concrete.index("{"), concrete.index("}")
                concrete = concrete[:a] + "1" + concrete[b + 1:]
            for method in ("get", "post", "put", "patch", "delete"):
                if method not in item:
                    continue
                body = {} if method in ("post", "put", "patch") else None
                status, headers, _ = request(base, method.upper(), concrete, body)
                hit += 1
                assert status != 404, (method, path)
                assert headers.get("API-Version") == version, (method, path)
                if path == "/events" and method == "get":
                    assert status == 426
        assert hit >= 3

        for method, path in (("GET", "/no-such-path"), ("POST", "/no-such-path"), ("GET", "/v9/state")):
            status, headers, _ = request(base, method, path, {} if method == "POST" else None)
            assert status == 404 and headers.get("API-Version") == version


def test_openapi_and_version_header_without_dev():
    """R-DV-11: 開発でない扉も定義を返し、応答のすべてに API-Version が付く。開発の口(/fault)は 404。"""
    ctl = new_ctl()
    with serve(ctl, dev=False) as (_, base):
        doc = call(base, "GET", "/openapi.json")
        version = doc["info"]["version"]
        for method, path, body in (("GET", "/state", None), ("POST", "/unlock", {"by": "Matter"}),
                                   ("GET", "/audit?since=0", None), ("GET", "/fault", None),
                                   ("POST", "/fault", {"lock": "応答しない"}), ("GET", "/nowhere", None)):
            _, headers, _ = request(base, method, path, body)
            assert headers.get("API-Version") == version, (method, path)


# ---- 27 出来事の配信 ----

def test_events_in_order_without_gaps_across_reconnect():
    """R-DV-14: WebSocket で記録の全部が連番の順に、抜けも重なりも無く届き、受け取った最後の連番でつなぎ直せば間の物が全部届く。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (srv, base):
        call(base, "POST", "/event", {"tamper": "蓋"})          # つなぐ前からある記録
        ws = WS(srv.server_port, since=0)
        try:
            got = ws.records_until(len(audit_rows(base)))
            call(base, "POST", "/unlock", {"by": "Matter"})
            call(base, "POST", "/lock", {"by": "Matter"})
            call(base, "POST", "/event", {"door_open": True})
            call(base, "POST", "/event", {"door_open": False})
            got += ws.records_until(len(audit_rows(base)))
            ws.send(0x8, struct.pack("!H", 1000))
        finally:
            ws.close()

        # 切れている間の操作
        call(base, "POST", "/unlock", {"by": "Matter"})
        call(base, "POST", "/event", {"tamper": "配線"})
        call(base, "POST", "/lock", {"by": "Matter"})

        last = got[-1]["seq"]
        ws = WS(srv.server_port, since=last)
        try:
            got += ws.records_until(len(audit_rows(base)))
            call(base, "POST", "/event", {"manual": "機械キー"})
            got += ws.records_until(len(audit_rows(base)))
            got += ws.quiet(0.3)                                   # 二度送られる物は無い
        finally:
            ws.close()

        rows = audit_rows(base)
        assert [r["seq"] for r in got] == list(range(1, len(rows) + 1))
        assert got == rows                                         # /audit の一件と同じ欄
        assert rows == [asdict(e) for e in ctl.audit.entries]


def test_events_since_sends_only_later_records():
    """R-DV-14: since=N なら、連番が N より後の物だけが順に届く。"""
    ctl = new_ctl()
    for i in range(5):
        ctl.on_tamper(f"蓋{i}")
    n = len(ctl.audit.entries)
    with serve(ctl, dev=True) as (srv, base):
        ws = WS(srv.server_port, since=3)
        try:
            got = ws.records_until(n)
            assert [r["seq"] for r in got] == list(range(4, n + 1))
            assert ws.quiet(0.2) == []
        finally:
            ws.close()


def test_events_without_upgrade_is_426():
    """R-DV-14: Upgrade の無い GET /events は 426。差分は /audit?since=N で引ける。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (_, base):
        status, _, _ = request(base, "GET", "/events?since=0")
        assert status == 426
        status, _, _ = request(base, "GET", "/events")
        assert status == 426
        assert [r["seq"] for r in call(base, "GET", "/audit?since=0")] == [1]


def test_events_ping_pong_and_close():
    """R-DV-14: 受け手の ping に pong を返し、close に close を返して終える。ほかの文は読み捨てる。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (srv, base):
        ws = WS(srv.server_port, since=0)
        try:
            ws.records_until(len(ctl.audit.entries))
            ws.send(0x1, b'{"lock": true}')                       # 命令は受けない
            ws.send(0x9, b"hi")
            ws.sock.settimeout(3)
            while True:
                op, data = ws.frame()
                if op != 0x1:
                    break
            assert (op, data) == (0xA, b"hi")
            assert ctl.locked is True and ctl.contacts.outputs == []

            ws.send(0x8, struct.pack("!H", 1000))
            while True:
                op, data = ws.frame()
                if op != 0x1:
                    break
            assert op == 0x8
            ws.sock.settimeout(3)
            assert ws.sock.recv(1) == b""                          # 終えた後は切れる
        finally:
            ws.close()


def test_events_subscriber_limit_is_8():
    """R-DV-14: 配信を同時に受ける相手は 8 まで。9 番目は 503。一つ切れば、また受けられる。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (srv, base):
        conns = []
        try:
            for _ in range(8):
                conns.append(WS(srv.server_port, since=0))
            with pytest.raises(WSRejected) as e:
                conns.append(WS(srv.server_port, since=0))
            assert e.value.status == 503

            first = conns.pop(0)
            first.send(0x8, struct.pack("!H", 1000))
            first.close()

            def can_join():
                try:
                    conns.append(WS(srv.server_port, since=0))
                    return True
                except WSRejected:
                    return False
            assert wait_until(can_join, timeout=3, step=0.1)
        finally:
            for c in conns:
                c.close()


# ---- 28 配信と判断 ----

def test_non_reading_subscriber_does_not_block_autolock_or_commands():
    """R-DV-14: 受け手が何も読まないまま記録を大量に増やしても、自動施錠と命令の受け付けは止まらない。"""
    ctl = new_ctl(autolock_s=0.3)
    lock, clock = threading.Lock(), Clock()
    start_workers(ctl, lock, interval=0.05, clock=clock)
    with serve(ctl, lock, dev=True, clock=clock) as (srv, base):
        stuck = [WS(srv.server_port, since=0, rcvbuf=4096) for _ in range(2)]   # つないだまま読まない
        try:
            what = "蓋" * 1000
            t0 = time.time()
            for _ in range(2000):
                status, _, _ = request(base, "POST", "/event", {"tamper": what}, timeout=5)
                assert status == 200
                assert time.time() - t0 < 60, "記録を増やす命令が詰まっている"
            assert lock.acquire(timeout=2), "配信が判断の排他を握っている"
            lock.release()

            assert call(base, "POST", "/unlock", {"by": "Matter"})["result"] == "解錠"
            assert wait_until(lambda: ctl.locked, timeout=3), "自動施錠が止まっている"
            assert any(e.event == "施錠" and "自動施錠" in e.detail for e in ctl.audit.entries[-5:])

            assert call(base, "POST", "/unlock", {"by": "Matter"})["result"] == "解錠"
            assert call(base, "POST", "/lock", {"by": "Matter"})["result"] == "施錠"
            assert call(base, "GET", "/state")["locked"] is True
        finally:
            for s in stuck:
                s.close()


# ---- 29 故障注入: 錠が応答しない ----

def test_fault_lock_unresponsive_then_normal():
    """R-DV-03: 錠が応答しないを注入して解錠すると錠の位置は動かず失敗の数が増える。正常に戻して解錠すると動く。注入は記録に残る。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (_, base):
        assert call(base, "GET", "/fault") == {"lock": "正常", "clock_offset_s": 0, "failures": 0}

        got = call(base, "POST", "/fault", {"lock": "応答しない"})
        assert set(got) == FAULT_KEYS and got["lock"] == "応答しない" and got["failures"] == 0
        before = len(ctl.audit.entries)
        inject = fault_records(audit_rows(base))
        assert len(inject) == 1 and "応答しない" in record_text(inject[0])

        call(base, "POST", "/unlock", {"by": "Matter"})
        state = call(base, "GET", "/state")
        assert state["bolt_out"] is True                       # 錠は動かない
        assert ctl.contacts.outputs == []                      # 届いた命令の並びに足さない
        assert call(base, "GET", "/fault")["failures"] == 1
        unlock_rows = [r for r in audit_rows(base)[before:] if r["event"] == "解錠"]
        assert unlock_rows, "判断の側は命令を出した側として記録を続ける"
        assert inject[0]["seq"] < unlock_rows[0]["seq"]        # 記録だけで注入による失敗と分かる順

        back = call(base, "POST", "/fault", {"lock": "正常"})
        assert back["lock"] == "正常" and back["failures"] == 1
        assert len(fault_records(audit_rows(base))) == 2

        # 判断の側は解錠済みと見ているので、施錠してから解錠し直す(どちらも錠に届く)
        assert call(base, "POST", "/lock", {"by": "Matter"})["result"] == "施錠"
        call(base, "POST", "/unlock", {"by": "Matter"})
        assert call(base, "GET", "/state")["bolt_out"] is False
        assert ctl.contacts.outputs == ["lock", "unlock"]
        assert call(base, "GET", "/fault")["failures"] == 1


def test_fault_both_in_one_request():
    """R-DV-03: 一つの要求で錠と時計の両方を指示できる。"""
    ctl = new_ctl()
    clock = Clock()
    with serve(ctl, dev=True, clock=clock) as (_, base):
        got = call(base, "POST", "/fault", {"lock": "応答しない", "clock_offset_s": 42})
        assert got == {"lock": "応答しない", "clock_offset_s": 42, "failures": 0}
        assert call(base, "GET", "/fault") == got
        assert clock.offset_s == 42
        assert abs(clock.now() - (time.time() + 42)) < 1
        got = call(base, "POST", "/fault", {"lock": "正常", "clock_offset_s": 0})
        assert got == {"lock": "正常", "clock_offset_s": 0, "failures": 0} and clock.offset_s == 0


def test_clock_default():
    """R-DV-03: 時計は既定でずれが 0 で、実際の時刻にずれを足した値を返す。"""
    c = Clock()
    assert c.offset_s == 0
    assert abs(c.now() - time.time()) < 1
    c.offset_s = 100
    assert abs(c.now() - (time.time() + 100)) < 1


def test_fault_is_404_when_not_dev():
    """R-DV-03: 開発でない扉では故障注入の口は無い(404)。錠は注入されない。"""
    ctl = new_ctl()
    with serve(ctl, dev=False) as (_, base):
        assert request(base, "GET", "/fault")[0] == 404
        assert request(base, "POST", "/fault", {"lock": "応答しない"})[0] == 404
        call(base, "POST", "/unlock", {"by": "Matter"})
        assert call(base, "GET", "/state")["bolt_out"] is False
        assert fault_records(audit_rows(base)) == []


@pytest.mark.parametrize("body", [
    {"foo": 1},
    {"lock": "壊れる"},
    {"lock": 1},
    {"clock_offset_s": "100"},
    {"clock_offset_s": None},
    {"lock": "応答しない", "foo": 1},
    {"lock": "応答しない", "clock_offset_s": "x"},
    {"lock": "壊れる", "clock_offset_s": 100},
])
def test_fault_bad_request_changes_nothing(body):
    """R-DV-03: 無い項目か許されない値が一つでもあれば 400 で、何も変えない。"""
    ctl = new_ctl()
    clock = Clock()
    with serve(ctl, dev=True, clock=clock) as (_, base):
        before = call(base, "GET", "/fault")
        status, _, _ = request(base, "POST", "/fault", body)
        assert status == 400
        assert call(base, "GET", "/fault") == before
        assert ctl.contacts.fault_lock == "正常" and clock.offset_s == 0
        assert not [r for r in fault_records(audit_rows(base)) if r["result"] != "拒否"]
        call(base, "POST", "/unlock", {"by": "Matter"})
        assert call(base, "GET", "/state")["bolt_out"] is False


def test_fault_from_other_machine_is_403_and_recorded():
    """R-DV-03: 同じ機械でない相手からの故障注入は 403 で拒み、記録に残す。何も変えない。"""
    lan = lan_address()
    if lan is None:
        pytest.skip("ループバックでない番地が無い")
    ctl = new_ctl()
    with serve(ctl, dev=True, host="0.0.0.0") as (srv, base):
        remote = f"http://{lan}:{srv.server_port}"
        before = len(ctl.audit.entries)
        try:
            status, _, _ = request(remote, "POST", "/fault", {"lock": "応答しない"})
        except OSError:
            pytest.skip("自分の LAN の番地へつなげない")
        assert status == 403
        assert len(ctl.audit.entries) > before                  # 拒んだことが残る
        assert call(base, "GET", "/fault")["lock"] == "正常"     # 同じ機械からは受ける
        assert ctl.contacts.fault_lock == "正常"
        assert not [r for r in fault_records(audit_rows(base)) if "応答しない" in record_text(r)
                    and r["result"] != "拒否"]


# ---- 30 故障注入: 時計がずれる ----

def test_clock_offset_then_time_sync_records_time_jump():
    """R-DV-03 R-CT-24: 時計のずれ(time_jump_s を超える)を注入して時刻を同期すると「異常/時刻のずれ」が残る。"""
    ctl = new_ctl()
    assert ctl.config.time_jump_s == 300
    lock, clock = threading.Lock(), Clock()
    start_workers(ctl, lock, interval=0.05, clock=clock)
    with serve(ctl, lock, dev=True, clock=clock) as (_, base):
        got = call(base, "POST", "/fault", {"clock_offset_s": 1000})
        assert got["clock_offset_s"] == 1000 and got["lock"] == "正常"
        inject = fault_records(audit_rows(base))
        assert len(inject) == 1 and "1000" in record_text(inject[0])

        assert wait_until(lambda: call(base, "GET", "/state")["now"] > time.time() + 900), "扉の時計がずれない"
        assert not [r for r in audit_rows(base) if r["event"] == "時刻のずれ"]

        call(base, "POST", "/time", {"now": time.time()})
        jumps = [r for r in audit_rows(base) if r["kind"] == "異常" and r["event"] == "時刻のずれ"]
        assert len(jumps) == 1

        assert call(base, "POST", "/fault", {"clock_offset_s": 0})["clock_offset_s"] == 0
        assert clock.offset_s == 0


def test_clock_offset_within_time_jump_is_not_an_anomaly():
    """R-DV-03 R-CT-24: ずれが time_jump_s 以内なら、同期しても「時刻のずれ」は残らない。"""
    ctl = new_ctl()
    lock, clock = threading.Lock(), Clock()
    start_workers(ctl, lock, interval=0.05, clock=clock)
    with serve(ctl, lock, dev=True, clock=clock) as (_, base):
        call(base, "POST", "/fault", {"clock_offset_s": 100})
        assert wait_until(lambda: call(base, "GET", "/state")["now"] > time.time() + 50)
        call(base, "POST", "/time", {"now": time.time()})
        assert not [r for r in audit_rows(base) if r["event"] == "時刻のずれ"]


def test_clock_offset_advances_autolock():
    """R-DV-03: ずらすのは自動施錠を進める扉の時計。秒数より大きく進めれば、解錠のあと自動施錠する。"""
    ctl = new_ctl(autolock_s=600)
    lock, clock = threading.Lock(), Clock()
    start_workers(ctl, lock, interval=0.05, clock=clock)
    with serve(ctl, lock, dev=True, clock=clock) as (_, base):
        assert call(base, "POST", "/unlock", {"by": "Matter"})["result"] == "解錠"
        time.sleep(0.2)
        assert ctl.locked is False
        call(base, "POST", "/fault", {"clock_offset_s": 1000})
        assert wait_until(lambda: ctl.locked, timeout=3)
