"""監査の記録 — 錠の中に残す連番とハッシュの連鎖と署名。

要求(電子錠要求仕様 共通コア 6章): 連番は単調増加で欠番が分かる。各記録は前の記録のハッシュを持つ。
署名はエクスポートできない鍵で行う(今はソフトの Ed25519、後で STSAFE に差し替える)。同期前の時刻は「未同期」の印を付ける。

署名の鍵は置き場のファイルに持つ。新しい置き場は Ed25519(audit.ed25519、所有者だけが読める)。
前からの置き場で HMAC の鍵(audit.key)がある物は、記録の連鎖を切らないよう HMAC のまま続ける。
HMAC は受け手が同じ鍵を持たないと検べられないので、伝票(slips.py)は Ed25519 の置き場だけが出せる。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class Signer(Protocol):
    def sign(self, data: bytes) -> str: ...

    def verify(self, data: bytes, signature: str) -> bool: ...


@dataclass
class HmacSigner:
    """ソフトの署名。鍵は箱の中のファイル。STSAFE に替えるときは同じインターフェースで。"""

    key: bytes

    def sign(self, data: bytes) -> str:
        return hmac.new(self.key, data, hashlib.sha256).hexdigest()

    def verify(self, data: bytes, signature: str) -> bool:
        return hmac.compare_digest(self.sign(data), signature)


class Ed25519Verifier:
    """公開鍵だけで検べる側。受け手(管理会社、事業所サーバー)が持つ。"""

    alg = "Ed25519"

    def __init__(self, public: bytes) -> None:
        self._pub = Ed25519PublicKey.from_public_bytes(public)

    def verify(self, data: bytes, signature: str) -> bool:
        try:
            self._pub.verify(bytes.fromhex(signature), data)
            return True
        except (InvalidSignature, ValueError):
            return False


class Ed25519Signer:
    """ソフトの公開鍵署名。秘密鍵は箱の中のファイル。STSAFE に替えるときは同じインターフェースで。"""

    alg = "Ed25519"

    def __init__(self, private: bytes) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(private)

    @classmethod
    def generate(cls) -> "Ed25519Signer":
        return cls(Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))

    def private_bytes(self) -> bytes:
        return self._key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                       serialization.NoEncryption())

    def public_key(self) -> bytes:
        return self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def sign(self, data: bytes) -> str:
        return self._key.sign(data).hex()

    def verify(self, data: bytes, signature: str) -> bool:
        return Ed25519Verifier(self.public_key()).verify(data, signature)


def load_signer(directory: Path | str) -> Signer:
    """置き場の署名の鍵を開く。HMAC の鍵(audit.key)があればそれを、無ければ Ed25519 の鍵を(無ければ作って)使う。"""
    d = Path(directory)
    legacy = d / "audit.key"
    if legacy.exists():
        return HmacSigner(legacy.read_bytes())
    keyfile = d / "audit.ed25519"
    if not keyfile.exists():
        d.mkdir(parents=True, exist_ok=True)
        fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Ed25519Signer.generate().private_bytes())
    return Ed25519Signer(keyfile.read_bytes())


@dataclass
class Entry:
    seq: int
    at: float          # 錠の時計(unix秒)
    synced: bool       # 時刻が同期済みか
    kind: str          # 操作/物理/認証/資格情報/設定/非常/異常/時刻
    event: str
    result: str = ""
    subject: str = ""  # 仮名化した識別子
    route: str = ""    # 系統1/系統2/有線/機械キー
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
        return verify_chain(self.entries, self.signer)

    def since(self, seq: int) -> list[Entry]:
        """ゲートウェイの差分取得。seq より後を返す。"""
        return [e for e in self.entries if e.seq > seq]


def verify_chain(entries: list[Entry], verifier, *, start: int = 1, prev: str = "") -> tuple[bool, int, str]:
    """連番、前ハッシュの連鎖、内容のハッシュ、署名を順に検べる。(合否, 件数か止まった連番, 理由)。

    途中から検べるとき(受け手が取り込んだ分を消した箱)は、start に最初の連番、prev にその前ハッシュを渡す。
    """
    expect = start
    for e in entries:
        if e.seq != expect:
            return False, expect - 1, f"欠番: {expect} の前に {e.seq}"
        if e.prev_hash != prev:
            return False, e.seq, "連鎖が切れています"
        if hashlib.sha256(e.body()).hexdigest() != e.hash:
            return False, e.seq, "内容が変わっています"
        if not verifier.verify(e.hash.encode(), e.signature):
            return False, e.seq, "署名が合いません"
        prev, expect = e.hash, expect + 1
    return True, len(entries), ""
