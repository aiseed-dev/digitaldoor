"""In-process fake Sesame (device side of the protocol) for tests and for a
GUI demo mode without hardware (`python main.py --fake`)."""
from __future__ import annotations

import asyncio
import struct
import time
import uuid
from typing import Callable, Optional

from . import protocol as p
from .device import SesameDevice


class FakeSesame:
    def __init__(self, registered_secret: Optional[bytes] = None, token: bytes = b"\xde\xad\xbe\xef"):
        self.token = token
        self.secret = registered_secret
        self.cipher: Optional[p.SesameCipher] = None
        self.rx = p.SegmentReceiver()
        self.locked = True
        self.app: Optional[SesameDevice] = None
        self.received: list[tuple[int, bytes]] = []

    # ---- device -> app
    def _emit(self, plaintext: bytes, encrypt: bool):
        seg = p.SegmentType.PLAIN
        if encrypt:
            plaintext = self.cipher.encrypt(plaintext)
            seg = p.SegmentType.CIPHER
        for c in p.split_segments(seg, plaintext):
            self.app.on_notify(c)

    def publish_initial(self):
        self._emit(bytes([p.OpCode.PUBLISH, p.ItemCode.INITIAL]) + self.token, False)

    def _respond(self, item, result, payload=b"", encrypt=True):
        self._emit(bytes([p.OpCode.RESPONSE, item, result]) + payload, encrypt)

    def mech_status_bytes(self):
        flags = 0x02 if self.locked else 0x04
        return struct.pack("<Hhh", 2950, -32768, 20 if self.locked else 300) + bytes([flags])

    def publish_mech_status(self):
        self._emit(bytes([p.OpCode.PUBLISH, p.ItemCode.MECH_STATUS]) + self.mech_status_bytes(), True)

    # ---- app -> device
    async def write(self, chunk: bytes):
        msg = self.rx.feed(chunk)
        if msg is None:
            return
        seg, body = msg
        if seg == p.SegmentType.CIPHER:
            body = self.cipher.decrypt(body)
        item, data = body[0], body[1:]
        self.received.append((item, data))
        if item == p.ItemCode.REGISTRATION:
            assert seg == p.SegmentType.PLAIN and len(data) == 68
            if self.secret is not None:
                self._respond(item, p.ResultCode.INVALID_ACTION, encrypt=False)
                return
            key = p.EphemeralKey()
            self.secret = key.derive_device_secret(data[:64])
            self.cipher = p.SesameCipher(p.aes_cmac(self.secret, self.token), self.token)
            payload = self.mech_status_bytes() + struct.pack("<hhh", 20, 300, 0) + key.public_bytes_raw()
            self._respond(item, p.ResultCode.SUCCESS, payload, encrypt=False)
        elif item == p.ItemCode.LOGIN:
            assert seg == p.SegmentType.PLAIN
            expected = p.aes_cmac(self.secret, self.token)
            if data != expected[:4]:
                self._respond(item, p.ResultCode.INVALID_SIG, encrypt=False)
                return
            self.cipher = p.SesameCipher(expected, self.token)
            self._respond(item, p.ResultCode.SUCCESS, struct.pack("<I", int(time.time()) - 100), encrypt=False)
            self.publish_mech_status()
        elif item in (p.ItemCode.LOCK, p.ItemCode.UNLOCK):
            assert seg == p.SegmentType.CIPHER
            assert data[:2] == b"\x00\x0e"
            self.locked = item == p.ItemCode.LOCK
            self._respond(item, p.ResultCode.SUCCESS)
            self.publish_mech_status()
        elif item == p.ItemCode.MECH_STATUS:
            self._respond(item, p.ResultCode.SUCCESS, self.mech_status_bytes())
        elif item == p.ItemCode.VERSION_TAG:
            self._respond(item, p.ResultCode.SUCCESS, b"3.0-6-abcdef")
        elif item == p.ItemCode.TIME:
            self._respond(item, p.ResultCode.SUCCESS)
        else:
            self._respond(item, p.ResultCode.NOT_SUPPORTED)


# ---------------------------------------------------------------------------
# Drop-in replacements for transport_bleak.SesameScanner / BleakSesameConnection
# ---------------------------------------------------------------------------
_FAKE_DEVICES: dict[str, FakeSesame] = {}
_FAKE_ADVS: list[p.Advertisement] = [
    p.Advertisement(address="AA:BB:CC:DD:EE:01", name="Sesame6Pro", rssi=-48, product_type=21,
                    is_registered=False, has_history=False,
                    device_uuid=uuid.UUID("11111111-2222-3333-4444-555555555501")),
    p.Advertisement(address="AA:BB:CC:DD:EE:02", name="Sesame5", rssi=-70, product_type=5,
                    is_registered=True, has_history=False,
                    device_uuid=uuid.UUID("11111111-2222-3333-4444-555555555502")),
]


_INITIALLY_REGISTERED = {str(a.device_uuid): a.is_registered for a in _FAKE_ADVS}   # 広告の印は後で書き換わるので、始めの状態を控える


def _fake_device(adv: p.Advertisement) -> FakeSesame:
    key = str(adv.device_uuid)
    if key not in _FAKE_DEVICES:
        _FAKE_DEVICES[key] = FakeSesame(registered_secret=bytes(16) if _INITIALLY_REGISTERED.get(key, adv.is_registered) else None)
    return _FAKE_DEVICES[key]


async def fake_scan_once(timeout: float = 5.0) -> list[tuple[p.Advertisement, p.Advertisement]]:
    """`SesameScanner.scan_once` の疑似。待たずに、疑似の機器の広告を返す(登録済みかは疑似の機器の今の状態)。"""
    out = []
    for adv in _FAKE_ADVS:
        adv.is_registered = _fake_device(adv).secret is not None
        out.append((adv, adv))
    return out


class FakeScanner:
    def __init__(self, on_adv: Callable[[p.Advertisement, object], None]) -> None:
        self._on_adv = on_adv
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        async def run():
            while True:
                for adv in _FAKE_ADVS:
                    dev = _fake_device(adv)
                    adv.is_registered = dev.secret is not None
                    self._on_adv(adv, adv)
                await asyncio.sleep(1.0)
        self._task = asyncio.create_task(run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None


class FakeConnection:
    def __init__(self, target: p.Advertisement, *, on_status=None, on_log=None, on_disconnect=None,
                 history_tag: str = "flet") -> None:
        self._fake = _fake_device(target)
        self._on_disconnect = on_disconnect
        self._connected = False
        self.device = SesameDevice(self._fake.write, on_status=on_status, on_log=on_log, history_tag=history_tag)
        self._fake.app = self.device

    async def connect(self) -> None:
        await asyncio.sleep(0.3)
        self._connected = True
        self._fake.rx = p.SegmentReceiver()
        self._fake.publish_initial()
        await self.device.wait_for_token()

    async def disconnect(self) -> None:
        self._connected = False
        if self._on_disconnect:
            self._on_disconnect()

    @property
    def is_connected(self) -> bool:
        return self._connected


async def fake_adapter_available() -> tuple[bool, str]:
    return True, "FAKE (demo mode, no real Bluetooth)"


class FakeSesameLock:
    """疑似の Sesame を、digitalkey の錠のドライバのインターフェース(`unlock()` / `lock()`。`digitalkey.entrance.keyring.LockDriver`)で動かす。

    実機と同じ道を通る: 呼ぶたびに SesameDevice を作り、疑似の機器にログインして(秘密は 16 バイトの 0)、LOCK か UNLOCK を送る。
    疑似の機器(`fake`)は呼びの間も残るので、`locked` で今の状態を読める。`reachable = False` にすると、
    実機が見つからないときと同じ RuntimeError を出す(錠に届かないときの試験用)。実機にも Bluetooth にも触れない。
    """

    def __init__(self, lock_id: str = "fake", *, reachable: bool = True) -> None:
        self.id = lock_id
        self.reachable = reachable
        self.fake = FakeSesame(registered_secret=bytes(16))

    @property
    def locked(self) -> bool:
        return self.fake.locked

    async def _run(self, action: str) -> None:
        if not self.reachable:
            raise RuntimeError(f"{self.id}: 錠が見つかりません")
        fake = self.fake
        fake.rx = p.SegmentReceiver()
        device = SesameDevice(fake.write, history_tag="entrance")
        fake.app = device
        fake.publish_initial()
        await device.wait_for_token()
        await device.login(bytes(16))
        await (device.unlock() if action == "unlock" else device.lock())

    async def unlock(self) -> None:
        await self._run("unlock")

    async def lock(self) -> None:
        await self._run("lock")


# ---------------------------------------------------------------------------
# 疑似の保存領域・端末の認証・カメラ(試験と、実機なしの画面の確かめ用。本物の鍵にも端末にも触れない)
# ---------------------------------------------------------------------------
class FakeKV:
    """メモリの中の保存領域。SharedPreferences と SecureStorage の代わり。失敗は旗で起こす(試験用)。

    fail_get / fail_set / fail_remove: その操作が例外を出す(端末の保管庫がエラーを返す場合)。
    drop_writes: set は成功を返すが、何も保存しない(書いたはずが読めない場合)。
    fail_get_after_set: set のあとの最初の get が例外になる(書いた直後に落ちる場合)。
    log: (操作, key) の並び。値は入れない。
    """

    def __init__(self, data: Optional[dict] = None) -> None:
        self.data: dict = dict(data or {})
        self.fail_get = self.fail_set = self.fail_remove = False
        self.drop_writes = False
        self.fail_get_after_set = False
        self._just_set = False
        self.log: list[tuple[str, str]] = []

    async def get(self, key: str):
        self.log.append(("get", key))
        if self.fail_get or (self.fail_get_after_set and self._just_set):
            self._just_set = False
            raise RuntimeError("PlatformException(疑似の保管庫のエラー)")
        return self.data.get(key)

    async def set(self, key: str, value) -> bool:
        self.log.append(("set", key))
        if self.fail_set:
            raise RuntimeError("PlatformException(疑似の保管庫のエラー)")
        if not self.drop_writes:
            self.data[key] = value
        self._just_set = True
        return True

    async def remove(self, key: str) -> bool:
        self.log.append(("remove", key))
        if self.fail_remove:
            raise RuntimeError("PlatformException(疑似の保管庫のエラー)")
        return self.data.pop(key, None) is not None


class FakeGate:
    """端末の認証の代わり。allow=False で認証が通らない場合、available=False で使えない端末の場合。"""

    def __init__(self, available: bool = True, allow: bool = True) -> None:
        self._available, self.allow = available, allow
        self.prepared = 0
        self.verified = 0

    def available(self) -> bool:
        return self._available

    async def prepare(self) -> None:
        if not self._available:
            raise RuntimeError("端末の認証を使えません")
        self.prepared += 1

    async def verify(self) -> bool:
        self.verified += 1
        return self.allow


class FakeQrReader:
    """画像のバイト列を、そのまま UTF-8 の文字として返す QR の読み取りの代わり(試験では「画像」に QR の文字を入れる)。"""

    def available(self) -> bool:
        return True

    async def read(self, image: bytes) -> Optional[str]:
        try:
            return bytes(image).decode("utf-8")
        except Exception:
            return None


def fake_camera_path():
    """疑似のカメラの置き場。疑似の画面(--fake)で、渡す側が出した QR の文字をここに置き、受け取る側がここから読む。
    鍵の置き場を変えた 2 つの画面で試すときは、環境変数 SESAME_FAKE_CAMERA で同じ場所を指す。"""
    import os
    from pathlib import Path
    from .keystore import default_path
    env = os.environ.get("SESAME_FAKE_CAMERA")
    return Path(env) if env else default_path().with_name("sesame-fake-camera.txt")


class FakeCameraScanner:
    """カメラで QR を読むインターフェース(qrcamera.QrScanner)の疑似。`text` を渡すとそれを読んだことにする。
    `channel`(ファイル)を渡すと、そこに文字が置かれるのを待って読む(疑似の画面で、渡す側と受け取る側を別々に動かすため)。
    `ok=False` は、カメラを始められない場合(許可されない等)。"""

    def __init__(self, text: Optional[str] = None, channel=None, ok: bool = True) -> None:
        self.text, self.channel, self.ok = text, channel, ok
        self.started = self.stopped = False
        self._task: Optional[asyncio.Task] = None

    def build(self):
        import flet as ft
        return ft.Container(content=ft.Text("疑似のカメラ(QR が置かれるのを待っています)", size=12), height=80, padding=8)

    async def start(self, on_text) -> bool:
        if not self.ok:
            return False
        self.started = True

        async def run():
            last = None
            while not self.stopped:
                text = self.text
                if text is None and self.channel is not None and self.channel.exists():
                    text = self.channel.read_text(encoding="utf-8").strip() or None
                if text and text != last:
                    last = text
                    if await on_text(text):
                        return
                await asyncio.sleep(0.3)

        self._task = asyncio.create_task(run())
        return True

    async def stop(self) -> None:
        self.stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
