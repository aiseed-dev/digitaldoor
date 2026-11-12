"""html/js/madoguchi.js の、DOM に頼らない関数を node で動かす試験。

番号なし(サイトの決まりの確かめ)。スクリプトは node から require すると、
頁を触らずに純粋な関数だけを module.exports に出す。サインインの後の動き
(送る、一覧、消す、コピーの表)のうち、表示の文字を作る所と、戻りの URL を読む所を
ここで確かめる。PocketBase と DOM を通した動きは、docs/窓口仕様書.md 11.4 で手で確かめる。
node が無い環境では skip する。
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "html" / "js" / "madoguchi.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node がありません")


def run(expr: str):
    """M(madoguchi.js の module.exports)を使う式を node で評価し、JSON で受ける。地方時は Asia/Tokyo。"""
    code = (f"const M = require({json.dumps(str(JS))});\n"
            f"process.stdout.write(JSON.stringify((() => {{ return {expr}; }})()));\n")
    env = dict(os.environ, TZ="Asia/Tokyo")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_script_parses():
    """node --check が通る。"""
    out = subprocess.run([NODE, "--check", str(JS)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr


def test_redirect_url_from_page_location():
    """戻り先は今の origin と path から作り、末尾の index.html を除く(4.2)。"""
    assert run('M.redirectURLFrom("https://door.aiseed.dev", "/contact/")') == "https://door.aiseed.dev/contact/"
    assert run('M.redirectURLFrom("https://door.aiseed.dev", "/contact/index.html")') == \
        "https://door.aiseed.dev/contact/"
    assert run('M.redirectURLFrom("http://127.0.0.1:8000", "/contact/")') == "http://127.0.0.1:8000/contact/"
    assert run('M.baseFrom("https://pb.example.test/")') == "https://pb.example.test"


def test_read_return_none():
    """code、state、error のどれも無ければ戻りではない。"""
    r = run('M.readReturn("https://door.aiseed.dev/contact/")')
    assert r["isReturn"] is False


def test_read_return_strips_all_query():
    """戻りの code と state を読み、URL からは code、state と提供者が付けたほかの問いを全部消す。"""
    r = run('M.readReturn("https://door.aiseed.dev/contact/?state=S1&code=C%2F1&scope=email+openid'
            '&authuser=0&prompt=consent")')
    assert r["isReturn"] is True
    assert r["code"] == "C/1" and r["state"] == "S1" and r["error"] is None
    assert r["cleanURL"] == "https://door.aiseed.dev/contact/"
    r = run('M.readReturn("http://127.0.0.1:8000/contact/?error=access_denied&state=S1")')
    assert r["isReturn"] is True and r["error"] == "access_denied"
    assert r["cleanURL"] == "http://127.0.0.1:8000/contact/"


CONTROL = {"name": "google", "state": "S1", "codeVerifier": "V1",
           "redirectURL": "https://door.aiseed.dev/contact/"}


def check(ret: dict, control):
    return run(f"M.checkReturn({json.dumps(ret)}, {json.dumps(control)})")


def test_check_return_without_control():
    """控えが無い時は「サインインの控えがありません」。error より先に見る。"""
    r = check({"code": "C", "state": "S1", "error": "access_denied"}, None)
    assert r == {"ok": False, "message": "サインインの控えがありません。もう一度ボタンを押してください"}


def test_check_return_error():
    """error が付いていれば「取り消されたか」。"""
    r = check({"code": None, "state": "S1", "error": "access_denied"}, CONTROL)
    assert r["ok"] is False and r["message"].startswith("サインインが取り消されたか")


def test_check_return_state_mismatch_or_no_code():
    """state が違う時と code が無い時は「確かめが合いません」。"""
    for ret in ({"code": "C", "state": "S2", "error": None}, {"code": None, "state": "S1", "error": None},
                {"code": "C", "state": None, "error": None}):
        r = check(ret, CONTROL)
        assert r["ok"] is False and r["message"].startswith("サインインの確かめが合いません"), ret


def test_check_return_ok_builds_exchange_body():
    """合えば、auth-with-oauth2 に送る本文を控えから作る(redirectURL は控えの値)。"""
    r = check({"code": "C", "state": "S1", "error": None}, CONTROL)
    assert r == {"ok": True, "body": {"provider": "google", "code": "C", "codeVerifier": "V1",
                                      "redirectURL": "https://door.aiseed.dev/contact/"}}


def test_parse_control():
    """控えの文字列が壊れていれば null。"""
    assert run('M.parseControl("{oops")') is None
    assert run("M.parseControl(null)") is None
    assert run(f"M.parseControl({json.dumps(json.dumps(CONTROL))})") == CONTROL


def test_provider_buttons():
    """提供者ごとに「<displayName> でサインイン」。無効か空なら空の列。"""
    methods = {"oauth2": {"enabled": True, "providers": [
        {"name": "google", "displayName": "Google"}, {"name": "apple", "displayName": "Apple"}]}}
    assert run(f"M.providerButtons({json.dumps(methods)})") == [
        {"name": "google", "label": "Google でサインイン"}, {"name": "apple", "label": "Apple でサインイン"}]
    assert run('M.providerButtons({"oauth2": {"enabled": false, "providers": []}})') == []
    assert run('M.providerButtons({"password": {"enabled": true}})') == []
    assert run("M.providerButtons(null)") == []


def test_error_messages():
    """4.6 の表の文。"""
    assert run("M.errorMessage(0)").startswith("窓口につながりません")
    assert run('M.errorMessage(400, "send")').startswith("送れませんでした")
    assert run("M.errorMessage(401)") == "サインインし直してください"
    assert run("M.errorMessage(403)") == "サインインし直してください"
    assert run("M.errorMessage(429)").startswith("続けて送りすぎました")
    assert run("M.errorMessage(500)") == "うまくいきませんでした(500)"


def test_format_created_to_local_time():
    """PocketBase の UTC の日時を、地方時の YYYY-MM-DD HH:MM にする。"""
    assert run('M.formatCreated("2026-11-11 03:04:05.123Z")') == "2026-11-11 12:04"
    assert run('M.formatCreated("2026-12-31 15:30:00.000Z")') == "2027-01-01 00:30"
    assert run('M.formatCreated("")') == ""
    assert run('M.formatCreated("not a date")') == "not a date"


def test_toiawase_rows():
    """一覧の一件ごとの文字。値は文字のまま持ち、HTML として読まない(表示は textContent)。"""
    items = [{"id": "r1", "created": "2026-11-11 03:04:05.123Z", "kind": "質問",
              "subject": "<b>件名</b>", "body": "本文\n二行目", "reply": True, "status": "新しい"},
             {"id": "r2", "created": "2026-11-10 00:00:00.000Z", "kind": "相談",
              "subject": "架空の件名", "body": "", "reply": False, "status": "返事済み"}]
    rows = run(f"M.toiawaseRows({json.dumps(items, ensure_ascii=False)})")
    assert rows[0] == {"id": "r1", "created": "2026-11-11 12:04", "kind": "質問", "subject": "<b>件名</b>",
                       "status": "新しい", "reply": "返事が要る", "body": "本文\n二行目"}
    assert rows[1]["reply"] == "返事は要らない" and rows[1]["status"] == "返事済み"
    assert run("M.toiawaseRows(undefined)") == []


def test_delete_controls_two_steps():
    """消す時の確かめは頁の中の二段: 「消す」→ 注意の文と「やめる」「本当に消す」。
    「消す」のあった所(一つ目)は「やめる」で、二度押しでは消えない。"""
    assert run("M.deleteControls(false)") == {"note": "", "buttons": [{"action": "ask", "label": "消す"}]}
    assert run("M.deleteControls(true)") == {
        "note": "この問い合わせを消します。元に戻せません",
        "buttons": [{"action": "cancel", "label": "やめる"}, {"action": "do", "label": "本当に消す"}]}


def test_toiawase_body():
    """送る本文は status に「新しい」を入れる。件名か本文が空(空白だけを含む)なら null。"""
    assert run('M.toiawaseBody("u1", "質問", "件名", "本文", true)') == {
        "owner": "u1", "kind": "質問", "subject": "件名", "body": "本文", "reply": True, "status": "新しい"}
    assert run('M.toiawaseBody("u1", "質問", "  ", "本文", true)') is None
    assert run('M.toiawaseBody("u1", "質問", "件名", "\\n", false)') is None


SHEET = {"owner": "k3x9m2p7q1w8e5r", "issuer": "3f2a9c0d1e4b5a67", "made_at": "2026-12-10T12:00",
         "sheet": {"形式": "digitalkey-shoukai/1", "時刻": "2026-12-10T12:00",
                   "権限": [{"番号": "20261111T101500-a1b2c3", "対象の種類": "扉", "対象": "玄関",
                            "開始": "2026-12-01T09:00", "終了": "", "状態": "失効"}],
                   "鍵": [{"権限": "20261111T101500-a1b2c3", "経路": "署名つき資格情報", "扉": ["玄関", "裏口"],
                          "機器": "", "識別": "9d4e…", "状態": "待ち", "効くまで": "2027-01-01T00:00"},
                         {"権限": "20261111T101500-a1b2c3", "経路": "物理キー", "扉": [], "機器": "架空の箱",
                          "識別": "K-7", "状態": "人の作業が要る", "効くまで": ""}]}}


def test_sheet_view_ok():
    """コピーの表: 終了の空は「期限なし」、扉は「、」でつなぎ、扉が無ければ機器。"""
    v = run(f"M.sheetView([{json.dumps(SHEET, ensure_ascii=False)}])")
    assert v["state"] == "ok"
    assert v["madeAt"] == "2026-12-10T12:00" and v["issuer"] == "3f2a9c0d1e4b5a67"
    assert v["perms"] == [["20261111T101500-a1b2c3", "扉", "玄関", "2026-12-01T09:00", "期限なし", "失効"]]
    assert v["keys"] == [
        ["署名つき資格情報", "玄関、裏口", "9d4e…", "待ち", "2027-01-01T00:00", "20261111T101500-a1b2c3"],
        ["物理キー", "架空の箱", "K-7", "人の作業が要る", "", "20261111T101500-a1b2c3"]]


def test_sheet_view_none_and_unreadable():
    """コピーが無ければ none。形式が違えば unreadable。"""
    assert run("M.sheetView([])") == {"state": "none"}
    assert run("M.sheetView(undefined)") == {"state": "none"}
    other = dict(SHEET, sheet=dict(SHEET["sheet"], **{"形式": "digitalkey-shoukai/2"}))
    assert run(f"M.sheetView([{json.dumps(other, ensure_ascii=False)}])") == {"state": "unreadable"}
    assert run('M.sheetView([{"sheet": null}])') == {"state": "unreadable"}


def test_replace_query_params_like_sdk():
    """apple の時の URL: state を clientId に置き換え、問いを encodeURIComponent で書き直す(4.3 の 6)。"""
    auth = ("https://appleid.example.test/auth/authorize?client_id=x&state=OLD&response_mode=form_post"
            "&scope=name+email&redirect_uri=")
    url = run(f'M.replaceQueryParams({json.dumps(auth + "https://pb.example.test/api/oauth2-redirect")}, '
              '{"state": "CLIENT1"})')
    assert url == ("https://appleid.example.test/auth/authorize?client_id=x&state=CLIENT1"
                   "&response_mode=form_post&scope=name%20email"
                   "&redirect_uri=https%3A%2F%2Fpb.example.test%2Fapi%2Foauth2-redirect")
    assert run('M.replaceQueryParams("https://a.example.test/p", {"state": "S"})') == \
        "https://a.example.test/p?state=S"
    assert run('M.replaceQueryParams("https://a.example.test/p?a=1&b=2", {"a": null})') == \
        "https://a.example.test/p?b=2"
