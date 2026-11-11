import pytest

from digitalkey.entrance.ledger import Ledger


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "vault", roles={"オーナー": {"経営者"}})


@pytest.fixture(autouse=True)
def isolated_key_files(tmp_path, monkeypatch):
    """どの試験も、鍵の置き場を一時のフォルダにする。本物の鍵のファイル(~/.config/digitalkey/sesame-keys.json)を読まない、作らない。"""
    monkeypatch.setenv("SESAME_KEYSTORE", str(tmp_path / "keys" / "sesame-keys.json"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield


@pytest.fixture(autouse=True)
def fresh_fake_devices():
    """疑似の機器はモジュールの中で状態を持つので、試験ごとに作り直す。"""
    from digitalkey_sesame import fake
    fake._FAKE_DEVICES.clear()
    yield
    fake._FAKE_DEVICES.clear()
