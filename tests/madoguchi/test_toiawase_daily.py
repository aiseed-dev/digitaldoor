"""窓口仕様書 9 章、11.2: 毎朝の一覧 `deploy/madoguchi/toiawase_daily.py`(番号なし。サイトの運用の道具)。

実装より先に書いた。道具は deb2 の `.venv` の python で動かす物なので、試しも別のプロセスとして動かす
(`sys.executable deploy/madoguchi/toiawase_daily.py ...`)。時刻帯は TZ=Asia/Tokyo に決めて、受けた日時の直し方を確かめる。
相手は疑似の PocketBase(fake_pb.py)だけ。入るのは役 asa の kikai。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from madoguchi_common import REPO, Result, no_secrets, write_env

SCRIPT = REPO / "deploy" / "madoguchi" / "toiawase_daily.py"
AUTH_PATH = "/api/collections/kikai/auth-with-password"
HEAD = re.compile(r"^新しいお問い合わせ (\d+) 件\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}) 現在\)$")
NONE = re.compile(r"^新しいお問い合わせはありません\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}) 現在\)$")


@pytest.fixture
def daily(home):
    """毎朝の一覧を別のプロセスで動かす。HOME は試しの置き場、MADOGUCHI_* は渡さない(渡す物は extra_env で)。"""
    assert SCRIPT.exists(), f"{SCRIPT} がありません"

    def run(*argv, extra_env: dict | None = None) -> Result:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "TZ": "Asia/Tokyo", "LANG": "C.UTF-8",
               "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
        env.update(extra_env or {})
        p = subprocess.run([sys.executable, str(SCRIPT), *[str(a) for a in argv]], capture_output=True, text=True,
                           encoding="utf-8", env=env, timeout=60, cwd=str(REPO))
        return Result(p.returncode, p.stdout, p.stderr)
    return run


def _toiawase(pb, owner, kind, subject, created, status="新しい", body="本文の架空の文ヌルヲ"):
    return pb.add_record("toiawase", {"owner": owner, "kind": kind, "subject": subject, "body": body, "reply": True,
                                      "status": status}, created=created)


def test_new_items(pb, asa_creds, daily):
    """11.2 の 1: status が 新しい の物二つと 返事済み の物一つがある時、出力は件数 2 の行と、古い順の二行
    (受けた日時を地方時の YYYY-MM-DD HH:MM に直し、種類、件名をタブでつなぐ)。本文、メールアドレス、owner、id が現れない。"""
    user = pb.add_user(email="toiawase-hito@example.invalid")
    r2 = _toiawase(pb, user["id"], "教室", "件名の文その二", "2026-11-11 21:40:00.000Z")
    r1 = _toiawase(pb, user["id"], "質問", "件名の文その一", "2026-11-11 12:03:00.000Z")
    r3 = _toiawase(pb, user["id"], "相談", "返事済みの件名", "2026-11-10 01:00:00.000Z", status="返事済み")
    res = daily("--env", asa_creds.env)
    assert res.code == 0, res.err
    assert len(res.lines) == 3
    m = HEAD.match(res.lines[0])
    assert m and m.group(1) == "2"
    assert res.lines[1:] == ["2026-11-11 21:03\t質問\t件名の文その一", "2026-11-12 06:40\t教室\t件名の文その二"]
    for s in ("本文の架空の文ヌルヲ", user["email"], user["id"], r1["id"], r2["id"], r3["id"], "返事済みの件名"):
        assert s not in res.out + res.err
    no_secrets(res, asa_creds.identity, asa_creds.password, pb=pb)


def test_request_shape_and_asa(pb, asa_creds, daily):
    """11.2 の 2、9 章: 役 asa の kikai で入り、toiawase の一覧を filter status="新しい"、sort created で引く。
    fields に body が入っていない。"""
    user = pb.add_user()
    _toiawase(pb, user["id"], "質問", "件名", "2026-11-11 12:03:00.000Z")
    res = daily("--env", asa_creds.env)
    assert res.code == 0, res.err
    auth = [r for r in pb.requests if r.path == AUTH_PATH]
    assert auth and auth[0].json == {"identity": asa_creds.identity, "password": asa_creds.password}
    lists = pb.record_requests(method="GET", collection="toiawase")
    assert lists
    for r in lists:
        assert r.q("filter").replace(" ", "") == 'status="新しい"'
        assert r.q("sort") == "created"
        assert "body" not in [f.strip() for f in r.q("fields", "").split(",")]
        assert set(f.strip() for f in r.q("fields").split(",")) == {"id", "created", "kind", "subject"}
        assert r.auth is not None and r.auth[0] == "kikai"
    # 入ったのは asa の kikai(疑似の決まりで、shoukai の kikai なら一覧は空になる)
    for r in pb.requests:
        assert r.path == AUTH_PATH or r.path == "/api/collections/toiawase/records", r.path


def test_subject_control_chars(pb, asa_creds, daily):
    """11.2 の 3、9 章: 件名の改行、タブ、制御文字は空白に直す。"""
    user = pb.add_user()
    _toiawase(pb, user["id"], "質問", "一行目\n二行目\tタブ\x1b[31m色", "2026-11-11 12:03:00.000Z")
    res = daily("--env", asa_creds.env)
    assert res.code == 0, res.err
    assert len(res.lines) == 2
    when, kind, subj = res.lines[1].split("\t")
    assert (when, kind) == ("2026-11-11 21:03", "質問")
    assert "\n" not in subj and "\x1b" not in subj
    assert subj.startswith("一行目 二行目 タブ ")
    assert "\x1b" not in res.out


def test_long_subject_cut(pb, asa_creds, daily):
    """9 章: 長い件名は作る時に選んだ長さ(例 80 文字)で切って「…」を付ける。
    【解釈】長さは実装が選ぶので、200 文字の件名が「…」で終わり、元より短くなる事だけを見る。"""
    user = pb.add_user()
    long = "長" * 200
    _toiawase(pb, user["id"], "質問", long, "2026-11-11 12:03:00.000Z")
    res = daily("--env", asa_creds.env)
    assert res.code == 0, res.err
    subj = res.lines[1].split("\t")[2]
    assert subj.endswith("…") and len(subj) < 200


def test_zero(pb, asa_creds, daily):
    """11.2 の 4: 0 件なら `新しいお問い合わせはありません` で始まる一行、終了コード 0。"""
    user = pb.add_user()
    _toiawase(pb, user["id"], "質問", "閉じた件名", "2026-11-11 12:03:00.000Z", status="閉じた")
    res = daily("--env", asa_creds.env)
    assert res.code == 0, res.err
    assert len(res.lines) == 1
    assert NONE.match(res.lines[0]), res.out


def test_auth_failure(pb, asa_creds, daily, tmp_path):
    """11.2 の 5: 入れない時、`エラー: ` で始まる行が標準エラーに出て、終了コード 1。パスワードは出ない。"""
    wrong = "x" + os.urandom(8).hex()
    env = write_env(tmp_path / "w.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": asa_creds.identity,
                                          "MADOGUCHI_PASSWORD": wrong})
    res = daily("--env", env, "--debug")
    assert res.code == 1
    assert any(ln.startswith("エラー: ") for ln in res.err_lines)
    no_secrets(res, asa_creds.identity, wrong)


def test_env_file_mode(pb, asa_creds, daily):
    """9 章、8.2: env のファイルの権限が 644 なら、要求を出さずに終了コード 1。"""
    os.chmod(asa_creds.env, 0o644)
    res = daily("--env", asa_creds.env)
    assert res.code == 1
    assert any(ln.startswith("エラー: env のファイルの権限が広すぎます") for ln in res.err_lines)
    assert pb.requests == []


def test_default_env_path(pb, asa_creds, daily, home):
    """9 章: --env の既定は ~/.config/digitaldoor/toiawase.env。"""
    write_env(home / ".config" / "digitaldoor" / "toiawase.env", {
        "MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": asa_creds.identity, "MADOGUCHI_PASSWORD": asa_creds.password})
    res = daily()
    assert res.code == 0, res.err
    assert NONE.match(res.lines[0])


def test_environment_wins(pb, asa_creds, daily, tmp_path):
    """9 章、8.2: 環境変数は env のファイルの同じ名前より勝つ。"""
    env = write_env(tmp_path / "w.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": asa_creds.identity,
                                          "MADOGUCHI_PASSWORD": "wrong-password-for-test"})
    res = daily("--env", env, extra_env={"MADOGUCHI_PASSWORD": asa_creds.password})
    assert res.code == 0, res.err


def test_missing_values(pb, daily, tmp_path, home):
    """9 章、8.2: 値が足りない時は終了コード 1、`エラー: 窓口に入る値がありません(...)`。"""
    env = write_env(tmp_path / "m.env", {"MADOGUCHI_URL": pb.url})
    res = daily("--env", env)
    assert res.code == 1
    assert any(ln.startswith("エラー: 窓口に入る値がありません(") for ln in res.err_lines)
    assert pb.requests == []


def test_bad_url(pb, asa_creds, daily):
    """9 章、8.2: URL は https:// か試しの http://127.0.0.1 などだけ。ほかは終了コード 2(引数の誤り)。"""
    res = daily("--env", asa_creds.env, "--url", "http://example.com")
    assert res.code == 2
    assert pb.requests == []


def test_paging(pb, asa_creds, daily):
    """9 章の 2: 全部を引くまで繰り返す。一頁(perPage の上限 1000)に入らない数でも全部を数える。"""
    user = pb.add_user()
    n = 1001
    for i in range(n):
        _toiawase(pb, user["id"], "質問", f"件名{i:04d}", f"2026-11-11 {i // 60 % 24:02d}:{i % 60:02d}:00.000Z")
    res = daily("--env", asa_creds.env)
    assert res.code == 0, res.err
    m = HEAD.match(res.lines[0])
    assert m and m.group(1) == str(n)
    assert len(res.lines) == n + 1
    pages = [r.q("page") for r in pb.record_requests(method="GET", collection="toiawase")]
    assert "1" in pages and "2" in pages


def test_help(daily):
    """9 章: 説明は docstring と --help に書く。--help は終了コード 0 で、パスワードと識別を受ける引数が無い。"""
    res = daily("--help")
    assert res.code == 0
    assert "--env" in res.out
    assert not re.search(r"--(password|identity)\b", res.out)


def test_no_digitalkey_import():
    """9 章: digitalkey を import しない(deb2 の .venv には python だけを入れる)。標準ライブラリだけで書く。"""
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+digitalkey", text, re.M)


def test_wrong_role_entrance(pb, daily, tmp_path):
    """9 章: 入った直後に応答の record の yaku を確かめ、asa でなければ、toiawase を引く要求を出さずに終了コード 1、
    `エラー: この道具の役のアカウントではありません(要る役: asa)`。識別、パスワード、札、応答の役の値は出さない。
    (役の違うアカウントだと、決まりが空の一覧を返し、「ありません」と正常に終わってしまうため。)"""
    ident, pw = pb.add_kikai("shoukai")
    env = write_env(tmp_path / "shoukai.env", {"MADOGUCHI_URL": pb.url, "MADOGUCHI_IDENTITY": ident,
                                                "MADOGUCHI_PASSWORD": pw})
    user = pb.add_user()
    _toiawase(pb, user["id"], "質問", "届いている件名", "2026-11-11 12:03:00.000Z")
    res = daily("--env", env)
    assert res.code == 1
    assert "エラー: この道具の役のアカウントではありません(要る役: asa)" in res.err_lines
    assert [r.path for r in pb.requests] == [AUTH_PATH]
    assert pb.record_requests() == []
    assert "新しいお問い合わせ" not in res.out
    assert "shoukai" not in (res.out + res.err).replace(ident, "")
    no_secrets(res, ident, pw, pb=pb)
