import os

from kagi import cli

FAKE = "AKIAEXAMPLEEXAMPLE12"  # 偽の形。実際の鍵ではない


def test_report_one_page_no_values(tmp_path, monkeypatch, capsys):
    d = tmp_path / "k"
    cli.main(["init", "--dir", str(d)])
    (d / "inventory.toml").write_text(
        '[[secret]]\nid = "s1"\nkind = "token"\nwhere = "pc:~/x"\nholder = "h"\nrole = "r"\nexpires = 2000-01-01\n'
    )
    (d / "ports.toml").write_text('[[port]]\nhost = "h"\nport = 22\nbind = "127.0.0.1"\n')
    ss = tmp_path / "ss.txt"
    ss.write_text('LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(f"k = '{FAKE}'\n")
    keyf = tmp_path / "key"
    keyf.write_bytes(b"report-test-key-not-a-secret-0")
    os.chmod(keyf, 0o600)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("KAGI_LOG_KEY_FILE", str(keyf))
    cli.main(["log", "add", "--dir", str(d), "--actor", "t", "--action", "a", "--detail", "s1"])
    capsys.readouterr()

    rc = cli.main(["report", "--dir", str(d), "--ss", str(ss), "--host", "h", "--scan", str(src)])
    out = capsys.readouterr().out
    assert rc == 0
    for head in ("# kagi の報告", "## 在庫", "## 口", "## 走査", "## 記録"):
        assert head in out
    assert "期限切れ 1" in out
    assert "外向きで表と違う口 1" in out
    assert "aws-access-key: 1" in out
    assert "ok 1 lines" in out
    assert FAKE not in out
    assert "report-test-key" not in out


def test_report_without_inputs(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("KAGI_LOG_KEY_FILE", raising=False)
    cli.main(["init", "--dir", str(tmp_path)])
    capsys.readouterr()
    assert cli.main(["report", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "突き合わせていない" in out and "走らせていない" in out and "検証していない" in out
