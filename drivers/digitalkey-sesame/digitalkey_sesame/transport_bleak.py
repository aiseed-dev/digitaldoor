"""bleak transport: scanning and GATT I/O for Linux (BlueZ), Windows and macOS."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from . import protocol as p
from .device import SesameDevice

log = logging.getLogger(__name__)

ScanCb = Callable[[p.Advertisement, BLEDevice], None]


class SesameScanner:
    """Continuous scanner that reports CANDY HOUSE advertisements."""

    def __init__(self, on_adv: ScanCb) -> None:
        self._on_adv = on_adv
        self._scanner: Optional[BleakScanner] = None
        self.devices: dict[str, tuple[p.Advertisement, BLEDevice]] = {}

    def _detect(self, device: BLEDevice, adv: AdvertisementData) -> None:
        parsed = p.parse_advertisement(device.address, adv.local_name or device.name,
                                       adv.rssi, adv.manufacturer_data)
        if parsed is None:
            return
        key = str(parsed.device_uuid) if parsed.device_uuid else device.address
        self.devices[key] = (parsed, device)
        self._on_adv(parsed, device)

    async def start(self) -> None:
        if self._scanner is not None:
            return
        # No service-UUID filter: BlueZ sometimes hides the adv payload with it and
        # we match on manufacturer data (company 0x055A) anyway.
        self._scanner = BleakScanner(detection_callback=self._detect)
        await self._scanner.start()

    async def stop(self) -> None:
        if self._scanner is not None:
            try:
                await self._scanner.stop()
            finally:
                self._scanner = None

    @staticmethod
    async def scan_once(timeout: float = 5.0) -> list[tuple[p.Advertisement, BLEDevice]]:
        found: dict[str, tuple[p.Advertisement, BLEDevice]] = {}

        def cb(adv: p.Advertisement, dev: BLEDevice) -> None:
            found[str(adv.device_uuid or dev.address)] = (adv, dev)

        s = SesameScanner(cb)
        await s.start()
        try:
            await asyncio.sleep(timeout)
        finally:
            await s.stop()
        return list(found.values())


class BleakSesameConnection:
    """Owns a BleakClient and a SesameDevice bound to it."""

    def __init__(self, target: BLEDevice | str, *, on_status=None, on_log=None,
                 on_disconnect: Optional[Callable[[], None]] = None, history_tag: str = "flet") -> None:
        self._target = target
        self._on_disconnect = on_disconnect
        self.client = BleakClient(target, disconnected_callback=self._disconnected, timeout=20.0)
        self.device = SesameDevice(self._write, on_status=on_status, on_log=on_log, history_tag=history_tag)

    def _disconnected(self, _client: BleakClient) -> None:
        log.info("disconnected")
        self.device.logged_in = False
        if self._on_disconnect:
            self._on_disconnect()

    async def _write(self, chunk: bytes) -> None:
        await self.client.write_gatt_char(p.SESAME_WRITE_CHAR_UUID, chunk, response=False)

    def _notify(self, _char, data: bytearray) -> None:
        self.device.on_notify(bytes(data))

    async def connect(self) -> None:
        await self.client.connect()
        await self.client.start_notify(p.SESAME_NOTIFY_CHAR_UUID, self._notify)
        # the device publishes INITIAL (session token) right after this
        await self.device.wait_for_token()

    async def disconnect(self) -> None:
        try:
            if self.client.is_connected:
                await self.client.disconnect()
        except Exception as e:  # pragma: no cover
            log.warning("disconnect error: %s", e)

    @property
    def is_connected(self) -> bool:
        return bool(self.client.is_connected)


async def adapter_available() -> tuple[bool, str]:
    """Best-effort check that a BLE adapter exists and is powered."""
    try:
        s = BleakScanner()
        await s.start()
        await s.stop()
        return True, "Bluetooth adapter OK"
    except Exception as e:  # BleakError / DBus errors
        msg = str(e)
        if "No Bluetooth adapters found" in msg:
            return False, "アダプタなし（USB BLE ドングルが必要）"
        if ("org.bluez" in msg and "NotReady" in msg) or "Powered" in msg:
            return False, "アダプタが OFF（bluetoothctl power on）"
        return False, f"{type(e).__name__}: {msg}"
