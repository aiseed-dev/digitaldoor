from kagi import cli, smb

# 見本は偽物。番地は文書用の範囲（192.0.2.0/24、198.51.100.0/24）だけ。
FAKE_PW = "not-a-real-pw-Zq81"

GOOD = """\
# 良い形の見本
[ Global ]
   Server Role = standalone server
   server   min protocol = SMB2_10
   SERVER SIGNING = Mandatory
   map to guest = Never
   interfaces = lo eth0 192.0.2.10/24
   bind interfaces only = yes
   passdb backend = tdbsam:/var/lib/samba/private/passdb.tdb
; include は辿らない
   include = /etc/samba/extra.conf

[keiri]
   path = /srv/share/keiri
   valid users = @keiri
   write list = @keiri
   guest ok = no

[homes]
   valid users = %S
   browseable = no
"""

BAD = f"""\
[global]
   server role = member server
   server signing = auto
   map to guest = Bad User
   interfaces = 0.0.0.0/0
   ldap admin password = {FAKE_PW}
   smb passwd file = /etc/samba/smbpasswd

[public]
   path = /srv/public
   public = yes
   valid users = tanaka suzuki
   write list = tanaka
"""


def test_parse_normalizes_keys_and_comments():
    conf = smb.parse(GOOD)
    assert conf.get("global", "serverminprotocol") == "SMB2_10"
    assert conf.get("global", "serversigning") == "Mandatory"
    assert conf.shares() == ["keiri", "homes"]
    assert conf.includes == [("global", 11)]
    assert "include" not in conf.sections["global"]


def test_good(tmp_path, capsys):
    f = tmp_path / "smb.conf"
    f.write_text(GOOD)
    findings = smb.check(smb.load(f))
    assert smb.fixes(findings) == 0
    rc = cli.main(["check", "smb", "--conf", str(f)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "直す物は無い" in out
    assert "standalone server" in out
    assert "tdbsam:/var/lib/samba/private/passdb.tdb" in out
    assert "辿っていない" in out


def test_bad(tmp_path, capsys):
    f = tmp_path / "smb.conf"
    f.write_text(BAD)
    rc = cli.main(["check", "smb", "--conf", str(f)])
    out = capsys.readouterr().out
    assert rc == 1
    for want in (
        "SMB1 が通る",
        "署名が任意",
        "ゲストが入れる（Never でない）",
        "| 直す | public | guest ok / public | ゲストが入れる |",
        "全部の口に聞く",
        "| 直す | public | valid users | 個人で付けている",
        "| 直す | public | write list | 個人で付けている",
        "member server",
        "/etc/samba/smbpasswd",
        "password を含む行（6 行目）",
    ):
        assert want in out, want
    assert FAKE_PW not in out
    assert "tanaka" not in out


def test_outward_interfaces_with_bind_only():
    conf = smb.parse("[global]\ninterfaces = eth0 198.51.100.5/24 0.0.0.0/0\nbind interfaces only = yes\n")
    got = [f for f in smb.check(conf) if f.what == "interfaces"]
    assert len(got) == 1 and "1 個" in got[0].result


def test_unreadable(tmp_path, capsys):
    assert cli.main(["check", "smb", "--conf", str(tmp_path / "none.conf")]) == 2


def test_report_smb(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("KAGI_LOG_KEY_FILE", raising=False)
    cli.main(["init", "--dir", str(tmp_path)])
    f = tmp_path / "smb.conf"
    f.write_text(BAD)
    capsys.readouterr()
    assert cli.main(["report", "--dir", str(tmp_path), "--smb", str(f)]) == 0
    out = capsys.readouterr().out
    assert "Samba（smb.conf）の直す物 7" in out
    assert FAKE_PW not in out
    assert cli.main(["report", "--dir", str(tmp_path)]) == 0
    assert "Samba は見ていない" in capsys.readouterr().out
