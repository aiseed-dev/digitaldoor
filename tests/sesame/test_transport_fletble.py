"""Unit test of the flet-ble transport with a stubbed FletBle service: verifies
event routing, hex framing and the connect sequence, without Flutter."""
import asyncio
import types

import pytest

from digitalkey.sesame import protocol as p
from digitalkey.sesame.fake import FakeSesame
from digitalkey.sesame import transport_fletble as t
from flet_ble import BleNotifyEvent, BleScanResultEvent, BleConnectionStateEvent


class StubBle:
    """Stands in for the FletBle service; drives a FakeSesame behind the GATT calls."""

    def __init__(self, fake: FakeSesame):
        self.fake = fake
        self.calls = []
        self.on_notify = None
        self.notify_enabled = False

    async def is_supported(self): return True
    async def adapter_state(self): return "on"
    async def turn_on(self): return True
    async def start_scan(self, **kw): self.calls.append(("start_scan", kw))
    async def stop_scan(self): self.calls.append(("stop_scan",))
    async def connect(self, device_id, timeout_ms=20000): self.calls.append(("connect", device_id))
    async def disconnect(self, device_id): self.calls.append(("disconnect", device_id))
    async def discover_services(self, device_id):
        return [{"uuid": p.SESAME_SERVICE_UUID, "characteristics": [
            {"uuid": p.SESAME_WRITE_CHAR_UUID.upper()}, {"uuid": p.SESAME_NOTIFY_CHAR_UUID}]}]
    async def set_notify(self, device_id, characteristic, enable=True):
        self.notify_enabled = enable
        # device publishes INITIAL once notifications are on
        self.fake.publish_initial()
        return True
    async def write(self, device_id, characteristic, data, without_response=True):
        assert characteristic == p.SESAME_WRITE_CHAR_UUID and without_response
        assert len(data) <= 20
        await self.fake.write(bytes(data))


def make_hub(stub):
    hub = t.FletBleHub.__new__(t.FletBleHub)
    hub.page = None
    hub.ble = stub
    hub.scanner = None
    hub.connection = None
    hub.on_error = None
    return hub


@pytest.mark.asyncio
async def test_scan_event_routing():
    stub = StubBle(FakeSesame())
    hub = make_hub(stub)
    seen = []
    scanner = hub.make_scanner(lambda adv, dev_id: seen.append((adv, dev_id)))
    await scanner.start()
    assert stub.calls[0][0] == "start_scan" and stub.calls[0][1]["manufacturer_ids"] == [0x055A]
    import uuid
    u = uuid.uuid4()
    hub._on_scan_result(BleScanResultEvent(name="scan_result", control=None, device_id="AA:BB",
                                           local_name="Sesame6Pro", rssi=-40,
                                           manufacturer_data={"1370": (bytes([21, 0, 0]) + u.bytes).hex()}))
    hub._on_scan_result(BleScanResultEvent(name="scan_result", control=None, device_id="CC:DD",
                                           local_name="other", rssi=-40, manufacturer_data={"76": "0011"}))
    assert len(seen) == 1
    adv, dev_id = seen[0]
    assert dev_id == "AA:BB" and adv.model_id == "sesame_6_pro" and adv.device_uuid == u and not adv.is_registered


@pytest.mark.asyncio
async def test_connect_register_unlock_via_stub():
    fake = FakeSesame()
    stub = StubBle(fake)
    hub = make_hub(stub)
    disconnected = []
    conn = hub.make_connection("AA:BB", history_tag="phone", on_disconnect=lambda: disconnected.append(1))
    # notifications from the fake go through the hub like Flutter events would
    fake.app = types.SimpleNamespace(on_notify=lambda chunk: hub._on_notify(
        BleNotifyEvent(name="notify", control=None, device_id="AA:BB",
                       characteristic=p.SESAME_NOTIFY_CHAR_UUID.upper(), value=chunk.hex())))
    await conn.connect()
    assert stub.notify_enabled and conn.device.session_token == fake.token
    secret = await conn.device.register()
    assert secret == fake.secret
    await conn.device.unlock()
    assert not fake.locked and conn.device.state == "unlocked"
    assert (p.ItemCode.UNLOCK, b"\x00\x0ephone") in fake.received
    hub._on_connection_state(BleConnectionStateEvent(name="connection_state", control=None,
                                                     device_id="AA:BB", connected=False))
    assert disconnected == [1] and not conn.is_connected and not conn.device.logged_in
