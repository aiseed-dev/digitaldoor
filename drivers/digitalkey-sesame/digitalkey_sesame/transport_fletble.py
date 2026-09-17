"""Transport for Android/iOS (and any platform where the app is built with
`flet build`): uses the flet-ble extension (flutter_blue_plus) instead of bleak.

Same surface as transport_bleak: a scanner with start/stop + callback, and a
connection object exposing `.device` (SesameDevice), connect()/disconnect().
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import flet as ft
from flet_ble import BleConnectionStateEvent, BleNotifyEvent, BleScanResultEvent, FletBle

from . import protocol as p
from .device import SesameDevice

log = logging.getLogger(__name__)


class FletBleHub:
    """Owns the single FletBle service and routes its events to the active
    scanner / connection.  Create once per page, before adding other controls."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.ble = FletBle(
            on_scan_result=self._on_scan_result,
            on_notify=self._on_notify,
            on_connection_state=self._on_connection_state,
            on_error=self._on_error,
        )
        page.services.append(self.ble)
        page.update()
        self.scanner: Optional[FletBleScanner] = None
        self.connection: Optional[FletBleConnection] = None
        self.on_error: Optional[Callable[[str], None]] = None

    # event routing
    def _on_scan_result(self, e: BleScanResultEvent) -> None:
        if self.scanner is not None:
            self.scanner._handle(e)

    def _on_notify(self, e: BleNotifyEvent) -> None:
        c = self.connection
        if c is not None and e.device_id == c.device_id and e.characteristic.lower() == p.SESAME_NOTIFY_CHAR_UUID:
            c.device.on_notify(e.bytes)

    def _on_connection_state(self, e: BleConnectionStateEvent) -> None:
        c = self.connection
        if c is not None and e.device_id == c.device_id:
            c._connection_state(e.connected)

    def _on_error(self, e) -> None:
        log.warning("flet_ble error: %s", e.data)
        if self.on_error:
            self.on_error(str(e.data))

    # factories mirroring transport_bleak
    def make_scanner(self, on_adv) -> "FletBleScanner":
        self.scanner = FletBleScanner(self, on_adv)
        return self.scanner

    def make_connection(self, device_id: str, **kw) -> "FletBleConnection":
        self.connection = FletBleConnection(self, device_id, **kw)
        return self.connection

    async def adapter_available(self) -> tuple[bool, str]:
        try:
            if not await self.ble.is_supported():
                return False, "この端末は BLE 非対応です"
            state = await self.ble.adapter_state()
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        if state == "on":
            return True, "Bluetooth ON"
        if state == "off":
            return False, "Bluetooth が OFF です（スキャン開始で ON を要求します）"
        if state == "unauthorized":
            return False, "Bluetooth の権限がありません（設定 → アプリ → 権限）"
        return False, f"Bluetooth: {state}"


class FletBleScanner:
    def __init__(self, hub: FletBleHub, on_adv) -> None:
        self.hub = hub
        self._on_adv = on_adv
        self.devices: dict[str, tuple[p.Advertisement, str]] = {}
        self.running = False

    def _handle(self, e: BleScanResultEvent) -> None:
        try:
            adv = p.parse_advertisement(e.device_id, e.local_name or None, e.rssi, e.manufacturer_bytes)
        except Exception as ex:  # malformed adv
            log.debug("bad adv from %s: %s", e.device_id, ex)
            return
        if adv is None:
            return
        key = str(adv.device_uuid) if adv.device_uuid else e.device_id
        self.devices[key] = (adv, e.device_id)
        self._on_adv(adv, e.device_id)

    async def start(self) -> None:
        state = await self.hub.ble.adapter_state()
        if state == "off":
            await self.hub.ble.turn_on()
            for _ in range(20):
                await asyncio.sleep(0.25)
                if await self.hub.ble.adapter_state() == "on":
                    break
        # filter by CANDY HOUSE manufacturer id; the plugin requests runtime permissions here
        await self.hub.ble.start_scan(manufacturer_ids=[p.CANDY_HOUSE_COMPANY_ID], fine_location=True)
        self.running = True

    async def stop(self) -> None:
        if self.running:
            self.running = False
            try:
                await self.hub.ble.stop_scan()
            except Exception as e:
                log.warning("stop_scan: %s", e)


class FletBleConnection:
    def __init__(self, hub: FletBleHub, device_id: str, *, on_status=None, on_log=None,
                 on_disconnect: Optional[Callable[[], None]] = None, history_tag: str = "flet") -> None:
        self.hub = hub
        self.device_id = device_id
        self._on_disconnect = on_disconnect
        self._connected = False
        self.device = SesameDevice(self._write, on_status=on_status, on_log=on_log, history_tag=history_tag)

    async def _write(self, chunk: bytes) -> None:
        await self.hub.ble.write(self.device_id, p.SESAME_WRITE_CHAR_UUID, chunk, without_response=True)

    def _connection_state(self, connected: bool) -> None:
        was = self._connected
        self._connected = connected
        if was and not connected:
            self.device.logged_in = False
            if self._on_disconnect:
                self._on_disconnect()

    async def connect(self) -> None:
        ble = self.hub.ble
        await ble.connect(self.device_id, timeout_ms=20000)
        self._connected = True
        services = await ble.discover_services(self.device_id)
        chars = {c["uuid"].lower() for s in services for c in s.get("characteristics", [])}
        if p.SESAME_NOTIFY_CHAR_UUID not in chars or p.SESAME_WRITE_CHAR_UUID not in chars:
            await ble.disconnect(self.device_id)
            raise RuntimeError(f"Sesame characteristics not found (got {sorted(chars)})")
        await ble.set_notify(self.device_id, p.SESAME_NOTIFY_CHAR_UUID, True)
        await self.device.wait_for_token()

    async def disconnect(self) -> None:
        try:
            await self.hub.ble.disconnect(self.device_id)
        except Exception as e:
            log.warning("disconnect: %s", e)
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
