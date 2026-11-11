"""走査。文字列は偽の形（実際の鍵ではない）。出力に種類だけが出て、文字列そのものは出ない。"""

from kagi import cli, scan

# 実際の鍵ではない。形だけを真似た文字列
FAKE = {
    "aws-access-key": "AKIAEXAMPLEEXAMPLE12",
    "github-token": "ghp_" + "FAKEfake" * 4 + "0000",
    "openai-key": "sk-" + "fakefakefake_fakefake-0",
    "slack-token": "xoxb-" + "0000-fake",
    "assignment": "Fake0nly.NotReal",
}
PEM = "-----BEGIN RSA PRIVATE KEY-----"


def make_tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "conf.py").write_text(
        "x = 1\n"
        f"aws = '{FAKE['aws-access-key']}'\n"
        f"gh = '{FAKE['github-token']}'\n"
        f"ai = '{FAKE['openai-key']}'\n"
        f"sl = '{FAKE['slack-token']}'\n"
    )
    (root / "app.env").write_text(f"password = {FAKE['assignment']}\n")
    (root / "key.pem").write_text(PEM + "\nAAAA\n-----END RSA PRIVATE KEY-----\n")
    (root / "app.env.example").write_text(f"password = {FAKE['assignment']}\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text(f"aws = {FAKE['aws-access-key']}\n")
    (root / "blob.bin").write_bytes(b"\x00\x01" + FAKE["aws-access-key"].encode())
    (root / "big.txt").write_text(FAKE["aws-access-key"] + "\n" + "a" * (scan.MAX_BYTES + 1))
    return root


def test_kinds_only_no_values(tmp_path, capsys):
    root = make_tree(tmp_path)
    rc = cli.main(["scan", str(root)])
    out = capsys.readouterr()
    assert rc == 1
    lines = out.out.splitlines()
    assert f"{root}/conf.py:2  aws-access-key" in lines
    assert f"{root}/conf.py:3  github-token" in lines
    assert f"{root}/conf.py:4  openai-key" in lines
    assert f"{root}/conf.py:5  slack-token" in lines
    assert f"{root}/app.env:1  assignment" in lines
    assert f"{root}/key.pem:1  pem-private-key" in lines
    for v in list(FAKE.values()) + [PEM, "AAAA"]:
        assert v not in out.out
        assert v not in out.err


def test_skips_git_binary_big_and_example(tmp_path, capsys):
    root = make_tree(tmp_path)
    cli.main(["scan", str(root)])
    out = capsys.readouterr().out
    assert ".git" not in out
    assert "blob.bin" not in out
    assert "big.txt" not in out
    assert "example" not in out


def test_exclude(tmp_path, capsys):
    root = make_tree(tmp_path)
    cli.main(["scan", str(root), "--exclude", "*.pem", "--exclude", "conf.py"])
    out = capsys.readouterr().out
    assert "key.pem" not in out and "conf.py" not in out
    assert "app.env:1" in out


def test_nothing_found(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("nothing here\n")
    assert cli.main(["scan", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""


def test_hit_holds_no_text():
    hits = scan.scan_text(f"k = '{FAKE['aws-access-key']}'\n", "x.py")
    assert hits == [scan.Hit("x.py", 1, "aws-access-key")]
    assert FAKE["aws-access-key"] not in repr(hits)
