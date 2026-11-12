# SPDX-License-Identifier: AGPL-3.0-or-later
"""照会用のコピーを窓口へ置きに行く(窓口仕様書 8 章)。

    digitalkey madoguchi push --file /media/usb/shoukai.json
    digitalkey madoguchi push --file /media/usb/shoukai.json --dry-run

コピーのファイルは `digitalkey kanri portal export --out` が出した物です。道具は、窓口の PocketBase のコレクション
shoukai を、ファイルと同じ中身にそろえます。

  1. コピーのファイルを確かめる(窓口に触れる前)。形が違う、空の列のファイルは、何も変えずに止まる
  2. システムのアカウント(kikai、役 shoukai)で入る。入った応答のレコードの役(yaku)が shoukai でなければ、ほかの要求を
     出さずに止まる(役の違うアカウントでは、窓口の決まりが空の一覧を返し、取り違えに気づけないため)
  3. 今あるコピーを全部引く。ファイルと違う発行者のコピーがあれば、何も変えずに止まる
  4. ファイルの順に、無い人のコピーを作り、中身(sheet の 時刻 を除いた物と issuer)が違う人のコピーを直す
  5. ファイルに無い人のコピーを消す(作る・直すの後)
  6. 標準出力に `作った <n>、直した <n>、消した <n>、同じ <n>` を出す

4 と 5 で一件が失敗しても残りを続け、失敗した物を `エラー: 作れませんでした <窓口の番号>(HTTP <数>)` の形で
標準エラーに出して、終了コード 1 で終わります。--dry-run は 1〜3 を行い、作る・直す・消す物を一件一行で出すだけで、
窓口のコピーを変えません。

結んだ人を全部外して窓口のコピーを全部消す時は、空の列のファイルでは消えません(途中で切れたファイルや作り損ねた
ファイルでコピーが全部消える事を防ぐため)。運営者が窓口の管理画面で消します。

入る値は env のファイル(一行に 名前=値。権限は 600 か、より狭く)と環境変数から読みます。同じ名前があれば
環境変数が勝ちます。パスワードと識別を受ける引数はありません(シェルの履歴に残るため)。

  MADOGUCHI_URL       窓口の PocketBase の基の URL(https:// で始まる物。試しの http://127.0.0.1 なども受ける)
  MADOGUCHI_IDENTITY  kikai のレコードの識別(email)。役 shoukai のレコード
  MADOGUCHI_PASSWORD  同じレコードのパスワード

パスワード、札、識別は、標準出力、標準エラー、例外の文に出しません。

終了コード: 0 できた(変える物が無かった時と、--dry-run を含む)、1 止まった、または一件でも失敗した
(env のファイルの権限、値の不足、コピーのファイルの誤り、入れない、役の違うアカウント、ほかの発行者のコピー、ネットワークの失敗)、
2 引数の誤り(--file が無い、URL の形)。エラーは `エラー: ` で始まる行で出し、追跡は --debug の時だけ出します。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

NAMES = ("MADOGUCHI_URL", "MADOGUCHI_IDENTITY", "MADOGUCHI_PASSWORD")
DEFAULT_ENV = "~/.config/digitaldoor/madoguchi.env"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
SHEET_FORMAT = "digitalkey-shoukai/1"
ITEM_KEYS = {"owner", "issuer", "made_at", "sheet"}
OWNER_RE = re.compile(r"[A-Za-z0-9]{1,40}")
ISSUER_RE = re.compile(r"[0-9a-f]{16}")
MADE_AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}")
PER_PAGE = 200          # 今あるコピーを一度に引く数(PocketBase の上限は 1000)
TIMEOUT = 30            # 一つの要求を待つ秒
FIELDS = "id,owner,issuer,made_at,sheet"
ROLE = "shoukai"        # この道具が入る kikai のレコードの役(yaku)


class Stop(Exception):
    """利用者に見せる短いエラーで止まる。code は終了コード。文に秘密の値を入れない。"""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


# ---- 入る値 ----
def check_url(url: str) -> str:
    """https:// で始まる URL か、試しの http://127.0.0.1、http://localhost、http://[::1]。ほかは終了コード 2。"""
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
    """env のファイルを読む。持ち主のほかに読み書き実行のどれかがあれば、読まずに止まる。"""
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


def load_values(env_arg: str | None, default: str) -> dict[str, str]:
    """env のファイル(--env か既定の場所。既定の場所に無ければ読まない)を読み、環境変数を上に重ねる。"""
    if env_arg is not None:
        values = read_env_file(Path(env_arg).expanduser())
    else:
        path = Path(default).expanduser()
        values = read_env_file(path) if os.path.lexists(path) else {}
    for name in NAMES:
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


def need(values: dict[str, str], url_arg: str | None) -> tuple[str, str, str]:
    """URL(--url が勝つ)、識別、パスワード。URL の形が違えば終了コード 2、足りなければ 1。"""
    url = url_arg if url_arg is not None else values.get("MADOGUCHI_URL", "")
    if url:
        url = check_url(url)
    missing = [n for n, v in (("MADOGUCHI_URL", url), ("MADOGUCHI_IDENTITY", values.get("MADOGUCHI_IDENTITY")),
                              ("MADOGUCHI_PASSWORD", values.get("MADOGUCHI_PASSWORD"))) if not v]
    if missing:
        raise Stop(f"窓口に入る値がありません({'、'.join(missing)})")
    return url, values["MADOGUCHI_IDENTITY"], values["MADOGUCHI_PASSWORD"]


# ---- コピーのファイル(8.3) ----
def read_items(path: Path) -> list[dict]:
    """コピーのファイルを読み、8.3 の形を確かめる。外れたら Stop。空の列も Stop。"""
    def bad(why: str) -> Stop:
        return Stop(f"コピーのファイルが読めません({why})")

    try:
        raw = path.read_bytes()
    except OSError:
        raise bad("ファイルを開けません") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise bad("UTF-8 ではありません") from None
    try:
        data = json.loads(text)
    except ValueError:
        raise bad("JSON ではありません") from None
    if not isinstance(data, list):
        raise bad("一番外が列ではありません")
    if not data:
        raise Stop("コピーが空です。何も変えていません")
    seen: set[str] = set()
    issuer = None
    for n, x in enumerate(data, 1):
        if not isinstance(x, dict):
            raise bad(f"{n} 人目が辞書ではありません")
        if set(x) != ITEM_KEYS:
            raise bad(f"{n} 人目の鍵が owner、issuer、made_at、sheet の四つではありません")
        owner = x["owner"]
        if not (isinstance(owner, str) and OWNER_RE.fullmatch(owner)):
            raise bad(f"{n} 人目の owner は英数字だけで 1〜40 文字にしてください")
        if owner in seen:
            raise bad(f"owner {owner} が二度出ています")
        seen.add(owner)
        if not (isinstance(x["issuer"], str) and ISSUER_RE.fullmatch(x["issuer"])):
            raise bad(f"{n} 人目の issuer は 16 桁の小文字の 16 進にしてください")
        if issuer is None:
            issuer = x["issuer"]
        elif x["issuer"] != issuer:
            raise bad("issuer が人によって違います")
        if not (isinstance(x["made_at"], str) and MADE_AT_RE.fullmatch(x["made_at"])):
            raise bad(f"{n} 人目の made_at の形が違います(YYYY-MM-DDTHH:MM)")
        sheet = x["sheet"]
        if not isinstance(sheet, dict) or sheet.get("形式") != SHEET_FORMAT:
            raise bad(f"{n} 人目の sheet の 形式 が {SHEET_FORMAT} ではありません")
    return data


# ---- 窓口への要求 ----
class Client:
    """窓口の PocketBase への要求。応答の本文と要求の本文は、例外の文に入れない。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self.token = ""

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        """(HTTP の数, 応答の JSON か None)。HTTP の誤りは数で返し、ネットワークの失敗は Stop にする。"""
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = self.token
        req = Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=TIMEOUT) as res:
                status, payload = res.status, res.read()
        except HTTPError as e:
            status = e.code
            e.close()
            return status, None
        except (URLError, OSError) as e:
            why = getattr(e, "reason", None)
            kind = type(why if isinstance(why, BaseException) else e).__name__
            raise Stop(f"窓口につながりません({kind})") from None
        if not payload:
            return status, None
        try:
            return status, json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return status, None

    def sign_in(self, identity: str, password: str) -> None:
        status, data = self.call("POST", "/api/collections/kikai/auth-with-password",
                                 {"identity": identity, "password": password})
        token = data.get("token") if status == 200 and isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise Stop(f"窓口に入れませんでした(HTTP {status})")
        record = data.get("record")
        if not isinstance(record, dict) or record.get("yaku") != ROLE:
            raise Stop(f"この道具の役のアカウントではありません(要る役: {ROLE})")
        self.token = token


def fetch_all(client: Client) -> list[dict]:
    """shoukai の今あるコピーを、page を 1 から増やして全部引く。"""
    out: list[dict] = []
    ids: set[str] = set()
    page = 1
    while True:
        q = urlencode({"page": page, "perPage": PER_PAGE, "skipTotal": 1, "fields": FIELDS})
        status, data = client.call("GET", f"/api/collections/shoukai/records?{q}")
        items = data.get("items") if status == 200 and isinstance(data, dict) else None
        if not isinstance(items, list):
            raise Stop(f"今あるコピーを引けませんでした(HTTP {status})")
        for r in items:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str):
                raise Stop("今あるコピーの形が違います")
            if r["id"] in ids:
                raise Stop("今あるコピーの頁が重なっています")
            ids.add(r["id"])
            out.append(r)
        if len(items) < PER_PAGE:
            return out
        page += 1


def _content(sheet) -> object:
    """比べる中身。sheet から 時刻 を除いた物(8.6)。"""
    return {k: v for k, v in sheet.items() if k != "時刻"} if isinstance(sheet, dict) else sheet


def plan(items: list[dict], existing: list[dict]) -> tuple[list[tuple[str, dict, str]], list[dict], int]:
    """(ファイルの順の (作る|直す, 一人分, 今あるコピーの id) の列、消すコピーの列、同じの数)。"""
    by_owner: dict[str, dict] = {}
    for r in existing:
        by_owner.setdefault(r.get("owner"), r)
    todo, same = [], 0
    for x in items:
        r = by_owner.get(x["owner"])
        if r is None:
            todo.append(("作る", x, ""))
        elif r.get("issuer") == x["issuer"] and _content(r.get("sheet")) == _content(x["sheet"]):
            same += 1
        else:
            todo.append(("直す", x, r["id"]))
    owners = {x["owner"] for x in items}
    delete = [r for r in existing if r.get("owner") not in owners]
    return todo, delete, same


def run(args) -> int:
    values = load_values(args.env, DEFAULT_ENV)
    url, identity, password = need(values, args.url)
    items = read_items(Path(args.file))
    client = Client(url)
    client.sign_in(identity, password)
    existing = fetch_all(client)
    issuer = items[0]["issuer"]
    if any(r.get("issuer") != issuer for r in existing):
        raise Stop("ほかの発行者のコピーがあります")
    todo, delete, same = plan(items, existing)

    if args.dry_run:
        for kind, x, _ in todo:
            print(f"{kind} {x['owner']}")
        for r in delete:
            print(f"消す {r.get('owner')}")
        n_create = sum(1 for kind, _, _ in todo if kind == "作る")
        print(f"作る {n_create}、直す {len(todo) - n_create}、消す {len(delete)}、同じ {same}"
              "(試しなので、何も変えていません)")
        return 0

    made = fixed = gone = 0
    failed = False
    for kind, x, rid in todo:      # 作る・直すが先、消すが後(8.6)
        if kind == "作る":
            status, _ = client.call("POST", "/api/collections/shoukai/records", x)
            if status in (200, 201):
                made += 1
            else:
                failed = True
                sys.stderr.write(f"エラー: 作れませんでした {x['owner']}(HTTP {status})\n")
        else:
            body = {"issuer": x["issuer"], "made_at": x["made_at"], "sheet": x["sheet"]}
            status, _ = client.call("PATCH", f"/api/collections/shoukai/records/{quote(rid, safe='')}", body)
            if status == 200:
                fixed += 1
            else:
                failed = True
                sys.stderr.write(f"エラー: 直せませんでした {x['owner']}(HTTP {status})\n")
    for r in delete:
        status, _ = client.call("DELETE", f"/api/collections/shoukai/records/{quote(r['id'], safe='')}")
        if status in (200, 204):
            gone += 1
        else:
            failed = True
            sys.stderr.write(f"エラー: 消せませんでした {r.get('owner')}(HTTP {status})\n")
    print(f"作った {made}、直した {fixed}、消した {gone}、同じ {same}")
    return 1 if failed else 0


def build() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="digitalkey madoguchi", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<命令>")
    p = sub.add_parser("push", help="照会用のコピーを窓口へ置きに行く", description=__doc__,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True, help="digitalkey kanri portal export --out が出したコピーのファイル")
    p.add_argument("--env", help=f"入る値のファイル(権限 600。省けば {DEFAULT_ENV}。無ければ環境変数だけを読む)")
    p.add_argument("--url", help="窓口の PocketBase の基の URL(省けば env の MADOGUCHI_URL)")
    p.add_argument("--dry-run", action="store_true", help="作る・直す・消す物を出すだけで、窓口のコピーを変えない")
    p.add_argument("--debug", action="store_true", help="失敗の時、例外の追跡も出す(パスワード、札、本文は出さない)")
    return ap


def main(argv: list[str] | None = None) -> int:
    """`digitalkey madoguchi push ...` を動かし、終了コードを返す(argv は madoguchi の後ろの語)。"""
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
