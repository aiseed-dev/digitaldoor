"""digitalkey の錠ドライバ(entry point `digitalkey.locks`)。

  sesame:<機器UUID>      BLE 直結。事前に `digitalkey sesame register` で鍵を登録しておく
  sesameweb:<機器UUID>   CANDY HOUSE の Web API(Hub 3 経由)。API key と機器の secret key は
                         $SESAME_KEYS が指す JSON({"api_key": "...", "<uuid>": "<secret hex>"})か、
                         環境変数 SESAME_API_KEY / SESAME_SECRET_<UUIDの16進を大文字で> で渡す
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class SesameLock:
    """CANDY HOUSE Sesame(SesameOS3)を BLE で直接駆動する。

    digitalkey_sesame(このパッケージ)の BLE の実装を使う。
    登録(register)は `digitalkey sesame register` で一度だけ行い、鍵は KeyStore の JSON に残る。
    """

    id: str
    device_uuid: str
    keystore_path: str | None = None
    scan_seconds: float = 5.0

    async def _run(self, action: str) -> None:
        from .keystore import KeyStore
        from .transport_bleak import BleakSesameConnection, scan_once

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


def _keys() -> dict:
    path = os.environ.get("SESAME_KEYS")
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def make_ble(lock_id: str, arg: str):
    """`sesame:<uuid>` → SesameLock"""
    return SesameLock(lock_id, arg)


def make_web(lock_id: str, arg: str):
    """`sesameweb:<uuid>` → SesameWebLock"""
    keys = _keys()
    secret = keys.get(arg) or os.environ.get("SESAME_SECRET_" + arg.replace("-", "").upper(), "") or os.environ.get("SESAME_SECRET", "")
    api_key = keys.get("api_key") or os.environ.get("SESAME_API_KEY", "")
    if not (secret and api_key):
        raise ValueError(f"{lock_id}: SESAME_API_KEY と機器の secret key が要ります($SESAME_KEYS の JSON か環境変数)")
    return SesameWebLock(lock_id, arg, api_key, secret)
