"""設計書 19.12: 命令 `digitalkey kanri`(digitalkey.kanri.cli.main(argv))。ネットワークから切り離して動かす確かめ(19.13)もここに置く。

argv は `kanri` の後ろの語(`digitalkey/cli.py` の振り分けがほかの命令に渡す形と同じ)。
終了コードは、main の戻り値か SystemExit の code で読む(どちらの作りでも同じに読む)。
時刻は `--at` に固定の値を渡す(19.12)。`--at` を省いて今の時計で動く事は、期間を広く取った試験一つで見る。
"""
from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from digitaldoor_controller.audit import Ed25519Signer
from digitaldoor_controller.slips import key_id

from digitalkey.kanri import Unit, cli as kanri_cli, reasons
from kanri_common import GATE, PHYSICAL, T0, T1, plus

AT = "2026-11-20T00:00"   # 発行と実体を出す時刻(開始の前)
IN = "2026-12-05T10:00"   # 期間の内
REV = "2026-12-10T12:00"  # 失効の時刻(19.10 の例)


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


W = ("--by", "adm", "--role")   # 書く命令の by(--role は続けて書く)
SLIP = re.compile(r"^(\S+) (\S+)$")


def _one_slip(res: Result, form: str) -> str:
    """人が読む形の `<様式> <番号>` 一行から番号を返す。"""
    assert res.code == 0, res.err
    assert len(res.lines) == 1, res.out
    m = SLIP.match(res.lines[0])
    assert m and m.group(1) == form, res.out
    return m.group(2)


def _json_slip(res: Result, form: str) -> str:
    assert res.code == 0, res.err
    j = res.json
    assert j["ok"] is True and j["様式"] == form
    return j["番号"]


def _granted(kanri, d, subject="s1", door="玄関"):
    """命令で申請、承認、発行する。(権限の番号, 承認の番号, 発行の番号)。"""
    pid = _json_slip(kanri("request", "--dir", d, *W, "名簿の係", "--subject", subject, "--door", door,
                           "--from", T0, "--until", T1, "--reason", "業務", "--json"), "権限_申請")
    aid = _json_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                           "--approve", "--json"), "権限_承認")
    iid = _json_slip(kanri("issue", "--dir", d, *W, "管理者", "--approval", aid, "--at", AT, "--subject", subject,
                           "--door", door, "--from", T0, "--until", T1, "--json"), "権限_発行")
    return pid, aid, iid


# ---- init ----

def test_init_human(kanri, tmp_path):
    """19.12: init は `発行者 <識別>` と `公開鍵 <16 進>` の二行。鍵/発行者.key を作る。"""
    d = tmp_path / "srv"
    res = kanri("init", "--dir", d, "--admin", "adm", "--admin-name", "管理者A")
    assert res.code == 0, res.err
    info = json.loads((d / "kanri.json").read_text(encoding="utf-8"))
    assert res.lines == [f"発行者 {info['発行者']}", f"公開鍵 {info['公開鍵']}"]
    assert re.fullmatch(r"[0-9a-f]{64}", info["公開鍵"])
    assert info["発行者"] == key_id(bytes.fromhex(info["公開鍵"]))
    assert stat.S_IMODE(os.stat(d / "鍵" / "発行者.key").st_mode) == 0o600
    u = Unit.open(d)
    assert u.subject("adm").持つ役 == {"管理者", "本人"}


def test_init_json_and_kind(kanri, tmp_path):
    """19.12: init --json は {"ok", "発行者", "公開鍵", "台帳": "<置き場>/台帳"}。--admin-kind は種別。"""
    d = tmp_path / "srv"
    res = kanri("init", "--dir", d, "--admin", "adm", "--admin-name", "管理者A", "--admin-kind", "住人", "--json")
    assert res.code == 0, res.err
    j = res.json
    u = Unit.open(d)
    assert (j["ok"], j["発行者"], j["公開鍵"]) == (True, u.issuer, u.public_key.hex())
    assert Path(j["台帳"]).resolve() == (d / "台帳").resolve()
    assert u.subject("adm").種別 == "住人"


def test_init_with_issuer_key(kanri, tmp_path):
    """19.12: 発行者の鍵は init --issuer-key FILE のファイルの道で渡す。"""
    signer = Ed25519Signer.generate()
    keyfile = tmp_path / "issuer.key"
    keyfile.write_bytes(signer.private_bytes())
    res = kanri("init", "--dir", tmp_path / "srv", "--admin", "adm", "--admin-name", "管理者A",
                "--issuer-key", keyfile)
    assert res.code == 0, res.err
    assert res.lines[0] == f"発行者 {key_id(signer.public_key())}"
    assert not (tmp_path / "srv" / "鍵" / "発行者.key").exists()


def test_init_twice_is_refused(kanri, tmp_path):
    """19.12: 拒否(ALREADY_CREATED)は標準エラーに `拒否: <理由>`、終了コード 1。"""
    d = tmp_path / "srv"
    assert kanri("init", "--dir", d, "--admin", "adm", "--admin-name", "管理者A").code == 0
    res = kanri("init", "--dir", d, "--admin", "adm", "--admin-name", "管理者A")
    assert res.code == 1
    assert res.err.startswith("拒否: 既に作られています")


# ---- 引数と置き場 ----

def test_dir_from_env(kanri, file_unit, monkeypatch):
    """19.12: --dir を省けば環境変数 DIGITALKEY_KANRI_DIR。"""
    monkeypatch.setenv("DIGITALKEY_KANRI_DIR", str(file_unit.root))
    res = kanri("subject", "list", "--json")
    assert res.code == 0, res.err
    assert [i["識別"] for i in res.json["items"]] == ["adm", "apr", "s1"]


def test_no_dir_is_usage_error(kanri, monkeypatch):
    """19.12: --dir も環境変数も無ければ終了コード 2。"""
    monkeypatch.delenv("DIGITALKEY_KANRI_DIR", raising=False)
    assert kanri("subject", "list").code == 2


def test_write_command_needs_by_and_role(kanri, file_unit):
    """19.12: 書く命令に --by か --role が無ければ終了コード 2。読む命令では要らない。"""
    d = file_unit.root
    n = file_unit.ledger.count()
    assert kanri("subject", "add", "--dir", d, "--role", "名簿の係", "--id", "c1", "--name", "清掃A",
                 "--kind", "清掃").code == 2
    assert kanri("subject", "add", "--dir", d, "--by", "adm", "--id", "c1", "--name", "清掃A",
                 "--kind", "清掃").code == 2
    assert Unit.open(d).ledger.count() == n
    assert kanri("door", "list", "--dir", d).code == 0


def test_argparse_error_is_2(kanri, file_unit):
    """19.12: argparse の誤りは終了コード 2。"""
    assert kanri("nonsense", "--dir", file_unit.root).code == 2
    assert kanri("request", "--dir", file_unit.root, *W, "名簿の係", "--subject", "s1", "--door", "玄関",
                 "--group", "事務所", "--from", T0, "--reason", "業務").code == 2


def test_no_unit_is_3(kanri, tmp_path):
    """19.12: 置き場を開けない(NO_UNIT)は終了コード 3、標準エラーに理由。"""
    res = kanri("subject", "list", "--dir", tmp_path / "none")
    assert res.code == 3
    assert "置き場がありません" in res.err


def test_no_key_is_3(kanri, make_unit):
    """19.12: 鍵のファイルが空の置き場(signer で作った物)は NO_KEY で終了コード 3。"""
    u = make_unit("A")
    res = kanri("subject", "list", "--dir", u.root)
    assert res.code == 3
    assert "発行者の鍵がありません" in res.err


def test_no_key_value_arguments(kanri, tmp_path):
    """19.12: 鍵の値はどの引数でも受けない(--issuer-key はファイルの道)。16 進の値を渡すと読めず NO_KEY で 3、置き場はできない。"""
    hexkey = Ed25519Signer.generate().private_bytes().hex()
    res = kanri("init", "--dir", tmp_path / "srv", "--admin", "adm", "--admin-name", "管理者A",
                "--issuer-key", hexkey)
    assert res.code == 3
    assert "発行者の鍵がありません" in res.err
    assert not (tmp_path / "srv" / "kanri.json").exists()


@pytest.mark.parametrize("content", [None, b"\x01" * 31])
def test_init_unreadable_issuer_key_is_3(kanri, tmp_path, content):
    """19.12 の終了コード: init --issuer-key のファイルが読めない時と 32 バイトでない時の NO_KEY は 3、標準エラーに理由。"""
    keyfile = tmp_path / "issuer.key"
    if content is not None:
        keyfile.write_bytes(content)
    res = kanri("init", "--dir", tmp_path / "srv", "--admin", "adm", "--admin-name", "管理者A",
                "--issuer-key", keyfile)
    assert res.code == 3
    assert "発行者の鍵がありません" in res.err
    assert not (tmp_path / "srv" / "kanri.json").exists()


# ---- 伝票を書いた時の出力と拒否 ----

def test_slip_output_human_and_json(kanri, file_unit):
    """19.12: 伝票を書いた時は `<様式> <番号>` 一行、--json は {"ok", "様式", "番号"}。"""
    d = file_unit.root
    num = _one_slip(kanri("subject", "add", "--dir", d, *W, "名簿の係", "--id", "c1", "--name", "清掃A",
                          "--kind", "清掃"), "主体_登録")
    assert Unit.open(d).ledger.get(num)["識別"] == "c1"
    num = _json_slip(kanri("subject", "add", "--dir", d, *W, "名簿の係", "--id", "v1", "--name", "保守会社A",
                           "--kind", "業者", "--vendor", "--json"), "主体_登録")
    assert Unit.open(d).subject("v1").業者 is True


def test_refused_human_and_json(kanri, file_unit):
    """19.12: 拒否は標準エラーに `拒否: <理由>`、--json は標準出力に {"ok": false, "理由", "詳細"}。終了コード 1。"""
    d = file_unit.root
    res = kanri("subject", "add", "--dir", d, "--by", "s1", "--role", "名簿の係", "--id", "c1", "--name", "清掃A",
                "--kind", "清掃")
    assert res.code == 1
    assert res.err.startswith("拒否: その役を持っていません")
    res = kanri("group", "add", "--dir", d, *W, "管理者", "--name", "事務所", "--doors", "玄関,倉庫", "--json")
    assert res.code == 1
    assert res.json == {"ok": False, "理由": reasons.UNKNOWN_DOOR, "詳細": "倉庫"}
    res = kanri("group", "add", "--dir", d, *W, "管理者", "--name", "事務所", "--doors", "玄関,倉庫")
    assert res.code == 1
    assert res.err.strip() == "拒否: この発行者の扉ではありません(倉庫)"


def test_spec_example_issue(kanri, file_unit):
    """19.12 の例: issue は `権限_発行 <番号>`。申請の番号を --approval に渡すと `拒否: 承認がありません`、終了コード 1。"""
    d = file_unit.root
    pid = _one_slip(kanri("request", "--dir", d, *W, "名簿の係", "--subject", "s1", "--door", "玄関",
                          "--from", T0, "--until", T1, "--reason", "業務"), "権限_申請")
    aid = _one_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                          "--approve"), "権限_承認")
    n = Unit.open(d).ledger.count()
    res = kanri("issue", "--dir", d, *W, "管理者", "--approval", pid, "--at", AT, "--subject", "s1", "--door", "玄関",
                "--from", T0, "--until", T1)
    assert res.code == 1
    assert res.err.strip() == "拒否: 承認がありません"
    assert Unit.open(d).ledger.count() == n
    _one_slip(kanri("issue", "--dir", d, *W, "管理者", "--approval", aid, "--at", AT, "--subject", "s1", "--door", "玄関",
                    "--from", T0, "--until", T1), "権限_発行")


def test_issue_mismatch_detail(kanri, file_unit):
    """19.12: 詳細のある拒否は `拒否: <理由>(<詳細>)`。"""
    d = file_unit.root
    pid = _json_slip(kanri("request", "--dir", d, *W, "名簿の係", "--subject", "s1", "--door", "玄関",
                           "--from", T0, "--until", T1, "--reason", "業務", "--json"), "権限_申請")
    aid = _json_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                           "--approve", "--json"), "権限_承認")
    res = kanri("issue", "--dir", d, *W, "管理者", "--approval", aid, "--at", AT, "--subject", "apr", "--door", "玄関",
                "--from", T0, "--until", T1)
    assert res.code == 1
    assert res.err.strip() == "拒否: 承認と合いません(主体)"


def test_decide_reject_and_group_request(kanri, file_unit):
    """19.12: decide --reject、request --group。"""
    d = file_unit.root
    _one_slip(kanri("group", "add", "--dir", d, *W, "管理者", "--name", "事務所", "--doors", "玄関"), "群")
    pid = _json_slip(kanri("request", "--dir", d, *W, "名簿の係", "--subject", "s1", "--group", "事務所",
                           "--from", T0, "--reason", "業務", "--json"), "権限_申請")
    rid = _json_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                           "--reject", "--reason", "必要が無い", "--json"), "権限_承認")
    u = Unit.open(d)
    assert u.ledger.get(rid)["判断"] == "却下"
    p = u.permission(pid)
    assert (p.対象の種類, p.対象, p.終了, p.状態) == ("群", "事務所", "", "却下")


def test_roster_commands(kanri, file_unit):
    """19.12: subject change、role grant / remove、door add(--retire)、setting set。"""
    d = file_unit.root
    _one_slip(kanri("subject", "change", "--dir", d, *W, "名簿の係", "--id", "s1", "--vendor", "はい",
                    "--state", "停止", "--reason", "契約の変更"), "主体_変更")
    _one_slip(kanri("role", "grant", "--dir", d, *W, "管理者", "--subject", "apr", "--name", "監査",
                    "--reason", "監査の当番"), "役_割当")
    rm = _one_slip(kanri("role", "remove", "--dir", d, *W, "管理者", "--subject", "apr", "--name", "運用",
                         "--reason", "当番の終わり"), "役_割当")
    _one_slip(kanri("door", "add", "--dir", d, *W, "管理者", "--name", "倉庫", "--routes", "物理キー",
                    "--place", "一階の奥"), "扉")
    _one_slip(kanri("door", "add", "--dir", d, *W, "管理者", "--name", "倉庫", "--routes", "物理キー",
                    "--retire"), "扉")
    _one_slip(kanri("setting", "set", "--dir", d, *W, "管理者", "--item", "業者の最長の日数", "--value", "30",
                    "--reason", "契約の上限"), "設定")
    u = Unit.open(d)
    s = u.subject("s1")
    assert (s.業者, s.状態) == (True, "停止")
    assert "監査" in u.subject("apr").持つ役
    assert u.ledger.get(rm)["判断"] == "解除"
    assert [(x.名前, x.状態) for x in u.doors()] == [("玄関", "使用"), ("倉庫", "撤去")]
    assert u.setting("業者の最長の日数") == "30"
    res = kanri("setting", "set", "--dir", d, *W, "管理者", "--item", "自己承認", "--value", "はい",
                "--reason", "試し")
    assert res.code == 1 and res.err.startswith("拒否: 設定の値が読めません")


def test_group_retire(kanri, file_unit):
    """19.12: group add --retire で状態 廃止。"""
    d = file_unit.root
    _one_slip(kanri("group", "add", "--dir", d, *W, "管理者", "--name", "事務所", "--doors", "玄関"), "群")
    _one_slip(kanri("group", "add", "--dir", d, *W, "管理者", "--name", "事務所", "--doors", "玄関", "--retire"),
              "群")
    assert [(g.名前, g.状態) for g in Unit.open(d).groups()] == [("事務所", "廃止")]


# ---- 一覧と trust-entry ----

def test_lists(kanri, file_unit):
    """19.12: 一覧は一件一行(タブでつなぐ)、--json は {"ok", "items": [<型の属性を鍵にした辞書>]}(set と list は列)。"""
    d = file_unit.root
    res = kanri("subject", "list", "--dir", d)
    assert res.code == 0 and len(res.lines) == 3
    assert all("\t" in line for line in res.lines)
    items = kanri("subject", "list", "--dir", d, "--json").json["items"]
    adm = items[0]
    assert set(adm) >= {"識別", "呼び名", "種別", "業者", "状態", "仮名", "持つ役"}
    assert adm["識別"] == "adm" and sorted(adm["持つ役"]) == sorted(["管理者", "名簿の係", "本人"])
    doors = kanri("door", "list", "--dir", d, "--json").json["items"]
    assert doors[0]["名前"] == "玄関" and doors[0]["経路"] == ["署名つき資格情報", "門番を通す錠", "物理キー"]


def test_permission_and_entity_lists(kanri, file_unit):
    """19.12: permission list [--subject] [--at]、entity list [--subject]。"""
    d = file_unit.root
    pid, aid, iid = _granted(kanri, d)
    items = kanri("permission", "list", "--dir", d, "--subject", "s1", "--at", IN,
                  "--json").json["items"]
    assert [(i["番号"], i["状態"], i["承認"], i["発行"], i["扉"]) for i in items] == \
        [(pid, "発行済み", aid, iid, ["玄関"])]
    items = kanri("permission", "list", "--dir", d, "--at", T1, "--json").json["items"]
    assert [i["状態"] for i in items] == ["期限切れ"]
    _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE, "--json"),
               "実体_発行")
    items = kanri("entity", "list", "--dir", d, "--subject", "s1", "--json").json["items"]
    assert [(i["権限"], i["経路"], i["扉"]) for i in items] == [(pid, GATE, ["玄関"])]
    assert kanri("entity", "list", "--dir", d, "--subject", "apr", "--json").json["items"] == []


def test_trust_entry_is_always_json(kanri, file_unit):
    """R-AC-09 19.12: trust-entry はいつも JSON で、Unit.trust_entry() と同じ。"""
    res = kanri("trust-entry", "--dir", file_unit.root)
    assert res.code == 0
    assert json.loads(res.out) == file_unit.trust_entry()


# ---- entity issue ----

def test_entity_issue_credential_needs_out(kanri, file_unit):
    """19.12: entity issue --route 署名つき資格情報 は --out が要る(無ければ終了コード 2)。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    n = Unit.open(d).ledger.count()
    assert kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT,
                 "--route", "署名つき資格情報").code == 2
    assert Unit.open(d).ledger.count() == n


def test_entity_issue_credential_writes_out_file(kanri, file_unit, tmp_path):
    """19.12: 資格情報の JSON を --out へ権限 600 で書き、標準出力には出さない。--json の形。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    out = tmp_path / "cred.json"
    res = kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", "署名つき資格情報",
                "--until", plus(T1, days=-1), "--out", out, "--json")
    assert res.code == 0, res.err
    cred = json.loads(out.read_text(encoding="utf-8"))
    assert set(cred) == {"body", "sig"}
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert cred["sig"] not in res.out and cred["body"] not in res.out
    j = res.json
    body = json.loads(cred["body"])
    assert (j["ok"], j["様式"], j["経路"], j["識別"], j["扉"], j["開始"], j["終了"]) == \
        (True, "実体_発行", "署名つき資格情報", body["id"], ["玄関"], T0, plus(T1, days=-1))
    assert Path(j["資格情報のファイル"]).resolve() == out.resolve()
    assert Unit.open(d).ledger.get(j["番号"]).form == "実体_発行"


def test_entity_issue_human(kanri, file_unit, tmp_path):
    """19.12: entity issue の人が読む形は `実体_発行 <番号>`。資格情報は出さない。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    out = tmp_path / "cred.json"
    res = kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", "署名つき資格情報",
                "--out", out)
    _one_slip(res, "実体_発行")
    assert json.loads(out.read_text(encoding="utf-8"))["sig"] not in res.out


def test_entity_issue_out_exists(kanri, file_unit, tmp_path):
    """19.12: --out のファイルが既にあれば、実体を出す前に OUT_EXISTS で拒む。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    out = tmp_path / "cred.json"
    out.write_text("前からある", encoding="utf-8")
    n = Unit.open(d).ledger.count()
    res = kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", "署名つき資格情報",
                "--out", out)
    assert res.code == 1
    assert res.err.startswith("拒否: 出力のファイルが既にあります")
    assert out.read_text(encoding="utf-8") == "前からある"
    assert Unit.open(d).ledger.count() == n


def test_entity_issue_physical_and_return(kanri, file_unit):
    """19.12: entity issue --key-number、physical return --entity [--result] [--note]。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    res = kanri("entity", "issue", "--dir", d, *W, "名簿の係", "--permission", pid, "--at", AT, "--route", PHYSICAL,
                "--key-number", "K-7", "--json")
    eid = _json_slip(res, "実体_発行")
    assert res.json["識別"] == "K-7" and res.json["資格情報のファイル"] == ""
    ret = _one_slip(kanri("physical", "return", "--dir", d, *W, "名簿の係", "--entity", eid, "--result", "錠を交換",
                          "--note", "戻らなかった"), "物理キー_回収")
    slip = Unit.open(d).ledger.get(ret)
    assert (slip["結果"], slip["備考"]) == ("錠を交換", "戻らなかった")


# ---- may-open、progress、revoke、confirm ----

def test_may_open(kanri, file_unit):
    """19.12: may-open は `開けてよい`(0)か `拒否 <理由>`(1)。--json は {"ok", "開けてよい", "理由", "実体"}。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    eid = _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE,
                           "--json"), "実体_発行")
    res = kanri("may-open", "--dir", d, "--subject", "s1", "--door", "玄関", "--at", IN)
    assert (res.code, res.lines) == (0, ["開けてよい"])
    res = kanri("may-open", "--dir", d, "--subject", "s1", "--door", "玄関", "--at", IN, "--json")
    assert res.code == 0
    assert res.json == {"ok": True, "開けてよい": True, "理由": "", "実体": eid}
    res = kanri("may-open", "--dir", d, "--subject", "s1", "--door", "玄関", "--at", T1)
    assert (res.code, res.lines) == (1, ["拒否 期間の外"])
    res = kanri("may-open", "--dir", d, "--subject", "apr", "--door", "玄関", "--at", IN, "--json")
    assert res.code == 1
    assert res.json == {"ok": True, "開けてよい": False, "理由": "権限がありません", "実体": ""}


def test_may_open_at_with_offset(kanri, file_unit):
    """19.12: --at は datetime.fromisoformat で読める物(時差つき可)。"""
    from datetime import datetime
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE, "--json"),
               "実体_発行")
    at = datetime.fromisoformat(IN).astimezone().isoformat()
    res = kanri("may-open", "--dir", d, "--subject", "s1", "--door", "玄関", "--at", at)
    assert (res.code, res.lines) == (0, ["開けてよい"])


def test_revoke_progress_confirm(kanri, file_unit, tmp_path):
    """19.12、20.2: revoke は書いた伝票を一枚一行、続けて進み具合。progress は一行目 `<主体> <時刻> 済み|未了`、
    続けて `<状態>\\t<経路>\\t<識別>\\t<扉>\\t<効くまで>\\t<作業>\\t<機器>`(20.2 で 7 列)。済みでも未了でも 0。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    cid = _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT,
                           "--route", "署名つき資格情報", "--out", tmp_path / "c.json", "--json"), "実体_発行")
    gid = _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE,
                           "--json"), "実体_発行")
    res = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV)
    assert res.code == 0, res.err
    # 権限_失効 1 枚、実体_失効 2 枚(署名つき 待ち、門番 止まった。19.10 の 2)を一枚一行、
    # 一覧を作ったので続けて 失効_一覧 <番号>(21.2)、続けて進み具合
    assert [SLIP.match(line).group(1) for line in res.lines[:4]] == ["権限_失効", "実体_失効", "実体_失効", "失効_一覧"]
    assert Unit.open(d).ledger.get(SLIP.match(res.lines[0]).group(2))["権限"] == pid
    assert res.lines[4] == f"s1 {REV} 未了"
    assert len(res.lines) == 7

    res = kanri("progress", "--dir", d, "--subject", "s1", "--at", REV)
    assert res.code == 0
    assert res.lines[0] == f"s1 {REV} 未了"
    rows = {line.split("\t")[2]: line.split("\t") for line in res.lines[1:]}
    assert all(len(r) == 7 and r[6] == "" for r in rows.values())   # 20.2: 末尾に 機器(扉の経路では空)
    u = Unit.open(d)
    ents = {e.番号: e for e in u.entities("s1")}
    cred_row = rows[ents[cid].識別]
    gate_row = rows[ents[gid].識別]
    assert (cred_row[0], cred_row[1], cred_row[4]) == ("待ち", "署名つき資格情報", T1)
    assert (gate_row[0], gate_row[1]) == ("止まった", GATE)

    res = kanri("progress", "--dir", d, "--subject", "s1", "--at", T1)
    assert (res.code, res.lines[0]) == (0, f"s1 {T1} 済み")

    res = kanri("progress", "--dir", d, "--subject", "s1", "--at", T1, "--json")
    j = res.json
    assert (j["ok"], j["主体"], j["時刻"], j["済み"]) == (True, "s1", T1, True)
    assert {r["実体"] for r in j["実体"]} == {cid, gid}
    for r in j["実体"]:
        assert set(r) == {"実体", "経路", "識別", "扉", "状態", "効くまで", "作業", "機器"}   # 20.2: 鍵 機器 を足す
        assert r["扉"] == ["玄関"] and r["機器"] == ""

    # confirm --at: 済みでない時刻では拒否、権限の終了では 失効_確認 を書く
    res = kanri("confirm", "--dir", d, *W, "管理者", "--subject", "s1", "--at", REV)
    assert res.code == 1 and res.err.startswith(f"拒否: 止まっていない実体があります({cid})")
    num = _one_slip(kanri("confirm", "--dir", d, *W, "管理者", "--subject", "s1", "--at", T1), "失効_確認")
    assert set(u.ledger.get(num)["実体"].split(", ")) == {cid, gid}


def test_confirm_without_entities(kanri, file_unit):
    """19.12: confirm --subject は 失効_確認 を書く(実体が無ければ なし)。"""
    d = file_unit.root
    num = _one_slip(kanri("confirm", "--dir", d, *W, "管理者", "--subject", "apr"), "失効_確認")
    assert Unit.open(d).ledger.get(num)["実体"] == "なし"


def test_revoke_json_and_permission(kanri, file_unit):
    """19.12: revoke --json は {"ok", "権限_失効": [...], "実体_失効": [...], "進み具合": <progress の形>}。--permission で一つの権限。"""
    d = file_unit.root
    pid, _, _ = _granted(kanri, d)
    gid = _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE,
                           "--json"), "実体_発行")
    res = kanri("revoke", "--dir", d, *W, "名簿の係", "--permission", pid, "--at", AT, "--reason", "紛失", "--json")
    assert res.code == 0, res.err
    j = res.json
    assert j["ok"] is True and len(j["権限_失効"]) == 1
    assert len(j["実体_失効"]) == 1   # 門番にも 止まった の一枚(19.10 の 2)
    assert j["進み具合"]["主体"] == "s1"
    assert [r["実体"] for r in j["進み具合"]["実体"]] == [gid]
    assert Unit.open(d).permission(pid).状態 == "失効"
    # 二度目は伝票を増やさない
    n = Unit.open(d).ledger.count()
    res = kanri("revoke", "--dir", d, *W, "名簿の係", "--permission", pid, "--at", AT, "--reason", "紛失", "--json")
    assert res.code == 0 and res.json["権限_失効"] == [] and res.json["実体_失効"] == []
    assert Unit.open(d).ledger.count() == n


# ---- import ----

def test_import_csv(kanri, file_unit, tmp_path):
    """R-EB-37 19.12: import の CSV は UTF-8(BOM は読み飛ばす)、一行目が見出し。申請を一枚一行、飛ばした行を `飛ばした <行> <理由>`。"""
    d = file_unit.root
    csv = tmp_path / "hr.csv"
    csv.write_bytes("﻿識別,対象の種類,対象,開始,終了,理由\n"
                    f"x9,扉,玄関,{T0},{T1},入社\n"
                    f"s1,扉,玄関,{T0},{T1},異動\n".encode("utf-8"))
    res = kanri("import", "--dir", d, *W, "名簿の係", "--file", csv, "--source", "人事の届け")
    assert res.code == 0, res.err
    assert len(res.lines) == 2
    assert SLIP.match(res.lines[0]).group(1) == "権限_申請"
    assert res.lines[1] == "飛ばした 1 名簿にありません"
    slip = Unit.open(d).ledger.get(SLIP.match(res.lines[0]).group(2))
    assert (slip["主体"], slip["出どころ"], slip["一覧の名"]) == ("s1", "外の一覧", "人事の届け")


def test_import_csv_json(kanri, file_unit, tmp_path):
    """R-EB-37 19.12: import --json は {"ok", "申請": [番号], "飛ばした": [{"行", "理由"}]}。"""
    d = file_unit.root
    csv = tmp_path / "hr.csv"
    csv.write_text("識別,対象の種類,対象,開始,終了,理由\n"
                   f"s1,扉,玄関,{T0},{T1},異動\n"
                   f"s1,扉,倉庫,{T0},{T1},異動\n", encoding="utf-8")
    res = kanri("import", "--dir", d, *W, "名簿の係", "--file", csv, "--source", "予約", "--json")
    assert res.code == 0, res.err
    j = res.json
    assert j["ok"] is True and len(j["申請"]) == 1
    assert j["飛ばした"] == [{"行": 2, "理由": "この発行者の扉ではありません"}]


# ---- --at を省いた時 ----

def test_at_omitted_uses_now(kanri, file_unit):
    """19.12: --at を省けば今。期間を広く取り(2000〜2099)、issue、entity issue、may-open、revoke、progress、confirm を --at なしで呼ぶ。"""
    d = file_unit.root
    start, end = "2000-01-01T00:00", "2099-01-01T00:00"
    pid = _json_slip(kanri("request", "--dir", d, *W, "名簿の係", "--subject", "s1", "--door", "玄関",
                           "--from", start, "--until", end, "--reason", "業務", "--json"), "権限_申請")
    aid = _json_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                           "--approve", "--json"), "権限_承認")
    _json_slip(kanri("issue", "--dir", d, *W, "管理者", "--approval", aid, "--subject", "s1", "--door", "玄関",
                     "--from", start, "--until", end, "--json"), "権限_発行")
    _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--route", GATE, "--json"),
               "実体_発行")
    assert kanri("may-open", "--dir", d, "--subject", "s1", "--door", "玄関").lines == ["開けてよい"]
    assert kanri("permission", "list", "--dir", d, "--json").json["items"][0]["状態"] == "発行済み"
    assert kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職").code == 0
    j = kanri("progress", "--dir", d, "--subject", "s1", "--json").json
    assert (j["済み"], [r["状態"] for r in j["実体"]]) == (True, ["止まった"])
    _one_slip(kanri("confirm", "--dir", d, *W, "管理者", "--subject", "s1"), "失効_確認")


# ---- verify ----

def test_verify(kanri, file_unit):
    """19.12: verify は `連鎖 正常 <件数>`(0)、改ざんがあれば `連鎖 異常 <理由>`(1)。"""
    d = file_unit.root
    n = file_unit.ledger.count()
    res = kanri("verify", "--dir", d)
    assert (res.code, res.lines) == (0, [f"連鎖 正常 {n}"])
    res = kanri("verify", "--dir", d, "--json")
    assert res.json == {"ok": True, "件数": n, "理由": ""}
    # 改ざんを模す(試験だけ): 最後の伝票のファイルを書き換える
    last = file_unit.ledger.select()[-1].path
    os.chmod(last, 0o640)
    last.write_text(last.read_text(encoding="utf-8") + "書き足し:: x\n", encoding="utf-8")
    res = kanri("verify", "--dir", d)
    assert res.code == 1 and res.lines[0].startswith("連鎖 異常 ")
    res = kanri("verify", "--dir", d, "--json")
    assert res.code == 1 and res.json["ok"] is False and res.json["理由"]


# ---- ネットワークから切り離して動かす ----

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


if not getattr(sys, "_kanri_test_hook", False):
    sys.addaudithook(_audit_hook)
    sys._kanri_test_hook = True


def test_r_ac_19_offline_on_admins_machine(kanri, tmp_path, monkeypatch):
    """R-AC-19 管理者が自分のコンピューターで、ネットワークから切り離して、命令だけで発行から失効まで動かせる(19.13 の 1〜3)。"""
    # 1. ネットワークを使えなくする
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
    out = tmp_path / "s1-cred.json"

    # 2. 命令を順に呼ぶ(番号は --json の出力から読む)
    _WATCH["paths"] = []
    try:
        codes = []

        def step(*argv):
            res = kanri(*argv, "--dir", d, "--json")
            codes.append((argv[:2], res.code, res.err))
            return res

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
        step("entity", "issue", *W, "管理者", "--permission", pid, "--at", AT, "--route", "署名つき資格情報", "--out", out)
        step("entity", "issue", *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE)
        step("revoke", *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV)
        progress = step("progress", "--subject", "s1", "--at", REV).json
        verify = step("verify").json
        written = list(_WATCH["paths"])
    finally:
        _WATCH["paths"] = None

    assert [c for c in codes if c[1] != 0] == [], codes
    rows = {r["経路"]: r for r in progress["実体"]}
    assert len(progress["実体"]) == 2
    assert rows[GATE]["状態"] == "止まった"
    assert rows["署名つき資格情報"]["状態"] == "待ち"
    assert verify["ok"] is True
    assert out.exists()

    # 3. 置き場の外にファイルを書かない(書くのは --dir の下と --out だけ)
    root = os.path.realpath(d)
    allowed_out = os.path.realpath(out)
    outside = [p for p in written
               if not (os.path.realpath(p) == root or os.path.realpath(p).startswith(root + os.sep)
                       or os.path.realpath(p) == allowed_out)]
    assert outside == []
    assert written, "書いた道が一つも見えない(監視が効いていない)"
    assert sorted(x.name for x in tmp_path.iterdir()) == sorted(["cwd", "kanri", "s1-cred.json"])
    assert list(cwd.iterdir()) == []
