import datetime as dt

from kagi import cli, inventory

TODAY = dt.date(2026, 11, 6)

FULL = """
[[secret]]
id = "a"
kind = "token"
where = "web1:/etc/x"
holder = "h"
role = "r"
expires = {exp}
rotate_days = 90
lost = "l"
leaked = "k"
"""


def write(tmp_path, body):
    (tmp_path / "inventory.toml").write_text(body, "utf-8")
    return tmp_path / "inventory.toml"


def test_ok(tmp_path):
    res = inventory.check(inventory.load(write(tmp_path, FULL.format(exp="2027-06-01"))), 30, TODAY)
    assert res.count == 1 and res.findings == [] and not res.failed


def test_missing_required_and_wanted(tmp_path):
    p = write(tmp_path, '[[secret]]\nid = "b"\nkind = "key"\n')
    res = inventory.check(inventory.load(p), 30, TODAY)
    missing = {f.detail.split()[0] for f in res.by("missing")}
    assert {"where", "holder", "role", "expires", "rotate_days", "lost", "leaked"} <= missing
    assert res.failed


def test_expired(tmp_path):
    res = inventory.check(inventory.load(write(tmp_path, FULL.format(exp="2026-11-01"))), 30, TODAY)
    assert [f.id for f in res.by("expired")] == ["a"] and res.failed


def test_soon_within_days(tmp_path):
    p = write(tmp_path, FULL.format(exp="2026-11-20"))
    res = inventory.check(inventory.load(p), 30, TODAY)
    assert [f.id for f in res.by("soon")] == ["a"]
    assert not res.failed  # 近いだけでは 1 にしない
    assert inventory.check(inventory.load(p), 7, TODAY).by("soon") == []


def test_value_field_refused(tmp_path, capsys):
    fake = "not-a-real-secret-0000"
    write(tmp_path, FULL.format(exp="2027-06-01") + f'value = "{fake}"\n')
    assert cli.main(["check", "inventory", "--dir", str(tmp_path)]) == 2
    out = capsys.readouterr()
    assert "value" in out.err
    assert fake not in out.out + out.err


def test_cli_exit_codes(tmp_path, capsys):
    write(tmp_path, FULL.format(exp="2000-01-01"))
    assert cli.main(["check", "inventory", "--dir", str(tmp_path)]) == 1
    assert "期限切れ" in capsys.readouterr().out
    write(tmp_path, FULL.format(exp="2999-01-01"))
    assert cli.main(["check", "inventory", "--dir", str(tmp_path)]) == 0


def test_init_template_checks_clean(tmp_path, capsys):
    d = tmp_path / "k"
    assert cli.main(["init", "--dir", str(d)]) == 0
    assert {p.name for p in d.iterdir()} == {"inventory.toml", "ports.toml", "machines.toml", "audit.log"}
    assert (d / "audit.log").read_text() == ""
    assert cli.main(["check", "inventory", "--dir", str(d)]) == 0
    # 二度目は触らない
    (d / "inventory.toml").write_text("# mine\n")
    assert cli.main(["--dir", str(d), "init"]) == 0
    assert (d / "inventory.toml").read_text() == "# mine\n"
