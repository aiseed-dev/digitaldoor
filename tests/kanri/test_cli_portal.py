"""設計書 22.9: 命令 `digitalkey kanri portal link|unlink|list|export`。ネットワークから切り離して動かす確かめ(22.10 の R-AC-19)もここに置く。

段 5a の実装より先に書いた。argv は `kanri` の後ろの語。終了コードは main の戻り値か SystemExit の code で読む。
時刻は `--at` に固定の値を渡す。
"""
from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
from dataclasses import dataclass
from datetime import datetime

import pytest

from digitalkey.kanri import Unit, cli as kanri_cli
from kanri_common import CREDENTIAL, ELLIPSIS, EXPORT_KEYS, GATE, KEY_KEYS, PERM_KEYS, SHEET_FORMAT, SHEET_KEYS, T0, T1, dt

AT = "2026-11-20T00:00"
REV = "2026-12-10T12:00"
A1 = "k3x9m2p7q1w8e5r"   # 22.6 の例の窓口の番号(架空)
A2 = "w5e6r7t8y9u0i1o"   # 架空
R = ("--by", "ros", "--role", "名簿の係")


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def json(self):
        return json.loads(self.out)

    @property
    def lines(self):
        return self.out.splitlines()


@pytest.fixture
def kanri(capsys):
    def run(*argv) -> Result:
        try:
            code = kanri_cli.main([str(a) for a in argv])
        except SystemExit as e:
            code = e.code
        out, err = capsys.readouterr()
        return Result(0 if code is None else code, out, err)
    return run


@pytest.fixture
def d(pfile_unit):
    """命令で開ける置き場(22.10 の下ごしらえ済み)の道。"""
    return pfile_unit.root


def _arg_error(res: Result) -> None:
    """終了コード 2 が、命令 portal の引数の誤りによる物(portal を知らない命令とした物ではない)。"""
    assert res.code == 2
    assert "invalid choice" not in res.err, res.err


def _grant_cli_free(u):
    """メソッドで s1 に 玄関 の権限と、門番を通す錠、署名つき資格情報を出す(命令の試験の下ごしらえ)。"""
    r = u.request("s1", "扉", "玄関", T0, T1, "業務", by="adm", role="名簿の係")
    a = u.decide(r.id, "承認", "確かめた", by="apr", role="承認する人")
    u.issue(a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=dt(AT))
    u.issue_entity(r.id, CREDENTIAL, by="adm", role="管理者", at=dt(AT))
    u.issue_entity(r.id, GATE, by="adm", role="管理者", at=dt(AT))
    return r.id


# ---- portal link ----

def test_link_human(kanri, d, pfile_unit):
    """R-EB-36 22.9 の例: portal link の人が読む形は `主体_結び <番号>` の一行、終了コード 0。"""
    res = kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    assert res.code == 0, res.err
    assert len(res.lines) == 1
    m = re.fullmatch(r"主体_結び (\S+)", res.lines[0])
    assert m
    u = Unit.open(d)
    assert u.portal_account("s1") == A1
    s = u.ledger.get(m.group(1))
    assert (s.form, s.issuer, s["確かめ方"], s["役"]) == ("主体_結び", "ros", "対面", "名簿の係")


def test_link_json(kanri, d):
    """R-EB-36 22.9: --json は {"ok": true, "様式": "主体_結び", "番号": "..."}。"""
    res = kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "ビデオ通話", "--json")
    assert res.code == 0, res.err
    j = res.json
    assert set(j) == {"ok", "様式", "番号"}
    assert (j["ok"], j["様式"]) == (True, "主体_結び")
    assert Unit.open(d).portal_links()[0].伝票 == j["番号"]


@pytest.mark.parametrize("drop", ["--by", "--role"])
def test_link_needs_by_and_role(kanri, d, drop):
    """R-EB-37 22.9、19.12: 書く命令(link)には --by と --role が要る(無ければ終了コード 2)。台帳は変わらない。"""
    argv = {"--by": ("--role", "名簿の係"), "--role": ("--by", "ros")}[drop]
    n = Unit.open(d).ledger.count()
    res = kanri("portal", "link", "--dir", d, *argv, "--subject", "s1", "--account", A1, "--how", "対面")
    _arg_error(res)
    assert Unit.open(d).ledger.count() == n


def test_link_needs_how(kanri, d):
    """R-EB-36 22.9: --how が無ければ終了コード 2。"""
    _arg_error(kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1))


def test_link_how_checked_by_form(kanri, d):
    """R-EB-36 22.9: --how は argparse の選択にせず、様式で確かめる。外れは FORM(終了コード 1)、標準エラーに `拒否: 様式に合いません`。"""
    res = kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "メール")
    assert res.code == 1
    assert res.err.startswith("拒否: 様式に合いません")
    res = kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "")
    assert res.code == 1
    assert res.err.strip() == "拒否: 様式に合いません(確かめ方: 必須です)"


def test_link_refused_account_linked(kanri, d):
    """R-EB-37 22.9、19.12: 拒否は標準エラーに `拒否: <理由>(<詳細>)`、終了コード 1。--json は標準出力に {"ok": false, ...}。"""
    assert kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面").code == 0
    res = kanri("portal", "link", "--dir", d, *R, "--subject", "s2", "--account", A1, "--how", "対面")
    assert res.code == 1
    assert res.err.strip() == "拒否: その番号は、ほかの主体に結ばれています(s1)"
    res = kanri("portal", "link", "--dir", d, *R, "--subject", "s2", "--account", A1, "--how", "対面", "--json")
    assert res.code == 1
    assert res.json == {"ok": False, "理由": "その番号は、ほかの主体に結ばれています", "詳細": "s1"}


def test_link_refused_role(kanri, d):
    """R-EB-37 22.9: 役が 管理者 なら ROLE_NOT_ALLOWED(終了コード 1)。"""
    res = kanri("portal", "link", "--dir", d, "--by", "adm", "--role", "管理者", "--subject", "s1", "--account", A1,
                "--how", "対面")
    assert res.code == 1
    assert res.err.strip() == "拒否: その役ではできません"


def test_link_account_not_alnum(kanri, d):
    """R-EB-37 22.9、22.6: 英数字だけでない番号は `拒否: 様式に合いません(窓口の番号: 英数字だけで書いてください)`。"""
    res = kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", "abc-123", "--how", "対面")
    assert res.code == 1
    assert res.err.strip() == "拒否: 様式に合いません(窓口の番号: 英数字だけで書いてください)"


# ---- portal unlink ----

def test_unlink_example(kanri, d):
    """R-EB-37 22.9 の例: 結んでいない主体の unlink は `拒否: 結ばれていません`(標準エラー、終了コード 1)。
    結んだ主体の unlink は `主体_結び <番号>`。"""
    res = kanri("portal", "unlink", "--dir", d, *R, "--subject", "s1", "--reason", "本人の申し出")
    assert res.code == 1
    assert res.err.strip() == "拒否: 結ばれていません"
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    res = kanri("portal", "unlink", "--dir", d, *R, "--subject", "s1", "--reason", "本人の申し出")
    assert res.code == 0, res.err
    assert re.fullmatch(r"主体_結び \S+", res.out.strip())
    assert Unit.open(d).portal_account("s1") == ""


def test_unlink_json_and_empty_reason(kanri, d):
    """R-EB-37 22.9: unlink --json は {"ok": true, "様式": "主体_結び", "番号"}。理由が空なら `拒否: 理由が要ります`(1)。"""
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    res = kanri("portal", "unlink", "--dir", d, *R, "--subject", "s1", "--reason", "")
    assert res.code == 1
    assert res.err.strip() == "拒否: 理由が要ります"
    res = kanri("portal", "unlink", "--dir", d, *R, "--subject", "s1", "--reason", "本人の申し出", "--json")
    assert res.code == 0, res.err
    assert res.json["ok"] is True and res.json["様式"] == "主体_結び" and res.json["番号"]


@pytest.mark.parametrize("drop", ["--by", "--role"])
def test_unlink_needs_by_and_role(kanri, d, drop):
    """R-EB-37 22.9: unlink にも --by と --role が要る(無ければ 2)。"""
    argv = {"--by": ("--role", "名簿の係"), "--role": ("--by", "ros")}[drop]
    _arg_error(kanri("portal", "unlink", "--dir", d, *argv, "--subject", "s1", "--reason", "本人の申し出"))


# ---- portal list ----

def test_list_human_and_json(kanri, d):
    """R-EB-37 22.9: portal list は --by、--role が要らない。一件一行 `<主体>\\t<窓口の番号>\\t<確かめ方>\\t<伝票>`。
    --json は {"ok": true, "items": [{"主体", "窓口の番号", "確かめ方", "伝票"}, ...]}。並びは portal_links のとおり。"""
    kanri("portal", "link", "--dir", d, *R, "--subject", "s2", "--account", A2, "--how", "ビデオ通話")
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    links = Unit.open(d).portal_links()
    res = kanri("portal", "list", "--dir", d)
    assert res.code == 0, res.err
    assert res.lines == [f"{x.主体}\t{x.窓口の番号}\t{x.確かめ方}\t{x.伝票}" for x in links]
    assert [ln.split("\t")[:3] for ln in res.lines] == [["s1", A1, "対面"], ["s2", A2, "ビデオ通話"]]
    res = kanri("portal", "list", "--dir", d, "--json")
    assert res.code == 0, res.err
    assert res.json == {"ok": True, "items": [
        {"主体": x.主体, "窓口の番号": x.窓口の番号, "確かめ方": x.確かめ方, "伝票": x.伝票} for x in links]}


def test_list_empty(kanri, d):
    """R-EB-37 22.9: 結びが無ければ、人が読む形は何も出さず、--json は items が空の列。終了コード 0。"""
    res = kanri("portal", "list", "--dir", d)
    assert (res.code, res.out) == (0, "")
    assert kanri("portal", "list", "--dir", d, "--json").json == {"ok": True, "items": []}


# ---- portal export ----

def test_export_human(kanri, d, tmp_path):
    """R-AC-19 22.9 の例: portal export は `コピー <人数> 人` と `ファイル <--out>` の二行。--by、--role は要らない。
    ファイルは権限 600 で新しく作り、中身は portal_export の列を json.dumps(..., ensure_ascii=False) にして改行を一つ付けた物。"""
    u = Unit.open(d)
    _grant_cli_free(u)
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    out = tmp_path / "shoukai.json"
    res = kanri("portal", "export", "--dir", d, "--out", out, "--at", REV)
    assert res.code == 0, res.err
    assert res.lines == ["コピー 1 人", f"ファイル {out}"]
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    want = Unit.open(d).portal_export(at=dt(REV))
    assert out.read_text(encoding="utf-8") == json.dumps(want, ensure_ascii=False) + "\n"
    assert json.loads(out.read_text(encoding="utf-8"))[0]["owner"] == A1


def test_export_json(kanri, d, tmp_path):
    """R-AC-19 22.9: --json は {"ok": true, "人数": n, "ファイル": "<--out>"}。"""
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    kanri("portal", "link", "--dir", d, *R, "--subject", "s2", "--account", A2, "--how", "対面")
    out = tmp_path / "shoukai.json"
    res = kanri("portal", "export", "--dir", d, "--out", out, "--at", REV, "--json")
    assert res.code == 0, res.err
    assert res.json == {"ok": True, "人数": 2, "ファイル": str(out)}


def test_export_empty(kanri, d, tmp_path):
    """R-AC-19 22.9、22.10 の働きの 5: 結んだ人が居なければ空の列 `[]` を書き、`コピー 0 人`。"""
    out = tmp_path / "shoukai.json"
    res = kanri("portal", "export", "--dir", d, "--out", out, "--at", REV)
    assert res.code == 0, res.err
    assert res.lines == ["コピー 0 人", f"ファイル {out}"]
    assert out.read_text(encoding="utf-8") == "[]\n"


def test_export_needs_out(kanri, d):
    """R-AC-19 22.9: portal export は --out が要る(無ければ終了コード 2)。"""
    _arg_error(kanri("portal", "export", "--dir", d, "--at", REV))


def test_export_out_exists(kanri, d, tmp_path):
    """R-AC-19 22.9: --out のファイルが既にあれば OUT_EXISTS(終了コード 1)。ファイルの中身と台帳は変わらない。"""
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    out = tmp_path / "shoukai.json"
    out.write_text("前からある中身\n", encoding="utf-8")
    n = Unit.open(d).ledger.count()
    res = kanri("portal", "export", "--dir", d, "--out", out, "--at", REV)
    assert res.code == 1
    assert res.err.strip() == "拒否: 出力のファイルが既にあります"
    assert out.read_text(encoding="utf-8") == "前からある中身\n"
    assert Unit.open(d).ledger.count() == n


def test_export_at_aware(kanri, d, tmp_path):
    """R-AC-19 22.9: --at は datetime.fromisoformat で読める物(時差つき可)。時差つきは地方時に直した時刻が made_at。"""
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    out = tmp_path / "shoukai.json"
    at = "2026-12-10T03:00:00+00:00"
    res = kanri("portal", "export", "--dir", d, "--out", out, "--at", at)
    assert res.code == 0, res.err
    local = datetime.fromisoformat(at).astimezone().strftime("%Y-%m-%dT%H:%M")
    item = json.loads(out.read_text(encoding="utf-8"))[0]
    assert item["made_at"] == local and item["sheet"]["時刻"] == local


def test_export_at_omitted(kanri, d, tmp_path):
    """R-AC-19 22.9: --at を省けば今。made_at は 19.5 の形。"""
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    out = tmp_path / "shoukai.json"
    lo = datetime.now().strftime("%Y-%m-%dT%H:%M")
    assert kanri("portal", "export", "--dir", d, "--out", out).code == 0
    hi = datetime.now().strftime("%Y-%m-%dT%H:%M")
    made = json.loads(out.read_text(encoding="utf-8"))[0]["made_at"]
    assert lo <= made <= hi


def test_export_does_not_change_ledger(kanri, d, tmp_path):
    """R-AC-13 22.10 の R-AC-13 の 3: portal export は台帳の伝票の数を変えない。"""
    kanri("portal", "link", "--dir", d, *R, "--subject", "s1", "--account", A1, "--how", "対面")
    n = Unit.open(d).ledger.count()
    assert kanri("portal", "export", "--dir", d, "--out", tmp_path / "x.json", "--at", REV).code == 0
    assert Unit.open(d).ledger.count() == n


# ---- R-AC-19 ネットワークから切り離して動かす ----

_WATCH: dict = {"paths": None}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _audit_hook(event, args):
    paths = _WATCH["paths"]
    if paths is None:
        return
    targets = []
    if event == "open":
        path, mode, flags = args
        writing = (isinstance(flags, int) and flags & _WRITE_FLAGS) or \
            (isinstance(mode, str) and any(c in mode for c in "wax+"))
        if writing:
            targets.append(path)
    elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate", "os.utime", "sqlite3.connect"):
        targets.append(args[0])
    elif event in ("os.rename", "os.replace", "os.symlink", "os.link"):
        targets.extend(args[:2])
    for t in targets:
        if isinstance(t, int) or t is None:
            continue
        p = os.path.abspath(os.fsdecode(t))
        if "__pycache__" in p or p.endswith(".pyc") or p.startswith("/dev/") or p == ":memory:":
            continue
        paths.append(p)


if not getattr(sys, "_kanri_portal_test_hook", False):
    sys.addaudithook(_audit_hook)
    sys._kanri_portal_test_hook = True


def test_r_ac_19_portal_offline(kanri, tmp_path, monkeypatch):
    """R-AC-19 22.10: 19.13 の筋書きのネットワークを塞いだまま、続けて portal link、portal list、portal export --out FILE、
    portal unlink を main([...]) で呼ぶ。全部の終了コードが 0 で、--out のファイルが権限 600 で 22.7 の形の JSON の列。
    書くのは --dir の下と --out だけ。既にある --out は OUT_EXISTS で、ファイルの中身は変わらない。"""
    real_socket = socket.socket

    class NoNetwork(real_socket):
        def __init__(self, *a, **kw):
            raise OSError("ネットワークは使わない")

    def no_connection(*a, **kw):
        raise OSError("ネットワークは使わない")

    monkeypatch.setattr(socket, "socket", NoNetwork)
    monkeypatch.setattr(socket, "create_connection", no_connection)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    d = tmp_path / "kanri"
    cred_out = tmp_path / "s1-cred.json"
    out = tmp_path / "shoukai.json"
    W = ("--by", "adm", "--role")

    _WATCH["paths"] = []
    try:
        codes = []

        def step(*argv):
            res = kanri(*argv, "--dir", d, "--json")
            codes.append((argv[:2], res.code, res.err))
            return res

        # 19.13 の筋書き
        step("init", "--admin", "adm", "--admin-name", "管理者A")
        step("role", "grant", *W, "管理者", "--subject", "adm", "--name", "名簿の係", "--reason", "兼務")
        step("subject", "add", *W, "名簿の係", "--id", "apr", "--name", "承認者A", "--kind", "社員")
        step("subject", "add", *W, "名簿の係", "--id", "s1", "--name", "社員A", "--kind", "社員")
        step("role", "grant", *W, "管理者", "--subject", "apr", "--name", "承認する人", "--reason", "承認の係")
        step("door", "add", *W, "管理者", "--name", "玄関", "--routes", "署名つき資格情報,門番を通す錠")
        pid = step("request", *W, "名簿の係", "--subject", "s1", "--door", "玄関", "--from", T0, "--until", T1,
                   "--reason", "業務").json["番号"]
        aid = step("decide", "--by", "apr", "--role", "承認する人", "--request", pid, "--approve").json["番号"]
        step("issue", *W, "管理者", "--approval", aid, "--at", AT, "--subject", "s1", "--door", "玄関", "--from", T0,
             "--until", T1)
        step("entity", "issue", *W, "管理者", "--permission", pid, "--at", AT, "--route", "署名つき資格情報",
             "--out", cred_out)
        step("entity", "issue", *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE)
        step("revoke", *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV)
        # 段 5a の命令
        step("portal", "link", *W, "名簿の係", "--subject", "s1", "--account", A1, "--how", "対面")
        listed = step("portal", "list").json
        exported = step("portal", "export", "--out", out, "--at", REV).json
        step("portal", "unlink", *W, "名簿の係", "--subject", "s1", "--reason", "本人の申し出")
        before = out.read_bytes()
        again = kanri("portal", "export", "--dir", d, "--out", out, "--at", REV)
        written = list(_WATCH["paths"])
    finally:
        _WATCH["paths"] = None

    assert [c for c in codes if c[1] != 0] == [], codes
    assert [x["窓口の番号"] for x in listed["items"]] == [A1]
    assert exported == {"ok": True, "人数": 1, "ファイル": str(out)}
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    item = data[0]
    assert set(item) == set(EXPORT_KEYS) and item["owner"] == A1 and item["made_at"] == REV
    assert set(item["sheet"]) == set(SHEET_KEYS) and item["sheet"]["形式"] == SHEET_FORMAT
    assert all(set(p) == set(PERM_KEYS) for p in item["sheet"]["権限"])
    assert all(set(k) == set(KEY_KEYS) for k in item["sheet"]["鍵"])
    rows = {k["経路"]: k for k in item["sheet"]["鍵"]}
    assert rows[CREDENTIAL]["状態"] == "待ち" and rows[CREDENTIAL]["識別"].endswith(ELLIPSIS)
    assert rows[GATE]["状態"] == "止まった"
    # 既にある --out は OUT_EXISTS で、中身は変わらない
    assert again.code == 1 and "出力のファイルが既にあります" in again.out + again.err
    assert out.read_bytes() == before
    # 書くのは --dir の下と --out だけ
    root = os.path.realpath(d)
    allowed = {os.path.realpath(out), os.path.realpath(cred_out)}
    outside = [p for p in written
               if not (os.path.realpath(p) == root or os.path.realpath(p).startswith(root + os.sep)
                       or os.path.realpath(p) in allowed)]
    assert outside == []
    assert written, "書いた道が一つも見えない(監視が効いていない)"
    assert sorted(x.name for x in tmp_path.iterdir()) == sorted(["cwd", "kanri", "s1-cred.json", "shoukai.json"])
    assert list(cwd.iterdir()) == []
