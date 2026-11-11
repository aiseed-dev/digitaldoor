import datetime as dt
import os
import sys

from kagi import app, audit, cli

FAKE = "AKIAEXAMPLEEXAMPLE12"  # 偽の形。実際の鍵ではない
KEY_BYTES = b"app-test-key-not-a-secret-0123"
TODAY = dt.date(2026, 11, 6)


def _setup(tmp_path):
    d = tmp_path / "k"
    d.mkdir()
    (d / "inventory.toml").write_text(
        '[[secret]]\nid = "old"\nkind = "token"\nwhere = "pc:~/x"\nholder = "h"\nrole = "r"\n'
        'expires = 2000-01-01\nrotate_days = 90\nlost = "l"\nleaked = "k"\n'
        '[[secret]]\nid = "near"\nkind = "key"\nwhere = "pc:~/y"\nholder = "h"\nrole = "r"\n'
        'expires = 2026-11-20\nrotate_days = 90\nlost = "l"\nleaked = "k"\n'
        '[[secret]]\nid = "bare"\nkind = "password"\nwhere = "paper"\nholder = "h"\nrole = "r"\n'
    )
    (d / "ports.toml").write_text('[[port]]\nhost = "h"\nport = 22\nbind = "127.0.0.1"\n')
    ss = tmp_path / "ss.txt"
    ss.write_text(
        'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'
        'LISTEN 0 128 127.0.0.1:5432 0.0.0.0:* users:(("postgres",pid=2,fd=3))\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(f"x = 1\nk = '{FAKE}'\n")
    keyf = tmp_path / "key"
    keyf.write_bytes(KEY_BYTES)
    os.chmod(keyf, 0o600)
    return d, ss, src, keyf


def _all_text(cards) -> str:
    return repr(cards)


def test_build_cards(tmp_path):
    d, ss, src, keyf = _setup(tmp_path)
    env = {"KAGI_LOG_KEY_FILE": str(keyf)}
    key = audit.find_key(env)
    audit.add(d / "audit.log", key, "t", "inventory.add", "old")
    audit.add(d / "audit.log", key, "t", "inventory.add", "near")

    c = app.build_cards(d, ss, [src], host="h", today=TODAY, env=env)
    assert c["dir"] == str(d)

    inv = c["inventory"]
    assert inv["error"] is None
    assert "件数 3" in inv["summary"] and "期限切れ 1" in inv["summary"]
    assert "30 日以内 1" in inv["summary"] and "抜け 4" in inv["summary"]
    assert ["old", "期限切れ", "2000-01-01 に切れた"] in inv["rows"]
    assert any(r[0] == "bare" and r[1] == "抜け" for r in inv["rows"])
    assert any(r[0] == "near" and r[1] == "期限が近い" for r in inv["rows"])

    prt = c["ports"]
    assert "表に無い口 1" in prt["summary"] and "外向きで表と違う口 1" in prt["summary"]
    assert ["表に無い口", "h", "5432", "127.0.0.1", "postgres"] in prt["rows"]
    assert any(r[0].startswith("外向きで表と違う") and r[2] == "22" for r in prt["rows"])

    scn = c["scan"]
    assert "見つかった 1" in scn["summary"]
    assert scn["lines"] == [f"{src / 'a.py'}:2  aws-access-key"]

    aud = c["audit"]
    assert aud["key"] is True and aud["summary"] == "ok 2 lines" and aud["failures"] == []

    text = _all_text(c)
    assert FAKE not in text
    assert KEY_BYTES.decode() not in text


def test_build_cards_without_inputs(tmp_path):
    d, _ss, _src, _keyf = _setup(tmp_path)
    c = app.build_cards(d, None, [], today=TODAY, env={})
    assert c["ports"]["summary"].startswith("突き合わせていない")
    assert c["ports"]["rows"] == []
    assert c["scan"]["summary"].startswith("走らせていない")
    assert c["audit"]["key"] is False
    assert "署名の鍵が無い" in c["audit"]["summary"]


def test_build_cards_audit_broken_and_value_refused(tmp_path):
    d, _ss, _src, keyf = _setup(tmp_path)
    env = {"KAGI_LOG_KEY_FILE": str(keyf)}
    key = audit.find_key(env)
    for i in range(3):
        audit.add(d / "audit.log", key, "t", "a", f"d{i}")
    lines = (d / "audit.log").read_text().splitlines()
    (d / "audit.log").write_text("\n".join([lines[0], lines[2]]) + "\n")
    (d / "inventory.toml").write_text('[[secret]]\nid = "s"\nvalue = "hunter2-not-a-secret"\n')

    c = app.build_cards(d, today=TODAY, env=env)
    assert c["audit"]["summary"].startswith("検証が落ちた")
    assert any(f.startswith("seq 3") for f in c["audit"]["failures"])
    assert "value の欄がある" in c["inventory"]["error"]
    assert "hunter2" not in _all_text(c)


def test_write_report(tmp_path):
    d, ss, src, _keyf = _setup(tmp_path)
    path, text = app.write_report(d, ss, [src], host="h", today=TODAY)
    assert path == d / "report-2026-11-06.md"
    assert path.read_text("utf-8") == text
    assert "# kagi の報告（2026-11-06）" in text and "aws-access-key: 1" in text
    assert FAKE not in text


def test_app_without_flet(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "flet", None)  # import flet が ImportError になる
    rc = cli.main(["app", "--dir", str(tmp_path)])
    err = capsys.readouterr().err.strip().splitlines()
    assert rc == 2
    assert len(err) == 1 and "pip install flet" in err[0]
