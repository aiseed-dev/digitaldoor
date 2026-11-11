"""Persistent storage of registered device secrets.

The secret (16 bytes, hex) derived during registration is the only thing that
lets a phone/PC log in to a Sesame later.  Keep the file private.

Two backends:
  * a JSON file       – desktop / CLI use (権限 600)
  * a "kv" backend    – anything with async get(key)/set(key, value)/remove(key).
                        スマートフォンでは端末の保管庫(Flet の SecureStorage。securestore.py)。
                        今までの版は SharedPreferences に置いたので、`migrate_kv` が保管庫へ移す

鍵ごとに役(role)を持つ: "admin"(管理者)か "user"(利用者)。役の欄が無い今までの鍵は、その端末で登録した物として
管理者で引き継ぐ。役の欄はあるが読めない値のときは、利用者として読む(迷ったら権限の小さい側)。
役は画面の歯止めで、鍵の守りではない(Sesame の錠が知っている鍵は 32 桁の一つだけ)。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Optional, Protocol

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)
ROLE_LABELS = {ROLE_ADMIN: "管理者", ROLE_USER: "利用者"}


class KeyStoreError(Exception):
    """鍵の置き場に書けない、または読めない。文に鍵は入れない。"""


@dataclass
class DeviceKey:
    device_uuid: str          # lower-case UUID string
    model_id: str             # e.g. "sesame_6_pro"
    secret_hex: str           # 32 hex chars
    name: str = ""            # user-friendly name
    address: str = ""         # last known BLE address (informational)
    registered_at: float = 0  # unix seconds
    role: str = ROLE_ADMIN    # "admin" | "user"。欄の無い今までの鍵は管理者

    @property
    def secret(self) -> bytes:
        return bytes.fromhex(self.secret_hex)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def device_key_from_dict(row: dict) -> DeviceKey:
    """保存した 1 件から DeviceKey を作る。知らない欄は読み飛ばす。役の欄が無ければ管理者、読めない値なら利用者。"""
    if not isinstance(row, dict):
        raise KeyStoreError("鍵の置き場の中身の形が違います")
    names = {f.name for f in fields(DeviceKey)}
    data = {k: v for k, v in row.items() if k in names}
    role = data.get("role", ROLE_ADMIN)
    data["role"] = role if role in ROLES else ROLE_USER
    try:
        return DeviceKey(**data)
    except TypeError:
        raise KeyStoreError("鍵の置き場の中身の形が違います") from None


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
    """文字列を key で出し入れする口。`set` は失敗したとき False を返してよい(None は成功として扱う)。"""
    async def get(self, key: str): ...
    async def set(self, key: str, value) -> bool: ...
    async def remove(self, key: str): ...


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
        cache: dict[str, DeviceKey] = {}
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                raise KeyStoreError("鍵の置き場の中身を読めません") from None   # 中身は文に入れない
            for k, v in data.items():
                cache[k.lower()] = device_key_from_dict(v)
        self._cache = cache
        self._loaded = True
        return dict(self._cache)

    async def _save(self) -> None:
        raw = json.dumps({k: asdict(v) for k, v in self._cache.items()}, indent=2)
        if self._kv is not None:
            if await self._kv.set(DEFAULT_KEY, raw) is False:
                raise KeyStoreError("鍵を保存できませんでした")
        elif self._path:
            _write_private(self._path, raw)

    async def _commit(self, change) -> None:
        """メモリの控えを変えて保存する。保存に失敗したら控えを元に戻して例外を出す(保存できていない鍵を、あるように見せない)。"""
        before = dict(self._cache)
        change()
        try:
            await self._save()
        except Exception:
            self._cache = before
            raise

    async def get(self, device_uuid: str) -> Optional[DeviceKey]:
        if not self._loaded:
            await self.load()
        return self._cache.get(device_uuid.lower())

    async def put(self, key: DeviceKey) -> None:
        if not self._loaded:
            await self.load()
        await self._commit(lambda: self._cache.__setitem__(key.device_uuid.lower(), key))

    async def remove(self, device_uuid: str) -> None:
        if not self._loaded:
            await self.load()
        await self._commit(lambda: self._cache.pop(device_uuid.lower(), None))

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


def app_data_path(fake: bool = False) -> Path:
    """PC のアプリが、設定と渡した相手の控えを置く JSON(鍵そのものは入れない)。疑似は別の名前。"""
    return default_path().with_name("sesame-app-fake.json" if fake else "sesame-app.json")


def _write_private(path: Path, text: str) -> None:
    """権限 600 の一時ファイルに書いてから置き換える(書いている間も他のユーザーに読ませない)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


class JsonFileKV:
    """文字列を key で出し入れする JSON のファイル(PC 用。`AsyncKV` の口)。設定と控えを置く。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except ValueError:
            raise KeyStoreError("アプリの設定の置き場を読めません") from None
        if not isinstance(data, dict):
            raise KeyStoreError("アプリの設定の置き場を読めません")
        return data

    async def get(self, key: str):
        return self._read().get(key)

    async def set(self, key: str, value) -> bool:
        data = self._read()
        data[key] = value
        _write_private(self._path, json.dumps(data, indent=2, ensure_ascii=False))
        return True

    async def remove(self, key: str) -> bool:
        data = self._read()
        existed = data.pop(key, None) is not None
        _write_private(self._path, json.dumps(data, indent=2, ensure_ascii=False))
        return existed


SETTINGS_KEY = "digitalkey_sesame.settings"


class Settings:
    """アプリの設定(鍵ではない)。`require_device_auth`: 管理者の画面を開くとき端末の認証を求めるか(管理者が選ぶ)。
    `history_tag`: Sesame の履歴に残る自分の呼び名(名乗りなので偽れる)。"""

    DEFAULTS = {"require_device_auth": False, "history_tag": ""}

    def __init__(self, kv: AsyncKV) -> None:
        self._kv = kv
        self._data: dict = dict(self.DEFAULTS)

    async def load(self) -> "Settings":
        raw = await self._kv.get(SETTINGS_KEY)
        data = {}
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                data = {}
        self._data = {**self.DEFAULTS, **{k: v for k, v in (data if isinstance(data, dict) else {}).items() if k in self.DEFAULTS}}
        return self

    def __getitem__(self, name: str):
        return self._data[name]

    async def set(self, name: str, value) -> None:
        if name not in self.DEFAULTS:
            raise KeyError(name)
        new = {**self._data, name: value}
        if await self._kv.set(SETTINGS_KEY, json.dumps(new, ensure_ascii=False)) is False:
            raise KeyStoreError("設定を保存できませんでした")
        self._data = new


# ---------------------------------------------------------------------------
# 今までの保存領域(SharedPreferences)から保管庫(SecureStorage)への移し替え
# ---------------------------------------------------------------------------
MOVED_MARKER = DEFAULT_KEY + ".moved"   # 移し終えた印(元の保存領域に残す)。保管庫が空なのにこの印がある=鍵を失った疑い


@dataclass
class Migration:
    """移し替えの結果。`state` は none(移す物が無い)、migrated(移して確かめ、元を消した)、
    kept(移して確かめたが元を残した。衝突か、元を消せなかった)、failed(移せなかった。元は触っていない)、
    lost(保管庫が空なのに、移し終えた印がある)。`use` はアプリが以後使う置き場: "new"(保管庫)か "old"(今までの保存領域)。
    `detail` は人に見せる一文で、鍵は入らない。"""
    state: str
    use: str
    detail: str = ""
    moved: int = 0


def _parse_store(raw) -> dict:
    """鍵の置き場の中身(JSON の文字列)を、uuid -> 生の行 の辞書にする。形が違えば ValueError(文に中身は入れない)。"""
    if raw in (None, ""):
        return {}
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError("形が違います")
    return {str(k).lower(): dict(v) for k, v in data.items()}


async def migrate_kv(old: AsyncKV, new: AsyncKV, *, key: str = DEFAULT_KEY, marker: str = MOVED_MARKER) -> Migration:
    """鍵を `old`(今までの保存領域)から `new`(端末の保管庫)へ移す。どんな失敗でも、元の鍵を失わない。

    順序: 元を読む → 保管庫を読む(読めなければ書かない) → 足りない鍵だけを足して書く → 読み戻して全部そろっていることを確かめる
    → そのときだけ元を消す。途中のどこで失敗しても(例外、書いたはずが読めない、中身の食い違い)、元は消さず、
    `use="old"` で今までの保存領域のまま動けるようにして、次の起動でもう一度やり直す。
    保管庫に同じ Sesame の別の鍵があるとき(リセット後に登録し直した等)は、保管庫の鍵を正として、元を消さずに残す(kept)。
    移すのは `key` の 1 項目だけで、保存領域のほかの項目には触れない。保管庫を `clear` することはしない。
    """
    try:
        raw_old = await old.get(key)
    except Exception as e:
        # 元を読めない。保管庫に鍵があればそれを使う。無ければ元の置き場を使う(読めないままなので、アプリは空と見なさずに止まる)
        try:
            have = bool(_parse_store(await new.get(key)))
        except Exception:
            have = False
        return Migration("failed", "new" if have else "old", f"今までの保存領域を読めませんでした({type(e).__name__})。何も書いていません")
    try:
        old_rows = _parse_store(raw_old)
    except Exception:
        return Migration("failed", "old", "今までの保存領域の鍵を読めませんでした。元は消していません")
    try:
        raw_new = await new.get(key)
    except Exception as e:
        return Migration("failed", "old", f"保管庫を読めませんでした({type(e).__name__})。元は消していません。今までの保存領域のまま使います")
    try:
        new_rows = _parse_store(raw_new)
    except Exception:
        return Migration("failed", "old", "保管庫の中身を読めませんでした。上書きしていません。今までの保存領域のまま使います")

    if not old_rows:
        try:
            had_moved = bool(await old.get(marker))
        except Exception:
            had_moved = False
        if had_moved and raw_new in (None, ""):
            return Migration("lost", "new", "保管庫が空です。以前に移した鍵が失われた可能性があります。PC に写した鍵があれば取り込めます")
        return Migration("none", "new")

    merged = dict(new_rows)
    conflict = False
    for uuid, row in old_rows.items():
        row = {**row}
        row.setdefault("role", ROLE_ADMIN)     # 役の欄が無い今までの鍵は管理者(読むときと同じ規則を、書くときに明示する)
        if uuid not in merged:
            merged[uuid] = row
        elif merged[uuid].get("secret_hex") != row.get("secret_hex"):
            conflict = True                    # 保管庫の鍵を正とする。元は消さない

    if merged != new_rows:
        try:
            result = await new.set(key, json.dumps(merged, indent=2))
        except Exception as e:
            return Migration("failed", "old", f"保管庫に書けませんでした({type(e).__name__})。元は消していません。今までの保存領域のまま使います")
        if result is False:
            return Migration("failed", "old", "保管庫に書けませんでした。元は消していません。今までの保存領域のまま使います")
    try:
        back = _parse_store(await new.get(key))
    except Exception:
        return Migration("failed", "old", "保管庫から読み戻せませんでした。元は消していません。今までの保存領域のまま使います")
    if back != merged:
        return Migration("failed", "old", "保管庫の中身が書いた物と合いません。元は消していません。今までの保存領域のまま使います")

    if conflict:
        return Migration("kept", "new", "保管庫と今までの保存領域に、同じ Sesame の別の鍵があります。保管庫の鍵を使い、元は消していません", len(old_rows))
    try:
        await old.remove(key)
        if await old.get(key) not in (None, ""):
            raise RuntimeError("元が残っています")
    except Exception:
        return Migration("kept", "new", "保管庫に移して確かめました。元を消せなかったので残しています(次の起動で消します)", len(old_rows))
    try:
        await old.set(marker, "1")
    except Exception:
        pass
    return Migration("migrated", "new", f"鍵 {len(old_rows)} 件を保管庫に移し、読み戻して確かめてから、元を消しました", len(old_rows))
