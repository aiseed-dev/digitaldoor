import base64
import datetime as dt
import hashlib
import os
import shutil
import subprocess

import pytest

from kagi import app, audit, cli, install, machine, report
from kagi.inventory import Refused

# 見本は全部偽物。鍵は公開鍵の形だけ（中身は決まった字の並びで、対になる秘密鍵は無い）。
NOW = dt.datetime(2026, 11, 7, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


def _s(b: bytes) -> bytes:
    return len(b).to_bytes(4, "big") + b


def pub_ed(seed: int, comment: str = "someone@pc") -> str:
    blob = _s(b"ssh-ed25519") + _s(bytes([seed]) * 32)
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}"


def pub_rsa(bits: int) -> str:
    n = (1 << (bits - 1)) | 1
    blob = _s(b"ssh-rsa") + _s(b"\x01\x00\x01") + _s(b"\x00" + n.to_bytes((bits + 7) // 8, "big"))
    return f"ssh-rsa {base64.b64encode(blob).decode()} old@pc"


def fp_of(line: str) -> str:
    blob = base64.b64decode(line.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


KEY_A, KEY_B, HOSTKEY = pub_ed(1), pub_ed(2), pub_ed(9)
FP_A, FP_B, FP_HOST = fp_of(KEY_A), fp_of(KEY_B), fp_of(HOSTKEY)


def table_text(pub_path: str, hostkey: str = FP_HOST, extra: str = "") -> str:
    return f"""\
[[machine]]
host = "box"
ssh = "box"
role = "見本のサーバー"
os = "debian-13"
users = ["aoki"]
admins = ["aoki"]
keys = ["k-a"]
hostkey = "{hostkey}"
{extra}
[[key]]
id = "k-a"
holder = "someone"
pub = "{pub_path}"
"""


def facts_text(**over) -> str:
    """良い形のコピー。over で節を差し替える。"""
    sec = {
        "collected": "2026-11-07T08:00:00+0900\nuid=1000",
        "os": 'ID=debian\nVERSION_ID="13"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nKERNEL=6.12.0\nHOSTNAME=box',
        "reboot": "no",
        "updates": "manager=apt\nnow=1794000000\nlists=1793990000",
        "auto": 'status=install ok installed\nAPT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";',
        "sshd": "effective=0\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nAllowUsers aoki",
        "hostkeys": f"256 {FP_HOST} (ED25519)\n3072 SHA256:{'r' * 43} (RSA)",
        "authorized": f"aoki 256 {FP_A} (ED25519)",
        "users": "aoki /bin/bash",
        "admins": "sudo aoki",
        "accounts": "",
        "sudoers": "",
        "firewall": "ufw=active\nnftables=inactive\nfirewalld=inactive\nENABLED=yes",
        "listen": 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))',
        "time": "NTP=yes\nNTPSynchronized=yes\nTimezone=Asia/Tokyo",
        "failed": "",
        "disk": "/ 41%",
        "crypt": "crypt=1",
    }
    sec.update(over)
    return machine.HEAD + "\n" + "".join(f"\n## kagi {k}\n{v}\n" for k, v in sec.items())


PORTS = [{"host": "box", "port": 22, "proto": "tcp", "bind": "0.0.0.0", "service": "ssh"}]


@pytest.fixture
def tbl(tmp_path):
    pub = tmp_path / "a.pub"
    pub.write_text(KEY_A + "\n")
    return machine.parse_table(table_text(str(pub)))


def run_check(tbl, text, ports_table=PORTS, ids=frozenset({"k-a"})):
    return machine.check(tbl, "box", machine.parse_facts(text), ports_table, set(ids), NOW)


def whats(findings, fix=True):
    return [(f.what, f.result) for f in findings if f.fix == fix]


# ---- 鍵 ----


def test_key_info_matches_ssh_keygen_form():
    info = machine.key_info(KEY_A)
    assert info.type == "ssh-ed25519" and info.fingerprint == FP_A and info.bits is None
    assert machine.FP.match(info.fingerprint)
    assert machine.key_info(pub_rsa(2048)).bits == 2048
    # authorized_keys の前置きの options があっても読む
    assert machine.key_info('from="192.0.2.1",no-pty ' + KEY_A).fingerprint == FP_A


def test_private_key_is_refused_unread(tmp_path):
    with pytest.raises(Refused, match="秘密鍵は読まない"):
        machine.key_info("-----BEGIN OPENSSH PRIVATE KEY-----")
    p = tmp_path / "id_ed25519"
    p.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nnot-a-real-key\n")
    with pytest.raises(Refused, match=r"\.pub"):
        machine.read_pub(p)  # 名で拒む。中身は開かない
    with pytest.raises(Refused, match="公開鍵として読めない"):
        machine.key_info("ssh-ed25519 !!!! x")


def test_table_refuses_value_fields_and_bad_names(tmp_path):
    with pytest.raises(Refused, match="K-13"):
        machine.parse_table('[[key]]\nid = "k"\nfingerprint = "' + FP_A + '"\nprivate = "x"\n')
    with pytest.raises(Refused, match="K-13"):
        machine.parse_table('[[machine]]\nhost = "a"\npassword = "x"\n')
    with pytest.raises(Refused, match="host"):
        machine.parse_table('[[machine]]\nhost = "-oProxyCommand=x"\n')
    with pytest.raises(Refused, match="ssh"):
        machine.parse_table('[[machine]]\nhost = "a"\nssh = "-oProxyCommand=x"\n')
    with pytest.raises(Refused, match="hostkey"):
        machine.parse_table('[[machine]]\nhost = "a"\nhostkey = "abc"\n')
    with pytest.raises(Refused, match="重複"):
        machine.parse_table('[[machine]]\nhost = "a"\n[[machine]]\nhost = "a"\n')


# ---- コピーと突き合わせ ----


def test_good_machine_has_nothing_to_fix(tbl):
    findings = run_check(tbl, facts_text())
    assert whats(findings) == []
    seen = whats(findings, fix=False)
    assert ("認証（ssh）", "鍵でだけ入る形。設定のファイルから（Match の中は見ていない）") in seen
    assert any(w == "コピー" and "root でない" in r for w, r in seen)
    assert any(w == "コピー" and "他の人のポートのプロセスの名" in r for w, r in seen)


def test_auth_findings(tbl):
    f = run_check(
        tbl,
        facts_text(
            sshd="effective=1\npasswordauthentication yes\npermitrootlogin yes\nkbdinteractiveauthentication yes"
        ),
    )
    res = [r for w, r in whats(f) if w == "認証（ssh）"]
    assert any("パスワードで入れる" in r and "実際に効いている値" in r for r in res)
    assert any("root がパスワードで入れる" in r for r in res)
    assert any("尋ねて答える形" in r for r in res)
    # 書いていなければ既定（yes）として直す物に
    f = run_check(tbl, facts_text(sshd="effective=0\nPermitRootLogin no"))
    assert any("書いていない。既定は yes" in r for _w, r in whats(f))
    # 最初に出た値が効く（drop-in が先）
    f = run_check(
        tbl,
        facts_text(
            sshd="effective=0\nPasswordAuthentication no\nPasswordAuthentication yes\nPermitRootLogin no"
        ),
    )
    assert not [r for w, r in whats(f) if w == "認証（ssh）"]


def test_key_findings(tbl):
    extra = f"aoki 256 {FP_A} (ED25519)\naoki 256 {FP_B} (ED25519)\naoki 1024 SHA256:{'w' * 43} (RSA)"
    f = run_check(tbl, facts_text(authorized=extra))
    res = [r for w, r in whats(f) if w == "入っている鍵"]
    assert any("表に無い鍵が aoki に入っている" in r and FP_B in r for r in res)
    assert any("弱い鍵" in r and "1024" in r for r in res)
    assert not any(FP_A in r for r in res)
    # 直す手は消す場所を言うだけで、鍵の中身は無い
    script = machine.fix_script("box", machine.parse_facts(facts_text(authorized=extra)), f)
    assert FP_B in script and "AAAA" not in script


def test_hostkey_mismatch_and_missing(tbl, tmp_path):
    f = run_check(tbl, facts_text(hostkeys=f"256 {FP_B} (ED25519)"))
    assert any(w == "ホストの鍵" and "表と違う" in r for w, r in whats(f))
    pub = tmp_path / "a.pub"
    t2 = machine.parse_table(table_text(str(pub)).replace(f'hostkey = "{FP_HOST}"\n', ""))
    f = machine.check(t2, "box", machine.parse_facts(facts_text()), PORTS, {"k-a"}, NOW)
    assert any(w == "ホストの鍵" and f'hostkey = "{FP_HOST}"' in r for w, r in whats(f, fix=False))


def test_key_missing_from_inventory_and_table(tbl):
    f = run_check(tbl, facts_text(), ids=frozenset())
    assert ("鍵の表", "鍵 k-a が在庫表に無い（K-14。inventory.toml に同じ id で一件）") in whats(f)
    tbl.machines[0]["keys"] = ["k-a", "ghost"]
    f = run_check(tbl, facts_text())
    assert any("ghost が [[key]] に無い" in r for _w, r in whats(f))


def test_people_findings(tbl):
    f = run_check(
        tbl,
        facts_text(
            users="aoki /bin/bash\nformer /bin/bash\nsvc /usr/sbin/nologin",
            admins="sudo aoki,former",
            accounts="aoki P\nformer NP",
            sudoers="nopasswd=2",
            collected="2026-11-07T08:00:00+0900\nuid=0",
        ),
    )
    got = whats(f)
    assert ("アカウント", "表に無いアカウント: former") in got
    assert ("管理者", "表に無い管理者（sudo の組）: former") in got
    assert ("アカウント", "パスワードが空: former") in got
    assert any("パスワード無しの sudo が 2 行" in r for _w, r in got)
    assert not any("svc" in r for _w, r in got)  # 入れないアカウントは数えない
    assert "sudo usermod -L -e 1 former" in machine.fix_script("box", machine.parse_facts(facts_text()), f)


def test_updates_firewall_ports_time_disk(tbl):
    f = run_check(
        tbl,
        facts_text(
            reboot="required",
            updates="manager=apt\nnow=1794000000\nlists=1793000000\npkg openssl trixie-security\npkg vim trixie",
            auto="",
            firewall="ufw=active\nnftables=inactive\nfirewalld=inactive\nENABLED=no",
            listen='LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:5432 0.0.0.0:* users:(("postgres",pid=2,fd=3))',
            time="NTP=no\nNTPSynchronized=no",
            failed="foo.service",
            disk="/ 41%\n/srv 91%",
            crypt="crypt=0",
        ),
    )
    got = whats(f)
    assert ("更新", "再起動が要る") in got
    assert ("更新", "更新が 2 件（うち security 1）") in got
    assert any("一覧が 11 日古い" in r for _w, r in got)
    assert ("更新", "自動の更新が無い（unattended-upgrades）") in got
    assert any(w == "防火壁" for w, _r in got)
    assert ("ポート", "表に無いポート: 5432（0.0.0.0 postgres）") in got
    assert ("時計", "時計が合わせられていない（K-11）") in got
    assert ("サービス", "落ちている: foo.service") in got
    assert ("ディスク", "/srv が 91%") in got
    assert any(w == "ディスク" and "暗号化されていない" in r for w, r in whats(f, fix=False))
    script = machine.fix_script("box", machine.parse_facts(facts_text()), f)
    assert "sudo ufw limit 22/tcp" in script and script.index("ufw limit") < script.index("sudo ufw enable")


def test_old_facts_and_no_ports_table(tbl):
    f = run_check(tbl, facts_text(collected="2026-10-20T08:00:00+0900\nuid=1000"), ports_table=None)
    seen = whats(f, fix=False)
    assert ("コピー", "18 日前のコピー（集め直す）") in seen
    assert ("ポート", "ポートの表にこのサーバーが無い（開いているポート 1: 22）") in seen


def test_parse_facts_refuses_other_text():
    with pytest.raises(Refused):
        machine.parse_facts("LISTEN 0 128 0.0.0.0:22\n")


# ---- コピーを取る ----


def test_ssh_command_is_keys_only_and_known_hosts_only():
    cmd = machine.ssh_command("box")
    assert cmd[0] == "ssh" and cmd[-4:] == ["--", "box", "sh", "-s"]
    for opt in ("BatchMode=yes", "StrictHostKeyChecking=yes", "PasswordAuthentication=no"):
        assert opt in cmd
    with pytest.raises(Refused):
        machine.ssh_command("-oProxyCommand=evil")


def test_collect_with_runner(tmp_path):
    calls = []

    def ok(cmd, **kw):
        calls.append((cmd, kw["input"]))
        return subprocess.CompletedProcess(cmd, 0, facts_text(), "")

    text = machine.collect("box", ok)
    assert calls[0][0][0] == "ssh" and calls[0][1] == machine.SCRIPT
    p = machine.save_facts(tmp_path, "box", text, dt.datetime(2026, 11, 7, 9, 0, 0))
    assert p.name == "box-20261107-090000.txt" and oct(p.stat().st_mode & 0o777) == "0o600"
    assert machine.latest_facts(tmp_path, "box") == p

    def denied(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 255, "", "box: Permission denied (publickey).\n")

    with pytest.raises(Refused, match="Permission denied"):
        machine.collect("box", denied)


def test_script_reads_only_and_runs_here():
    # 変える命令が無い事（書き込み、消す、入れる、止める）
    for word in ("rm ", "apt install", "apt-get", "systemctl re", "systemctl st", "tee ", "chmod", "> /"):
        assert word not in machine.SCRIPT, word
    for word in ("ufw enable", "ufw allow", "usermod", "passwd -l", "sudo sh", "sudo -"):
        assert word not in machine.SCRIPT, word
    facts = machine.parse_facts(machine.collect(None))  # この PC で読むだけ
    assert facts.collected is not None and facts.pairs("os").get("KERNEL")


# ---- 入れる ----


def test_install_files(tbl, tmp_path):
    tbl.machines[0]["lan"] = "192.0.2.0/24"
    files = install.build(tbl, "box", dt.date(2026, 11, 7))
    pre, first, steps = files["preseed.cfg"], files["firstboot.sh"], files["手順.md"]
    # パスワードもディスクも書かない
    assert "password" not in pre.replace("# No password", "").replace("The password is typed", "").lower()
    assert "partman" not in pre
    assert "d-i passwd/root-login boolean false" in pre and "d-i passwd/username string aoki" in pre
    assert "d-i netcfg/hostname string box" in pre
    late = next(ln for ln in pre.splitlines() if "late_command" in ln)
    b64s = [w for w in late.split() if len(w) > 60]
    ak = base64.b64decode(b64s[0]).decode()
    conf = base64.b64decode(b64s[1]).decode()
    assert ak == KEY_A.rsplit(" ", 1)[0] + " k-a\n"  # 注釈（someone@pc）は入れず、表の id を付ける
    assert "PasswordAuthentication no" in conf and "PermitRootLogin no" in conf and "AllowUsers aoki" in conf
    assert late.count("'") == 2  # 一重の引用の中に一重の引用が無い
    assert "ufw allow from 192.0.2.0/24 to any port 22 proto tcp" in first
    assert first.index("ufw allow") < first.index("ufw --force enable")
    assert FP_A in steps and "Permission denied (publickey)" in steps and "まだ確かめていない" in steps
    assert subprocess.run(["sh", "-n"], check=False, input=first, text=True).returncode == 0


def test_install_refuses_half_shapes(tbl, tmp_path):
    def with_(**kw):
        t = machine.parse_table(table_text(str(tmp_path / "a.pub")))
        t.machines[0].update(kw)
        return t

    for kw, msg in (
        ({"os": "ubuntu-24.04"}, "debian-13"),
        ({"admins": []}, "admins"),
        ({"keys": []}, "公開鍵が要る"),
        ({"lan": "192.0.2.1/24"}, "lan"),
        ({"profile": "x"}, "profile"),
        ({"mirror": "evil'; rm"}, "mirror"),
    ):
        with pytest.raises(Refused, match=msg):
            install.build(with_(**kw), "box")
    weak = tmp_path / "w.pub"
    weak.write_text(pub_rsa(1024) + "\n")
    t = machine.parse_table(table_text(str(weak)))
    with pytest.raises(Refused, match="弱い"):
        install.build(t, "box")


# ---- 命令、画面、報告 ----


def _dir(tmp_path, pub_line=KEY_A):
    d = tmp_path / "k"
    d.mkdir()
    (tmp_path / "a.pub").write_text(pub_line + "\n")
    (d / "machines.toml").write_text(table_text(str(tmp_path / "a.pub")))
    (d / "ports.toml").write_text('[[port]]\nhost = "box"\nport = 22\nbind = "0.0.0.0"\n')
    (d / "inventory.toml").write_text(
        '[[secret]]\nid = "k-a"\nkind = "key"\nwhere = "pc:~/.ssh"\nholder = "someone"\nrole = "box に入る"\n'
        'expires = 2999-01-01\nrotate_days = 365\nlost = "l"\nleaked = "k"\n'
    )
    return d


def test_cli_machine_and_key(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("KAGI_LOG_KEY_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    d = _dir(tmp_path)
    assert cli.main(["machine", "check", "--dir", str(d), "--host", "box"]) == 2  # コピーが無い
    assert cli.main(["machine", "check", "--dir", str(d)]) == 1
    assert "見ていない" in capsys.readouterr().out

    bad = tmp_path / "bad.txt"
    bad.write_text(facts_text(sshd="effective=0\nPasswordAuthentication yes"))
    assert cli.main(["machine", "check", "--dir", str(d), "--host", "box", "--facts", str(bad)]) == 1
    assert "パスワードで入れる" in capsys.readouterr().out
    assert cli.main(["machine", "fix", "--dir", str(d), "--host", "box", "--facts", str(bad)]) == 1
    assert "sudo sshd -t" in capsys.readouterr().out

    machine.save_facts(
        d, "box", facts_text(collected=f"{dt.datetime.now().astimezone():%Y-%m-%dT%H:%M:%S%z}\nuid=1000")
    )
    assert cli.main(["machine", "check", "--dir", str(d), "--host", "box"]) == 0
    assert "直す物は無い" in capsys.readouterr().out
    assert cli.main(["machine", "list", "--dir", str(d)]) == 0
    assert cli.main(["machine", "script"]) == 0
    assert capsys.readouterr().out.endswith(machine.SCRIPT)

    assert cli.main(["machine", "install", "--dir", str(d), "--host", "box"]) == 0
    assert {p.name for p in (d / "install" / "box").iterdir()} == {"preseed.cfg", "firstboot.sh", "手順.md"}
    assert cli.main(["scan", str(d / "install")]) == 0  # 作った物に秘密らしい文字列が無い
    capsys.readouterr()

    assert cli.main(["key", "list", "--dir", str(d)]) == 0
    out = capsys.readouterr().out
    assert FP_A in out and "box" in out and "期限 2999-01-01" in out
    assert cli.main(["key", "fingerprint", str(tmp_path / "a.pub")]) == 0
    assert capsys.readouterr().out.startswith(FP_A)
    (d / "inventory.toml").write_text("")
    assert cli.main(["key", "list", "--dir", str(d)]) == 1
    assert "無い（K-14）" in capsys.readouterr().out


def test_cli_collect_local_and_log(tmp_path, capsys, monkeypatch):
    d = _dir(tmp_path)
    keyf = tmp_path / "key"
    keyf.write_bytes(b"machine-test-key-not-a-secret")
    os.chmod(keyf, 0o600)
    monkeypatch.setenv("KAGI_LOG_KEY_FILE", str(keyf))
    assert cli.main(["machine", "collect", "--dir", str(d), "--host", "box", "--local"]) == 0
    assert machine.latest_facts(d, "box") is not None
    assert not (d / "audit.log").exists()  # この PC で取った時は ssh の鍵を使っていないので足さない

    def fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, facts_text(), "")

    monkeypatch.setattr(machine.subprocess, "run", fake)
    assert cli.main(["machine", "collect", "--dir", str(d), "--host", "box"]) == 0
    n, fails = audit.verify(d / "audit.log", audit.find_key())
    assert n == 1 and not fails and "machine.collect" in (d / "audit.log").read_text()
    assert cli.main(["machine", "collect", "--dir", str(d), "--host", "nope"]) == 2


def test_app_card_and_report(tmp_path, monkeypatch):
    monkeypatch.delenv("KAGI_LOG_KEY_FILE", raising=False)
    d = _dir(tmp_path)
    c = app.build_cards(d)["machines"]
    assert c["summary"] == "サーバー 1 台、直す物のあるサーバー 0、コピーの無いサーバー 1"
    machine.save_facts(
        d, "box", facts_text(sshd="effective=0\nPasswordAuthentication yes", reboot="required")
    )
    c = app.build_cards(d)["machines"]
    assert "直す物のあるサーバー 1" in c["summary"]
    assert c["rows"][0][0] == "box" and "認証（ssh）" in c["rows"][0][4] and "更新" in c["rows"][0][4]
    assert ["box", "更新", "再起動が要る"] in c["detail"]
    assert "sudo systemctl reboot" in app.fix_text(d, "box")
    text = report.build(d)
    assert "## サーバー" in text and "- box: 直す物" in text

    def fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, facts_text(), "")

    p = app.collect_machine(d, "box", runner=fake, env={})
    assert p.exists()
    empty = tmp_path / "e"
    empty.mkdir()
    assert "サーバーの表が無い" in app.build_cards(empty)["machines"]["summary"]
    assert "サーバーの表が無い" in report.build(empty)


# ---- 管理の画面（Flet を使わない所）、サービス、動き ----


def test_services_and_load(tbl):
    tbl.machines[0]["services"] = ["seed", "caddy.service"]
    tbl.machines[0]["user_services"] = ["sync"]
    text = facts_text(
        services="caddy.service\nssh.service",
        **{
            "user-services": "sync.service",
            "load": "since=2026-10-28 14:25:57\nloadavg=1.01 1.01 1.00\nMemTotal=129468412\nMemAvailable=23993536",
        },
    )
    f = run_check(tbl, text)
    assert ("サービス", "動いていない: seed") in whats(f)
    assert not any("caddy" in r for _w, r in whats(f))
    seen = whats(f, fix=False)
    assert ("サービス", "表の 1 件が動いている（sync）") in seen
    assert ("動き", "起動 2026-10-28 14:25:57、load 1.01 1.01 1.00、メモリ 101/123 GB") in seen
    assert ("ディスク", "/ 41%") in seen
    script = machine.fix_script("box", machine.parse_facts(text), f)
    assert "sudo systemctl restart seed" in script and "systemctl status seed" in script
    tbl.machines[0]["user_services"] = ["sd-server"]
    f = run_check(tbl, text)
    assert ("サービス", "動いていない: sd-server（その人の systemd）") in whats(f)
    assert "systemctl --user restart sd-server" in machine.fix_script("box", machine.parse_facts(text), f)
    with pytest.raises(Refused, match="サービスの名"):
        machine.parse_table('[[machine]]\nhost = "a"\nservices = ["x; rm -rf /"]\n')


def test_port_findings_have_guidance(tbl):
    f = run_check(tbl, facts_text(listen="LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*"))
    hows = {x.result: x.how for x in f if x.fix and x.what == "ポート"}
    assert any("grep ':8080 '" in h[0] for r, h in hows.items() if "表に無いポート" in r)
    assert all(h for h in hows.values())


def test_add_machine_and_key_from_the_desk(tmp_path):
    d = tmp_path / "k"
    d.mkdir()
    pub = tmp_path / "a.pub"
    pub.write_text(KEY_A + "\n")
    assert app.keys_view(d)["error"]
    app.add_machine(d, "box", "", "見本", "aoki、ops", "ops", "k-a", "seed, caddy", local=True)
    assert app.add_key(d, "k-a", "someone", str(pub)) == FP_A
    t = machine.load(d / "machines.toml")
    m = t.machine("box")
    assert m["users"] == ["aoki", "ops"] and m["admins"] == ["ops"] and m["services"] == ["seed", "caddy"]
    assert m["local"] is True and "ssh" not in m and m["os"] == "debian-13"
    assert app.keys_view(d)["rows"] == [["k-a", "someone", FP_A, "box", "見ていない"]]
    # 読めない形は足さない（ファイルは元のまま）
    before = (d / "machines.toml").read_text()
    for bad in ({"host": "box"}, {"host": "-x"}, {"host": "ok", "users": "Bad Name"}):
        with pytest.raises(Refused):
            app.add_machine(d, **bad)
    with pytest.raises(Refused):
        app.add_key(d, "k-b", "x", str(tmp_path / "id_ed25519"))  # .pub でない
    assert (d / "machines.toml").read_text() == before


def test_machine_view_and_local_collect(tmp_path, monkeypatch):
    monkeypatch.delenv("KAGI_LOG_KEY_FILE", raising=False)
    d = tmp_path / "k"
    d.mkdir()
    app.add_machine(d, "box", users="aoki", admins="aoki", local=True)
    v = app.machine_view(d, "box")
    assert v["facts"] is None and "コピーが無い" in v["head"]
    assert app.machine_view(d, "nope")["error"] == "サーバーの表に nope が無い"
    called = []

    def fake(cmd, **kw):
        called.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, facts_text(reboot="required"), "")

    app.collect_machine(d, "box", runner=fake)
    assert called == [["sh", "-s"]]  # local = true のサーバーは ssh を使わない
    assert not (d / "audit.log").exists()
    v = app.machine_view(d, "box")
    assert v["head"].startswith("サーバー box（") and "直す物" in v["head"]
    fix = next(f for f in v["fixes"] if f["result"] == "再起動が要る")
    assert fix["what"] == "更新" and fix["how"] == ["sudo systemctl reboot   # 時を選んで"]
    assert ["OS", "Debian GNU/Linux 13 (trixie)、kernel 6.12.0"] in v["notes"]
    with pytest.raises(Refused, match="公開鍵が要る"):
        app.install_files(d, "box")


# ---- 入口の表（誰が、何で守られて入るか） ----

CADDY = """site
site (cf)
  header
site open.example.org
  import cf
  reverse_proxy 127.0.0.1:8550
site files.example.org, www.example.org
  root /srv/www
  file_server
site app.example.org
  reverse_proxy 127.0.0.1:8560
site admin.example.org
  basic_auth
  reverse_proxy 127.0.0.1:9000
site :11444
  matcher expression
  reverse_proxy 127.0.0.1:11434
  respond 401
site lan.example.org
  matcher {
  remote_ip
  respond 403
  reverse_proxy 127.0.0.1:8090"""


def _doors(tbl, *rows):
    tbl.doors[:] = [{"host": "box", "site": s, "auth": a, "who": w} for s, a, w in rows]
    return tbl


def test_parse_caddy_sites():
    sites, unreadable = machine.parse_caddy(CADDY.splitlines())
    by = {s.name: s for s in sites}
    assert not unreadable and "(cf)" not in by and "" not in by
    assert set(by) == {
        "open.example.org",
        "files.example.org",
        "www.example.org",
        "app.example.org",
        "admin.example.org",
        ":11444",
        "lan.example.org",
    }
    assert by["open.example.org"].upstreams == ["127.0.0.1:8550"] and not by["open.example.org"].gate
    assert by["files.example.org"].static and by["www.example.org"].static
    assert by["admin.example.org"].gate == ["basic_auth"]
    assert by[":11444"].gate == ["Authorization の照合"]
    assert by["lan.example.org"].lan and not by["lan.example.org"].gate


def test_door_findings(tbl):
    text = facts_text(caddy=CADDY)
    # 入口の表にこのサーバーが無ければ、見るだけで入口を並べる
    f = run_check(tbl, text)
    assert any(w == "入口" and "入口の表にこのサーバーが無い（入口 7" in r for w, r in whats(f, fix=False))
    _doors(
        tbl,
        ("open.example.org", "public", "誰でも"),
        ("app.example.org", "gate", "招待した人"),
        ("admin.example.org", "gate", "係"),
        (":11444", "gate", "家のサーバー"),
        ("lan.example.org", "lan", "家の中"),
        ("files.example.org", "lan", "家の中"),
        ("later.example.org", "app", "これから置く"),
    )
    f = run_check(tbl, text)
    fixes = [r for w, r in whats(f) if w == "入口"]
    assert any(r.startswith("表に無い入口: www.example.org") for r in fixes)
    assert any(r.startswith("門が無い: app.example.org") and "招待した人" in r for r in fixes)
    assert any(r.startswith("ネットワークで絞っていない: files.example.org") for r in fixes)
    assert "表にあって、Caddy の設定に無い: later.example.org" in fixes
    assert len(fixes) == 4
    notes = [r for w, r in whats(f, fix=False) if w == "入口"]
    assert any(r.startswith("admin.example.org: Caddy が確かめる") and "basic_auth" in r for r in notes)
    assert any(r.startswith(":11444: Caddy が確かめる") for r in notes)
    assert any(r.startswith("open.example.org: 誰でも") for r in notes)
    # app は「アプリの中は見ていない」と必ず言う
    _doors(tbl, ("app.example.org", "app", "招待した人"))
    f = run_check(tbl, facts_text(caddy="site app.example.org\n  reverse_proxy 127.0.0.1:8560"))
    assert any("アプリの中は見ていない" in r for w, r in whats(f, fix=False) if w == "入口")
    with pytest.raises(Refused, match="auth"):
        machine.parse_table('[[door]]\nhost = "a"\nsite = "x"\nauth = "none"\n')


def test_script_never_prints_caddy_values(tmp_path):
    # コピーの文の caddy の所だけを、見本の Caddyfile に当てる。秘密らしい文字列が出ない事
    conf = tmp_path / "Caddyfile"
    fake = "not-a-real-hash-Zq81Zq81Zq81Zq81"
    conf.write_text(
        "{\n\tadmin off\n}\n"
        "admin.example.org {\n"
        f"\tbasic_auth {{\n\t\tkakari {fake}\n\t}}\n"
        f'\t@ok header Authorization "Bearer {fake}"\n'
        "\treverse_proxy 127.0.0.1:9000 {\n"
        f"\t\theader_up X-Token {fake}\n\t}}\n"
        '\trespond "nope" 401\n}\n'
    )
    start = machine.SCRIPT.index("  awk '", machine.SCRIPT.index("sec caddy"))
    end = machine.SCRIPT.index("' /etc/caddy/Caddyfile", start)
    prog = machine.SCRIPT[start + len("  awk '") : end]
    r = subprocess.run(["awk", prog, str(conf)], check=True, capture_output=True, text=True)
    assert fake not in r.stdout and "kakari" not in r.stdout and "nope" not in r.stdout
    sites, _ = machine.parse_caddy(r.stdout.splitlines())
    assert [s.name for s in sites] == ["admin.example.org"]
    assert sites[0].gate == ["basic_auth", "Authorization の照合"] and sites[0].upstreams == [
        "127.0.0.1:9000"
    ]


def test_data_paths(tbl, tmp_path):
    f = run_check(tbl, facts_text(data="path /srv/photos 251658240 14% 1503238553\nmissing /mnt/backup"))
    assert ("データ", "置き場が無い: /mnt/backup") in whats(f)
    assert ("データ", "/srv/photos: 240.0 GB（そのディスクは 14% 使用、残り 1.4 TB）") in whats(f, fix=False)
    # 道は引数で渡す（文は変えない）。字を絞る
    assert machine.ssh_command("box", ["/srv/photos"])[-6:] == ["--", "box", "sh", "-s", "--", "/srv/photos"]
    assert machine.ssh_command("box")[-1] == "-s"
    for bad in ("relative", "/a; rm -rf /", "/a/../b", "/a b", "/$(x)"):
        with pytest.raises(Refused):
            machine.ssh_command("box", [bad])
        with pytest.raises(Refused):
            machine.parse_table(f'[[machine]]\nhost = "a"\ndata = ["{bad}"]\n')
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "x").write_bytes(b"0" * 4096)
    facts = machine.parse_facts(
        machine.collect(None, paths=[str(tmp_path / "big"), "/nonexistent-kagi-test"])
    )
    lines = facts.lines("data")
    assert lines[0].startswith(f"path {tmp_path / 'big'} ") and lines[1] == "missing /nonexistent-kagi-test"


def test_encrypt_expected(tbl):
    tbl.machines[0]["encrypt"] = True
    f = run_check(tbl, facts_text(crypt="crypt=0"))
    assert any(w == "ディスク" and "encrypt = true" in r for w, r in whats(f))
    f = run_check(tbl, facts_text(crypt="crypt=1"))
    assert ("ディスク", "暗号化されている（表の通り）") in whats(f, fix=False) and not whats(f)


# ---- 連載「Claude と一緒に育てる Debian サーバー」の形（第 5・10・11 章）


def test_series_shape_firewall_backup_errors(tbl):
    fw = 'ufw=active\nnftables=inactive\nfirewalld=inactive\nENABLED=yes\nDEFAULT_INPUT_POLICY="DROP"\nDEFAULT_OUTPUT_POLICY="ACCEPT"\nfail2ban=inactive'
    f = run_check(tbl, facts_text(firewall=fw, timers="apt-daily.timer\nlogrotate.timer", errors="err7d=12"))
    assert any(w == "防火壁" and "出る向きを既定で通している" in r for w, r in whats(f))
    script = machine.fix_script("box", machine.parse_facts(facts_text()), f)
    assert script.index("ufw allow out 53") < script.index("ufw default deny outgoing")
    seen = whats(f, fix=False)
    assert any(w == "防火壁" and "fail2ban が動いていない" in r for w, r in seen)
    assert any(w == "控え" and "表に書いていない" in r for w, r in seen)
    assert ("記録", "この 7 日のエラーの行 12（root でないコピーで読めた分）") in seen
    # 入りも出も既定は拒否で、fail2ban が動いていれば、防火壁の行は無い
    good = fw.replace("ACCEPT", "DROP").replace("fail2ban=inactive", "fail2ban=active")
    f = run_check(tbl, facts_text(firewall=good))
    assert not [1 for x in f if x.what == "防火壁"]
    # 表に書いた timer が止まれば直す物
    tbl.machines[0]["timers"] = ["backup"]
    f = run_check(tbl, facts_text(timers="apt-daily.timer"))
    assert ("控え", "timer が動いていない: backup") in whats(f)
    f = run_check(tbl, facts_text(timers="backup.timer\napt-daily.timer"))
    assert ("控え", "表の timer が動いている（backup）") in whats(f, fix=False)


def _firewall_how(tbl, fw):
    f = run_check(tbl, facts_text(firewall=fw))
    (x,) = [x for x in f if x.what == "防火壁" and x.fix and "default deny outgoing" in "\n".join(x.later)]
    assert "default deny outgoing" not in "\n".join(x.how)  # 出る向きを閉じる手は二段目だけ
    return x


def test_closing_outgoing_counts_ports_and_sets_undo_timer(tbl):
    fw = 'ufw=active\nnftables=inactive\nfirewalld=inactive\nENABLED=yes\nDEFAULT_INPUT_POLICY="DROP"\nDEFAULT_OUTPUT_POLICY="ACCEPT"\nfail2ban=active'
    x = _firewall_how(tbl, fw)
    assert x.confirm  # 防火壁の手には今も印が付く
    how = x.later
    count = how.index(machine.OUT_PORTS_NOW)
    allow = how.index(
        "sudo ufw allow out 53 && sudo ufw allow out 123/udp && sudo ufw allow out 80,443/tcp && sudo ufw allow out 67/udp"
    )
    assert "sudo ufw allow out 67/udp" in how[allow]  # DHCP の要求（宛先 67/udp）
    timer = how.index(machine.UFW_UNDO_TIMER)
    deny = how.index("sudo ufw default deny outgoing")
    cancel = how.index(machine.UFW_UNDO_CANCEL)
    assert count < allow < timer < deny < how.index("sudo apt update") < cancel
    assert "ufw-modosu" in machine.UFW_UNDO_TIMER and "ufw-modosu.timer" in machine.UFW_UNDO_CANCEL


def test_no_firewall_closes_incoming_first_and_outgoing_in_stage_two(tbl):
    fw = "ufw=inactive\nnftables=inactive\nfirewalld=inactive"
    x = _firewall_how(tbl, fw)
    assert x.confirm
    how = x.how
    ssh_in = next(i for i, ln in enumerate(how) if ln.startswith("sudo ufw limit 22/tcp"))
    assert how.index("sudo apt install -y ufw") < how.index("sudo ufw default deny incoming") < ssh_in
    assert ssh_in < how.index("sudo ufw enable")
    assert x.later == machine.OUT_CLOSE
    # 一段目の後に、見出しを付けた二段目。数える行と閉じる行は二段目にだけある
    text = facts_text(firewall=fw, reboot="required", failed="foo.service")
    f = run_check(tbl, text)
    script = machine.fix_script("box", machine.parse_facts(text), f)
    head = script.index(machine.STAGE2_HEAD[0])
    assert script.index("sudo ufw enable") < head < script.index(machine.STAGE2_HEAD[1])
    assert head < script.index(machine.OUT_PORTS_NOW) < script.index("sudo ufw default deny outgoing")
    assert script.count(machine.OUT_PORTS_NOW) == 1 and script.count("ufw default deny outgoing") == 1
    # 二段目は台本の終わり。ほかの直す物の手は全部その前
    last_other = max(script.index(ln) for g in f if g.fix and g.what != "防火壁" for ln in g.how)
    assert last_other < head
    # 画面で一つの物ごとに見せる時も、一段目、見出し、二段目の順
    assert x.all_how() == [*x.how, *machine.STAGE2_HEAD, *machine.OUT_CLOSE]


def test_outgoing_open_only_puts_everything_in_stage_two(tbl):
    fw = 'ufw=active\nnftables=inactive\nfirewalld=inactive\nENABLED=yes\nDEFAULT_INPUT_POLICY="DROP"\nDEFAULT_OUTPUT_POLICY="ACCEPT"\nfail2ban=active'
    f = run_check(tbl, facts_text(firewall=fw))
    script = machine.fix_script("box", machine.parse_facts(facts_text(firewall=fw)), f)
    assert "#   手は終わりの二段目" in script
    assert script.index("#   手は終わりの二段目") < script.index(machine.STAGE2_HEAD[0])


def test_incoming_only_machine_has_no_stage_two(tbl):
    fw = 'ufw=active\nnftables=inactive\nfirewalld=inactive\nENABLED=yes\nDEFAULT_INPUT_POLICY="ACCEPT"\nDEFAULT_OUTPUT_POLICY="DROP"\nfail2ban=active'
    f = run_check(tbl, facts_text(firewall=fw))
    assert ("防火壁", "入る向きを既定で通している（ufw）") in whats(f)
    script = machine.fix_script("box", machine.parse_facts(facts_text(firewall=fw)), f)
    assert "sudo ufw default deny incoming" in script
    assert "二段目" not in script and machine.OUT_PORTS_NOW not in script


def test_fix_script_passes_sh_n(tbl):
    for fw in (
        'ufw=active\nnftables=inactive\nfirewalld=inactive\nENABLED=yes\nDEFAULT_INPUT_POLICY="ACCEPT"\nDEFAULT_OUTPUT_POLICY="ACCEPT"\nfail2ban=inactive',
        "ufw=inactive\nnftables=inactive\nfirewalld=inactive",
    ):
        f = run_check(tbl, facts_text(firewall=fw))
        script = machine.fix_script("box", machine.parse_facts(facts_text(firewall=fw)), f)
        assert machine.OUT_PORTS_NOW in script
        assert subprocess.run(["sh", "-n"], check=False, input=script, text=True).returncode == 0


@pytest.mark.skipif(shutil.which("ss") is None, reason="ss が無い")
def test_out_ports_count_line_runs():
    r = subprocess.run(["sh", "-c", machine.OUT_PORTS_NOW], check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_firstboot_follows_the_series(tbl):
    tbl.machines[0]["lan"] = "192.0.2.0/24"
    first = install.build(tbl, "box")["firstboot.sh"]
    for line in (
        "ufw default deny incoming",
        "ufw default deny outgoing",
        "ufw allow out 53",
        "ufw allow out 123/udp",
        "ufw allow out 80,443/tcp",
        "ufw allow out 67/udp",
        "ufw allow out to 192.0.2.0/24",
        "'bantime = 10m' 'findtime = 10m' 'maxretry = 5'",
    ):
        assert line in first, line
    # 入れる物を取ってから出口を閉じ、ssh を通してから有効にする
    assert (
        first.index("apt-get install")
        < first.index("ufw default deny outgoing")
        < first.index("ufw --force enable")
    )
    assert first.index("to any port 22") < first.index("ufw --force enable")
    assert subprocess.run(["sh", "-n"], check=False, input=first, text=True).returncode == 0


def test_changes_since_previous_facts_and_weekly(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("KAGI_LOG_KEY_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    d = _dir(tmp_path)
    stamp = f"{dt.datetime.now().astimezone():%Y-%m-%dT%H:%M:%S%z}\nuid=1000"
    machine.save_facts(
        d, "box", facts_text(collected=stamp, reboot="required"), dt.datetime(2026, 11, 1, 9, 0)
    )
    machine.save_facts(
        d, "box", facts_text(collected=stamp, failed="foo.service"), dt.datetime(2026, 11, 6, 9, 0)
    )
    row = report.machine_rows(d)[0]
    assert row["prev"] == "box-20261101-090000.txt"
    assert [(f.what, f.result) for f in row["new"]] == [("サービス", "落ちている: foo.service")]
    assert [(f.what, f.result) for f in row["gone"]] == [("更新", "再起動が要る")]
    assert cli.main(["machine", "check", "--dir", str(d), "--host", "box"]) == 1
    out = capsys.readouterr().out
    assert "増えた 1、消えた 1" in out and "消えた: 更新: 再起動が要る" in out
    assert any("増えた 1、消えた 1" in c for c in app.machine_view(d, "box")["changes"])

    # 週の定例: コピーを取り直し、一枚を書く
    def fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, facts_text(collected=stamp), "")

    monkeypatch.setattr(machine.subprocess, "run", fake)
    ledger = tmp_path / "tokens.json"
    ledger.write_text(
        '{"tokens": [{"name": "買い手", "app": "seed", "scope": ["buyer"], "sha256": "0", "expires": "2000-01-01"}]}'
    )
    assert cli.main(["weekly", "--dir", str(d), "--tokens", str(ledger)]) == 0
    out = capsys.readouterr().out
    assert (
        "## サーバー" in out
        and "- box: 直す物は無い" in out
        and "消えた: サービス: 落ちている: foo.service" in out
    )
    assert "## 出した token" in out and "期限切れのまま 1" in out
    weekly = list(d.glob("weekly-*.md"))
    assert (
        len(weekly) == 1 and weekly[0].read_text() == out.rstrip("\n") + "\n" or weekly[0].read_text() in out
    )

    # 入れないサーバーがあっても報告は出る
    def down(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 255, "", "ssh: connect to host box port 22: No route to host\n"
        )

    monkeypatch.setattr(machine.subprocess, "run", down)
    assert cli.main(["weekly", "--dir", str(d), "--tokens", str(ledger)]) == 0
    assert "コピーが取れなかったサーバー" in capsys.readouterr().out


def test_dir_default_uses_here_when_tables_are_here(tmp_path, monkeypatch):
    """--dir が無く ./kagi-data も無い時、今の場所に表があれば今の場所を使う。"""
    import argparse

    ns = argparse.Namespace(sub_dir=None, top_dir=None)
    monkeypatch.chdir(tmp_path)
    assert cli._dir(ns) == cli.Path(cli.DEFAULT_DIR)  # 表が無ければ既定のまま
    (tmp_path / "machines.toml").write_text("", "utf-8")
    assert cli._dir(ns) == cli.Path(".")
    (tmp_path / "kagi-data").mkdir()
    assert cli._dir(ns) == cli.Path(cli.DEFAULT_DIR)  # ./kagi-data が在ればそちら
    ns.top_dir = str(tmp_path / "other")
    assert cli._dir(ns) == tmp_path / "other"  # --dir が先


def test_dir_default_inventory_only(tmp_path, monkeypatch):
    import argparse

    monkeypatch.chdir(tmp_path)
    (tmp_path / "inventory.toml").write_text("", "utf-8")
    assert cli._dir(argparse.Namespace(sub_dir=None, top_dir=None)) == cli.Path(".")
    assert cli.main(["check", "inventory"]) == 0


def test_sudo_holders_always_one_line(tbl):
    """表と合っている時も、sudo を持つ人を「見るだけ」に一行。"""
    seen = whats(run_check(tbl, facts_text()), fix=False)
    assert ("管理者", "sudo を持つ人 1: aoki（表の通り）") in seen
    f = run_check(tbl, facts_text(users="aoki /bin/bash\nop /bin/bash", admins="sudo aoki,op"))
    assert ("管理者", "表に無い管理者（sudo の組）: op") in whats(f)
    assert ("管理者", "sudo を持つ人 2: aoki、op（表に無い人が居る）") in whats(f, fix=False)
    f = run_check(tbl, facts_text(admins="sudo"))
    assert ("管理者", "sudo を持つ人 0: 無し（表の admins に居て持たない: aoki）") in whats(f, fix=False)
    tbl.machines[0]["admins"] = []
    f = run_check(tbl, facts_text())
    seen = whats(f, fix=False)
    assert any(w == "管理者" and "比べていない" in r for w, r in seen)
    assert not any("sudo を持つ人" in r for _w, r in seen)


def test_check_inventory_empty_but_keys(tmp_path, capsys):
    pub = tmp_path / "a.pub"
    pub.write_text(KEY_A + "\n")
    (tmp_path / "machines.toml").write_text(table_text(str(pub)), "utf-8")
    (tmp_path / "inventory.toml").write_text("", "utf-8")
    assert cli.main(["check", "inventory", "--dir", str(tmp_path)]) == 0  # 終了コードは変えない
    assert "鍵の表に鍵が 1 件あるが、在庫表は空（kagi key list で見る）" in capsys.readouterr().out
    (tmp_path / "machines.toml").write_text("[[machine]\n", "utf-8")  # 読めない表なら言わない
    assert cli.main(["check", "inventory", "--dir", str(tmp_path)]) == 0
    assert "在庫表は空" not in capsys.readouterr().out


def test_confirm_marks_the_eight(tbl):
    """連載 2-02 の八つに当たる直す物に印。更新やサービスには付けない。"""
    f = run_check(
        tbl,
        facts_text(
            sshd="effective=0\nPasswordAuthentication yes\nKbdInteractiveAuthentication no\nPermitRootLogin no",
            reboot="required",
        ),
    )
    by = {(x.what, x.result): x for x in f if x.fix}
    ssh = [x for x in by.values() if x.what == "認証（ssh）"]
    assert ssh and all(x.confirm for x in ssh)
    upd = [x for x in by.values() if x.what == "更新"]
    assert upd and not any(x.confirm for x in upd)
    assert not any(x.confirm for x in f if not x.fix)  # 見るだけには付けない
    text = machine.fix_script("box", machine.parse_facts(facts_text()), f)
    assert "# 【読んで確かめてから】 認証（ssh）:" in text
    assert "# 更新: 再起動が要る" in text
    assert "印のある手は、一行ずつ読んで確かめてから。" in text


def test_confirm_port_closed_is_not_marked():
    a = machine.Finding(True, "ポート", "表にあって開いていない: 8010（seed）")
    b = machine.Finding(True, "ポート", "外向きで表と違う: 11435（サーバーは 0.0.0.0、表は 127.0.0.1）")
    c = machine.Finding(True, "サービス", "動いていない: seed")
    assert [machine._needs_confirm(x) for x in (a, b, c)] == [False, True, False]
