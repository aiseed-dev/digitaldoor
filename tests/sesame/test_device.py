"""End-to-end session test against an in-process fake Sesame (device side of
the same protocol), covering register -> encrypted commands and login."""
import asyncio
import struct
import time

import pytest

from digitalkey.sesame import protocol as p
from digitalkey.sesame.device import SesameDevice, SesameError
from digitalkey.sesame.fake import FakeSesame


@pytest.mark.asyncio
async def test_register_then_commands():
    fake = FakeSesame()
    statuses = []
    dev = SesameDevice(fake.write, on_status=lambda d: statuses.append(d.state), history_tag="pytest")
    fake.app = dev
    fake.publish_initial()
    secret = await dev.register()
    assert secret == fake.secret and dev.logged_in and dev.state == "locked"
    await dev.unlock()
    assert dev.state == "unlocked" and not fake.locked
    await dev.toggle()
    assert dev.state == "locked" and fake.locked
    assert await dev.get_version() == "3.0-6-abcdef"
    assert (p.ItemCode.UNLOCK, b"\x00\x0epytest") in fake.received


@pytest.mark.asyncio
async def test_login_with_saved_secret_and_time_sync():
    secret = bytes(range(16))
    fake = FakeSesame(registered_secret=secret)
    dev = SesameDevice(fake.write)
    fake.app = dev
    fake.publish_initial()
    await dev.login(secret)
    assert dev.logged_in
    # device clock was 100s behind -> TIME command sent, encrypted
    assert any(item == p.ItemCode.TIME for item, _ in fake.received)
    await dev.lock()
    assert fake.locked


@pytest.mark.asyncio
async def test_login_wrong_secret():
    fake = FakeSesame(registered_secret=bytes(16))
    dev = SesameDevice(fake.write)
    fake.app = dev
    fake.publish_initial()
    with pytest.raises(SesameError, match="login failed"):
        await dev.login(b"\x01" * 16)
    assert not dev.logged_in


@pytest.mark.asyncio
async def test_register_already_registered():
    fake = FakeSesame(registered_secret=bytes(16))
    dev = SesameDevice(fake.write)
    fake.app = dev
    fake.publish_initial()
    with pytest.raises(SesameError, match="registration refused"):
        await dev.register()


@pytest.mark.asyncio
async def test_no_token_timeout():
    async def write(_):
        pass
    dev = SesameDevice(write)
    with pytest.raises(SesameError, match="INITIAL"):
        await dev.wait_for_token(timeout=0.05)


@pytest.mark.asyncio
async def test_keystore_roundtrip(tmp_path):
    from digitalkey.sesame.keystore import KeyStore, DeviceKey
    ks = KeyStore(path=tmp_path / "k.json")
    await ks.put(DeviceKey(device_uuid="AABB", model_id="sesame_6_pro", secret_hex="00" * 16, name="door"))
    ks2 = KeyStore(path=tmp_path / "k.json")
    k = await ks2.get("aabb")
    assert k and k.secret == bytes(16) and k.name == "door"
    await ks2.remove("AABB")
    assert await KeyStore(path=tmp_path / "k.json").get("aabb") is None
