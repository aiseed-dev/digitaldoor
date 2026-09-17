"""監査の記録 — 錠の中に残す連番とハッシュの連鎖と署名。

要求(電子錠要求仕様 共通コア 6章): 連番は単調増加で欠番が分かる。各記録は前の記録のハッシュを持つ。
署名はエクスポートできない鍵で行う(今はソフトの HMAC、後で STSAFE に差し替える)。同期前の時刻は「未同期」の印を付ける。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol


class Signer(Protocol):
    def sign(self, data: bytes) -> str: ...

    def verify(self, data: bytes, signature: str) -> bool: ...


@dataclass
class HmacSigner:
    """ソフトの署名。鍵は箱の中のファイル。STSAFE に替えるときは同じ口で。"""

    key: bytes

    def sign(self, data: bytes) -> str:
        return hmac.new(self.key, data, hashlib.sha256).hexdigest()

    def verify(self, data: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(data), signature)


@dataclass
class Entry:
    seq: int
    at: float          # 錠の時計(unix秒)
    synced: bool       # 時刻が同期済みか
    kind: str          # 操作/物理/認証/資格情報/設定/非常/異常/時刻
    event: str
    result: str = ""
    subject: str = ""  # 仮名化した識別子
    route: str = ""    # 系統1/系統2/有線/機械
    detail: str = ""
    prev_hash: str = ""
    hash: str = ""
    signature: str = ""

    def body(self) -> bytes:
        d = asdict(self)
        d.pop("hash"); d.pop("signature")
        return json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")


class Audit:
    def __init__(self, signer: Signer, path: Path | str | None = None) -> None:
        self.signer = signer
        self.path = Path(path) if path else None
        self.entries: list[Entry] = []
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.entries.append(Entry(**json.loads(line)))

    @property
    def last_hash(self) -> str:
        return self.entries[-1].hash if self.entries else ""

    def append(self, *, at: float, synced: bool, kind: str, event: str, result: str = "", subject: str = "",
               route: str = "", detail: str = "") -> Entry:
        e = Entry(seq=len(self.entries) + 1, at=at, synced=synced, kind=kind, event=event, result=result,
                  subject=subject, route=route, detail=detail, prev_hash=self.last_hash)
        e.hash = hashlib.sha256(e.body()).hexdigest()
        e.signature = self.signer.sign(e.hash.encode())
        self.entries.append(e)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
        return e

    def verify(self) -> tuple[bool, int, str]:
        prev, expect = "", 1
        for e in self.entries:
            if e.seq != expect:
                return False, expect - 1, f"欠番: {expect} の前に {e.seq}"
            if e.prev_hash != prev:
                return False, e.seq, "連鎖が切れています"
            if hashlib.sha256(e.body()).hexdigest() != e.hash:
                return False, e.seq, "内容が変わっています"
            if not self.signer.verify(e.hash.encode(), e.signature):
                return False, e.seq, "署名が合いません"
            prev, expect = e.hash, expect + 1
        return True, len(self.entries), ""

    def since(self, seq: int) -> list[Entry]:
        """ゲートウェイの差分取得。seq より後を返す。"""
        return [e for e in self.entries if e.seq > seq]
