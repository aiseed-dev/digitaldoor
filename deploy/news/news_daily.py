# SPDX-License-Identifier: AGPL-3.0-or-later
"""ニュースを集める毎朝の仕事(deb2)。ニュースの写しで news の枝を origin/main から作り直し、
tools/collect_news.py で集めて整え、news/items.json だけを commit する。どこにも push しない。

    .venv/bin/python deploy/news/news_daily.py              # 毎朝の仕事(cron から動かす)
    .venv/bin/python deploy/news/news_daily.py --max 20     # AI で整える上限を変える(既定 40)

ニュースの写し(例: ~/dev/digitaldoor-news)の中で、写しの .venv の python で動かす。.venv は
miniforge(conda-forge)で作り、python と anthropic を入れる(手順は docs/使い方.md の「公開」)。
cron の行とログの置き場の例は deploy/news/crontab.example、置き場のファイルの見本は deploy/news/news.env.example。
この道具は Python の標準ライブラリと git だけを使う。collect_news.py は既定で、この道具を
動かしている python(--python で変えられる)で動かす。

流れ:
  1. 排他を取る(写しの .git/news-daily.lock)。取れなければ「別の仕事が動いています」と出して終わる
  2. 写しの追跡しているファイルに、commit していない変更があれば、何もせずに止まる
  3. git fetch origin(取りに行く先は写しの origin。読むだけで、push はしない)
  4. git checkout -B news origin/main。news の枝を、毎回その日の origin/main から作り直す
     (前の日の news の commit は枝から外れる。取り込んでいなくても、collect_news.py が直近の物を
     集め直すので、次の朝の枝に入る)
  5. API キーを読む。環境変数 ANTHROPIC_API_KEY があればそれを、無ければ --env-file の置き場
     (既定 ~/.config/digitaldoor/news.env。権限は 600)から読む。値は collect_news.py の環境にだけ
     渡し、出力とログには出さない(collect_news.py の出力に値が混じったときは *** に置き換える)。
     置き場のファイルの値は、同じ名前の環境変数が無いときだけ使う。キーが無ければ、
     collect_news.py は題のまま置く
  6. <python> tools/collect_news.py --max N
  7. news/items.json が JSON として読めることを確かめ、変化があれば、それだけを commit する
     (作者 news-bot)。変化が無ければ commit しない
  8. 結果を短い日本語で出す

終了コード:
  0  commit した、または変化が無かった
  1  それ以外。写しが汚れている、fetch に失敗、置き場のファイルの権限が広い、collect_news.py が
     0 以外で終わった(資格情報があるのに一件も整わなかった等)、items.json が壊れている、など。
     collect_news.py が 0 以外で終わっても、items.json が読めて変化があれば、集めた物は commit して
     から 1 で終わる。items.json が壊れていたときは、元に戻してから 1 で終わる

開発機での取り込み(git remote add deb2news deb2:dev/digitaldoor-news → git fetch deb2news →
git merge deb2news/news)は、docs/使い方.md の「ニュースの仕組み」にある。

Python で書いたのは、公開の道具 tools/publish.py と同じ流儀(標準ライブラリと git、fcntl の排他、
短い日本語の結果、終了コード)にそろえ、置き場のファイルをシェルで読み込まずに(source せずに)
値として読み、キーを出力から隠し、git の写しを使った試験(tests/news/)で確かめるためである。
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows では排他を取らない(deb2 は Linux)
    fcntl = None

REPO = Path(__file__).resolve().parents[2]
ITEMS = "news/items.json"
COLLECT = "tools/collect_news.py"
KEY = "ANTHROPIC_API_KEY"
DEFAULT_ENV_FILE = "~/.config/digitaldoor/news.env"
AUTHOR = ("news-bot", "news-bot@localhost")


class NewsError(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    full = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C", **(env or {}))
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=full)
    except FileNotFoundError:
        raise NewsError("git が見つかりません")
    if check and r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()
        raise NewsError(f"git {args[0]} に失敗しました: {tail[-1] if tail else r.returncode}")
    return r


def git_dir(repo: Path) -> Path:
    if not (repo / ".git").exists():
        raise NewsError(f"{repo} は git の写しではありません")
    return Path(git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())


def dirty_files(repo: Path) -> list[str]:
    out = git(repo, "status", "--porcelain", "--untracked-files=no").stdout
    return [line[3:] for line in out.splitlines() if line.strip()]


class Lock:
    """同時に二つ動かないための排他。取れなければ NewsError。"""

    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.fd = open(self.path, "w")
        if fcntl is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.fd.close()
                raise NewsError("別の仕事が動いています")
        return self

    def __exit__(self, *exc):
        self.fd.close()


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE の行を値として読む(シェルとしては動かさない)。# の行と空の行は飛ばす。

    ファイルが無ければ空。グループや他人が読める権限なら NewsError(値は出さない)。
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise NewsError(f"{path} の権限が広すぎます({stat.S_IMODE(st.st_mode):o})。chmod 600 {path} にしてください")
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        name, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name.strip()] = value
    return out


def child_env(env_file: Path) -> tuple[dict[str, str], list[str], str]:
    """collect_news.py に渡す環境、隠す値の一覧、キーの出どころ(出力用の短い言葉)を返す。"""
    env = dict(os.environ)
    from_file = read_env_file(env_file)
    for name, value in from_file.items():
        if value and not env.get(name):
            env[name] = value
    if os.environ.get(KEY):
        source = "環境変数"
    elif from_file.get(KEY):
        source = f"置き場のファイル {env_file}"
    else:
        source = ""
    secrets = [v for k, v in from_file.items() if v] + ([os.environ[KEY]] if os.environ.get(KEY) else [])
    return env, secrets, source


def mask(text: str, secrets: list[str]) -> str:
    for s in sorted(set(secrets), key=len, reverse=True):
        if len(s) >= 4:
            text = text.replace(s, "***")
    return text


def load_items(text: str) -> list[dict]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("一覧ではありません")
    return data


def base_items(repo: Path, base_ref: str) -> list[dict]:
    r = git(repo, "show", f"{base_ref}:{ITEMS}", check=False)
    if r.returncode != 0:
        return []
    try:
        return load_items(r.stdout)
    except ValueError:
        return []


def counts(before: list[dict], after: list[dict]) -> str:
    known = {i.get("id") for i in before}
    new = sum(1 for i in after if i.get("id") not in known)
    raw_b = sum(1 for i in before if i.get("status") == "raw")
    raw_a = sum(1 for i in after if i.get("status") == "raw")
    return f"新しい物 {new} 件、合計 {len(after)} 件、整っていない物(raw){raw_b} 件 → {raw_a} 件"


def run(repo: Path, branch: str, base: str, max_items: int, env_file: Path, python: str) -> int:
    dirty = dirty_files(repo)
    if dirty:
        raise NewsError("写しに commit していない変更があります(" + "、".join(dirty[:5])
                        + ("…" if len(dirty) > 5 else "") + ")。何もしていません")
    git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}")
    base_ref = f"refs/remotes/origin/{base}"
    git(repo, "checkout", "--quiet", "-B", branch, base_ref)
    start = git(repo, "rev-parse", "--short=12", "HEAD").stdout.strip()

    env, secrets, source = child_env(env_file)
    print(f"ニュース: {branch} の枝を origin/{base}({start})から作り直しました。"
          + (f"API キーは{source}から読みました" if source else "API キーがありません(題のまま置きます)"))

    r = subprocess.run([python, COLLECT, "--max", str(max_items)], cwd=repo, env=env,
                       capture_output=True, text=True)
    for line in mask((r.stdout or "") + (r.stderr or ""), secrets).splitlines():
        print(f"  {line}")
    collect_failed = r.returncode != 0

    path = repo / ITEMS
    try:
        after = load_items(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        git(repo, "checkout", "--quiet", "--", ITEMS, check=False)
        raise NewsError(f"{ITEMS} が読めません({type(e).__name__})。元に戻しました。commit していません")

    git(repo, "add", "--", ITEMS)
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
        message = "変化なし。commit していません"
        committed = False
    else:
        day = datetime.now().strftime("%Y-%m-%d")
        msg = f"ニュース {day}" + ("(整えに失敗した物を含む)" if collect_failed else "")
        name, email = AUTHOR
        git(repo, "commit", "--quiet", "-m", msg, "--", ITEMS,
            env={"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                 "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email})
        head = git(repo, "rev-parse", "--short=12", "HEAD").stdout.strip()
        message = f"{branch} の枝に commit しました({head}。{counts(base_items(repo, base_ref), after)})"
        committed = True

    left = dirty_files(repo)
    if left:
        raise NewsError(f"{ITEMS} のほかに変わったファイルがあります(" + "、".join(left[:5]) + ")。"
                        "次の朝は止まるので、写しを直してください")
    if collect_failed:
        raise NewsError(f"collect_news.py が {r.returncode} で終わりました。"
                        + ("集めた物は commit しました" if committed else "commit した物はありません"))
    print(f"ニュース: {message}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="news_daily.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=REPO,
                    help="ニュースの写し。既定はこの道具のあるリポジトリ(例: ~/dev/digitaldoor-news)")
    ap.add_argument("--max", type=int, default=40, help="collect_news.py に渡す --max(AI で整える上限。既定 40)")
    ap.add_argument("--branch", default="news", help="作り直して commit する枝(既定: news)")
    ap.add_argument("--base", default="main", help="枝を作り直す元。origin/<base>(既定: main)")
    ap.add_argument("--env-file", type=Path, default=Path(DEFAULT_ENV_FILE),
                    help=f"API キーの置き場(KEY=VALUE の行。権限 600。既定: {DEFAULT_ENV_FILE})")
    ap.add_argument("--python", default=sys.executable,
                    help="collect_news.py を動かす python(既定: この道具を動かしている python)")
    ap.add_argument("--debug", action="store_true", help="エラーのとき、例外の追跡も出す")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.expanduser().absolute()
    try:
        if args.max < 0:
            raise NewsError("--max は 0 以上にしてください")
        with Lock(git_dir(repo) / "news-daily.lock"):
            return run(repo, args.branch, args.base, args.max, args.env_file.expanduser(), args.python)
    except NewsError as e:
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
