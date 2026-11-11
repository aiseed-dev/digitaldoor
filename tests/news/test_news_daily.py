"""ニュースを集める毎朝の仕事 deploy/news/news_daily.py の試験。

一時ディレクトリに、取りに行く先(裸のリポジトリ)、手元(main に commit する側)、deb2 のニュースの写し
(clone)を作って回す。collect_news.py は、写しの中に置いた疑似の物(網にも API にも出ない)。
サイトの道具なので、要求仕様(R-XX-nn)の番号は付けない(tests/publish と同じ扱い)。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("news_daily", ROOT / "deploy" / "news" / "news_daily.py")
nd = importlib.util.module_from_spec(SPEC)
sys.modules["news_daily"] = nd
SPEC.loader.exec_module(nd)

SECRET = "sk-ant-test-0123456789abcdef"

# 疑似の collect_news.py。FAKE_MODE で振る舞いを変える。受け取った引数とキーの有無を記録し、
# わざとキーの値を出力に混ぜる(news_daily が隠すことを確かめる)。
FAKE_COLLECT = r'''
import json, os, sys
from pathlib import Path
mode = os.environ.get("FAKE_MODE", "add")
key = os.environ.get("ANTHROPIC_API_KEY", "")
Path(os.environ["FAKE_LOG"]).write_text(json.dumps({"argv": sys.argv[1:], "has_key": bool(key)}))
print("疑似の collect_news: key=" + key)
items_path = Path("news/items.json")
items = json.loads(items_path.read_text(encoding="utf-8"))
if mode in ("add", "fail-after-add", "touch-other"):
    n = len(items)
    items.insert(0, {"id": f"new{n}", "status": "ai", "picked": False, "title": f"新しい {n}"})
    items_path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
if mode == "touch-other":
    Path("README.md").write_text("書き換えた\n")
if mode == "broken":
    items_path.write_text("{壊れた", encoding="utf-8")
if mode in ("fail-after-add", "fail"):
    print("エラー: 資格情報があるのに、AI で一件も整えられませんでした", file=sys.stderr)
    sys.exit(1)
'''


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


class Env:
    def __init__(self, tmp: Path, monkeypatch):
        self.tmp = tmp
        self.bare = tmp / "origin.git"
        self.work = tmp / "work"
        self.clone = tmp / "digitaldoor-news"
        self.log = tmp / "fake.json"
        self.env_file = tmp / "config" / "news.env"
        git(tmp, "init", "--bare", "-b", "main", str(self.bare))
        git(tmp, "clone", "-q", str(self.bare), str(self.work))
        git(self.work, "checkout", "-q", "-B", "main")
        (self.work / "tools").mkdir()
        (self.work / "news").mkdir()
        (self.work / "tools" / "collect_news.py").write_text(FAKE_COLLECT, encoding="utf-8")
        (self.work / "news" / "items.json").write_text(
            json.dumps([{"id": "a", "status": "raw", "picked": True}], indent=1) + "\n", encoding="utf-8")
        (self.work / "README.md").write_text("元\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "初め")
        git(self.work, "push", "-q", "origin", "main")
        git(tmp, "clone", "-q", str(self.bare), str(self.clone))
        monkeypatch.setenv("FAKE_LOG", str(self.log))
        monkeypatch.setenv("FAKE_MODE", "add")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def write_env(self, text=f"# 見本\nANTHROPIC_API_KEY={SECRET}\n", mode=0o600):
        self.env_file.parent.mkdir(exist_ok=True)
        self.env_file.write_text(text)
        self.env_file.chmod(mode)

    def run(self, *extra):
        return nd.main(["--repo", str(self.clone), "--env-file", str(self.env_file),
                        "--python", sys.executable, *extra])

    def push_main(self, text):
        (self.work / "README.md").write_text(text)
        git(self.work, "commit", "-q", "-am", "main を進める")
        git(self.work, "push", "-q", "origin", "main")

    def fake(self):
        return json.loads(self.log.read_text())


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_commits_only_items_on_news_branch(env, capsys):
    """news の枝に items.json だけを news-bot の名前で commit し、origin には何も送らないこと。"""
    env.write_env()
    assert env.run() == 0
    out = capsys.readouterr()
    assert "news の枝に commit しました" in out.out and "新しい物 1 件" in out.out
    assert "置き場のファイル" in out.out
    assert git(env.clone, "rev-parse", "--abbrev-ref", "HEAD") == "news"
    assert git(env.clone, "log", "-1", "--format=%an <%ae>") == "news-bot <news-bot@localhost>"
    assert git(env.clone, "show", "--name-only", "--format=", "HEAD") == "news/items.json"
    assert git(env.clone, "rev-parse", "HEAD~1") == git(env.bare, "rev-parse", "main")
    assert git(env.bare, "branch", "--list", "news") == ""
    assert env.fake() == {"argv": ["--max", "40"], "has_key": True}


def test_key_is_masked_and_never_printed(env, capsys):
    """キーの値は出力に出さない(collect_news.py が出しても *** に置き換える)こと。"""
    env.write_env()
    env.run()
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert "key=***" in out.out


def test_env_var_wins_over_file(env, monkeypatch, capsys):
    """環境変数 ANTHROPIC_API_KEY があれば、それを使うこと。"""
    env.write_env("ANTHROPIC_API_KEY=from-file-value\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    assert env.run() == 0
    out = capsys.readouterr().out
    assert "環境変数" in out and SECRET not in out


def test_wide_permissions_stop_before_collect(env, capsys):
    """置き場のファイルが 600 より広ければ、集めずに止まり、値を出さないこと。"""
    env.write_env(mode=0o644)
    assert env.run() == 1
    err = capsys.readouterr().err
    assert "権限が広すぎます" in err and SECRET not in err
    assert not env.log.exists()


def test_no_key_still_collects(env, capsys):
    """キーが無くても collect_news.py は動かし(題のまま置く)、その旨を出すこと。"""
    assert env.run("--max", "7") == 0
    assert "API キーがありません" in capsys.readouterr().out
    assert env.fake() == {"argv": ["--max", "7"], "has_key": False}


def test_no_change_no_commit(env, monkeypatch, capsys):
    """items.json が変わらなければ commit しないこと。"""
    monkeypatch.setenv("FAKE_MODE", "same")
    assert env.run() == 0
    assert "変化なし" in capsys.readouterr().out
    assert git(env.clone, "rev-parse", "HEAD") == git(env.bare, "rev-parse", "main")


def test_branch_is_rebuilt_from_origin_main(env):
    """news の枝は毎回 origin/main から作り直し、前の日の commit を引き継がないこと。"""
    assert env.run() == 0
    first = git(env.clone, "rev-parse", "HEAD")
    env.push_main("main が進んだ\n")
    assert env.run() == 0
    assert git(env.clone, "rev-parse", "HEAD~1") == git(env.bare, "rev-parse", "main")
    r = subprocess.run(["git", "merge-base", "--is-ancestor", first, "HEAD"], cwd=env.clone)
    assert r.returncode != 0


def test_dirty_clone_stops(env, capsys):
    """写しに commit していない変更があれば、何もせずに止まること。"""
    (env.clone / "README.md").write_text("手で直した\n")
    before = git(env.clone, "rev-parse", "HEAD")
    assert env.run() == 1
    assert "commit していない変更" in capsys.readouterr().err
    assert git(env.clone, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(env.clone, "rev-parse", "HEAD") == before
    assert not env.log.exists()


def test_collect_failure_commits_collected_then_fails(env, monkeypatch, capsys):
    """collect_news.py が 0 以外で終われば、集めた物は commit してから 0 以外で終わること。"""
    monkeypatch.setenv("FAKE_MODE", "fail-after-add")
    assert env.run() == 1
    out = capsys.readouterr()
    assert "AI で一件も整えられませんでした" in out.out
    assert "collect_news.py が 1 で終わりました" in out.err and "集めた物は commit しました" in out.err
    assert "整えに失敗" in git(env.clone, "log", "-1", "--format=%s")


def test_collect_failure_without_change(env, monkeypatch, capsys):
    """collect_news.py が失敗し、変化も無ければ、commit せずに 0 以外で終わること。"""
    monkeypatch.setenv("FAKE_MODE", "fail")
    assert env.run() == 1
    assert "commit した物はありません" in capsys.readouterr().err
    assert git(env.clone, "rev-parse", "HEAD") == git(env.bare, "rev-parse", "main")


def test_broken_items_are_restored(env, monkeypatch, capsys):
    """items.json が壊れたら元に戻し、commit せずに 0 以外で終わり、次の朝に止まらないこと。"""
    monkeypatch.setenv("FAKE_MODE", "broken")
    assert env.run() == 1
    assert "元に戻しました" in capsys.readouterr().err
    assert nd.dirty_files(env.clone) == []
    monkeypatch.setenv("FAKE_MODE", "add")
    assert env.run() == 0


def test_other_files_changed_is_an_error(env, monkeypatch, capsys):
    """items.json のほかが変わったら、items.json だけを commit し、0 以外で知らせること。"""
    monkeypatch.setenv("FAKE_MODE", "touch-other")
    assert env.run() == 1
    assert "ほかに変わったファイル" in capsys.readouterr().err
    assert git(env.clone, "show", "--name-only", "--format=", "HEAD") == "news/items.json"


def test_lock_prevents_two_runs(env, capsys):
    """同時に二つ動かないこと。"""
    import fcntl

    lock = Path(git(env.clone, "rev-parse", "--absolute-git-dir")) / "news-daily.lock"
    with open(lock, "w") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert env.run() == 1
    assert "別の仕事が動いています" in capsys.readouterr().err
    assert not env.log.exists()


def test_env_file_parsing(tmp_path):
    """置き場のファイルは KEY=VALUE の行を値として読む(# の行、export、引用符)こと。"""
    p = tmp_path / "news.env"
    p.write_text("# 説明\n\nexport ANTHROPIC_API_KEY='abc def'\nOTHER=\"x\"\nnot a line\n")
    p.chmod(0o600)
    assert nd.read_env_file(p) == {"ANTHROPIC_API_KEY": "abc def", "OTHER": "x"}
    assert nd.read_env_file(tmp_path / "none.env") == {}


def test_runs_as_script_with_help():
    """--help で使い方を出すこと。"""
    r = subprocess.run([sys.executable, str(ROOT / "deploy" / "news" / "news_daily.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "--env-file" in r.stdout and "news-bot" in r.stdout
