"""サイトの公開の道具 tools/publish.py の試験。

一時ディレクトリに、取りに行く先(裸のリポジトリ)、手元(commit を作る側)、deb2 の写し(clone)、
置き場(--root)を作って回す。網には出ない(git はローカルだけ)。
"""
import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("publish", Path(__file__).resolve().parents[2] / "tools" / "publish.py")
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)

URL = "https://door.aiseed.dev/"


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()


def sitemap(*paths):
    locs = "".join(f"<url><loc>{URL}{p}</loc></url>" for p in paths)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'


def good_site(version="v1"):
    return {
        "index.html": f"<html>{version}</html>",
        "404.html": "<html>404</html>",
        "sitemap.xml": sitemap("", "guide/", "about.html"),
        "guide/index.html": "guide",
        "about.html": "about",
        "css/site.css": "body{}",
        "_headers": "/*\n  X-Frame-Options: DENY\n",
        ".well-known/hidden": "x",
        "guide/.secret": "x",
    }


class Env:
    def __init__(self, tmp: Path):
        self.bare = tmp / "origin.git"
        self.work = tmp / "work"
        self.clone = tmp / "clone"
        self.root = tmp / "srv" / "door"
        git(tmp, "init", "--bare", "-b", "main", str(self.bare))
        git(tmp, "clone", "-q", str(self.bare), str(self.work))
        git(self.work, "checkout", "-q", "-B", "main")

    def commit(self, files: dict | None = None, remove=(), msg="c"):
        """手元で html/ を書き換えて commit し、push する。files の None は削除。"""
        html = self.work / "html"
        for rel in remove:
            (html / rel).unlink()
        for rel, body in (files or {}).items():
            p = html / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "--allow-empty", "-m", msg)
        git(self.work, "push", "-q", "origin", "main")
        return git(self.work, "rev-parse", "HEAD")

    def make_clone(self):
        git(self.bare.parent, "clone", "-q", "-b", "main", str(self.bare), str(self.clone))

    def run(self, capsys, *extra):
        argv = ["--repo", str(self.clone), "--root", str(self.root), *extra]
        code = publish.main(argv)
        out = capsys.readouterr()
        return code, out.out, out.err

    def www(self):
        link = self.root / "www"
        return os.readlink(link) if link.is_symlink() else None

    def releases(self):
        d = self.root / "releases"
        return sorted(p.name for p in d.iterdir() if not p.name.startswith(".")) if d.is_dir() else []

    def clone_head(self):
        return git(self.clone, "rev-parse", "HEAD")


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.first = e.commit(good_site("v1"))
    e.make_clone()
    return e


def test_first_publish(env, capsys):
    code, out, err = env.run(capsys)
    assert code == 0, err
    short = env.first[:12]
    assert env.www() == f"releases/{short}"
    assert (env.root / "www" / "index.html").read_text() == "<html>v1</html>"
    assert (env.root / "www" / "guide" / "index.html").read_text() == "guide"
    assert short in out


def test_no_change_does_nothing(env, capsys):
    env.run(capsys)
    placed = (env.root / "releases" / env.first[:12] / ".placed").read_text()
    code, out, err = env.run(capsys)
    assert code == 0 and "変更なし" in out
    assert env.releases() == [env.first[:12]]
    assert (env.root / "releases" / env.first[:12] / ".placed").read_text() == placed


def test_new_commit_switches_and_advances_clone(env, capsys):
    env.run(capsys)
    second = env.commit({"index.html": "<html>v2</html>"})
    code, out, err = env.run(capsys)
    assert code == 0, err
    assert env.www() == f"releases/{second[:12]}"
    assert (env.root / "www" / "index.html").read_text() == "<html>v2</html>"
    assert env.clone_head() == second
    assert env.releases() == sorted([env.first[:12], second[:12]])


def test_non_fast_forward_stops_and_changes_nothing(env, capsys):
    env.run(capsys)
    # 手元で履歴を書き換えて force push する
    git(env.work, "reset", "-q", "--hard", "HEAD~0")
    rewritten = env.commit({"index.html": "<html>x</html>"})
    git(env.work, "reset", "-q", "--hard", env.first)
    git(env.work, "commit", "-q", "--allow-empty", "-m", "rewritten")
    git(env.work, "push", "-q", "--force", "origin", "main")
    # 写しを、書き換え前の先の commit に進めておく(origin から見て先祖でなくなる)
    git(env.clone, "fetch", "-q", "origin", f"+{rewritten}:refs/tmp/x")
    git(env.clone, "reset", "-q", "--hard", rewritten)
    before = (env.www(), env.releases(), env.clone_head())
    code, out, err = env.run(capsys)
    assert code != 0 and "早送りできません" in err
    assert (env.www(), env.releases(), env.clone_head()) == before


@pytest.mark.parametrize("name", ["no_index", "no_404", "sitemap_missing_url", "half_files"])
def test_inspection_failure_does_not_switch(env, capsys, name):
    env.run(capsys)
    cur = env.www()
    head = env.clone_head()
    if name == "no_index":
        env.commit(remove=["index.html"])
    elif name == "no_404":
        env.commit(remove=["404.html"])
    elif name == "sitemap_missing_url":
        env.commit({"sitemap.xml": sitemap("", "guide/", "gone/")})
    else:
        env.commit({f"extra/{i}.html": "x" for i in range(10)})  # 今の版を大きくしておく
        assert env.run(capsys)[0] == 0
        cur = env.www()
        head = env.clone_head()
        env.commit(remove=[f"extra/{i}.html" for i in range(10)] + ["css/site.css", "about.html", "guide/index.html"],
                   files={"sitemap.xml": sitemap("")})
    code, out, err = env.run(capsys)
    assert code != 0 and "点検に落ちました" in err
    assert env.www() == cur
    assert env.clone_head() == head
    assert not [n for n in os.listdir(env.root / "releases") if n.startswith(".")]  # 一時の場所が残らない
    assert len(env.releases()) == (2 if name == "half_files" else 1)


def test_symlink_outside_html_fails(env, capsys):
    env.run(capsys)
    cur = env.www()
    (env.work / "html" / "evil").symlink_to("/etc")
    git(env.work, "add", "-A")
    git(env.work, "commit", "-q", "-m", "link")
    git(env.work, "push", "-q", "origin", "main")
    code, _, err = env.run(capsys)
    assert code != 0 and "シンボリックリンク" in err
    assert env.www() == cur


def test_symlink_inside_html_is_fine(env, capsys):
    (env.work / "html" / "alias.html").symlink_to("about.html")
    git(env.work, "add", "-A")
    git(env.work, "commit", "-q", "-m", "link")
    git(env.work, "push", "-q", "origin", "main")
    assert env.run(capsys)[0] == 0
    assert (env.root / "www" / "alias.html").read_text() == "about"


def test_headers_and_hidden_files_not_copied(env, capsys):
    assert env.run(capsys)[0] == 0
    www = env.root / "www"
    assert not (www / "_headers").exists()
    assert not (www / ".well-known").exists()
    assert not (www / "guide" / ".secret").exists()
    assert (www / "css" / "site.css").exists()


def test_keep_prunes_old_but_keeps_current_and_previous(env, capsys):
    ids = [env.first]
    assert env.run(capsys)[0] == 0
    for i in range(4):
        ids.append(env.commit({"index.html": f"<html>n{i}</html>"}))
        time.sleep(0.01)
        assert env.run(capsys, "--keep", "100")[0] == 0
    assert len(env.releases()) == 5
    # 続けて --keep 1: 今の版(最新)と一つ前は残り、他は消える
    ids.append(env.commit({"index.html": "<html>last</html>"}))
    code, out, _ = env.run(capsys, "--keep", "1")
    assert code == 0
    assert env.releases() == sorted([ids[-1][:12], ids[-2][:12]])
    assert env.www() == f"releases/{ids[-1][:12]}"


def test_rollback_and_status(env, capsys):
    env.run(capsys)
    second = env.commit({"index.html": "<html>v2</html>"})
    time.sleep(0.01)
    env.run(capsys)
    code, out, _ = env.run(capsys, "--status")
    assert code == 0 and second[:12] in out and env.first[:12] in out
    assert env.run(capsys, "--rollback")[0] == 0
    assert env.www() == f"releases/{env.first[:12]}"
    assert (env.root / "www" / "index.html").read_text() == "<html>v1</html>"
    # 戻した後、origin が変わらなければ、また進めはしない
    code, out, _ = env.run(capsys)
    assert code == 0 and "変更なし" in out
    assert env.www() == f"releases/{env.first[:12]}"
    # これより前の版は無い
    code, _, err = env.run(capsys, "--rollback")
    assert code != 0 and "戻せる版がありません" in err
    # 新しい commit が来たら、それに進む
    third = env.commit({"index.html": "<html>v3</html>"})
    assert env.run(capsys)[0] == 0
    assert env.www() == f"releases/{third[:12]}"


def test_status_without_release(env, capsys):
    code, out, _ = env.run(capsys, "--status")
    assert code == 0 and "ありません" in out


def test_rollback_without_previous_fails(env, capsys):
    env.run(capsys)
    code, _, err = env.run(capsys, "--rollback")
    assert code != 0 and "戻せる版がありません" in err


def test_dry_run_changes_nothing(env, capsys):
    head = env.clone_head()
    code, out, _ = env.run(capsys, "--dry-run")
    assert code == 0 and "予定" in out and env.first[:12] in out
    assert not env.root.exists()
    assert env.clone_head() == head
    # 公開済みの後に新しい commit が来ても、dry-run は動かさない
    env.run(capsys)
    env.commit({"index.html": "<html>v2</html>"})
    before = (env.www(), env.releases(), env.clone_head())
    code, out, _ = env.run(capsys, "--dry-run")
    assert code == 0 and "予定" in out
    assert (env.www(), env.releases(), env.clone_head()) == before
    # 点検に落ちる commit では、dry-run も落ちる
    env.commit(remove=["index.html"])
    code, _, err = env.run(capsys, "--dry-run")
    assert code != 0 and "点検に落ちました" in err
    assert (env.www(), env.releases(), env.clone_head()) == before


def test_error_hides_traceback_unless_debug(env, capsys):
    argv = ["--repo", str(env.clone), "--root", str(env.root), "--branch", "nope"]
    assert publish.main(argv) != 0
    assert "Traceback" not in capsys.readouterr().err


def test_runs_as_script_with_help():
    r = subprocess.run(["python3", str(Path(publish.__file__)), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--rollback" in r.stdout
