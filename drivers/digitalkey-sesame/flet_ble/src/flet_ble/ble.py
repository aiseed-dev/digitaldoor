"""Python side of the flet-ble extension (see ../../flutter/flet_ble/lib/src/ble.dart)."""

from dataclasses import dataclass, field
from typing import Any, Optional

import flet as ft

__all__ = ["FletBle", "BleScanResultEvent", "BleNotifyEvent", "BleConnectionStateEvent"]


@dataclass
class BleScanResultEvent(ft.Event["FletBle"]):
    """One advertisement seen while scanning."""

    device_id: str
    local_name: str = ""
    rssi: int = 0
    connectable: bool = True
    manufacturer_data: dict[str, str] = field(default_factory=dict)  # company id (decimal str) -> hex
    service_uuids: list[str] = field(default_factory=list)

    @property
    def manufacturer_bytes(self) -> dict[int, bytes]:
        return {int(k): bytes.fromhex(v) for k, v in (self.manufacturer_data or {}).items()}


@dataclass
class BleNotifyEvent(ft.Event["FletBle"]):
    """A notification/indication received from a characteristic."""

    device_id: str
    characteristic: str
    value: str = ""  # hex

    @property
    def bytes(self) -> bytes:
        return bytes.fromhex(self.value or "")


@dataclass
class BleConnectionStateEvent(ft.Event["FletBle"]):
    device_id: str
    connected: bool = False


@ft.control("FletBle")
class FletBle(ft.Service):
    """Minimal BLE central (GATT client) service backed by flutter_blue_plus.

    Add an instance to ``page.services`` and set the ``on_*`` handlers *before*
    adding it (the Flutter side only forwards events that have a handler).
    """

    on_scan_result: Optional[ft.EventHandler[BleScanResultEvent]] = None
    on_notify: Optional[ft.EventHandler[BleNotifyEvent]] = None
    on_connection_state: Optional[ft.EventHandler[BleConnectionStateEvent]] = None
    on_error: Optional[ft.ControlEventHandler["FletBle"]] = None

    # ------------------------------------------------------------ adapter
    async def is_supported(self) -> bool:
        return bool(await self._invoke_method("is_supported"))

    async def adapter_state(self) -> str:
        """'on', 'off', 'unauthorized', 'unavailable', 'turningOn', 'turningOff', 'unknown'."""
        return str(await self._invoke_method("adapter_state"))

    async def turn_on(self) -> bool:
        return bool(await self._invoke_method("turn_on"))

    # --------------------------------------------------------------- scan
    async def start_scan(self, services: Optional[list[str]] = None,
                         manufacturer_ids: Optional[list[int]] = None,
                         timeout_ms: Optional[int] = None, fine_location: bool = True) -> None:
        await self._invoke_method("start_scan", {
            "services": services or [],
            "manufacturer_ids": [int(m) for m in (manufacturer_ids or [])],
            "timeout_ms": timeout_ms,
            "fine_location": fine_location,
        }, timeout=30)

    async def stop_scan(self) -> None:
        await self._invoke_method("stop_scan")

    # ------------------------------------------------------------ connect
    async def connect(self, device_id: str, timeout_ms: int = 20000) -> None:
        await self._invoke_method("connect", {"device_id": device_id, "timeout_ms": timeout_ms},
                                  timeout=timeout_ms / 1000 + 5)

    async def disconnect(self, device_id: str) -> None:
        await self._invoke_method("disconnect", {"device_id": device_id})

    async def is_connected(self, device_id: str) -> bool:
        return bool(await self._invoke_method("is_connected", {"device_id": device_id}))

    async def discover_services(self, device_id: str) -> list[dict[str, Any]]:
        return await self._invoke_method("discover_services", {"device_id": device_id}, timeout=30)

    async def request_mtu(self, device_id: str, mtu: int) -> int:
        return int(await self._invoke_method("request_mtu", {"device_id": device_id, "mtu": mtu}))

    async def mtu(self, device_id: str) -> int:
        return int(await self._invoke_method("mtu", {"device_id": device_id}))

    # --------------------------------------------------------------- gatt
    async def write(self, device_id: str, characteristic: str, data: bytes,
                    without_response: bool = True) -> None:
        await self._invoke_method("write", {
            "device_id": device_id,
            "characteristic": characteristic,
            "data": bytes(data).hex(),
            "without_response": without_response,
        }, timeout=20)

    async def read(self, device_id: str, characteristic: str) -> bytes:
        r = await self._invoke_method("read", {"device_id": device_id, "characteristic": characteristic},
                                      timeout=20)
        return bytes.fromhex(r or "")

    async def set_notify(self, device_id: str, characteristic: str, enable: bool = True) -> bool:
        return bool(await self._invoke_method("set_notify", {
            "device_id": device_id, "characteristic": characteristic, "enable": enable,
        }, timeout=20))
