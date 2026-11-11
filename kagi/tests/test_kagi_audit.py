import json
import os

import pytest

from kagi import audit, cli


@pytest.fixture
def key(tmp_path, monkeypatch):
    p = tmp_path / "log-key"
    p.write_bytes(b"test-only-key-not-a-secret-000001")
    os.chmod(p, 0o600)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("KAGI_LOG_KEY_FILE", str(p))
    return p


def add3(d):
    for i in range(3):
        assert (
            cli.main(
                [
                    "log",
                    "add",
                    "--dir",
                    str(d),
                    "--actor",
                    "t",
                    "--action",
                    "inventory.add",
                    "--detail",
                    f"item-{i}",
                ]
            )
            == 0
        )


def test_add_then_verify_ok(tmp_path, key, capsys):
    add3(tmp_path)
    out = capsys.readouterr().out.split()
    assert out == ["1", "2", "3"]
    assert cli.main(["log", "verify", "--dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "ok 3 lines"
    first = json.loads((tmp_path / "audit.log").read_text().splitlines()[0])
    assert first["prev"] == "0" * 64


def test_delete_line_fails(tmp_path, key, capsys):
    add3(tmp_path)
    log = tmp_path / "audit.log"
    lines = log.read_text().splitlines()
    log.write_text(lines[0] + "\n" + lines[2] + "\n")
    capsys.readouterr()
    assert cli.main(["log", "verify", "--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "seq 3" in out and "連番" in out


def test_change_one_char_fails(tmp_path, key, capsys):
    add3(tmp_path)
    log = tmp_path / "audit.log"
    log.write_text(log.read_text().replace("item-1", "item-X"))
    capsys.readouterr()
    assert cli.main(["log", "verify", "--dir", str(tmp_path)]) == 1
    assert "seq 2: hash" in capsys.readouterr().out


def test_other_key_fails(tmp_path, key, capsys):
    add3(tmp_path)
    key.write_bytes(b"another-test-only-key-not-secret")
    capsys.readouterr()
    assert cli.main(["log", "verify", "--dir", str(tmp_path)]) == 1
    assert "sig" in capsys.readouterr().out


def test_no_key_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("KAGI_LOG_KEY_FILE", raising=False)
    assert (
        cli.main(["log", "add", "--dir", str(tmp_path), "--actor", "a", "--action", "b", "--detail", "c"])
        == 2
    )
    assert cli.main(["log", "verify", "--dir", str(tmp_path)]) == 2
    assert "KAGI_LOG_KEY_FILE" in capsys.readouterr().err
    assert not (tmp_path / "audit.log").exists()


def test_credentials_directory_first(tmp_path, key, monkeypatch):
    cred = tmp_path / "cred"
    cred.mkdir()
    (cred / "kagi-log-key").write_bytes(b"credential-dir-test-key-0000")
    os.chmod(cred / "kagi-log-key", 0o600)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(cred))
    assert audit.find_key().path == cred / "kagi-log-key"


def test_key_never_printed(tmp_path, key, capsys):
    os.chmod(key, 0o644)
    add3(tmp_path)
    cli.main(["log", "verify", "--dir", str(tmp_path)])
    out = capsys.readouterr()
    assert "644" in out.err  # 許可の警告
    secret = key.read_bytes().decode()
    assert secret not in out.out + out.err
    assert secret not in (tmp_path / "audit.log").read_text()
    assert secret not in repr(audit.find_key())
