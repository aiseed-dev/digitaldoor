"""鍵を外に出さないこと(ログ・標準出力)と、鍵の書き出し・取り込みの往復の試験。

疑似の Sesame(fake.py、`--fake`)だけを使う。実機にも Bluetooth にも、本物の鍵の置き場(sesame-keys.json)にも触れない。
試験の鍵は、この中で作った値か、試験用の決まった値(本物ではない)。
"""
import asyncio
import json
import logging
import os
import stat

import pytest

from digitalkey_sesame import cli, fake
from digitalkey_sesame.device import SesameDevice
from digitalkey_sesame.fake import FakeSesame
from digitalkey_sesame.keystore import KeyStore, format_secret_hex, parse_secret_hex

UNREGISTERED = "11111111-2222-3333-4444-555555555501"   # 疑似の Sesame 6 Pro(未登録で始まる)
REGISTERED = "11111111-2222-3333-4444-555555555502"     # 疑似の Sesame 5(登録済み。鍵は 16 バイトの 0)
ZERO_KEY = "00" * 16
TEST_SECRET = "00112233445566778899aabbccddeeff"


@pytest.fixture(autouse=True)
def fresh_fakes():
    """疑似の機器はモジュールの中で状態を持つので、試験ごとに作り直す。"""
    fake._FAKE_DEVICES.clear()
    yield
    fake._FAKE_DEVICES.clear()


@pytest.fixture(autouse=True)
def no_admin_code(monkeypatch):
    """`import` の管理者コードの尋ね(getpass)は、特に指定しない試験では空(利用者として取り込む)。"""
    monkeypatch.setattr(cli, "read_admin_code", lambda: "")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """鍵の置き場を試験の一時のフォルダにする(疑似は sesame-keys-fake.json)。"""
    monkeypatch.setenv("SESAME_KEYSTORE", str(tmp_path / "sesame-keys.json"))
    return tmp_path


def fake_keys(home):
    path = home / "sesame-keys-fake.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def typed(monkeypatch, text):
    """伏せた入力(getpass)の代わりに text を返す。"""
    monkeypatch.setattr(cli, "read_secret", lambda: text)


# ---- 1. 鍵をログに出さない ----
async def test_register_and_login_never_log_the_key(caplog):
    """R-CM-12: 登録とログインの間、ログ欄への行にも logging にも鍵が現れない。"""
    caplog.set_level(logging.DEBUG)
    lines = []
    sim = FakeSesame()
    dev = SesameDevice(sim.write, on_log=lines.append)
    sim.app = dev
    sim.publish_initial()
    secret = await dev.register()
    assert secret == sim.secret and lines
    again = FakeSesame(registered_secret=secret)
    dev2 = SesameDevice(again.write, on_log=lines.append)
    again.app = dev2
    again.publish_initial()
    await dev2.login(secret)
    seen = "\n".join(lines) + "\n" + caplog.text
    assert secret.hex() not in seen.lower() and secret.hex().upper() not in seen
    assert "registered" in seen and "logged in" in seen


async def test_wrong_key_error_does_not_contain_the_key():
    """R-CM-12: ログインに失敗した例外の文にも、試した鍵が入らない。"""
    sim = FakeSesame(registered_secret=bytes(16))
    dev = SesameDevice(sim.write)
    sim.app = dev
    sim.publish_initial()
    wrong = bytes.fromhex(TEST_SECRET)
    with pytest.raises(Exception) as ei:
        await dev.login(wrong)
    assert TEST_SECRET not in str(ei.value).lower()


def test_cli_register_and_keys_do_not_print_the_key(home, capsys):
    """R-CM-12: `register` と `keys` の標準出力・標準エラーに鍵が現れない(鍵は置き場のファイルにだけある)。"""
    assert cli.main(["--fake", "register", UNREGISTERED, "--name", "玄関"]) == 0
    stored = fake_keys(home)[UNREGISTERED]["secret_hex"]
    assert len(stored) == 32
    assert cli.main(["--fake", "keys"]) == 0
    out = capsys.readouterr()
    assert stored not in (out.out + out.err).lower()
    assert UNREGISTERED in out.out and "玄関" in out.out and "管理者" in out.out


# ---- 2. 形の確かめ ----
def test_parse_and_format_secret_round_trip():
    """R-CM-13: 書き出しの表示(4 桁ずつの空白区切り)をそのまま取り込める。形の違う入力は鍵を含まない文で断る。"""
    shown = format_secret_hex(TEST_SECRET)
    assert shown == "0011 2233 4455 6677 8899 aabb ccdd eeff"
    assert parse_secret_hex(shown) == bytes.fromhex(TEST_SECRET)
    assert parse_secret_hex("  " + TEST_SECRET.upper() + "\n") == bytes.fromhex(TEST_SECRET)
    assert parse_secret_hex("00112233-44556677-8899aabb-ccddeeff") == bytes.fromhex(TEST_SECRET)
    for bad in ("", "xyz", TEST_SECRET[:-1], TEST_SECRET + "0", "g" + TEST_SECRET[1:], None):
        with pytest.raises(ValueError) as ei:
            parse_secret_hex(bad)
        assert "32 桁" in str(ei.value)
        if bad:
            assert bad not in str(ei.value)


# ---- 3. 往復 ----
def test_export_then_import_round_trip(home, tmp_path, monkeypatch, capsys):
    """R-CM-13: 書き出した 32 桁を別の鍵の置き場へ取り込める。ログインを確かめ、機種を広告から取り、権限 600 で保存する。"""
    # 書き出す側(スマートフォンのアプリ相当): 疑似の Sesame に登録し、保存した鍵の表示を取る
    assert cli.main(["--fake", "register", UNREGISTERED]) == 0
    key = asyncio.run(KeyStore(path=home / "sesame-keys-fake.json").get(UNREGISTERED))
    shown = format_secret_hex(key.secret_hex)
    capsys.readouterr()
    # 取り込む側(miniPC 相当): 別の置き場
    other = tmp_path / "minipc"
    other.mkdir()
    monkeypatch.setenv("SESAME_KEYSTORE", str(other / "sesame-keys.json"))
    typed(monkeypatch, shown)
    assert cli.main(["--fake", "import", UNREGISTERED, "--name", "実演の錠"]) == 0
    out = capsys.readouterr()
    assert key.secret_hex not in (out.out + out.err).lower()
    saved = json.loads((other / "sesame-keys-fake.json").read_text(encoding="utf-8"))[UNREGISTERED]
    assert saved["secret_hex"] == key.secret_hex
    assert saved["model_id"] == "sesame_6_pro" and saved["name"] == "実演の錠"
    assert stat.S_IMODE(os.stat(other / "sesame-keys-fake.json").st_mode) == 0o600
    # 取り込んだ鍵で、疑似の Sesame を実際に動かせる
    assert cli.main(["--fake", "unlock", UNREGISTERED]) == 0
    assert fake._FAKE_DEVICES[UNREGISTERED].locked is False


def test_import_replaces_an_existing_key_after_login(home, monkeypatch, capsys):
    """R-CM-13: 既にある鍵は、新しい鍵でログインできたときだけ置き換える。"""
    typed(monkeypatch, ZERO_KEY)
    assert cli.main(["--fake", "import", REGISTERED]) == 0
    assert "置き換え" not in capsys.readouterr().out
    assert cli.main(["--fake", "import", REGISTERED, "--name", "二回目"]) == 0
    assert "置き換え" in capsys.readouterr().out
    assert fake_keys(home)[REGISTERED]["name"] == "二回目"


# ---- 4. 保存しない場合 ----
def test_wrong_key_is_not_saved(home, monkeypatch, capsys):
    """R-CM-13: 間違った鍵(ログインが通らない)は保存しない。鍵を画面に出さない。"""
    typed(monkeypatch, TEST_SECRET)
    assert cli.main(["--fake", "import", REGISTERED]) == 1
    out = capsys.readouterr()
    assert "保存しません" in out.err
    assert TEST_SECRET not in (out.out + out.err).lower()
    assert not (home / "sesame-keys-fake.json").exists()


@pytest.mark.parametrize("bad", ["", "xyz", TEST_SECRET[:-2], TEST_SECRET + "00", "zz" + TEST_SECRET[2:]])
def test_malformed_key_is_refused_before_any_connection(home, monkeypatch, capsys, bad):
    """R-CM-13: 32 桁の 16 進でない入力は、機器を探す前に断る。何も保存しない。"""
    async def never(*a, **k):
        raise AssertionError("形が違うのに機器を探した")
    monkeypatch.setattr(fake, "fake_scan_once", never)
    typed(monkeypatch, bad)
    assert cli.main(["--fake", "import", REGISTERED]) == 1
    assert "32 桁" in capsys.readouterr().err
    assert not (home / "sesame-keys-fake.json").exists()


def test_unregistered_sesame_is_refused(home, monkeypatch, capsys):
    """R-CM-13: 未登録の Sesame には取り込めない(鍵が無い)。register を案内して、何も保存しない。"""
    typed(monkeypatch, ZERO_KEY)
    assert cli.main(["--fake", "import", UNREGISTERED]) == 1
    assert "register" in capsys.readouterr().err
    assert not (home / "sesame-keys-fake.json").exists()


def test_unknown_sesame_is_refused(home, monkeypatch, capsys):
    """R-CM-13: 見つからない Sesame には何も保存しない。"""
    typed(monkeypatch, ZERO_KEY)
    assert cli.main(["--fake", "import", "99999999-0000-0000-0000-000000000000"]) == 2
    assert not (home / "sesame-keys-fake.json").exists()


def test_import_takes_the_key_from_a_hidden_prompt_not_the_arguments(monkeypatch):
    """R-CM-13: 鍵は引数では受けず、getpass で受ける。引数に鍵を書いても受け付けない。"""
    with pytest.raises(SystemExit):
        cli.main(["--fake", "import", REGISTERED, TEST_SECRET])
    asked = []
    monkeypatch.setattr(cli, "getpass", lambda prompt="": asked.append(prompt) or ZERO_KEY)
    assert cli.read_secret() == ZERO_KEY and asked and "画面に出ません" in asked[0]
