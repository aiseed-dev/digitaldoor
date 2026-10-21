"""Persistent storage of registered device secrets.

The secret (16 bytes, hex) derived during registration is the only thing that
lets a phone/PC log in to a Sesame later.  Keep the file private.

Two backends:
  * JsonFileKeyStore  – a JSON file (desktop / CLI use)
  * a "kv" backend    – anything with async get(key)/set(key, value) such as
                        flet's page.shared_preferences (works on Android too)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class DeviceKey:
    device_uuid: str          # lower-case UUID string
    model_id: str             # e.g. "sesame_6_pro"
    secret_hex: str           # 32 hex chars
    name: str = ""            # user-friendly name
    address: str = ""         # last known BLE address (informational)
    registered_at: float = 0  # unix seconds

    @property
    def secret(self) -> bytes:
        return bytes.fromhex(self.secret_hex)


SECRET_DIGITS = 32   # 16 バイトの鍵は 32 桁の 16 進


def parse_secret_hex(text: str) -> bytes:
    """人が書き写した 32 桁の 16 進を、16 バイトの鍵にする。

    空白とハイフンは区切りとして読み飛ばす(`format_secret_hex` の出力をそのまま貼れる)。32 桁の 16 進でなければ ValueError。
    エラーの文に入力は入れない(鍵がログや画面に残らないように)。
    """
    digits = re.sub(r"[\s-]", "", text or "")
    if len(digits) != SECRET_DIGITS or not re.fullmatch(r"[0-9a-fA-F]+", digits):
        raise ValueError(f"鍵は 32 桁の 16 進です(0-9 と a-f。{SECRET_DIGITS} 桁。区切りの空白とハイフンは読み飛ばします)")
    return bytes.fromhex(digits)


def format_secret_hex(secret_hex: str, group: int = 4) -> str:
    """32 桁の 16 進を、`group` 桁ずつ空白で区切って読みやすくする(既定は 4 桁ずつの 8 組)。`parse_secret_hex` で戻せる。"""
    return " ".join(secret_hex[i:i + group] for i in range(0, len(secret_hex), group))


class AsyncKV(Protocol):
    async def get(self, key: str): ...
    async def set(self, key: str, value) -> bool: ...


DEFAULT_KEY = "digitalkey_sesame.devices"


class KeyStore:
    """Async key store. Pass `kv` (e.g. flet SharedPreferences) or `path` (JSON)."""

    def __init__(self, kv: Optional[AsyncKV] = None, path: Optional[Path | str] = None) -> None:
        self._kv = kv
        if path is None and kv is None:
            path = default_path()
        self._path = Path(path) if path else None
        self._cache: dict[str, DeviceKey] = {}
        self._loaded = False

    async def load(self) -> dict[str, DeviceKey]:
        raw = None
        if self._kv is not None:
            raw = await self._kv.get(DEFAULT_KEY)
        elif self._path and self._path.exists():
            raw = self._path.read_text(encoding="utf-8")
        self._cache = {}
        if raw:
            data = json.loads(raw) if isinstance(raw, str) else raw
            for k, v in data.items():
                self._cache[k.lower()] = DeviceKey(**v)
        self._loaded = True
        return dict(self._cache)

    async def _save(self) -> None:
        raw = json.dumps({k: asdict(v) for k, v in self._cache.items()}, indent=2)
        if self._kv is not None:
            await self._kv.set(DEFAULT_KEY, raw)
        elif self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(raw, encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self._path)

    async def get(self, device_uuid: str) -> Optional[DeviceKey]:
        if not self._loaded:
            await self.load()
        return self._cache.get(device_uuid.lower())

    async def put(self, key: DeviceKey) -> None:
        if not self._loaded:
            await self.load()
        self._cache[key.device_uuid.lower()] = key
        await self._save()

    async def remove(self, device_uuid: str) -> None:
        if not self._loaded:
            await self.load()
        self._cache.pop(device_uuid.lower(), None)
        await self._save()

    async def all(self) -> dict[str, DeviceKey]:
        if not self._loaded:
            await self.load()
        return dict(self._cache)


def fake_path() -> Path:
    """疑似の Sesame(`--fake`)の鍵の置き場。実機の鍵のファイルとは別の名前にして、混ざらないようにする。"""
    return default_path().with_name("sesame-keys-fake.json")


def default_path() -> Path:
    env = os.environ.get("SESAME_KEYSTORE")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "digitalkey" / "sesame-keys.json"
