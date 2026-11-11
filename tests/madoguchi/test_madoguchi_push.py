"""窓口仕様書 8 章、11.1: 置きに行く道具 `digitalkey madoguchi push`(digitalkey.madoguchi.push.main(argv))。

実装より先に書いた。まだ無い名前(digitalkey.madoguchi)は関数の中で引く(収集は通り、試しは ImportError で落ちる)。
argv は `madoguchi` の後ろの語(`digitalkey/cli.py` の振り分けがほかの命令に渡す形と同じで、`push` は main が受ける。8.1)。
相手は疑似の PocketBase(fake_pb.py)だけ。入る値は、試しが作った権限 600 の env のファイルで渡す(11.1)。
"""
from __future__ import annotations

import json
import os
import re
import socket

import pytest

from madoguchi_common import (ISSUER, OTHER_ISSUER, Result, item, no_secrets, sheet, write_env, write_items)

AT = "2026-12-10T12:00"
LATER = "2026-12-11T09:00"
REC_PATH = re.compile(r"^/api/collections/shoukai/records(/[a-z0-9]+)?$")
AUTH_PATH = "/api/collections/kikai/auth-with-password"


@pytest.fixture
def push(capsys, home):
    """push.main(["push", ...]) を呼び、(終了コード, 標準出力, 標準エラー) を返す。"""
    def run(*argv) -> Result:
        from digitalkey.madoguchi import push as mod
        try:
            code = mod.main(["push", *[str(a) for a in argv]])
        except SystemExit as e:
            code = e.code
        out, err = capsys.readouterr()
        return Result(0 if code is None else code, out, err)
    return run


def _users(pb, n: int) -> list[str]:
    return [pb.add_user()["id"] for _ in range(n)]


def _put(pb, owner: str, at: str = AT, state: str = "効いている", issuer: str = ISSUER) -> dict:
    """疑似の PocketBase に、今ある写しを置く。"""
    return pb.add_record("shoukai", {"owner": owner, "issuer": issuer, "made_at": at, "sheet": sheet(at, state)})


def _changes(pb):
    """レコードを変える要求(作る、直す、消す)。"""
    return [r for r in pb.record_requests(collection="shoukai") if r.method in ("POST", "PATCH", "DELETE")]


# ---- 11.1 の働きの確かめ ----

def test_create_update_same(pb, creds, push, tmp_path):
    """11.1 の働きの 1: A(同じ中身、時刻 と made_at だけ違う)、B(鍵の状態が違う)、C(無い)の時、C を作り、B を直し、
    A に触れない。出力は `作った 1、直した 1、消した 0、同じ 1`。作る本文はファイルの一人分そのまま、直す本文は
    {issuer, made_at, sheet}(8.4 の 4)。"""
    a, b, c = _users(pb, 3)
    ra = _put(pb, a, AT)
    rb = _put(pb, b, AT, "効いている")
    items = [item(a, LATER), item(b, LATER, "止まった"), item(c, LATER)]
    f = write_items(tmp_path / "shoukai.json", items)
    res = push("--file", f, "--env", creds.env)
    assert res.code == 0, res.err
    assert res.lines[-1] == "作った 1、直した 1、消した 0、同じ 1"
    now = {r["owner"]: r for r in pb.records("shoukai")}
    assert now[a]["made_at"] == AT and now[a]["sheet"] == ra["sheet"]          # A に触れない
    assert now[b]["made_at"] == LATER and now[b]["sheet"]["鍵"][0]["状態"] == "止まった"
    assert now[c]["sheet"] == items[2]["sheet"] and now[c]["issuer"] == ISSUER
    posts = [r for r in _changes(pb) if r.method == "POST"]
    patches = [r for r in _changes(pb) if r.method == "PATCH"]
    assert len(posts) == 1 and posts[0].json == items[2]
    assert len(patches) == 1 and patches[0].path.endswith("/" + rb["id"])
    assert set(patches[0].json) == {"issuer", "made_at", "sheet"}
    assert patches[0].json == {"issuer": ISSUER, "made_at": LATER, "sheet": items[1]["sheet"]}
    assert not any(r.method == "PATCH" and r.path.endswith("/" + ra["id"]) for r in _changes(pb))
    no_secrets(res, creds.identity, creds.password, pb=pb)


def test_delete_missing(pb, creds, push, tmp_path):
    """11.1 の働きの 2、8.4 の 5: ファイルから B を外すと B を消す。"""
    a, b = _users(pb, 2)
    _put(pb, a)
    rb = _put(pb, b)
    f = write_items(tmp_path / "shoukai.json", [item(a)])
    res = push("--file", f, "--env", creds.env)
    assert res.code == 0, res.err
    assert res.lines[-1] == "作った 0、直した 0、消した 1、同じ 1"
    assert [r["owner"] for r in pb.records("shoukai")] == [a]
    assert [r.path for r in _changes(pb)] == [f"/api/collections/shoukai/records/{rb['id']}"]


@pytest.mark.parametrize("dry", [False, True])
def test_empty_file_stops(pb, creds, push, tmp_path, dry):
    """8.3: 列が空の時は、PocketBase に触れる前に、何も変えずに止まる(終了コード 1、
    `エラー: 写しが空です。何も変えていません`)。今ある写しは一枚も消えず、kikai で入る要求も出ない。--dry-run でも同じ。"""
    a, b = _users(pb, 2)
    _put(pb, a)
    _put(pb, b)
    before = pb.records("shoukai")
    f = write_items(tmp_path / "empty.json", [])
    res = push("--file", f, "--env", creds.env, *(["--dry-run"] if dry else []))
    assert res.code == 1
    assert "エラー: 写しが空です。何も変えていません" in res.err_lines
    assert pb.requests == []
    assert pb.records("shoukai") == before
    no_secrets(res, creds.identity, creds.password, pb=pb)


def test_create_and_update_before_delete(pb, creds, push, tmp_path):
    """8.6「作る・直すが先、消すが後」: 消す要求は、作る・直す要求の全部の後に出る。"""
    a, b, c = _users(pb, 3)
    _put(pb, a)
    _put(pb, b, AT, "効いている")
    f = write_items(tmp_path / "shoukai.json", [item(b, LATER, "止まった"), item(c)])
    res = push("--file", f, "--env", creds.env)
    assert res.code == 0, res.err
    methods = [r.method for r in _changes(pb)]
    assert sorted(methods) == ["DELETE", "PATCH", "POST"]
    assert methods[-1] == "DELETE"


def test_same_when_only_time_differs(pb, creds, push, tmp_path):
    """8.4 の 4、8.6: issuer が同じで、sheet から 時刻 を除いた物が同じなら「同じ」(made_at が違っても直さない)。"""
    a, = _users(pb, 1)
    _put(pb, a, AT)
    f = write_items(tmp_path / "shoukai.json", [item(a, "2027-03-01T08:00")])
    res = push("--file", f, "--env", creds.env)
    assert res.code == 0, res.err
    assert res.lines[-1] == "作った 0、直した 0、消した 0、同じ 1"
    assert _changes(pb) == []


def test_dry_run(pb, creds, push, tmp_path):
    """11.1 の働きの 3、8.4: --dry-run は、レコードを作る・直す・消す要求(POST、PATCH、DELETE)を一つも出さず、
    `作る <owner>`、`直す <owner>`、`消す <owner>` の行と、最後に `作る <n>、直す <n>、消す <n>、同じ <n>(試しなので、何も変えていません)`。
    入る(1)と今ある写しを引く(2)は行う。"""
    a, b, c, d = _users(pb, 4)
    _put(pb, a)
    _put(pb, b, AT, "効いている")
    _put(pb, d)
    before = pb.records("shoukai")
    f = write_items(tmp_path / "shoukai.json", [item(a, LATER), item(b, LATER, "止まった"), item(c)])
    res = push("--file", f, "--env", creds.env, "--dry-run")
    assert res.code == 0, res.err
    assert _changes(pb) == []
    assert pb.records("shoukai") == before
    assert set(res.lines[:-1]) == {f"作る {c}", f"直す {b}", f"消す {d}"}
    assert res.lines[-1] == "作る 1、直す 1、消す 1、同じ 1(試しなので、何も変えていません)"
    assert any(r.path == AUTH_PATH for r in pb.requests)
    assert any(r.method == "GET" and REC_PATH.match(r.path) for r in pb.requests)


def test_other_issuer_stops(pb, creds, push, tmp_path):
    """11.1 の働きの 4、8.4 の 3: 今ある写しに、ファイルと違う issuer の物があれば、何も変えずに終了コード 1、
    `エラー: ほかの発行者の写しがあります`。"""
    a, b = _users(pb, 2)
    _put(pb, a, issuer=OTHER_ISSUER)
    f = write_items(tmp_path / "shoukai.json", [item(b)])
    res = push("--file", f, "--env", creds.env)
    assert res.code == 1
    assert "エラー: ほかの発行者の写しがあります" in res.err_lines
    assert _changes(pb) == []
    no_secrets(res, creds.identity, creds.password, pb=pb)


def test_one_failure_continues(pb, creds, push, tmp_path):
    """11.1 の働きの 5、8.4: C の作成が 400(owner の users のレコードが無い)の時、ほかは続け、標準エラーに
    `エラー: 作れませんでした <C>(HTTP 400)` の行が出て、終了コード 1。結果の行は出す(作った物は数えない)。"""
    a, b, d = _users(pb, 3)
    c = "nouserrecord0001"
    _put(pb, a)
    _put(pb, b, AT, "効いている")
    _put(pb, d)
    f = write_items(tmp_path / "shoukai.json", [item(c), item(a, LATER), item(b, LATER, "止まった")])
    res = push("--file", f, "--env", creds.env, "--debug")
    assert res.code == 1
    assert f"エラー: 作れませんでした {c}(HTTP 400)" in res.err_lines
    now = {r["owner"]: r for r in pb.records("shoukai")}
    assert now[b]["sheet"]["鍵"][0]["状態"] == "止まった"   # B は直した
    assert d not in now                                      # D は消した
    assert c not in now
    assert res.lines[-1] == "作った 0、直した 1、消した 1、同じ 1"
    no_secrets(res, creds.identity, creds.password, pb=pb)


@pytest.mark.parametrize("case", [
    "not_json", "not_utf8", "not_list", "item_not_dict", "missing_key", "extra_key", "bad_format", "dup_owner",
    "owner_symbol", "owner_long", "owner_empty", "issuer_upper", "issuer_short", "issuer_mixed", "made_at_bad",
    "sheet_not_dict",
])
def test_bad_file_no_request(pb, creds, push, tmp_path, case):
    """11.1 の働きの 6、8.3: 写しのファイルの形が違う時、PocketBase に要求を出さずに終了コード 1、
    `エラー: 写しのファイルが読めません(<理由>)`。"""
    a, b = "aaaa1111", "bbbb2222"
    good = [item(a), item(b)]
    f = tmp_path / "shoukai.json"
    if case == "not_json":
        f.write_text("[{", encoding="utf-8")
    elif case == "not_utf8":
        f.write_bytes(json.dumps(good, ensure_ascii=False).encode("cp932"))
    elif case == "not_list":
        write_items(f, {"owner": a})
    elif case == "item_not_dict":
        write_items(f, [item(a), "b"])
    elif case == "missing_key":
        x = item(a)
        del x["made_at"]
        write_items(f, [x])
    elif case == "extra_key":
        x = item(a)
        x["name"] = "架空の呼び名"
        write_items(f, [x])
    elif case == "bad_format":
        x = item(a)
        x["sheet"]["形式"] = "digitalkey-shoukai/2"
        write_items(f, [x])
    elif case == "dup_owner":
        write_items(f, [item(a), item(a)])
    elif case == "owner_symbol":
        write_items(f, [item("abc-123")])
    elif case == "owner_long":
        write_items(f, [item("a" * 41)])
    elif case == "owner_empty":
        write_items(f, [item("")])
    elif case == "issuer_upper":
        write_items(f, [item(a, issuer=ISSUER.upper())])
    elif case == "issuer_short":
        write_items(f, [item(a, issuer=ISSUER[:15])])
    elif case == "issuer_mixed":
        write_items(f, [item(a), item(b, issuer=OTHER_ISSUER)])
    elif case == "made_at_bad":
        write_items(f, [item(a, at="2026-12-10 12:00")])
    elif case == "sheet_not_dict":
        x = item(a)
        x["sheet"] = []
        write_items(f, [x])
    res = push("--file", f, "--env", creds.env)
    assert res.code == 1
    assert any(ln.startswith("エラー: 写しのファイルが読めません(") for ln in res.err_lines), res.err
    assert pb.requests == []


def test_owner_forty_chars_ok(pb, creds, push, tmp_path):
    """8.3: owner は英数字だけで 1〜40 文字。40 文字は読める(疑似に users が無いので作れず 1 だが、ファイルの誤りではない)。"""
    owner = "a" * 40
    f = write_items(tmp_path / "shoukai.json", [item(owner)])
    res = push("--file", f, "--env", creds.env)
    assert not any(ln.startswith("エラー: 写しのファイルが読めません") for ln in res.err_lines)
    assert any(r.path == AUTH_PATH for r in pb.requests)


def test_paging(pb, creds, push, tmp_path):
    """11.1 の働きの 7、8.4 の 2: 今ある写しが一頁(perPage の上限 1000)に入らない数でも、全部を引いてから比べる。
    page を 1 から増やして引く。全部が同じなら、作る・直す・消すは出ない。"""
    n = 1001
    owners = _users(pb, n)
    for o in owners:
        _put(pb, o)
    f = write_items(tmp_path / "shoukai.json", [item(o, LATER) for o in owners])
    res = push("--file", f, "--env", creds.env)
    assert res.code == 0, res.err
    assert res.lines[-1] == f"作った 0、直した 0、消した 0、同じ {n}"
    assert _changes(pb) == []
    pages = [r.q("page") for r in pb.record_requests(method="GET", collection="shoukai")]
    assert "1" in pages and "2" in pages


def test_list_request_shape(pb, creds, push, tmp_path):
    """8.4 の 2: 今ある写しは GET shoukai/records に page、perPage(1000 以下)、skipTotal、fields を付けて引く。"""
    a, = _users(pb, 1)
    f = write_items(tmp_path / "shoukai.json", [item(a)])
    assert push("--file", f, "--env", creds.env).code == 0
    gets = pb.record_requests(method="GET", collection="shoukai")
    assert gets
    r = gets[0]
    assert r.q("page") == "1"
    assert 1 <= int(r.q("perPage")) <= 1000
    assert r.q("skipTotal") in ("1", "true")
    assert set(r.q("fields").split(",")) == {"id", "owner", "issuer", "made_at", "sheet"}


def test_bad_url(pb, creds, push, tmp_path):
    """11.1 の働きの 8、8.2: --url http://example.com は終了コード 2。https:// のほかで、試しの 127.0.0.1 などでない物は 2。"""
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    for url in ("http://example.com", "ftp://127.0.0.1", "http://127.0.0.2:1", "example.com"):
        res = push("--file", f, "--env", creds.env, "--url", url)
        assert res.code == 2, url
    assert pb.requests == []


def test_url_option_overrides_env(pb, creds, push, tmp_path, home):
    """8.1: --url は env の MADOGUCHI_URL の代わりになる。http://127.0.0.1 は試しのため受ける。"""
    env = write_env(tmp_path / "nourl.env", {"MADOGUCHI_IDENTITY": creds.identity,
                                              "MADOGUCHI_PASSWORD": creds.password})
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f, "--env", env, "--url", pb.url)
    assert res.code == 0, res.err
    assert res.lines[-1] == "作った 1、直した 0、消した 0、同じ 0"


def test_file_required(creds, push):
    """8.5: --file が無ければ終了コード 2。"""
    assert push("--env", creds.env).code == 2


def test_missing_values(pb, push, tmp_path, home):
    """8.2: 値が足りない時は終了コード 1、`エラー: 窓口に入る値がありません(<足りない名前>)`。要求は出さない。"""
    ident, _ = pb.add_kikai("shoukai")
    env = write_env(tmp_path / "m.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": ident})
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f, "--env", env)
    assert res.code == 1
    assert any(ln.startswith("エラー: 窓口に入る値がありません(") and "MADOGUCHI_PASSWORD" in ln for ln in res.err_lines)
    assert pb.requests == []
    assert ident not in res.out + res.err


def test_env_file_format(pb, push, tmp_path, home):
    """8.2: env のファイルは一行に 名前=値。空の行と # で始まる行は読み飛ばす。値の両端の空白と、両端の " か ' の一組を外す。
    三つのほかの名前は読み飛ばす。"""
    ident, pw = pb.add_kikai("shoukai")
    env = tmp_path / "fmt.env"
    env.write_text("# 試しの env\n\n"
                   f"MADOGUCHI_URL = {pb.url}  \n"
                   f"MADOGUCHI_IDENTITY=\"{ident}\"\n"
                   f"MADOGUCHI_PASSWORD='{pw}'\n"
                   "OTHER_NAME=何か\n", encoding="utf-8")
    os.chmod(env, 0o600)
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f, "--env", env)
    assert res.code == 0, res.err
    auth = [r for r in pb.requests if r.path == AUTH_PATH]
    assert auth and auth[0].json == {"identity": ident, "password": pw}
    no_secrets(res, ident, pw, pb=pb)


def test_default_env_path(pb, push, tmp_path, home):
    """8.1: --env を省けば ~/.config/digitaldoor/madoguchi.env を読む。"""
    ident, pw = pb.add_kikai("shoukai")
    write_env(home / ".config" / "digitaldoor" / "madoguchi.env",
              {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": ident, "MADOGUCHI_PASSWORD": pw})
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f)
    assert res.code == 0, res.err


def test_no_default_env_uses_environment(pb, push, tmp_path, home, monkeypatch):
    """8.2: 既定の場所に無ければ読まず、環境変数だけで動く。"""
    ident, pw = pb.add_kikai("shoukai")
    monkeypatch.setenv("MADOGUCHI_URL", pb.url)
    monkeypatch.setenv("MADOGUCHI_IDENTITY", ident)
    monkeypatch.setenv("MADOGUCHI_PASSWORD", pw)
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f)
    assert res.code == 0, res.err
    no_secrets(res, ident, pw, pb=pb)


def test_auth_failure_stops(pb, creds, push, tmp_path):
    """8.4 の 1: 入れない時は `エラー: 窓口に入れませんでした(HTTP <数>)`、終了コード 1。写しには触れない。"""
    env = write_env(tmp_path / "bad.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": creds.identity,
                                            "MADOGUCHI_PASSWORD": "wrong-password-for-test"})
    f = write_items(tmp_path / "shoukai.json", [item("aaaa1111")])
    res = push("--file", f, "--env", env)
    assert res.code == 1
    assert "エラー: 窓口に入れませんでした(HTTP 400)" in res.err_lines
    assert pb.record_requests() == []


def test_list_failure_stops(pb, creds, push, tmp_path):
    """8.4、8.5: 今ある写しを引くのが失敗したら、そこで止まり終了コード 1。作る・直す・消すは出さない。"""
    pb.overrides.append(lambda r: (500, {"status": 500, "message": "x", "data": {}})
                        if r.method == "GET" and REC_PATH.match(r.path) else None)
    a, = _users(pb, 1)
    f = write_items(tmp_path / "shoukai.json", [item(a)])
    res = push("--file", f, "--env", creds.env)
    assert res.code == 1
    assert any(ln.startswith("エラー: ") for ln in res.err_lines)
    assert _changes(pb) == []


def test_network_failure(pb, creds, push, tmp_path):
    """8.4、8.5: 網の失敗(閉じた口)は、そこで止まり終了コード 1。エラーは `エラー: ` で始まる行で、追跡は出さない。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = write_env(tmp_path / "closed.env", {"MADOGUCHI_URL": f"http://127.0.0.1:{port}",
                                               "MADOGUCHI_IDENTITY": creds.identity,
                                               "MADOGUCHI_PASSWORD": creds.password})
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f, "--env", env)
    assert res.code == 1
    assert res.err_lines and all(ln.startswith("エラー: ") for ln in res.err_lines)
    assert "Traceback" not in res.err
    no_secrets(res, creds.identity, creds.password)


# ---- R-CM-13(考え方を当てる) ----

def test_r_cm_13_1_no_secret_arguments(push):
    """R-CM-13 11.1 の 1: --help の文に password と identity の引数が無い。--password を渡すと終了コード 2。"""
    res = push("--help")
    assert res.code == 0
    assert "--file" in res.out
    assert not re.search(r"--(password|identity|pass|user)\b", res.out)
    assert push("--file", "x.json", "--password", "試しの値").code == 2
    assert push("--file", "x.json", "--identity", "試しの値").code == 2


def test_r_cm_13_2_auth_failure_hides_secrets(pb, push, tmp_path, home):
    """R-CM-13 11.1 の 2: 入れない時(疑似が 400)、--debug を付けても、標準出力と標準エラーにパスワードの値と識別の値が
    現れない。終了コードは 1。"""
    ident, _ = pb.add_kikai("shoukai")
    wrong = "x" + os.urandom(8).hex()
    env = write_env(tmp_path / "w.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": ident,
                                          "MADOGUCHI_PASSWORD": wrong})
    f = write_items(tmp_path / "shoukai.json", [item("aaaa1111")])
    res = push("--file", f, "--env", env, "--debug")
    assert res.code == 1
    no_secrets(res, ident, wrong)


def test_r_cm_13_2_record_failure_debug_hides_secrets(pb, creds, push, tmp_path):
    """R-CM-13 8.2: 一件の失敗を --debug で出しても、識別、パスワード、札、要求と応答の本文の中身が出ない。"""
    f = write_items(tmp_path / "shoukai.json", [item("nouserrecord0002")])
    res = push("--file", f, "--env", creds.env, "--debug")
    assert res.code == 1
    no_secrets(res, creds.identity, creds.password, pb=pb)
    assert "c07b" not in res.out + res.err      # 送った本文(写しの中身)を例外に入れない
    assert "validation_missing_rel_records" not in res.out + res.err   # 応答の本文を例外に入れない


@pytest.mark.parametrize("mode,ok", [(0o644, False), (0o640, False), (0o604, False), (0o660, False),
                                     (0o600, True), (0o400, True)])
def test_r_cm_13_3_env_file_mode(pb, creds, push, tmp_path, mode, ok):
    """R-CM-13 11.1 の 3、8.2: env のファイルの権限が 644 などなら、PocketBase に要求を出さずに終了コード 1、
    `エラー: env のファイルの権限が広すぎます。chmod 600 にしてください(<道>)`。600 と 400 は通る。"""
    os.chmod(creds.env, mode)
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f, "--env", creds.env)
    if ok:
        assert res.code == 0, res.err
    else:
        assert res.code == 1
        assert f"エラー: env のファイルの権限が広すぎます。chmod 600 にしてください({creds.env})" in res.err_lines
        assert pb.requests == []
    no_secrets(res, creds.identity, creds.password, pb=pb)


def test_r_cm_13_4_environment_wins(pb, creds, push, tmp_path, monkeypatch):
    """R-CM-13 11.1 の 4、8.2: 環境変数があれば、ファイルの同じ名前より環境変数が勝つ。"""
    env = write_env(tmp_path / "w.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": creds.identity,
                                          "MADOGUCHI_PASSWORD": "wrong-password-for-test"})
    monkeypatch.setenv("MADOGUCHI_PASSWORD", creds.password)
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f, "--env", env)
    assert res.code == 0, res.err
    auth = [r for r in pb.requests if r.path == AUTH_PATH]
    assert auth[-1].json["password"] == creds.password


# ---- R-AC-13 ----

def test_r_ac_13_1_no_listen(pb, creds, push, tmp_path, monkeypatch):
    """R-AC-13 11.1 の 1: push は socket の bind と listen を呼ばない(待ち受けの口を作らない)。"""
    a, = _users(pb, 1)
    f = write_items(tmp_path / "shoukai.json", [item(a)])

    def forbidden(*args, **kw):
        raise AssertionError("待ち受けの口を作った")

    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(socket.socket, "listen", forbidden)
    res = push("--file", f, "--env", creds.env)
    assert res.code == 0, res.err
    assert res.lines[-1] == "作った 1、直した 0、消した 0、同じ 0"


def test_r_ac_13_2_only_two_paths(pb, creds, push, tmp_path):
    """R-AC-13 11.1 の 2: 疑似が受けた要求は、kikai/auth-with-password と shoukai/records のほかの道に触れない。
    入るのは kikai(役 shoukai)。"""
    a, b, c = _users(pb, 3)
    _put(pb, a)
    _put(pb, b)
    f = write_items(tmp_path / "shoukai.json", [item(a, LATER, "止まった"), item(c)])
    assert push("--file", f, "--env", creds.env).code == 0
    assert pb.requests
    for r in pb.requests:
        assert r.path == AUTH_PATH or REC_PATH.match(r.path), r.path
        if r.path == AUTH_PATH:
            assert r.method == "POST" and r.json["identity"] == creds.identity
        else:
            assert r.auth is not None and r.auth[0] == "kikai"


def test_r_ac_13_3_no_secrets_in_file_and_bodies(pb, creds, push, tmp_path):
    """R-AC-13 11.1 の 3、設計書 22.10 の R-AC-13 の 2: kanri の置き場から portal export で作った写しのファイルと、
    道具が送った本文に、資格情報の sig の値、識別の全部、発行者の公開鍵の 16 進、鍵のファイルの道が入っていない。"""
    from digitalkey.kanri import Unit
    from digitalkey.kanri import cli as kanri_cli
    from datetime import datetime
    u = Unit.create(tmp_path / "kanri", admin="adm", admin_name="管理者A")
    u.assign_role("adm", "名簿の係", reason="兼務", by="adm", role="管理者")
    u.register_subject("apr", "承認者A", "社員", False, by="adm", role="名簿の係")
    u.assign_role("apr", "承認する人", reason="承認の係", by="adm", role="管理者")
    u.register_subject("s1", "社員A", "社員", False, by="adm", role="名簿の係")
    u.register_door("玄関", ["署名つき資格情報", "門番を通す錠"], by="adm", role="管理者")
    r = u.request("s1", "扉", "玄関", "2026-12-01T09:00", "2027-01-01T00:00", "業務", by="adm", role="名簿の係")
    a = u.decide(r.id, "承認", "確かめた", by="apr", role="承認する人")
    u.issue(a.id, "s1", "扉", "玄関", "2026-12-01T09:00", "2027-01-01T00:00", by="adm", role="管理者",
            at=datetime.fromisoformat("2026-11-20T00:00"))
    got = u.issue_entity(r.id, "署名つき資格情報", by="adm", role="管理者",
                         at=datetime.fromisoformat("2026-11-20T00:00"))
    u.issue_entity(r.id, "門番を通す錠", by="adm", role="管理者", at=datetime.fromisoformat("2026-11-20T00:00"))
    owner = pb.add_user()["id"]
    u.link_portal("s1", owner, "対面", by="adm", role="名簿の係")
    out = tmp_path / "shoukai.json"
    assert kanri_cli.main(["portal", "export", "--dir", str(u.root), "--out", str(out), "--at", AT]) in (0, None)
    res = push("--file", out, "--env", creds.env)
    assert res.code == 0, res.err
    key_file = json.loads((u.root / "kanri.json").read_text(encoding="utf-8"))["鍵のファイル"]
    secrets_ = [got.資格情報["sig"], got.実体.識別, json.loads(got.資格情報["body"])["id"], u.public_key.hex(), key_file,
                (u.root / "鍵" / "発行者.key").read_bytes().hex()]
    texts = [out.read_text(encoding="utf-8")] + [r.text for r in pb.requests]
    for t in texts:
        for s in secrets_:
            assert s not in t


# ---- 振り分け ----

def test_dispatch_from_digitalkey_cli(capsys, home):
    """8.1: `digitalkey/cli.py` の振り分けに madoguchi を足し、`digitalkey madoguchi push --help` が push の説明を出す。"""
    from digitalkey.cli import main
    try:
        code = main(["madoguchi", "push", "--help"])
    except SystemExit as e:
        code = e.code
    out, _ = capsys.readouterr()
    assert code in (0, None)
    assert "--file" in out and "--dry-run" in out


# ---- 入り口の役(8.4 の 1 の後) ----

@pytest.mark.parametrize("dry", [False, True])
def test_wrong_role_entrance(pb, push, tmp_path, home, dry):
    """R-CM-13 8.4: 入った直後に応答の record の yaku を確かめ、shoukai でなければ、ほかの要求を出さずに終了コード 1、
    `エラー: この道具の役の入り口ではありません(要る役: shoukai)`。shoukai を引く要求も書く要求も出ない(--dry-run でも同じ)。
    識別、パスワード、札、応答の役の値は出さない。"""
    ident, pw = pb.add_kikai("asa")
    env = write_env(tmp_path / "asa.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": ident,
                                            "MADOGUCHI_PASSWORD": pw})
    a = pb.add_user()["id"]
    _put(pb, a)
    before = pb.records("shoukai")
    f = write_items(tmp_path / "shoukai.json", [item(pb.add_user()["id"])])
    res = push("--file", f, "--env", env, *(["--dry-run"] if dry else []))
    assert res.code == 1
    assert "エラー: この道具の役の入り口ではありません(要る役: shoukai)" in res.err_lines
    assert [r.path for r in pb.requests] == [AUTH_PATH]
    assert pb.record_requests() == []
    assert pb.records("shoukai") == before
    assert "asa" not in res.out + res.err.replace(ident, "")
    no_secrets(res, ident, pw, pb=pb)
