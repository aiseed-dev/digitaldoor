"""窓口の二つの道具(置きに行く道具 `digitalkey madoguchi push`、毎朝の一覧 `deploy/madoguchi/toiawase_daily.py`)の試しの下ごしらえ。

窓口仕様書 8、9、11.1、11.2 章だけを読んで書いた。相手は疑似の PocketBase(fake_pb.py)だけで、127.0.0.1 の空いた口に立てる。
外の網は使わない(この置き場の試しの間、127.0.0.1 と ::1 のほかへの接続は例外にする)。
識別とパスワードは、その場で作る乱数(fake_pb.add_kikai)。時刻は固定の値を使う。共に使う値と関数は madoguchi_common.py。
"""
from __future__ import annotations

import socket

import pytest

from fake_pb import FakePB
from madoguchi_common import ENV_NAMES, Creds, write_env


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    """外の網へ出さない。127.0.0.1、::1、localhost のほかへの接続は例外にする。"""
    real = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise OSError(f"試しは外の網を使わない: {host}")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture
def pb():
    """疑似の PocketBase を 127.0.0.1 の空いた口に立てる。"""
    with FakePB() as p:
        yield p


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME を試しの置き場にし、MADOGUCHI_* の環境変数を消す(手元の本物の env のファイルを読まない)。"""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for n in ENV_NAMES:
        monkeypatch.delenv(n, raising=False)
    return h


@pytest.fixture
def creds(pb, home, tmp_path):
    """置きに行く道具の kikai(役 shoukai)を疑似に作り、権限 600 の env のファイルに書く。"""
    ident, pw = pb.add_kikai("shoukai")
    env = write_env(tmp_path / "madoguchi.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": ident,
                                                  "MADOGUCHI_PASSWORD": pw})
    return Creds(env, ident, pw)


@pytest.fixture
def asa_creds(pb, home, tmp_path):
    """毎朝の一覧の kikai(役 asa)を疑似に作り、権限 600 の env のファイルに書く。"""
    ident, pw = pb.add_kikai("asa")
    env = write_env(tmp_path / "toiawase.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": ident,
                                                 "MADOGUCHI_PASSWORD": pw})
    return Creds(env, ident, pw)
