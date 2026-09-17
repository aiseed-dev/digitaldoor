"""鍵 — 発行と失効と操作。

鍵は伝票である。発行の伝票に主体・錠・経路・開始・終了が書かれ、失効の伝票がそれを打ち消す。
錠の駆動は買った機器に任せる(Sesame は隣の sesame プロジェクトの BLE ドライバ、他は差し替え可)。
解錠するときは「発行済みで、失効しておらず、時間の窓の中」だけを見る。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Protocol

from .ledger import Ledger, Slip


class LockDriver(Protocol):
    id: str

    async def unlock(self) -> None: ...

    async def lock(self) -> None: ...


@dataclass
class DummyLock:
    """配線確認と試験用。動作を記録するだけ。"""

    id: str
    actions: list[str] = field(default_factory=list)

    async def unlock(self) -> None:
        self.actions.append("unlock")

    async def lock(self) -> None:
        self.actions.append("lock")


@dataclass
class SesameLock:
    """CANDY HOUSE Sesame(SesameOS3)を BLE で直接駆動する。

    digitalkey.sesame(BLE の実装)を使う。`pip install -e ".[sesame]"` が要る。
    登録(register)は `digitalkey sesame register` で一度だけ行い、鍵は KeyStore の JSON に残る。
    """

    id: str
    device_uuid: str
    keystore_path: str | None = None
    scan_seconds: float = 5.0

    async def _run(self, action: str) -> None:
        from digitalkey.sesame.keystore import KeyStore
        from digitalkey.sesame.transport_bleak import BleakSesameConnection, scan_once

        ks = KeyStore(path=self.keystore_path) if self.keystore_path else KeyStore()
        key = await ks.get(self.device_uuid)
        if key is None:
            raise RuntimeError(f"{self.id}: 登録された鍵がありません(digitalkey sesame register を先に)")
        found = await scan_once(self.scan_seconds)
        dev = None
        for adv, d in found:
            if str(adv.device_uuid) == self.device_uuid:
                dev = d
                break
        if dev is None:
            raise RuntimeError(f"{self.id}: 錠が見つかりません")
        conn = BleakSesameConnection(dev, history_tag="entrance")
        await conn.connect()
        try:
            await conn.device.login(key.secret)
            if action == "unlock":
                await conn.device.unlock()
            else:
                await conn.device.lock()
        finally:
            await conn.disconnect()

    async def unlock(self) -> None:
        await self._run("unlock")

    async def lock(self) -> None:
        await self._run("lock")


@dataclass
class SesameWebLock:
    """CANDY HOUSE Sesame を Hub 3 経由の Web API で駆動する(家に箱を置かない既定の形)。

    必要な物は三つ。API key(my.candyhouse.co で発行)、機器ごとの secret key(32桁の16進、アプリのQRから)、機器の UUID。
    署名は AES-CMAC(secret_key, 現在時刻のリトルエンディアン4バイトの真ん中3バイト)。
    cmd は 82=施錠、83=解錠、88=トグル。200 は受け付けただけで、動いたかは状態で確かめる。
    """

    id: str
    device_uuid: str
    api_key: str
    secret_key: str
    base_url: str = "https://app.candyhouse.co/api/sesame2"
    history_tag: str = "entrance"
    timeout: float = 15.0
    opener: "Callable[[str, str, dict, bytes | None], dict] | None" = None  # 試験用の差し替え

    CMD = {"lock": 82, "unlock": 83, "toggle": 88}

    def sign(self, now: int | None = None) -> str:
        from cryptography.hazmat.primitives import cmac
        from cryptography.hazmat.primitives.ciphers import algorithms

        key = bytes.fromhex(self.secret_key)
        if len(key) != 16:
            raise ValueError(f"{self.id}: secret key は16バイト(32桁の16進)")
        ts = int(now if now is not None else time.time()).to_bytes(4, "little")
        c = cmac.CMAC(algorithms.AES(key))
        c.update(ts[1:4])
        return c.finalize().hex()

    def body(self, action: str) -> dict:
        return {"cmd": self.CMD[action],
                "history": base64.b64encode(self.history_tag.encode()).decode(),
                "sign": self.sign()}

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        data = json.dumps(body).encode() if body is not None else None
        if self.opener is not None:
            return self.opener(method, self.base_url + path, headers, data)
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}

    def status(self) -> dict:
        return self._call("GET", f"/{self.device_uuid}")

    async def _run(self, action: str) -> None:
        await asyncio.to_thread(self._call, "POST", f"/{self.device_uuid}/cmd", self.body(action))

    async def unlock(self) -> None:
        await self._run("unlock")

    async def lock(self) -> None:
        await self._run("lock")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class Keyring:
    def __init__(self, ledger: Ledger, locks: dict[str, LockDriver] | None = None) -> None:
        self.ledger = ledger
        self.locks: dict[str, LockDriver] = locks or {}

    def issue(self, *, subject: str, lock_id: str, start: str, end: str, route: str = "遠隔解錠",
              reservation_id: str = "", basis: list[str] | tuple[str, ...] = (), issuer: str = "AI社員") -> Slip:
        return self.ledger.append("鍵_発行", {
            "主体": subject, "錠": lock_id, "経路": route, "開始": start, "終了": end,
            "予約番号": reservation_id, "根拠": ", ".join(basis),
        }, issuer=issuer, to=subject, basis=basis)

    def revoke(self, key_id: str, reason: str, *, issuer: str = "経営者") -> Slip:
        self.ledger.get(key_id)  # 無い番号なら KeyError
        return self.ledger.append("鍵_失効", {"鍵番号": key_id, "理由": reason}, issuer=issuer, basis=[key_id])

    def revoked(self, key_id: str) -> bool:
        return bool(self.ledger.select("鍵_失効", where={"鍵番号": key_id}))

    def is_valid(self, key_id: str, at: datetime | None = None) -> tuple[bool, str]:
        try:
            k = self.ledger.get(key_id)
        except KeyError:
            return False, "発行の記録がありません"
        if k.form != "鍵_発行":
            return False, "鍵の伝票ではありません"
        at = at or datetime.now()
        if self.revoked(key_id):
            return False, "失効しています"
        if at < _dt(k["開始"]):
            return False, "まだ有効ではありません"
        if at >= _dt(k["終了"]):
            return False, "期限が切れています"
        return True, ""

    def active(self, at: datetime | None = None, lock_id: str | None = None) -> list[Slip]:
        at = at or datetime.now()
        out = []
        for k in self.ledger.select("鍵_発行"):
            if lock_id and k["錠"] != lock_id:
                continue
            if self.is_valid(k.id, at)[0]:
                out.append(k)
        return out

    async def open(self, key_id: str, *, at: datetime | None = None, by: str = "AI社員") -> Slip:
        """鍵番号で解錠を試み、結果を必ず伝票に残す。"""
        ok, reason = self.is_valid(key_id, at)
        lock_id = ""
        try:
            lock_id = self.ledger.get(key_id)["錠"]
        except KeyError:
            pass
        if not ok:
            return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id or "?", "操作": "解錠",
                                               "結果": "拒否", "理由": reason, "主体": by}, issuer=by)
        drv = self.locks.get(lock_id)
        if drv is None:
            return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id, "操作": "解錠",
                                               "結果": "失敗", "理由": "錠の駆動が設定されていません", "主体": by},
                                      issuer=by, basis=[key_id])
        try:
            await drv.unlock()
        except Exception as e:  # 機器の失敗も記録に残す
            return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id, "操作": "解錠",
                                               "結果": "失敗", "理由": str(e)[:180], "主体": by},
                                      issuer=by, basis=[key_id])
        return self.ledger.append("鍵_操作", {"鍵番号": key_id, "錠": lock_id, "操作": "解錠",
                                           "結果": "成功", "主体": by}, issuer=by, basis=[key_id])
