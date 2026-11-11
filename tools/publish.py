# SPDX-License-Identifier: AGPL-3.0-or-later
"""サイトの公開の道具。deb2 の写し(clone)から html/ を取り出し、点検し、版として置き、切り替える。

Python の標準ライブラリと git だけで動く(入れる物は無い)。deb2 では、写しに miniforge(conda-forge)で作った
.venv の python で動かす(deb2 の Python のアプリは、miniforge の環境をアプリごとの .venv に持つ)。

    .venv/bin/python tools/publish.py --repo ~/dev/digitaldoor --root /srv/door             # 取りに行き、点検し、切り替える
    .venv/bin/python tools/publish.py --repo ~/dev/digitaldoor --root /srv/door --dry-run   # 何をするかだけ見る
    .venv/bin/python tools/publish.py --root /srv/door --status                             # 今の版と、置いた時刻
    .venv/bin/python tools/publish.py --root /srv/door --rollback                           # 一つ前の版に戻す

置き場(--root)の中身:

    <root>/releases/<commit の先頭 12 桁>/   置いた版。中は html/ の内容
    <root>/www                               今の版を指すシンボリックリンク(Caddy の root)

流れ:
  1. 写しの origin から --branch を fetch する。写しの位置(HEAD)から origin/<branch> へ早送りできる
     ときだけ進める。できなければ(履歴が書き換えられた等)何も変えずに終わる(終了コード 1)。
     HEAD が origin/<branch> と同じで、今の版もあるときは何もしない(終了コード 0)
  2. origin/<branch> の html/ を git archive で取り出し、一時の名前の場所に写す。
     `_headers` と、名前が `.` で始まる物は写さない
  3. 点検する。index.html と 404.html があること。sitemap.xml の URL(https://door.aiseed.dev/ の下)が
     全部ファイルとしてあること(末尾が / なら index.html)。ファイルの数が今の版の半分を下回らないこと。
     置いた版の外を指すシンボリックリンクが無いこと。一つでも落ちたら切り替えない
  4. 一時の名前を releases/<12 桁> に改め、www を一時のリンクから os.replace で一度に付け替える
  5. 写しを origin/<branch> まで早送りする
  6. --keep の数だけ新しい順に版を残し、他を消す。今の版と一つ前の版は消さない

点検に落ちたときの扱い: 写しの位置を進める前に点検するので、落ちたときは写しも www も動かさない
(取り出した一時の場所は消す)。次に動かしたとき、同じ commit をもう一度点検する。直した commit が
push されて origin が進めば、そこから先へ進む。履歴を書き換えて直す場合は、写しの位置が origin の
先祖でなくなるので、早送りできないとして止まる。人が写しを直す。

写しを進めるのは切り替えの後。切り替えのあとで写しを進められなかったとき(作業木の変更等)は、
エラーで終わるが、公開は済んでいる。次に動かしたとき、同じ版を使い回して写しを進め直す。

--rollback は www を一つ前の版に付け替えるだけで、写しの位置は動かさない。そのため、次に動かしたとき、
origin に新しい commit が無ければ何もせず、戻した版が出続ける。新しい commit が来たら、それに進む。
「一つ前」は、置いた時刻が今の版より前で一番新しい版。続けて戻すと、さらに前へ行く。

--dry-run は fetch だけ行い(origin/<branch> の記録は更新される)、点検まで試して、版も www も写しも
変えない。

終了コード: 0 は成功または変更なし、1 は止まった(点検落ち、早送り不可、その他のエラー)。
エラーは短い日本語で出す。例外の追跡は --debug のときだけ出す。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import fcntl
except ImportError:  # Linux 以外
    fcntl = None

SITE_URL = "https://door.aiseed.dev/"
SKIP_NAMES = {"_headers"}
PLACED = ".placed"  # 置いた版の中に書く、置いた時刻(エポックからのナノ秒)
DEFAULT_KEEP = 5


class PublishError(Exception):
    """利用者に見せる短いエラー。"""


# --- git -----------------------------------------------------------------

def git(repo: Path, *args: str, check: bool = True, text: bool = True):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text, env=env)
    except FileNotFoundError:
        raise PublishError("git が見つかりません")
    if check and r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        raise PublishError(f"git {args[0]} に失敗しました: {tail[-1] if tail else r.returncode}")
    return r


def fetch_target(repo: Path, branch: str) -> str:
    """origin/<branch> を取りに行き、その commit(40 桁)を返す。"""
    if not (repo / ".git").exists():
        raise PublishError(f"{repo} は git の写しではありません")
    git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    return git(repo, "rev-parse", "--verify", f"refs/remotes/origin/{branch}^{{commit}}").stdout.strip()


def can_fast_forward(repo: Path, target: str) -> bool:
    r = git(repo, "merge-base", "--is-ancestor", "HEAD", target, check=False)
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    raise PublishError(f"早送りできるか調べられませんでした: {(r.stderr or '').strip()}")


# --- 置き場 ---------------------------------------------------------------

def releases_dir(root: Path) -> Path:
    return root / "releases"


def placed_ns(release: Path) -> int:
    try:
        return int((release / PLACED).read_text().strip())
    except (OSError, ValueError):
        try:
            return release.stat().st_mtime_ns
        except OSError:
            return 0


def list_releases(root: Path) -> list[Path]:
    """置いた版を、新しい順に返す。"""
    d = releases_dir(root)
    if not d.is_dir():
        return []
    found = [p for p in d.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")]
    return sorted(found, key=lambda p: (placed_ns(p), p.name), reverse=True)


def current_release(root: Path) -> Path | None:
    link = root / "www"
    if not link.is_symlink():
        return None
    target = Path(os.path.realpath(link))
    return target if target.is_dir() and target.parent == Path(os.path.realpath(releases_dir(root))) else None


def previous_release(root: Path, cur: Path | None) -> Path | None:
    """今の版より前に置いた版のうち、一番新しい物。"""
    if cur is None:
        return None
    older = [p for p in list_releases(root) if p != cur and (placed_ns(p), p.name) < (placed_ns(cur), cur.name)]
    return older[0] if older else None


def count_files(base: Path) -> int:
    n = 0
    for _, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        n += sum(1 for f in files if not f.startswith("."))
    return n


def stamp(ns: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ns / 1e9))


def switch_www(root: Path, release: Path) -> None:
    """www を、一時のリンクを作って os.replace で一度に付け替える。"""
    link = root / "www"
    if link.exists() and not link.is_symlink():
        raise PublishError(f"{link} がシンボリックリンクではありません。人が片付けてください")
    tmp = root / f"www.tmp-{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(os.path.join("releases", release.name), tmp)
    try:
        os.replace(tmp, link)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def prune(root: Path, keep: int) -> list[str]:
    cur = current_release(root)
    protect = {cur, previous_release(root, cur)} - {None}
    kept = set(list_releases(root)[: max(keep, 0)]) | protect
    gone = []
    for p in list_releases(root):
        if p not in kept:
            shutil.rmtree(p)
            gone.append(p.name)
    return gone


# --- 取り出し -------------------------------------------------------------

def skipped(rel: str) -> bool:
    return any(part in SKIP_NAMES or part.startswith(".") for part in rel.split("/"))


def extract_html(repo: Path, target: str, dest: Path) -> None:
    """target の html/ を dest に写す。_headers と `.` で始まる名前は写さない。"""
    env = dict(os.environ, LC_ALL="C")
    proc = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", target, "html"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    dest.mkdir(parents=True)
    real = os.path.realpath(dest)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
            for m in tf:
                if not m.name.startswith("html/"):
                    continue
                rel = m.name[len("html/"):].rstrip("/")
                if not rel or skipped(rel):
                    continue
                if rel.startswith("/") or ".." in rel.split("/"):
                    raise PublishError(f"不正な名前があります: {m.name}")
                path = dest / rel
                if not os.path.realpath(path.parent).startswith(real):
                    raise PublishError(f"置き場の外に書く名前があります: {m.name}")
                if m.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                    os.chmod(path, 0o755)
                elif m.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tf.extractfile(m) as src, open(path, "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.chmod(path, 0o755 if m.mode & 0o111 else 0o644)
                elif m.issym():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(m.linkname, path)
                else:
                    raise PublishError(f"扱えない種類のファイルがあります: {m.name}")
    except tarfile.TarError as e:
        proc.kill()
        raise PublishError(f"html/ を取り出せませんでした: {e}")
    except BaseException:
        proc.kill()
        raise
    err = proc.stderr.read().decode(errors="replace").strip()
    proc.stdout.close()
    proc.stderr.close()
    if proc.wait() != 0:
        raise PublishError(f"html/ を取り出せませんでした: {err.splitlines()[-1] if err else 'git archive'}")


# --- 点検 -----------------------------------------------------------------

def sitemap_problems(base: Path) -> list[str]:
    sm = base / "sitemap.xml"
    if not sm.is_file():
        return ["sitemap.xml がありません"]
    try:
        tree = ET.parse(sm)
    except ET.ParseError as e:
        return [f"sitemap.xml を読めません: {e}"]
    real = os.path.realpath(base)
    missing = []
    for el in tree.iter():
        if not el.tag.endswith("loc") or not (el.text or "").strip():
            continue
        url = el.text.strip()
        if not url.startswith(SITE_URL):
            continue
        rel = unquote(urlsplit(url).path).lstrip("/")
        if rel == "" or rel.endswith("/"):
            rel += "index.html"
        path = os.path.realpath(base / rel)
        if not (path.startswith(real + os.sep) and os.path.isfile(path)):
            missing.append(rel)
    if not missing:
        return []
    shown = ", ".join(missing[:3]) + (f" ほか {len(missing) - 3} 件" if len(missing) > 3 else "")
    return [f"sitemap.xml の URL にファイルがありません: {shown}"]


def inspect(base: Path, current: Path | None) -> list[str]:
    """点検して、落ちた理由の一覧を返す(通れば空)。"""
    problems = []
    for name in ("index.html", "404.html"):
        if not (base / name).is_file():
            problems.append(f"{name} がありません")
    problems += sitemap_problems(base)
    if current is not None:
        before, now = count_files(current), count_files(base)
        if now * 2 < before:
            problems.append(f"ファイルが {before} から {now} に減りました(半分を下回っています)")
    real = os.path.realpath(base)
    for dirpath, dirs, files in os.walk(base):
        for name in dirs + files:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                dst = os.path.realpath(p)
                if dst != real and not dst.startswith(real + os.sep):
                    problems.append(f"html/ の外を指すシンボリックリンクがあります: {os.path.relpath(p, base)}")
    return problems


# --- 命令 -----------------------------------------------------------------

def cmd_status(root: Path) -> int:
    cur = current_release(root)
    if cur is None:
        print("公開している版はありません")
        return 0
    print(f"今の版: {cur.name}(置いた時刻 {stamp(placed_ns(cur))})")
    prev = previous_release(root, cur)
    if prev is not None:
        print(f"一つ前: {prev.name}(置いた時刻 {stamp(placed_ns(prev))})")
    return 0


def cmd_rollback(root: Path) -> int:
    cur = current_release(root)
    prev = previous_release(root, cur)
    if cur is None or prev is None:
        raise PublishError("戻せる版がありません")
    switch_www(root, prev)
    print(f"戻しました: {cur.name} から {prev.name}")
    return 0


def cmd_publish(repo: Path, root: Path, branch: str, keep: int, dry_run: bool) -> int:
    head = git(repo, "rev-parse", "--verify", "HEAD").stdout.strip()
    cur_branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if cur_branch != branch:
        raise PublishError(f"写しが {branch} にありません(今は {cur_branch or '分離した HEAD'})")
    target = fetch_target(repo, branch)
    short = target[:12]
    cur = current_release(root)
    if head == target and cur is not None:
        print(f"変更なし: 今の版 {cur.name}")
        return 0
    if not can_fast_forward(repo, target):
        raise PublishError(
            f"早送りできません: 写しの位置 {head[:12]} が origin/{branch} {short} の先祖ではありません"
            "(履歴が書き換えられた可能性)。何も変えていません")

    rel_dir = releases_dir(root) / short
    if dry_run:
        work = Path(tempfile.mkdtemp(prefix="publish-dry-")) / "html"
    else:
        rel_dir.parent.mkdir(parents=True, exist_ok=True)
        work = rel_dir.parent / f".tmp-{short}-{os.getpid()}"
    try:
        extract_html(repo, target, work)
        problems = inspect(work, cur)
        if problems:
            now = f"今の版 {cur.name} のまま" if cur else "公開している版はありません"
            raise PublishError(f"点検に落ちました({short}): " + "。".join(problems)
                               + f"。切り替えていません({now})。写しの位置も進めていません")
        if dry_run:
            same = " (今の版と同じ)" if cur is not None and cur.name == short else ""
            print(f"予定: {cur.name if cur else '(なし)'} から {short} に切り替える{same}。点検は通りました。何も変えていません")
            return 0
        (work / PLACED).write_text(str(time.time_ns()))
        if rel_dir.exists():  # 同じ commit の版が残っている。同じ中身なので使い回す
            shutil.rmtree(work)
            (rel_dir / PLACED).write_text(str(time.time_ns()))
        else:
            os.rename(work, rel_dir)
    finally:
        if dry_run:
            shutil.rmtree(work.parent, ignore_errors=True)
        elif work.exists():
            shutil.rmtree(work, ignore_errors=True)

    if cur is None or cur != rel_dir:
        switch_www(root, rel_dir)
    git(repo, "merge", "--ff-only", "--quiet", target)
    gone = prune(root, keep)
    tail = f"、{len(gone)} 版を消しました" if gone else ""
    print(f"公開しました: {short}(前は {cur.name if cur else 'なし'}){tail}")
    return 0


class Lock:
    """同時に二つ動かないための排他。取れなければ PublishError。"""

    def __init__(self, root: Path):
        self.path = root / ".publish.lock"
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.path, "w")
        if fcntl is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise PublishError("別の公開が動いています")
        return self

    def __exit__(self, *exc):
        self.fd.close()


def clean_stale(root: Path) -> None:
    """前に途中で止まった一時の物を消す(排他を取った後に呼ぶ)。"""
    d = releases_dir(root)
    if d.is_dir():
        for p in d.glob(".tmp-*"):
            shutil.rmtree(p, ignore_errors=True)
    for p in root.glob("www.tmp-*"):
        p.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="publish.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent,
                    help="取りに行く写し(clone)。既定はこの道具のあるリポジトリ")
    ap.add_argument("--root", type=Path, required=True, help="置き場(releases/ と www ができる)。例: /srv/door")
    ap.add_argument("--branch", default="main", help="公開するブランチ(既定: main)")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                    help=f"残す版の数(新しい順。既定: {DEFAULT_KEEP})。今の版と一つ前の版は、この数に関わらず消さない")
    ap.add_argument("--dry-run", action="store_true", help="何をするかだけ見る(fetch と点検までで、何も変えない)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="今の版の commit と、置いた時刻を出す")
    mode.add_argument("--rollback", action="store_true", help="一つ前の版に付け替える")
    ap.add_argument("--debug", action="store_true", help="エラーのとき、例外の追跡も出す")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.expanduser().absolute()
    repo = args.repo.expanduser().absolute()
    try:
        if args.keep < 0:
            raise PublishError("--keep は 0 以上にしてください")
        if args.status:
            return cmd_status(root)
        if args.rollback and args.dry_run:
            cur = current_release(root)
            prev = previous_release(root, cur)
            if cur is None or prev is None:
                raise PublishError("戻せる版がありません")
            print(f"予定: {cur.name} から {prev.name} に戻す。何も変えていません")
            return 0
        if args.dry_run:
            return cmd_publish(repo, root, args.branch, args.keep, True)
        with Lock(root):
            clean_stale(root)
            if args.rollback:
                return cmd_rollback(root)
            return cmd_publish(repo, root, args.branch, args.keep, False)
    except PublishError as e:
        print(f"エラー: {e}", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"エラー: 予期しない失敗です({type(e).__name__}: {e})。--debug で詳しく出せます", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
