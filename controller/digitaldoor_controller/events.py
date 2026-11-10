"""出来事の配信 — 監査の記録を WebSocket(RFC 6455)で順に送る(仕様書 5.5、R-DV-14)。標準ライブラリだけで書く。

- `GET /events?since=N` を WebSocket に切り替え、連番が N より後の記録を一件ずつ、`/audit` の一件と同じ欄の JSON で送る
- 追いついた後は、増えた記録を順に送る。抜かさず、まとめず、同じ物を二度送らない。つなぎ直しは受け取った最後の連番で
- 判断の排他(lock)の中では記録の写しを取るだけにし、送るのは排他の外で行う。受け手が遅くても判断は止まらない
- 受け手からの文は、ping に pong、close に close を返して終える。ほかは読み捨てる(この口から命令は受けない)
"""
from __future__ import annotations

import base64
import hashlib
import json
import select
import socket
import struct
import threading
from dataclasses import asdict

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_SUBSCRIBERS = 8          # 同時に受ける相手の数(仕様書 5.5 の選んでいる物)
POLL_S = 0.05                # 記録が増えたかを見る間隔

OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x8, 0x9, 0xA


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(opcode: int, payload: bytes) -> bytes:
    """サーバーから送る文(覆いなし)。"""
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 1 << 16:
        head = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return head + payload


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("切れました")
        buf += chunk
    return buf


def read_frame(sock: socket.socket) -> tuple[int, bytes]:
    """受け手からの文を一つ読む。受け手の文には覆い(mask)が付く。"""
    b1, b2 = _read_exact(sock, 2)
    opcode, n = b1 & 0x0F, b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", _read_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", _read_exact(sock, 8))[0]
    if n > 1 << 16:
        raise ConnectionError("文が大きすぎます")
    mask = _read_exact(sock, 4) if b2 & 0x80 else b"\0\0\0\0"
    data = _read_exact(sock, n)
    return opcode, bytes(c ^ mask[i % 4] for i, c in enumerate(data))


class Hub:
    """同時に受ける相手の数を数える。"""

    def __init__(self, limit: int = MAX_SUBSCRIBERS) -> None:
        self.limit = limit
        self.count = 0
        self._m = threading.Lock()

    def enter(self) -> bool:
        with self._m:
            if self.count >= self.limit:
                return False
            self.count += 1
            return True

    def leave(self) -> None:
        with self._m:
            self.count -= 1


def stream(sock: socket.socket, audit, lock: threading.Lock, since: int) -> None:
    """切れるか close を受けるまで、記録を送り続ける。"""
    sent = max(0, since)
    while True:
        with lock:  # 排他の中では写しを取るだけ(記録は追記だけなので、一件ずつは変わらない)
            rows = audit.entries[sent:]
        for e in rows:
            sock.sendall(frame(OP_TEXT, json.dumps(asdict(e), ensure_ascii=False).encode("utf-8")))
            sent += 1
        readable, _, _ = select.select([sock], [], [], 0 if rows else POLL_S)
        if readable:
            opcode, payload = read_frame(sock)
            if opcode == OP_CLOSE:
                sock.sendall(frame(OP_CLOSE, payload[:2]))
                return
            if opcode == OP_PING:
                sock.sendall(frame(OP_PONG, payload))
