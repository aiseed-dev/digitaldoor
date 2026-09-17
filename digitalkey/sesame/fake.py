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


def _fake_device(adv: p.Advertisement) -> FakeSesame:
    key = str(adv.device_uuid)
    if key not in _FAKE_DEVICES:
        _FAKE_DEVICES[key] = FakeSesame(registered_secret=bytes(16) if adv.is_registered else None)
    return _FAKE_DEVICES[key]


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
