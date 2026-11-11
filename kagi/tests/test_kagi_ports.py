from kagi import cli, ports

SS = """\
State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process
LISTEN 0      128        127.0.0.1:8080       0.0.0.0:*    users:(("python3",pid=101,fd=3))
LISTEN 0      128          0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=1,fd=3))
LISTEN 0      128             [::]:22            [::]:*    users:(("sshd",pid=1,fd=4))
LISTEN 0      4096               *:11434            *:*    users:(("ollama",pid=7,fd=9))
LISTEN 0      4096   127.0.0.53%lo:53         0.0.0.0:*
"""

TABLE = """
[[port]]
host = "db1"
port = 8080
bind = "127.0.0.1"
service = "seed"
[[port]]
host = "db1"
port = 22
bind = "0.0.0.0"
service = "ssh"
[[port]]
host = "db1"
port = 11434
bind = "127.0.0.1"
service = "ollama"
[[port]]
host = "db1"
port = 5432
bind = "127.0.0.1"
service = "postgres"
[[port]]
host = "web1"
port = 443
bind = "0.0.0.0"
"""


def test_parse_ss():
    got = ports.parse_ss(SS)
    assert ports.Listen("127.0.0.1", 8080, "python3") in got
    assert ports.Listen("0.0.0.0", 22, "sshd") in got
    assert ports.Listen("::", 22, "sshd") in got
    assert ports.Listen("*", 11434, "ollama") in got
    assert ports.Listen("127.0.0.53", 53, "") in got
    assert len(got) == 5


def test_three_differences(tmp_path):
    (tmp_path / "ports.toml").write_text(TABLE)
    diff = ports.compare(ports.load(tmp_path / "ports.toml"), ports.parse_ss(SS), "db1")
    assert [(l.port, l.bind) for l in diff.unlisted] == [(53, "127.0.0.53")]
    assert [p["port"] for p in diff.closed] == [5432]  # web1 の 443 は host で外れる
    assert [(l.port, l.bind, l.process) for l, _ in diff.outward] == [(11434, "*", "ollama")]
    # 22 は表が 0.0.0.0 なので :: も外向きとして表の通り


def test_cli(tmp_path, capsys):
    (tmp_path / "ports.toml").write_text(TABLE)
    (tmp_path / "ss.txt").write_text(SS)
    rc = cli.main(
        ["check", "ports", "--dir", str(tmp_path), "--host", "db1", "--ss", str(tmp_path / "ss.txt")]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "表に無い口" in out and "開いていない" in out and "外向き" in out and "ollama" in out


def test_cli_no_difference(tmp_path, capsys):
    (tmp_path / "ports.toml").write_text('[[port]]\nhost = "h"\nport = 8080\nbind = "127.0.0.1"\n')
    (tmp_path / "ss.txt").write_text(SS.splitlines()[0] + "\n" + SS.splitlines()[1] + "\n")
    rc = cli.main(["check", "ports", "--dir", str(tmp_path), "--host", "h", "--ss", str(tmp_path / "ss.txt")])
    assert rc == 0 and "差は無い" in capsys.readouterr().out
