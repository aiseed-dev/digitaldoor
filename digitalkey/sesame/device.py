"""Transport-independent Sesame OS3 device session (register / login / commands).

The transport only has to do two things:
  * call `SesameDevice.on_notify(bytes)` for every notification chunk from
    characteristic 16860003-...
  * provide an async `write(bytes)` that writes one <=20 byte chunk to
    characteristic 16860002-... (write without response)

See transport_bleak.py for the desktop implementation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from . import protocol as p

log = logging.getLogger(__name__)

WriteFn = Callable[[bytes], Awaitable[None]]
StatusCb = Callable[["SesameDevice"], None]
LogCb = Callable[[str], None]


class SesameError(Exception):
    pass


class SesameDevice:
    def __init__(self, write: WriteFn, *, on_status: Optional[StatusCb] = None,
                 on_log: Optional[LogCb] = None, history_tag: str = "flet") -> None:
        self._write = write
        self._on_status = on_status
        self._on_log = on_log
        self.history_tag = history_tag

        self._rx = p.SegmentReceiver()
        self._cipher: Optional[p.SesameCipher] = None
        self._tx_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future] = {}
        self._loop = asyncio.get_event_loop()

        self.session_token: Optional[bytes] = None
        self._token_event = asyncio.Event()
        self.device_secret: Optional[bytes] = None
        self.logged_in = False
        self.mech_status: Optional[p.MechStatus] = None
        self.mech_setting: Optional[p.MechSetting] = None
        self.version: Optional[str] = None
        self.device_time_offset: Optional[int] = None

    # ------------------------------------------------------------------ util
    def _log(self, msg: str) -> None:
        log.info(msg)
        if self._on_log:
            try:
                self._on_log(msg)
            except Exception:  # pragma: no cover
                log.exception("log callback failed")

    def _notify_status(self) -> None:
        if self._on_status:
            try:
                self._on_status(self)
            except Exception:  # pragma: no cover
                log.exception("status callback failed")

    @property
    def state(self) -> str:
        if self.mech_status is None:
            return "unknown"
        return self.mech_status.state

    # -------------------------------------------------------------- receive
    def on_notify(self, chunk: bytes) -> None:
        """Feed one raw notification chunk (called by the transport)."""
        try:
            msg = self._rx.feed(bytes(chunk))
        except Exception as e:
            self._log(f"segment error: {e}")
            return
        if msg is None:
            return
        seg_type, body = msg
        if seg_type == p.SegmentType.CIPHER:
            if self._cipher is None:
                self._log("cipher message before login; ignored")
                return
            try:
                body = self._cipher.decrypt(body)
            except Exception as e:
                self._log(f"decrypt failed: {e}")
                return
        parsed = p.parse_notification(body)
        if parsed is None:
            self._log(f"unparsed notification: {body.hex()}")
            return
        if isinstance(parsed, p.Response):
            self._log(f"<= response item={parsed.item_code} "
                      f"result={p.ResultCode.name_of(parsed.result_code)} payload={parsed.payload.hex()}")
            fut = self._pending.pop(parsed.item_code, None)
            if fut is not None and not fut.done():
                fut.set_result(parsed)
        else:
            self._handle_publish(parsed)

    def _handle_publish(self, pub: p.Publish) -> None:
        code = pub.item_code
        if code == p.ItemCode.INITIAL:
            self.session_token = pub.payload[:4]
            self._log(f"<= initial token={self.session_token.hex()}")
            self._token_event.set()
        elif code == p.ItemCode.MECH_STATUS:
            try:
                self.mech_status = p.MechStatus.parse(pub.payload)
            except ValueError as e:
                self._log(f"bad mech status: {e}")
                return
            ms = self.mech_status
            self._log(f"<= mech status {ms.state} pos={ms.position} target={ms.target} "
                      f"battery={ms.battery_voltage:.2f}V ({ms.battery_percent}%) flags=0x{ms.flags:02x}")
            self._notify_status()
        elif code == p.ItemCode.MECH_SETTING:
            try:
                self.mech_setting = p.MechSetting.parse(pub.payload)
                self._log(f"<= mech setting {self.mech_setting}")
            except ValueError as e:
                self._log(f"bad mech setting: {e}")
            self._notify_status()
        elif code == p.ItemCode.BATTERY_VOLTAGE:
            self._log(f"<= battery voltage payload={pub.payload.hex()}")
        else:
            self._log(f"<= publish item={code} payload={pub.payload.hex()}")

    # ----------------------------------------------------------------- send
    async def send(self, item_code: int, data: bytes = b"", *, encrypt: bool = True,
                   timeout: float = 8.0) -> p.Response:
        if encrypt and self._cipher is None:
            raise SesameError("not logged in")
        payload = p.build_command(item_code, data)
        fut: asyncio.Future = self._loop.create_future()
        async with self._tx_lock:
            old = self._pending.get(item_code)
            if old is not None and not old.done():
                old.cancel()
            self._pending[item_code] = fut
            if encrypt:
                body = self._cipher.encrypt(payload)
                seg_type = p.SegmentType.CIPHER
            else:
                body = payload
                seg_type = p.SegmentType.PLAIN
            self._log(f"=> item={item_code} {'cipher' if encrypt else 'plain'} data={data.hex()}")
            for chunk in p.split_segments(seg_type, body):
                await self._write(chunk)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(item_code, None)
            raise SesameError(f"timeout waiting for response to item {item_code}")

    # ------------------------------------------------------- session set-up
    async def wait_for_token(self, timeout: float = 10.0) -> bytes:
        """The device publishes a 4-byte random token right after notifications
        are enabled (ItemCode.INITIAL)."""
        if self.session_token is None:
            try:
                await asyncio.wait_for(self._token_event.wait(), timeout)
            except asyncio.TimeoutError:
                raise SesameError("no INITIAL token received from device "
                                  "(is notify enabled? is another phone connected?)")
        return self.session_token  # type: ignore[return-value]

    async def register(self) -> bytes:
        """Pair with an *unregistered* Sesame. Returns the 16-byte device secret.
        After this call the session is already authenticated (no login needed)."""
        token = await self.wait_for_token()
        key = p.EphemeralKey()
        resp = await self.send(p.ItemCode.REGISTRATION,
                               key.public_bytes_raw() + p.timestamp_le32(), encrypt=False, timeout=15)
        if not resp.ok:
            raise SesameError(f"registration refused: {p.ResultCode.name_of(resp.result_code)} "
                              f"(device already registered? reset it first)")
        reg = p.RegistrationResult.parse(resp.payload)
        self.device_secret = key.derive_device_secret(reg.device_public_key)
        self.mech_status = reg.mech_status
        self.mech_setting = reg.mech_setting
        session_key = p.session_key_from_secret(self.device_secret, token)
        self._cipher = p.SesameCipher(session_key, token)
        self.logged_in = True
        self._log(f"registered; secret={self.device_secret.hex()} state={self.state}")
        self._notify_status()
        return self.device_secret

    async def login(self, device_secret: bytes) -> None:
        token = await self.wait_for_token()
        self.device_secret = bytes(device_secret)
        session_key = p.session_key_from_secret(self.device_secret, token)
        self._cipher = p.SesameCipher(session_key, token)
        resp = await self.send(p.ItemCode.LOGIN, session_key[:4], encrypt=False)
        if not resp.ok:
            self._cipher = None
            raise SesameError(f"login failed: {p.ResultCode.name_of(resp.result_code)} (wrong secret?)")
        self.logged_in = True
        if len(resp.payload) >= 4:
            device_time = int.from_bytes(resp.payload[:4], "little")
            self.device_time_offset = int(time.time()) - device_time
            self._log(f"logged in; device time offset {self.device_time_offset:+d}s")
            if abs(self.device_time_offset) > 3:
                try:
                    await self.send(p.ItemCode.TIME, p.timestamp_le32())
                except SesameError as e:
                    self._log(f"time sync failed: {e}")
        else:
            self._log("logged in")
        self._notify_status()

    # ------------------------------------------------------------ commands
    async def lock(self) -> None:
        await self._simple(p.ItemCode.LOCK, p.build_history_tag(self.history_tag))

    async def unlock(self) -> None:
        await self._simple(p.ItemCode.UNLOCK, p.build_history_tag(self.history_tag))

    async def toggle(self) -> None:
        # Mirror CHSesame5Device.toggle(): decide from the last known mech status.
        if self.mech_status is not None and self.mech_status.is_in_lock_range:
            await self.unlock()
        else:
            await self.lock()

    async def get_version(self) -> str:
        resp = await self._simple(p.ItemCode.VERSION_TAG)
        self.version = resp.payload.decode("utf-8", "replace").strip("\x00")
        self._notify_status()
        return self.version

    async def request_mech_status(self) -> Optional[p.MechStatus]:
        """Ask for a fresh mech status (the device also publishes it on change)."""
        try:
            resp = await self._simple(p.ItemCode.MECH_STATUS)
        except SesameError as e:
            self._log(f"mech status read not supported: {e}")
            return self.mech_status
        if len(resp.payload) >= 7:
            self.mech_status = p.MechStatus.parse(resp.payload)
            self._notify_status()
        return self.mech_status

    async def set_autolock(self, seconds: int) -> None:
        await self._simple(p.ItemCode.AUTOLOCK, int(seconds).to_bytes(2, "little", signed=True))

    async def set_magnet(self) -> None:
        """Calibrate the magnet position (SDK: magnet())."""
        await self._simple(p.ItemCode.MAGNET)

    async def reset(self) -> None:
        """Factory-reset the device (drops registration). Needs a logged-in session."""
        await self._simple(p.ItemCode.RESET)
        self.logged_in = False
        self._cipher = None
        self._notify_status()

    async def _simple(self, item_code: int, data: bytes = b"") -> p.Response:
        resp = await self.send(item_code, data)
        if not resp.ok:
            raise SesameError(f"item {item_code} failed: {p.ResultCode.name_of(resp.result_code)}")
        return resp
