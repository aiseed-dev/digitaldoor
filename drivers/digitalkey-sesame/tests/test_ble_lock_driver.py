"""登録簿の `sesame:<UUID>`(SesameLock)を、BLE の層だけ疑似に差し替えて通す試験。

実機にも Bluetooth にも本物の鍵の置き場にも触れない。鍵の置き場は試験の一時のフォルダ。
"""
import pytest

from digitalkey.locks import make_lock
from digitalkey_sesame import fake, transport_bleak
from digitalkey_sesame.keystore import DeviceKey, KeyStore

SESAME = "11111111-2222-3333-4444-555555555502"   # 疑似の Sesame 5(登録済み。鍵は 16 バイトの 0)


@pytest.fixture(autouse=True)
def fake_ble(monkeypatch):
    fake._FAKE_DEVICES.clear()
    monkeypatch.setattr(transport_bleak.SesameScanner, "scan_once", staticmethod(fake.fake_scan_once))
    monkeypatch.setattr(transport_bleak, "BleakSesameConnection", fake.FakeConnection)
    yield
    fake._FAKE_DEVICES.clear()


async def test_registry_lock_unlocks_and_locks_with_the_saved_key(tmp_path):
    """R-CT-04: 登録簿の sesame:<UUID> は、鍵の置き場の鍵でログインして解錠・施錠する(BLE の層は差し替え)。"""
    store = tmp_path / "keys.json"
    await KeyStore(path=store).put(DeviceKey(SESAME, "sesame_5", "00" * 16, name="玄関"))
    lock = make_lock("genkan", f"sesame:{SESAME}")
    lock.keystore_path = str(store)
    await lock.unlock()
    assert fake._FAKE_DEVICES[SESAME].locked is False
    await lock.lock()
    assert fake._FAKE_DEVICES[SESAME].locked is True


async def test_registry_lock_without_a_saved_key_refuses(tmp_path):
    """R-CT-04: 鍵が置き場に無いときは、何を先にするかを添えて断る。"""
    lock = make_lock("genkan", f"sesame:{SESAME}")
    lock.keystore_path = str(tmp_path / "none.json")
    with pytest.raises(RuntimeError, match="register"):
        await lock.unlock()


async def test_registry_lock_with_a_wrong_key_does_not_move(tmp_path):
    """R-CT-04: 鍵が違えばログインが通らず、錠は動かない。"""
    store = tmp_path / "keys.json"
    await KeyStore(path=store).put(DeviceKey(SESAME, "sesame_5", "11" * 16))
    lock = make_lock("genkan", f"sesame:{SESAME}")
    lock.keystore_path = str(store)
    with pytest.raises(Exception, match="login failed"):
        await lock.unlock()
    assert fake._FAKE_DEVICES[SESAME].locked is True
