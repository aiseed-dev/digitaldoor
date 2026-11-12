#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""毎朝の一覧。窓口(お問い合わせと照会)に届いた、状態が「新しい」のお問い合わせの件数と、一件ごとの日時・種類・件名を
標準出力に出す(窓口仕様書 9 章)。

deb2 で動かすサイトの運用の道具です。Python の標準ライブラリだけで動き(入れる物は無い)、deb2 の公開のクローン
(~/dev/digitaldoor)の .venv の python で動かします。digitalkey は import しません。

    .venv/bin/python deploy/madoguchi/toiawase_daily.py
    .venv/bin/python deploy/madoguchi/toiawase_daily.py --env ~/.config/digitaldoor/toiawase.env

出す形:

    新しいお問い合わせ 2 件(2026-11-12 07:00 現在)
    2026-11-11 21:03<TAB>質問<TAB>件名の文
    2026-11-12 06:40<TAB>教室<TAB>件名の文

  - 一行目の時刻と、一件ごとの受けた日時は、動かしたコンピューターの地方時です。並びは古い順です。
  - 件名の改行、タブ、制御文字は空白に直し、80 文字を超える件名は 80 文字で切って「…」を付けます。
  - 0 件の時は `新しいお問い合わせはありません(<時刻> 現在)` の一行です。
  - 本文、メールアドレス、送った人、レコードの id は出しません。中身は窓口の管理画面で読みます。

入る値は env のファイル(一行に 名前=値。権限は 600 か、より狭く)と環境変数から読みます。同じ名前があれば
環境変数が勝ちます。パスワードと識別を受ける引数はありません。

  MADOGUCHI_URL       窓口の PocketBase の基の URL(https:// で始まる物。試しの http://127.0.0.1 なども受ける)
  MADOGUCHI_IDENTITY  kikai のレコードの識別(email)。役 asa のレコード(このレコードは照会のコピーを読めない)

入った直後に、応答のレコードの役(yaku)が asa かを確かめ、違えばほかの要求を出さずに止まります(役の違うアカウントでは、
窓口の決まりが空の一覧を返し、届いているのに「ありません」と出てしまうため)。
  MADOGUCHI_PASSWORD  同じレコードのパスワード

パスワード、札、識別は、標準出力、標準エラー、例外の文に出しません。

終了コード: 0 読めた(0 件を含む)、1 止まった(入れない、役の違うアカウント、ネットワークの失敗、値の不足、env のファイルの権限)、
2 引数の誤り(URL の形を含む)。エラーは `エラー: ` で始まる行で標準エラーに出し(deploy/web/crontab.example の
監視の決まりと同じ)、追跡は --debug の時だけ出します。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

NAMES = ("MADOGUCHI_URL", "MADOGUCHI_IDENTITY", "MADOGUCHI_PASSWORD")
DEFAULT_ENV = "~/.config/digitaldoor/toiawase.env"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
PER_PAGE = 200          # 一度に引く数(PocketBase の上限は 1000)
TIMEOUT = 30            # 一つの要求を待つ秒
SUBJECT_MAX = 80        # 件名を切る長さ(文字)
FIELDS = "id,created,kind,subject"
FILTER = 'status="新しい"'
SHOWN = "%Y-%m-%d %H:%M"
ROLE = "asa"            # この道具が入る kikai のレコードの役(yaku)


class Stop(Exception):
    """利用者に見せる短いエラーで止まる。code は終了コード。文に秘密の値を入れない。"""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# ---- 入る値(窓口仕様書 8.2 と同じ読み方) ----
def check_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        parts.port
    except ValueError:
        raise Stop(f"窓口の URL の形が違います({url})", 2) from None
    ok = (parts.scheme == "https" and host) or (parts.scheme == "http" and host in LOCAL_HOSTS)
    if not ok or parts.query or parts.fragment:
        raise Stop(f"窓口の URL は https:// で始めてください({url})", 2)
    return url.rstrip("/")


def read_env_file(path: Path) -> dict[str, str]:
    try:
        st = os.stat(path)
    except OSError:
        raise Stop(f"env のファイルが読めません({path})") from None
    if st.st_mode & 0o077:
        raise Stop(f"env のファイルの権限が広すぎます。chmod 600 にしてください({path})")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Stop(f"env のファイルが読めません({path})") from None
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in NAMES:
            out[name] = value
    return out


def load_values(env_arg: str | None) -> dict[str, str]:
    if env_arg is not None:
        values = read_env_file(Path(env_arg).expanduser())
    else:
        path = Path(DEFAULT_ENV).expanduser()
        values = read_env_file(path) if os.path.lexists(path) else {}
    for name in NAMES:
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


def need(values: dict[str, str], url_arg: str | None) -> tuple[str, str, str]:
    url = url_arg if url_arg is not None else values.get("MADOGUCHI_URL", "")
    if url:
        url = check_url(url)
    missing = [n for n, v in (("MADOGUCHI_URL", url), ("MADOGUCHI_IDENTITY", values.get("MADOGUCHI_IDENTITY")),
                              ("MADOGUCHI_PASSWORD", values.get("MADOGUCHI_PASSWORD"))) if not v]
    if missing:
        raise Stop(f"窓口に入る値がありません({'、'.join(missing)})")
    return url, values["MADOGUCHI_IDENTITY"], values["MADOGUCHI_PASSWORD"]


# ---- 窓口への要求 ----
def call(base: str, method: str, path: str, body: dict | None = None, token: str = "") -> tuple[int, object]:
    """(HTTP の数, 応答の JSON か None)。HTTP の誤りは数で返し、ネットワークの失敗は Stop にする。本文は例外に入れない。"""
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = token
    try:
        with urlopen(Request(base + path, data=data, method=method, headers=headers), timeout=TIMEOUT) as res:
            status, payload = res.status, res.read()
    except HTTPError as e:
        status = e.code
        e.close()
        return status, None
    except (URLError, OSError) as e:
        why = getattr(e, "reason", None)
        kind = type(why if isinstance(why, BaseException) else e).__name__
        raise Stop(f"窓口につながりません({kind})") from None
    try:
        return status, json.loads(payload.decode("utf-8")) if payload else None
    except (UnicodeDecodeError, ValueError):
        return status, None


def sign_in(base: str, identity: str, password: str) -> str:
    status, data = call(base, "POST", "/api/collections/kikai/auth-with-password",
                        {"identity": identity, "password": password})
    token = data.get("token") if status == 200 and isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise Stop(f"窓口に入れませんでした(HTTP {status})")
    record = data.get("record")
    if not isinstance(record, dict) or record.get("yaku") != ROLE:
        raise Stop(f"この道具の役のアカウントではありません(要る役: {ROLE})")
    return token


def fetch_new(base: str, token: str) -> list[dict]:
    """状態が 新しい のお問い合わせを、受けた順に全部引く。"""
    out: list[dict] = []
    ids: set[str] = set()
    page = 1
    while True:
        q = urlencode({"filter": FILTER, "sort": "created", "page": page, "perPage": PER_PAGE, "skipTotal": 1,
                       "fields": FIELDS})
        status, data = call(base, "GET", f"/api/collections/toiawase/records?{q}", token=token)
        items = data.get("items") if status == 200 and isinstance(data, dict) else None
        if not isinstance(items, list):
            raise Stop(f"お問い合わせを引けませんでした(HTTP {status})")
        for r in items:
            if not isinstance(r, dict):
                raise Stop("お問い合わせの形が違います")
            rid = r.get("id")
            if rid in ids:
                raise Stop("お問い合わせの頁が重なっています")
            ids.add(rid)
            out.append(r)
        if len(items) < PER_PAGE:
            return out
        page += 1


# ---- 出す形 ----
def clean(text, limit: int | None = None) -> str:
    """改行、タブ、制御文字(Unicode の C で始まる種類)を空白に直し、limit を超えれば切って「…」を付ける。"""
    s = "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(text or ""))
    if limit is not None and len(s) > limit:
        s = s[:limit] + "…"
    return s


def when(created) -> str:
    """PocketBase の created(UTC、`YYYY-MM-DD HH:MM:SS.mmmZ`)を地方時の `YYYY-MM-DD HH:MM` に。読めなければ ?。"""
    s = str(created or "").strip().replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            d = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return d.astimezone().strftime(SHOWN)
    return "?"


def run(args) -> int:
    values = load_values(args.env)
    url, identity, password = need(values, args.url)
    token = sign_in(url, identity, password)
    items = fetch_new(url, token)
    now = datetime.now().strftime(SHOWN)
    if not items:
        print(f"新しいお問い合わせはありません({now} 現在)")
        return 0
    print(f"新しいお問い合わせ {len(items)} 件({now} 現在)")
    for r in items:
        print(f"{when(r.get('created'))}\t{clean(r.get('kind'))}\t{clean(r.get('subject'), SUBJECT_MAX)}")
    return 0


def build() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="toiawase_daily.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help=f"入る値のファイル(権限 600。省けば {DEFAULT_ENV}。無ければ環境変数だけを読む)")
    ap.add_argument("--url", help="窓口の PocketBase の基の URL(省けば env の MADOGUCHI_URL)")
    ap.add_argument("--debug", action="store_true", help="失敗の時、例外の追跡も出す(パスワード、札、本文は出さない)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build().parse_args(argv)
    try:
        return run(args)
    except Stop as e:
        if args.debug:
            traceback.print_exc()
        sys.stderr.write(f"エラー: {e}\n")
        return e.code
    except Exception as e:   # 思っていなかった誤り。文に秘密の値が入らないよう、種類だけを出す
        if args.debug:
            traceback.print_exc()
        sys.stderr.write(f"エラー: 思っていなかった誤りで止まりました({type(e).__name__})\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
