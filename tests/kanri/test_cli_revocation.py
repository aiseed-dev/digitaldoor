"""設計書 21.12: 命令 `digitalkey kanri` に足す物(revocation publish / show / list / delivered、door add の
--revocation-max-min、revoke の --out)と、出力の形と終了コード。ネットワークから切り離して動かす確かめは test_requirements_2a.py。

argv は `kanri` の後ろの語。終了コードは main の戻り値か SystemExit の code で読む(test_cli.py と同じ)。
時刻は `--at` に固定の値を渡す。段 2a の実装より先に書いた。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from digitalkey.kanri import Unit, cli as kanri_cli
from kanri_common import CREDENTIAL, GATE, ISSUE_AT, R2A, T0, T1, plus, unix

AT = ISSUE_AT
PUB = "2026-12-10T09:00"
REV = "2026-12-10T12:00"
SLIP = re.compile(r"^(\S+) (\S+)$")
W = ("--by", "adm", "--role")   # 書く命令の by(--role は続けて書く)


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


def _one_slip(res: Result, form: str) -> str:
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
    pid = _json_slip(kanri("request", "--dir", d, *W, "名簿の係", "--subject", subject, "--door", door,
                           "--from", T0, "--until", T1, "--reason", "業務", "--json"), "権限_申請")
    aid = _json_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                           "--approve", "--json"), "権限_承認")
    _json_slip(kanri("issue", "--dir", d, *W, "管理者", "--approval", aid, "--at", AT, "--subject", subject,
                     "--door", door, "--from", T0, "--until", T1, "--json"), "権限_発行")
    return pid


def _credential(kanri, d, out: Path, subject="s1", door="玄関"):
    """命令で署名つき資格情報を出す。(権限の番号, entity issue --json の出力)。"""
    pid = _granted(kanri, d, subject, door)
    res = kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", CREDENTIAL,
                "--out", out, "--json")
    assert res.code == 0, res.err
    return pid, res.json


def _count(d) -> int:
    return Unit.open(d).ledger.count()


# ---- door add --revocation-max-min、door list ----

def test_door_add_max_min_and_list(kanri, rfile_unit):
    """R-AC-14 21.12: door add --revocation-max-min N。door list の人が読む形は末尾の列が 失効の最長の分数(None は空の欄)、
    --json の項目に鍵 失効の最長の分数(数か null)。"""
    d = rfile_unit.root
    _one_slip(kanri("door", "add", "--dir", d, *W, "管理者", "--name", "通用口", "--routes", CREDENTIAL,
                    "--revocation-max-min", "1440"), "扉")
    res = kanri("door", "list", "--dir", d)
    assert res.code == 0
    assert res.lines == [
        "玄関\t署名つき資格情報, 門番を通す錠, 物理キー\t\t\t使用\t",
        "通用口\t署名つき資格情報\t\t\t使用\t1440",
    ]
    items = kanri("door", "list", "--dir", d, "--json").json["items"]
    assert [(i["名前"], i["失効の最長の分数"]) for i in items] == [("玄関", None), ("通用口", 1440)]
    assert list(items[0])[-1] == "失効の最長の分数"


def test_door_add_max_min_errors(kanri, rfile_unit):
    """R-AC-14 21.12: --revocation-max-min は argparse の int(整数でなければ 2)。1 より小さい値は様式で FORM(1)。"""
    d = rfile_unit.root
    n = _count(d)
    base = ("door", "add", "--dir", d, *W, "管理者", "--name", "通用口", "--routes", CREDENTIAL)
    assert kanri(*base, "--revocation-max-min", "一日").code == 2
    res = kanri(*base, "--revocation-max-min", "0")
    assert res.code == 1 and res.err.startswith("拒否: 様式に合いません")
    assert _count(d) == n


# ---- revocation publish ----

def test_revocation_publish_human(kanri, rfile_unit):
    """R-AC-22 21.12: revocation publish の人が読む形は `失効_一覧 <番号>` と `世代 <n>` の二行。"""
    d = rfile_unit.root
    res = kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB)
    assert res.code == 0, res.err
    assert len(res.lines) == 2
    m = SLIP.match(res.lines[0])
    assert m.group(1) == "失効_一覧"
    assert res.lines[1] == "世代 1"
    assert Unit.open(d).ledger.get(m.group(2))["世代"] == "1"
    res = kanri("revocation", "publish", "--dir", d, "--by", "ops", "--role", "運用", "--at", PUB)
    assert res.lines[1] == "世代 2"


def test_revocation_publish_json_and_out(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: --json は {"ok", "様式", "番号", "世代", "発行時刻", "識別", "一覧のファイル"}。--out のファイルに
    署名つきの一覧の JSON。--out が無ければ 一覧のファイル は空。"""
    d = rfile_unit.root
    j = kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB, "--json").json
    assert j == {"ok": True, "様式": "失効_一覧", "番号": j["番号"], "世代": 1, "発行時刻": unix(PUB), "識別": [],
                 "一覧のファイル": ""}
    out = tmp_path / "rev.json"
    j = kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB, "--out", out, "--json").json
    assert (j["世代"], j["発行時刻"]) == (2, unix(PUB) + 1)
    assert Path(j["一覧のファイル"]).resolve() == out.resolve()
    lst = json.loads(out.read_text(encoding="utf-8"))
    assert set(lst) == {"body", "sig"}
    assert lst == Unit.open(d).revocation(2)


def test_revocation_publish_ids(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: 識別 は一覧に入れた資格情報の識別の列。"""
    d = rfile_unit.root
    _, c = _credential(kanri, d, tmp_path / "c.json")
    assert kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV).code == 0
    j = kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", plus(REV, minutes=5), "--json").json
    assert (j["世代"], j["識別"]) == (2, [c["識別"]])


def test_revocation_publish_out_exists(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: --out のファイルが既にあれば、一覧を作る前に OUT_EXISTS(1)。台帳もファイルも変わらない。"""
    d = rfile_unit.root
    out = tmp_path / "rev.json"
    out.write_text("前からある", encoding="utf-8")
    n = _count(d)
    res = kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB, "--out", out)
    assert res.code == 1 and res.err.startswith("拒否: 出力のファイルが既にあります")
    assert (out.read_text(encoding="utf-8"), _count(d)) == ("前からある", n)


def test_revocation_publish_needs_by_and_role(kanri, rfile_unit):
    """R-AC-22 21.12: 書く命令には --by、--role が要る(無ければ 2)。役が違えば拒否(1)。"""
    d = rfile_unit.root
    assert kanri("revocation", "publish", "--dir", d, "--role", "管理者", "--at", PUB).code == 2
    assert kanri("revocation", "publish", "--dir", d, "--by", "adm", "--at", PUB).code == 2
    res = kanri("revocation", "publish", "--dir", d, *W, "名簿の係", "--at", PUB)
    assert res.code == 1 and res.err.startswith("拒否: その役ではできません")


def test_revocation_publish_at_with_offset(kanri, rfile_unit):
    """R-AC-22 21.12: --at は datetime.fromisoformat で読める物(時差つき可)。"""
    at = datetime.fromisoformat(PUB).astimezone().isoformat()
    j = kanri("revocation", "publish", "--dir", rfile_unit.root, *W, "管理者", "--at", at, "--json").json
    assert j["発行時刻"] == unix(PUB)


# ---- revocation show ----

def test_revocation_show(kanri, rfile_unit):
    """R-AC-22 21.12、21.14: revocation show は --by、--role が要らず、署名つきの一覧の JSON を一つ出す。--json でも同じ。
    --generation N でその世代。"""
    d = rfile_unit.root
    kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB)
    kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", plus(PUB, minutes=1))
    u = Unit.open(d)
    res = kanri("revocation", "show", "--dir", d)
    assert res.code == 0, res.err
    assert json.loads(res.out) == u.revocation() == u.revocation(2)
    assert kanri("revocation", "show", "--dir", d, "--json").json == u.revocation()
    assert kanri("revocation", "show", "--dir", d, "--generation", "1").json == u.revocation(1)


def test_revocation_show_out(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: --out ありは `世代 <n>` を一行、--json は {"ok", "世代", "一覧のファイル"}。既にあれば OUT_EXISTS。"""
    d = rfile_unit.root
    kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB)
    out = tmp_path / "a.json"
    res = kanri("revocation", "show", "--dir", d, "--out", out)
    assert (res.code, res.lines) == (0, ["世代 1"])
    assert json.loads(out.read_text(encoding="utf-8")) == Unit.open(d).revocation(1)
    out2 = tmp_path / "b.json"
    j = kanri("revocation", "show", "--dir", d, "--generation", "1", "--out", out2, "--json").json
    assert (j["ok"], j["世代"]) == (True, 1)
    assert Path(j["一覧のファイル"]).resolve() == out2.resolve()
    res = kanri("revocation", "show", "--dir", d, "--out", out)
    assert res.code == 1 and res.err.startswith("拒否: 出力のファイルが既にあります")


def test_revocation_show_refusals(kanri, rfile_unit):
    """R-AC-22 21.11、21.12: 一覧が無い → `拒否: 失効の一覧がありません`(1)。無い世代 → `拒否: その世代の一覧がありません(9)`(1)。
    --json は {"ok": false, "理由", "詳細"}。--generation が整数でなければ 2。"""
    d = rfile_unit.root
    res = kanri("revocation", "show", "--dir", d)
    assert (res.code, res.err.strip()) == (1, f"拒否: {R2A.NO_REVOCATION_LIST}")
    res = kanri("revocation", "show", "--dir", d, "--json")
    assert (res.code, res.json) == (1, {"ok": False, "理由": R2A.NO_REVOCATION_LIST, "詳細": ""})
    kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB)
    res = kanri("revocation", "show", "--dir", d, "--generation", "9")
    assert (res.code, res.err.strip()) == (1, "拒否: その世代の一覧がありません(9)")
    res = kanri("revocation", "show", "--dir", d, "--generation", "9", "--json")
    assert (res.code, res.json) == (1, {"ok": False, "理由": R2A.UNKNOWN_GENERATION, "詳細": "9"})
    assert kanri("revocation", "show", "--dir", d, "--generation", "最後").code == 2


# ---- revocation list ----

def test_revocation_list(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12、21.14: revocation list は一件一行 `<世代>\\t<発行時刻>\\t<件数>\\t<番号>`(発行時刻は unix 秒のまま)。
    --json は {"ok", "items": [{"番号", "世代", "発行時刻", "識別"}]}。"""
    d = rfile_unit.root
    _, c = _credential(kanri, d, tmp_path / "c.json")
    n1 = SLIP.match(kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB).lines[0]).group(2)
    res = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV)
    n2 = [SLIP.match(x).group(2) for x in res.lines if x.startswith("失効_一覧 ")][0]
    res = kanri("revocation", "list", "--dir", d)
    assert res.code == 0
    assert res.lines == [f"1\t{unix(PUB)}\t0\t{n1}", f"2\t{unix(REV)}\t1\t{n2}"]
    j = kanri("revocation", "list", "--dir", d, "--json").json
    assert j == {"ok": True, "items": [
        {"番号": n1, "世代": 1, "発行時刻": unix(PUB), "識別": []},
        {"番号": n2, "世代": 2, "発行時刻": unix(REV), "識別": [c["識別"]]},
    ]}


# ---- revocation delivered ----

def test_revocation_delivered(kanri, rfile_unit):
    """R-AC-22 21.12 の例: revocation delivered --door D --generation N [--note X] [--at T] は `失効_配布 <番号>`。
    --json は {"ok", "様式", "番号"}。"""
    d = rfile_unit.root
    kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB)
    num = _one_slip(kanri("revocation", "delivered", "--dir", d, "--by", "ops", "--role", "運用", "--door", "玄関",
                          "--generation", "1", "--note", "鍵の係が運んだ", "--at", "2026-12-10T15:00"), "失効_配布")
    slip = Unit.open(d).ledger.get(num)
    assert (slip["扉"], slip["世代"], slip["日時"], slip["備考"], slip["役"], slip.issuer) == \
        ("玄関", "1", "2026-12-10T15:00", "鍵の係が運んだ", "運用", "ops")
    res = kanri("revocation", "delivered", "--dir", d, *W, "管理者", "--door", "玄関", "--generation", "1",
                "--at", "2026-12-10T16:00", "--json")
    _json_slip(res, "失効_配布")
    assert set(res.json) == {"ok", "様式", "番号"}
    assert Unit.open(d).door_generation("玄関") == 1


def test_revocation_delivered_refusals(kanri, rfile_unit):
    """R-AC-22 21.12: 扉が無い(UNKNOWN_DOOR)、世代が無い(UNKNOWN_GENERATION)、役が違う は 1。--generation が整数でなければ 2。
    --door、--generation が無ければ 2。台帳は増えない。"""
    d = rfile_unit.root
    kanri("revocation", "publish", "--dir", d, *W, "管理者", "--at", PUB)
    n = _count(d)
    base = ("revocation", "delivered", "--dir", d, *W, "管理者")
    res = kanri(*base, "--door", "倉庫", "--generation", "1")
    assert (res.code, res.err.strip()) == (1, "拒否: この発行者の扉ではありません(倉庫)")
    res = kanri(*base, "--door", "玄関", "--generation", "9")
    assert (res.code, res.err.strip()) == (1, "拒否: その世代の一覧がありません(9)")
    res = kanri("revocation", "delivered", "--dir", d, *W, "名簿の係", "--door", "玄関", "--generation", "1")
    assert res.code == 1 and res.err.startswith("拒否: その役ではできません")
    assert kanri(*base, "--door", "玄関", "--generation", "一").code == 2
    assert kanri(*base, "--door", "玄関").code == 2
    assert kanri(*base, "--generation", "1").code == 2
    assert _count(d) == n


# ---- revoke(足す物) ----

def test_revoke_human_adds_list_line(kanri, rfile_unit, tmp_path):
    """R-AC-15 R-AC-22 21.12: revoke は 権限_失効、実体_失効を一枚一行、一覧を作った時は続けて `失効_一覧 <番号>`、
    続けて進み具合。"""
    d = rfile_unit.root
    pid, c = _credential(kanri, d, tmp_path / "c.json")
    _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE,
                     "--json"), "実体_発行")
    res = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV)
    assert res.code == 0, res.err
    assert [SLIP.match(x).group(1) for x in res.lines[:4]] == ["権限_失効", "実体_失効", "実体_失効", "失効_一覧"]
    assert res.lines[4] == f"s1 {REV} 未了"
    assert len(res.lines) == 7
    u = Unit.open(d)
    assert u.ledger.get(SLIP.match(res.lines[3]).group(2)).form == "失効_一覧"
    assert u.revocations()[-1].識別 == [c["識別"]]


def test_revoke_json_keys(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: revoke --json に鍵 失効_一覧(番号か空)、世代(数か null)、一覧のファイル(--out か空)を足す。"""
    d = rfile_unit.root
    _credential(kanri, d, tmp_path / "c.json")
    j = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV, "--json").json
    assert j["ok"] is True
    assert Unit.open(d).ledger.get(j["失効_一覧"]).form == "失効_一覧"
    assert (j["世代"], j["一覧のファイル"]) == (1, "")
    assert set(j) >= {"権限_失効", "実体_失効", "進み具合", "失効_一覧", "世代", "一覧のファイル"}
    # 二度目は一覧を作らない
    j = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV, "--json").json
    assert (j["権限_失効"], j["実体_失効"], j["失効_一覧"], j["世代"], j["一覧のファイル"]) == ([], [], "", None, "")


def test_revoke_out_writes_list(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: revoke --out FILE は、一覧を作った時にその一覧(署名つきの JSON)を書く。"""
    d = rfile_unit.root
    _credential(kanri, d, tmp_path / "c.json")
    out = tmp_path / "rev.json"
    j = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV, "--out", out,
              "--json").json
    assert Path(j["一覧のファイル"]).resolve() == out.resolve()
    assert json.loads(out.read_text(encoding="utf-8")) == Unit.open(d).revocation(j["世代"])


def test_revoke_out_not_created_without_list(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: 一覧を作らなかった時(門番を通す錠だけ)は --out のファイルを作らない。"""
    d = rfile_unit.root
    pid = _granted(kanri, d)
    _json_slip(kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE,
                     "--json"), "実体_発行")
    out = tmp_path / "rev.json"
    res = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV, "--out", out,
                "--json")
    assert res.code == 0, res.err
    assert (res.json["失効_一覧"], res.json["世代"], res.json["一覧のファイル"]) == ("", None, "")
    assert not out.exists()


def test_revoke_out_exists(kanri, rfile_unit, tmp_path):
    """R-AC-22 21.12: revoke の --out が既にあれば、失効の前に OUT_EXISTS(1)。台帳は増えない。"""
    d = rfile_unit.root
    _credential(kanri, d, tmp_path / "c.json")
    out = tmp_path / "rev.json"
    out.write_text("前からある", encoding="utf-8")
    n = _count(d)
    res = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV, "--out", out)
    assert res.code == 1 and res.err.startswith("拒否: 出力のファイルが既にあります")
    assert (out.read_text(encoding="utf-8"), _count(d)) == ("前からある", n)


def test_spec_example_21_12(kanri, rfile_unit, tmp_path):
    """R-AC-14 R-AC-22 21.12 の例: 通用口(1440 分)、s2 の revoke --out、revocation delivered、show --generation 9。"""
    d = rfile_unit.root
    _one_slip(kanri("door", "add", "--dir", d, *W, "管理者", "--name", "通用口", "--routes", CREDENTIAL,
                    "--revocation-max-min", "1440"), "扉")
    _, c = _credential(kanri, d, tmp_path / "s2.json", "s2", "通用口")
    out = tmp_path / "usb" / "rev.json"
    out.parent.mkdir()
    res = kanri("revoke", "--dir", d, *W, "管理者", "--subject", "s2", "--reason", "退職", "--out", out, "--at", REV)
    assert res.code == 0, res.err
    assert [SLIP.match(x).group(1) for x in res.lines[:3]] == ["権限_失効", "実体_失効", "失効_一覧"]
    assert res.lines[3:] == [f"s2 {REV} 未了", f"待ち\t{CREDENTIAL}\t{c['識別']}\t通用口\t2026-12-11T12:00\t\t"]
    assert out.exists()
    _one_slip(kanri("revocation", "delivered", "--dir", d, "--by", "ops", "--role", "運用", "--door", "通用口",
                    "--generation", "1", "--note", "鍵の係が運んだ", "--at", "2026-12-10T15:00"), "失効_配布")
    res = kanri("revocation", "show", "--dir", d, "--generation", "9")
    assert (res.code, res.err.strip()) == (1, "拒否: その世代の一覧がありません(9)")
    j = kanri("progress", "--dir", d, "--subject", "s2", "--at", REV, "--json").json
    assert (j["済み"], j["実体"][0]["状態"]) == (True, "止まった")


# ---- device change、entity issue --json(足す鍵) ----

def test_device_change_json_has_list_key(kanri, dfile_unit):
    """R-AC-22 21.12、21.14: device change --json に鍵 失効_一覧(番号か空)。機器の権限には署名つき資格情報が無いので空。
    人が読む形は 20.12 のまま(一覧の行は増えない)。"""
    d = dfile_unit.root
    res = kanri("device", "change", "--dir", d, *W, "管理者", "--name", "ルーター1", "--reason", "なくした",
                "--state", "紛失", "--at", REV, "--json")
    assert res.code == 0, res.err
    assert res.json["失効_一覧"] == ""
    res = kanri("device", "change", "--dir", d, *W, "管理者", "--name", "複合機1", "--reason", "なくした",
                "--state", "紛失", "--at", REV)
    assert [SLIP.match(x).group(1) for x in res.lines] == ["機器_変更"]


def test_entity_issue_json_effective_by(kanri, rfile_unit, tmp_path):
    """R-AC-14 21.9、21.12: entity issue --json に鍵 効くまで {"種類", "時刻", "分数"}(その実体の effective_by)。
    人が読む形は変えない。"""
    d = rfile_unit.root
    _one_slip(kanri("door", "add", "--dir", d, *W, "管理者", "--name", "通用口", "--routes", CREDENTIAL,
                    "--revocation-max-min", "1440"), "扉")
    _, j = _credential(kanri, d, tmp_path / "a.json", "s1", "玄関")
    assert j["効くまで"] == {"種類": "期限まで", "時刻": T1, "分数": None}
    _, j = _credential(kanri, d, tmp_path / "b.json", "s2", "通用口")
    assert j["効くまで"] == {"種類": "最長の時間まで", "時刻": "", "分数": 1440}
    pid = _granted(kanri, d, "s1", "玄関")
    j = kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE,
              "--json").json
    assert j["効くまで"] == {"種類": "即時", "時刻": "", "分数": None}
    res = kanri("entity", "issue", "--dir", d, *W, "管理者", "--permission", pid, "--at", AT, "--route", GATE)
    _one_slip(res, "実体_発行")
