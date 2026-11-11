"""設計書 20.12: 命令 `digitalkey kanri` に足す物(segment、device、access、vendor)と、段 1 の命令に足す物。

argv は `kanri` の後ろの語。終了コードは main の戻り値か SystemExit の code で読む(test_cli.py と同じ)。
時刻は `--at` に固定の値を渡す。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from digitalkey.kanri import Unit, cli as kanri_cli
from kanri_common import ACCESS, ACCESS_WORK, GATE, ISSUE_AT, RB, T0, T1, dt

AT = ISSUE_AT
IN = "2026-12-05T10:00"
REV = "2026-12-10T12:00"
SLIP = re.compile(r"^(\S+) (\S+)$")
BY = ("--by", "adm", "--role")   # 書く命令の by(--role は続けて書く)

DEVICE_KEYS = ["名前", "種類", "責任者", "置き場", "区画", "扉の系統", "扉", "製造者", "型", "製造番号", "網の番号", "版",
               "更新の期限", "更新の当て方", "秘密の在庫の番号", "記録の送り先", "中に残る物", "状態"]


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


def _device_granted(kanri, d, subject="t1", device="ルーター1", until=T1):
    """命令で機器の権限を申請、承認、発行する(request と issue の --device)。権限の番号を返す。"""
    pid = _json_slip(kanri("request", "--dir", d, *BY, "名簿の係", "--subject", subject, "--device", device,
                           "--from", T0, "--until", until, "--reason", "保守", "--json"), "権限_申請")
    aid = _json_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                           "--approve", "--json"), "権限_承認")
    _json_slip(kanri("issue", "--dir", d, *BY, "管理者", "--approval", aid, "--at", AT, "--subject", subject,
                     "--device", device, "--from", T0, "--until", until, "--json"), "権限_発行")
    return pid


def _access(kanri, d, pid, account="admin"):
    res = kanri("entity", "issue", "--dir", d, *BY, "管理者", "--permission", pid, "--at", AT, "--route", ACCESS,
                "--account", account, "--json")
    return _json_slip(res, "実体_発行"), res.json


# ---- 20.12 の例 ----

def test_spec_example(kanri, file_unit):
    """20.12 の例: device add は `機器_登録 <番号>`。device findings は `<機器>\\t<知らせ>\\t<詳細>` を一行ずつ。
    要る項目の無い据え付けの device check は `拒否: 確かめの項目が足りません(初めのパスワード)`、終了コード 1。"""
    d = file_unit.root
    num = _one_slip(kanri("device", "add", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--kind", "ルーター",
                          "--state", "使用"), "機器_登録")
    assert Unit.open(d).ledger.get(num)["名前"] == "ルーター1"
    res = kanri("device", "findings", "--dir", d, "--at", IN)
    assert res.code == 0
    assert res.lines == ["ルーター1\t責任者がいません\t", "ルーター1\t区画が決まっていません\t",
                         "ルーター1\t更新の期限が分かりません\t", "ルーター1\t据え付けの確かめがありません\t"]
    n = Unit.open(d).ledger.count()
    res = kanri("device", "check", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--scene", "据え付け",
                "--item", "外からの口=良い", "--at", IN)
    assert res.code == 1
    assert res.err.strip() == "拒否: 確かめの項目が足りません(初めのパスワード)"
    assert Unit.open(d).ledger.count() == n


# ---- segment ----

def test_segment_add_and_list(kanri, file_unit):
    """20.12: segment add --name N [--note X] [--retire]、segment list(一件一行、タブでつなぐ。--json は items)。"""
    d = file_unit.root
    _one_slip(kanri("segment", "add", "--dir", d, *BY, "管理者", "--name", "扉", "--note", "錠と扉のコントローラ"), "区画")
    _one_slip(kanri("segment", "add", "--dir", d, *BY, "管理者", "--name", "来客"), "区画")
    _one_slip(kanri("segment", "add", "--dir", d, *BY, "管理者", "--name", "来客", "--retire"), "区画")
    res = kanri("segment", "list", "--dir", d)
    assert res.code == 0
    assert res.lines == ["扉\t錠と扉のコントローラ\t使用", "来客\t\t廃止"]
    items = kanri("segment", "list", "--dir", d, "--json").json
    assert items == {"ok": True, "items": [{"名前": "扉", "説明": "錠と扉のコントローラ", "状態": "使用"},
                                           {"名前": "来客", "説明": "", "状態": "廃止"}]}


def test_segment_add_refused(kanri, file_unit):
    """20.12、19.12: 拒否は標準エラーに `拒否: <理由>`、終了コード 1。"""
    res = kanri("segment", "add", "--dir", file_unit.root, *BY, "管理者", "--name", "事,務")
    assert res.code == 1 and res.err.startswith("拒否: 名前に , は使えません")


# ---- device add、list ----

def test_device_add_all_options(kanri, dfile_unit):
    """20.12: device add の引数と register_device の対応。--door-system で扉の系統 はい。"""
    d = dfile_unit.root
    _one_slip(kanri("device", "add", "--dir", d, *BY, "管理者", "--name", "録画1", "--kind", "そのほか", "--state", "準備",
                    "--owner", "t2", "--place", "二階の倉庫", "--segment", "扉", "--door-system", "--door", "玄関",
                    "--maker", "製造者A", "--model", "型A", "--serial", "SN-001", "--net-id", "00:11:22:33:44:55",
                    "--version", "2.0", "--support-until", "2028-03-31", "--update-mode", "人", "--secret-ref", "kagi-12",
                    "--log-to", "記録のサーバー", "--holds", "録画、設定"), "機器_登録")
    got = Unit.open(d).device("録画1")
    assert [getattr(got, k) for k in DEVICE_KEYS] == \
        ["録画1", "そのほか", "t2", "二階の倉庫", "扉", True, "玄関", "製造者A", "型A", "SN-001", "00:11:22:33:44:55",
         "2.0", "2028-03-31", "人", "kagi-12", "記録のサーバー", "録画、設定", "準備"]
    _one_slip(kanri("device", "add", "--dir", d, *BY, "管理者", "--name", "PC1", "--kind", "PC", "--state", "使用"),
              "機器_登録")
    assert Unit.open(d).device("PC1").扉の系統 is False


def test_device_add_argument_errors(kanri, dfile_unit):
    """20.12: device add の --state は要る(無ければ 2)。--state、--update-mode の選べる値のほかは 2。--by が無ければ 2。"""
    d = dfile_unit.root
    n = Unit.open(d).ledger.count()
    base = ("device", "add", "--dir", d, *BY, "管理者", "--name", "PC1", "--kind", "PC")
    assert kanri(*base).code == 2
    assert kanri(*base, "--state", "撤去").code == 2
    assert kanri(*base, "--state", "使用", "--update-mode", "時々").code == 2
    assert kanri("device", "add", "--dir", d, "--role", "管理者", "--name", "PC1", "--kind", "PC",
                 "--state", "使用").code == 2
    assert Unit.open(d).ledger.count() == n


def test_device_add_json_and_refusal(kanri, dfile_unit):
    """20.12、19.12: --json は {"ok", "様式", "番号"}。拒否は {"ok": false, "理由", "詳細"} で終了コード 1。"""
    d = dfile_unit.root
    _json_slip(kanri("device", "add", "--dir", d, *BY, "管理者", "--name", "PC1", "--kind", "PC", "--state", "使用",
                     "--json"), "機器_登録")
    res = kanri("device", "add", "--dir", d, *BY, "管理者", "--name", "PC2", "--kind", "PC", "--state", "使用",
                "--segment", "設備", "--json")
    assert res.code == 1
    assert res.json == {"ok": False, "理由": RB.UNKNOWN_SEGMENT, "詳細": "設備"}


def test_device_list(kanri, dfile_unit):
    """20.12: device list は一件一行(属性をタブでつなぎ、bool は はい / いいえ)。--json は items(属性を鍵にした辞書)。"""
    d = dfile_unit.root
    res = kanri("device", "list", "--dir", d)
    assert res.code == 0
    assert [line.split("\t")[0] for line in res.lines] == ["ルーター1", "玄関の錠", "複合機1"]
    lock = res.lines[1].split("\t")
    assert len(lock) == len(DEVICE_KEYS)
    assert (lock[1], lock[4], lock[5], lock[6], lock[-1]) == ("錠", "扉", "はい", "玄関", "使用")
    assert res.lines[0].split("\t")[5] == "いいえ"
    items = kanri("device", "list", "--dir", d, "--json").json["items"]
    assert list(items[1]) == DEVICE_KEYS
    assert (items[1]["名前"], items[1]["扉の系統"], items[0]["扉の系統"]) == ("玄関の錠", True, False)


# ---- device change ----

def test_device_change_output(kanri, dfile_unit):
    """20.12: device change は書いた伝票を一枚一行(機器_変更、権限_失効、実体_失効の順)。--json の形。
    --door-system は はい|いいえ。"""
    d = dfile_unit.root
    _one_slip(kanri("device", "change", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--reason", "持ち主",
                    "--owner", "t1", "--door-system", "はい"), "機器_変更")
    u = Unit.open(d)
    assert (u.device("ルーター1").責任者, u.device("ルーター1").扉の系統) == ("t1", True)
    res = kanri("device", "change", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--reason", "戻した",
                "--door-system", "いいえ", "--json")
    assert res.code == 0
    j = res.json
    assert (j["ok"], j["様式"], j["権限_失効"], j["実体_失効"]) == (True, "機器_変更", [], [])
    assert Unit.open(d).device("ルーター1").扉の系統 is False


def test_device_change_lost_lines(kanri, dfile_unit):
    """R-AC-15 20.12: 紛失にすると、機器_変更、権限_失効、実体_失効の順に一枚一行。--at を止める時刻に渡す。"""
    d = dfile_unit.root
    p1 = _device_granted(kanri, d, "t1")
    eid, _ = _access(kanri, d, p1)
    p2 = _device_granted(kanri, d, "t2")
    res = kanri("device", "change", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--reason", "なくした",
                "--state", "紛失", "--at", REV)
    assert res.code == 0, res.err
    forms = [SLIP.match(line).group(1) for line in res.lines]
    assert forms == ["機器_変更", "権限_失効", "権限_失効", "実体_失効"]
    u = Unit.open(d)
    nums = [SLIP.match(line).group(2) for line in res.lines]
    assert [u.ledger.get(x)["権限"] for x in nums[1:3]] == [p1, p2]
    assert u.ledger.get(nums[3])["実体"] == eid


def test_device_change_lost_json(kanri, dfile_unit):
    """20.12: device change --json は {"ok", "様式": "機器_変更", "番号", "権限_失効": [...], "実体_失効": [...]}。"""
    d = dfile_unit.root
    p1 = _device_granted(kanri, d, "t1")
    eid, _ = _access(kanri, d, p1)
    res = kanri("device", "change", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--reason", "なくした",
                "--state", "廃棄", "--at", REV, "--json")
    assert res.code == 0, res.err
    j = res.json
    u = Unit.open(d)
    assert u.ledger.get(j["番号"]).form == "機器_変更"
    assert [u.ledger.get(x)["権限"] for x in j["権限_失効"]] == [p1]
    assert [u.ledger.get(x)["実体"] for x in j["実体_失効"]] == [eid]


def test_device_change_argument_errors(kanri, dfile_unit):
    """20.12: device change の --state と --door-system は選べる値のほかを 2。--reason が無ければ 2。"""
    d = dfile_unit.root
    base = ("device", "change", "--dir", d, *BY, "管理者", "--name", "ルーター1")
    assert kanri(*base, "--reason", "直し", "--state", "撤去").code == 2
    assert kanri(*base, "--reason", "直し", "--door-system", "たぶん").code == 2
    assert kanri(*base, "--place", "二階").code == 2
    res = kanri(*base, "--reason", "直し")
    assert res.code == 1 and res.err.startswith("拒否: 変える項目がありません")


# ---- device check、checks、update ----

def test_device_check_and_checks(kanri, dfile_unit):
    """20.12: device check --item 項目=結果(何度でも。同じ項目は後の値)、--note、--at。device checks の人が読む形の 結果 は
    `項目=結果` を様式の項目の順に「, 」でつなぐ。--json の 結果 は辞書。"""
    d = dfile_unit.root
    num = _one_slip(kanri("device", "check", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--scene", "点検",
                          "--item", "見覚えのない設定とアカウント=悪い", "--item", "外からの口=悪い",
                          "--item", "外からの口=良い", "--note", "転送を閉じた", "--at", "2026-12-01T09:00"),
                    "機器_確かめ")
    slip = Unit.open(d).ledger.get(num)
    assert (slip["外からの口"], slip["見覚えのない設定とアカウント"], slip["所見"], slip["日時"]) == \
        ("良い", "悪い", "転送を閉じた", "2026-12-01T09:00")
    res = kanri("device", "checks", "--dir", d, "--name", "ルーター1")
    assert res.code == 0
    line, = res.lines
    cols = line.split("\t")
    assert cols[:4] == [num, "ルーター1", "点検", "2026-12-01T09:00"]
    assert cols[4] == "外からの口=良い, 見覚えのない設定とアカウント=悪い"
    assert cols[5:] == ["転送を閉じた", "adm"]
    item, = kanri("device", "checks", "--dir", d, "--name", "ルーター1", "--json").json["items"]
    assert item == {"番号": num, "機器": "ルーター1", "場面": "点検", "日時": "2026-12-01T09:00",
                    "結果": {"外からの口": "良い", "見覚えのない設定とアカウント": "悪い"}, "所見": "転送を閉じた",
                    "書いた人": "adm"}


def test_device_check_argument_errors(kanri, dfile_unit):
    """20.12: --item に = の無い物は 2。--scene の選べる値のほかは 2。"""
    d = dfile_unit.root
    base = ("device", "check", "--dir", d, *BY, "管理者", "--name", "複合機1", "--at", IN)
    assert kanri(*base, "--scene", "点検", "--item", "版").code == 2
    assert kanri(*base, "--scene", "修理", "--item", "版=良い").code == 2
    assert kanri(*base, "--scene", "点検", "--item", "版=良い").code == 0


def test_device_checks_unknown(kanri, dfile_unit):
    """20.12、20.7: device checks の機器が無ければ拒否(終了コード 1)。"""
    res = kanri("device", "checks", "--dir", dfile_unit.root, "--name", "ルーター9")
    assert res.code == 1 and res.err.strip() == "拒否: この台帳の機器ではありません(ルーター9)"


def test_device_update(kanri, dfile_unit):
    """20.12: device update --name N --version V [--failed] [--reason R] [--at T]。--failed で 当てられなかった。"""
    d = dfile_unit.root
    _one_slip(kanri("device", "update", "--dir", d, *BY, "管理者", "--name", "ルーター1", "--version", "1.1",
                    "--at", "2026-12-01T09:00"), "機器_更新")
    assert Unit.open(d).device("ルーター1").版 == "1.1"
    res = kanri("device", "update", "--dir", d, "--by", "ops", "--role", "運用", "--name", "ルーター1", "--version", "1.2",
                "--failed", "--at", "2026-12-02T09:00")
    assert res.code == 1 and res.err.strip() == "拒否: 理由が要ります"
    num = _one_slip(kanri("device", "update", "--dir", d, "--by", "ops", "--role", "運用", "--name", "ルーター1",
                          "--version", "1.2", "--failed", "--reason", "容量が足りない", "--at", "2026-12-02T09:00"),
                    "機器_更新")
    slip = Unit.open(d).ledger.get(num)
    assert (slip["結果"], slip["理由"], slip["前の版"]) == ("当てられなかった", "容量が足りない", "1.1")


# ---- device findings ----

def test_device_findings_json_and_empty(kanri, file_unit):
    """20.12: device findings --json は items。知らせが無ければ何も出さず、終了コード 0。"""
    d = file_unit.root
    res = kanri("device", "findings", "--dir", d, "--at", IN)
    assert (res.code, res.out) == (0, "")
    _one_slip(kanri("device", "add", "--dir", d, *BY, "管理者", "--name", "PC1", "--kind", "PC", "--state", "休止"),
              "機器_登録")
    j = kanri("device", "findings", "--dir", d, "--at", IN, "--json").json
    assert j == {"ok": True, "items": [{"機器": "PC1", "知らせ": RB.F_NO_OWNER, "詳細": ""}]}


@pytest.mark.parametrize("cmd", [
    ("device", "findings"),
    ("vendor", "list"),
])
def test_read_commands_accept_aware_at(kanri, dfile_unit, cmd):
    """20.12: --at は datetime.fromisoformat で読める物(時差つき可)。"""
    at = datetime.fromisoformat(IN).astimezone(timezone.utc).isoformat()
    assert kanri(*cmd, "--dir", dfile_unit.root, "--at", at).code == 0


# ---- access remove、entity issue --account、progress ----

def test_entity_issue_account_and_lists(kanri, dfile_unit):
    """20.12: request と issue の --device(対象の種類 機器)、entity issue --account。entity list は末尾に 機器。"""
    d = dfile_unit.root
    pid = _device_granted(kanri, d, "t1")
    p = Unit.open(d).permission(pid, at=dt(IN))
    assert (p.対象の種類, p.対象, p.状態, p.扉) == ("機器", "ルーター1", "発行済み", [])
    eid, j = _access(kanri, d, pid)
    assert (j["経路"], j["識別"], j["扉"], j["開始"], j["終了"], j["資格情報のファイル"]) == (ACCESS, "admin", [], T0, T1, "")
    res = kanri("entity", "list", "--dir", d, "--subject", "t1")
    assert res.code == 0
    cols = res.lines[0].split("\t")
    assert cols[0] == eid and cols[-1] == "ルーター1"
    item, = kanri("entity", "list", "--dir", d, "--subject", "t1", "--json").json["items"]
    assert (item["番号"], item["機器"], list(item)[-1]) == (eid, "ルーター1", "機器")


def test_access_remove(kanri, dfile_unit):
    """20.12: access remove --entity N [--result ...] [--note X]。--result の外れは様式で確かめ FORM(終了コード 1)。"""
    d = dfile_unit.root
    pid = _device_granted(kanri, d, "t1")
    eid, _ = _access(kanri, d, pid)
    res = kanri("access", "remove", "--dir", d, *BY, "管理者", "--entity", eid, "--result", "捨てた")
    assert res.code == 1 and res.err.startswith("拒否: 様式に合いません")
    num = _one_slip(kanri("access", "remove", "--dir", d, "--by", "ops", "--role", "運用", "--entity", eid,
                          "--result", "パスワードを替えた", "--note", "共用の口"), "管理者の口_削除")
    slip = Unit.open(d).ledger.get(num)
    assert (slip["実体"], slip["機器"], slip["口の名"], slip["結果"], slip["備考"]) == \
        (eid, "ルーター1", "admin", "パスワードを替えた", "共用の口")
    res = kanri("access", "remove", "--dir", d, *BY, "管理者", "--entity", eid, "--json")
    assert res.code == 1 and res.json == {"ok": False, "理由": RB.ALREADY_REMOVED, "詳細": ""}


def test_progress_and_revoke_have_device_column(kanri, dfile_unit):
    """20.12: progress と revoke の進み具合は実体ごとに 7 列(末尾に 機器)。--json の行に鍵 機器。"""
    d = dfile_unit.root
    pid = _device_granted(kanri, d, "t1")
    eid, _ = _access(kanri, d, pid)
    res = kanri("revoke", "--dir", d, *BY, "管理者", "--subject", "t1", "--reason", "退職", "--at", REV)
    assert res.code == 0, res.err
    assert [SLIP.match(line).group(1) for line in res.lines[:2]] == ["権限_失効", "実体_失効"]
    assert res.lines[2] == f"t1 {REV} 未了"
    row = res.lines[3].split("\t")
    assert row == ["人の作業が要る", ACCESS, "admin", "", "", ACCESS_WORK.format("ルーター1", "admin"), "ルーター1"]
    res = kanri("progress", "--dir", d, "--subject", "t1", "--at", REV, "--json")
    r, = res.json["実体"]
    assert set(r) == {"実体", "経路", "識別", "扉", "状態", "効くまで", "作業", "機器"}
    assert (r["実体"], r["機器"], r["扉"]) == (eid, "ルーター1", [])
    res = kanri("revoke", "--dir", d, *BY, "管理者", "--subject", "t1", "--reason", "退職", "--at", REV, "--json")
    assert res.json["進み具合"]["実体"][0]["機器"] == "ルーター1"


def test_import_device_rows(kanri, dfile_unit, tmp_path):
    """20.12: import の CSV は 対象の種類 に 機器 を書ける。"""
    csv = tmp_path / "plan.csv"
    csv.write_text("識別,対象の種類,対象,開始,終了,理由\n"
                   f"t1,機器,ルーター1,{T0},{T1},保守\n"
                   f"t1,機器,ルーター9,{T0},{T1},保守\n", encoding="utf-8")
    res = kanri("import", "--dir", dfile_unit.root, *BY, "名簿の係", "--file", csv, "--source", "保守の予定", "--json")
    assert res.code == 0, res.err
    assert len(res.json["申請"]) == 1
    assert res.json["飛ばした"] == [{"行": 2, "理由": RB.UNKNOWN_DEVICE}]


@pytest.mark.parametrize("item", ["業者の最長の日数", "自己承認", "点検の間の日数", "更新の期限の知らせの日数"])
def test_setting_set_items(kanri, dfile_unit, item):
    """20.12: setting set の --item は四つ。"""
    value = "許す" if item == "自己承認" else "30"
    _one_slip(kanri("setting", "set", "--dir", dfile_unit.root, *BY, "管理者", "--item", item, "--value", value,
                    "--reason", "管理者が決めた"), "設定")
    assert Unit.open(dfile_unit.root).setting(item) == value


def test_setting_set_bad_days(kanri, dfile_unit):
    """20.2、20.12: 点検の間の日数 の外れは BAD_SETTING(終了コード 1)。"""
    res = kanri("setting", "set", "--dir", dfile_unit.root, *BY, "管理者", "--item", "点検の間の日数", "--value", "0",
                "--reason", "試し")
    assert res.code == 1 and res.err.startswith("拒否: 設定の値が読めません")


# ---- vendor list ----

def test_vendor_list(kanri, dfile_unit):
    """20.12: vendor list の人が読む形の 残り は `<実体>(<状態>)` を「, 」でつなぐ。--json の 残り は progress --json の行の形。"""
    d = dfile_unit.root
    pid = _device_granted(kanri, d, "v1")
    eid, _ = _access(kanri, d, pid, "maint")
    res = kanri("vendor", "list", "--dir", d, "--at", IN)
    assert res.code == 0
    line, = res.lines
    cols = line.split("\t")
    assert cols == ["v1", pid, "機器", "ルーター1", T0, T1, "発行済み", f"{eid}(効いている)"]
    assert kanri("revoke", "--dir", d, *BY, "管理者", "--subject", "v1", "--reason", "契約の終了", "--at", REV).code == 0
    item, = kanri("vendor", "list", "--dir", d, "--at", REV, "--json").json["items"]
    assert (item["主体"], item["権限"], item["状態"]) == ("v1", pid, "失効")
    r, = item["残り"]
    assert set(r) == {"実体", "経路", "識別", "扉", "状態", "効くまで", "作業", "機器"}
    assert (r["実体"], r["状態"], r["扉"], r["機器"]) == (eid, "人の作業が要る", [], "ルーター1")


def test_vendor_list_empty(kanri, dfile_unit):
    """20.12: 目録が空なら一行も出さず、終了コード 0。"""
    res = kanri("vendor", "list", "--dir", dfile_unit.root, "--at", IN)
    assert (res.code, res.out) == (0, "")


def test_gate_entity_list_device_empty(kanri, dfile_unit):
    """20.2: 扉の経路の実体の 機器 は空(entity list の末尾の列も空)。"""
    d = dfile_unit.root
    pid = _json_slip(kanri("request", "--dir", d, *BY, "名簿の係", "--subject", "t1", "--door", "玄関",
                           "--from", T0, "--until", T1, "--reason", "業務", "--json"), "権限_申請")
    aid = _json_slip(kanri("decide", "--dir", d, "--by", "apr", "--role", "承認する人", "--request", pid,
                           "--approve", "--json"), "権限_承認")
    _json_slip(kanri("issue", "--dir", d, *BY, "管理者", "--approval", aid, "--at", AT, "--subject", "t1",
                     "--door", "玄関", "--from", T0, "--until", T1, "--json"), "権限_発行")
    _json_slip(kanri("entity", "issue", "--dir", d, *BY, "管理者", "--permission", pid, "--at", AT, "--route", GATE,
                     "--json"), "実体_発行")
    item, = kanri("entity", "list", "--dir", d, "--subject", "t1", "--json").json["items"]
    assert item["機器"] == ""
    line, = kanri("entity", "list", "--dir", d, "--subject", "t1").lines
    assert line.split("\t")[-1] == ""
