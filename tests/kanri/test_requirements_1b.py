"""設計書 20.13: 段 1b の要求ごとの確かめ方(R-NW-02、R-NW-03、R-NW-04、R-NW-05、R-RS-04、R-AC-15、R-AC-10)。

共通の下ごしらえは conftest.py の setup_devices(20.13 の例のとおり)。日時は 2026-12-01 以後の架空の物を、テストが選ぶ。
"""
from __future__ import annotations

import json

import pytest

from digitalkey.kanri import Unit, cli as kanri_cli, reasons
from kanri_common import ACCESS, ACCESS_WORK, GATE, ISSUE_AT, PHYSICAL, RB, T0, T1, dt, findings, plus

AT = dt(ISSUE_AT)
IN = "2026-12-05T10:00"
REV = "2026-12-10T12:00"
NOW = "2026-12-20T12:00"
ADM = dict(by="adm", role="管理者")
ROSTER = dict(by="adm", role="名簿の係")
ROUTER_CHECK = {"外からの口": "良い", "見覚えのない設定とアカウント": "良い"}


# ---- R-NW-02 ----

def test_r_nw_02_mixed_segment(dunit):
    """R-NW-02 扉の系統をほかの機器と別の区画に分ける(20.13 の 1、2)。
    複合機1 を区画 扉 に載せると複合機1 に F_MIXED_SEGMENT(詳細 扉)、玄関の錠には出ない。事務に変えると消える。"""
    dunit.change_device("複合機1", reason="置き直し", segment="扉", **ADM)
    assert findings(dunit, NOW, RB.F_MIXED_SEGMENT) == [("複合機1", RB.F_MIXED_SEGMENT, "扉")]
    assert findings(dunit, NOW, RB.F_MIXED_SEGMENT, "玄関の錠") == []
    dunit.change_device("複合機1", reason="分けた", segment="事務", **ADM)
    assert findings(dunit, NOW, RB.F_MIXED_SEGMENT) == []


def test_r_nw_02_no_segment_and_retired_segment(dunit, refused):
    """R-NW-02 区画の無い使用の機器は F_NO_SEGMENT(詳細 空)。区画 来客 を廃止にすると、そこの使用の機器は詳細 来客。
    廃止の区画への register_device と change_device は UNKNOWN_SEGMENT(20.13 の 3)。"""
    assert findings(dunit, NOW, RB.F_NO_SEGMENT, "ルーター1") == [("ルーター1", RB.F_NO_SEGMENT, "")]
    dunit.change_device("ルーター1", reason="来客の網へ", segment="来客", **ADM)
    assert findings(dunit, NOW, RB.F_NO_SEGMENT, "ルーター1") == []
    dunit.register_segment("来客", state="廃止", **ADM)
    assert findings(dunit, NOW, RB.F_NO_SEGMENT, "ルーター1") == [("ルーター1", RB.F_NO_SEGMENT, "来客")]
    refused(dunit, RB.UNKNOWN_SEGMENT, dunit.register_device, "無線1", "網の機器", "使用", segment="来客", **ADM,
            detail="来客")
    refused(dunit, RB.UNKNOWN_SEGMENT, dunit.change_device, "複合機1", reason="移す", segment="来客", **ADM,
            detail="来客")


@pytest.mark.parametrize("item", ["区画", "外からの口"])
def test_r_nw_02_bad_item(dunit, item):
    """R-NW-02 確かめで 区画 か 外からの口 を 悪い と書くと F_BAD_ITEM(詳細に項目の名)。後の確かめで 良い と書くと消え、
    後の確かめでその項目が空なら 悪い のまま残る(20.13 の 4)。

    限界(20.13 の 5): 台帳で確かめられるのは載せ方までで、実際のつなぎ方とポートの転送は、人の点検の記録(項目 区画、
    外からの口)で照らす。網を読む照らし合わせは段 6。"""
    first = {**ROUTER_CHECK, item: "悪い"}
    dunit.check_device("ルーター1", "点検", first, at="2026-12-01T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "ルーター1") == [("ルーター1", RB.F_BAD_ITEM, item)]
    other = {"外からの口": "良い", "見覚えのない設定とアカウント": "良い", "版": "良い"}
    if item == "区画":
        dunit.check_device("ルーター1", "点検", other, at="2026-12-02T09:00", **ADM)   # 区画 は空
        assert findings(dunit, NOW, RB.F_BAD_ITEM, "ルーター1") == [("ルーター1", RB.F_BAD_ITEM, item)]
    else:
        dunit.check_device("ルーター1", "据え付け", {"初めのパスワード": "良い"}, at="2026-12-02T09:00", **ADM)
        assert findings(dunit, NOW, RB.F_BAD_ITEM, "ルーター1") == [("ルーター1", RB.F_BAD_ITEM, item)]
    dunit.check_device("ルーター1", "点検", {**ROUTER_CHECK, item: "良い"}, at="2026-12-03T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "ルーター1") == []


# ---- R-NW-03 ----

def test_r_nw_03_no_support_end(dunit):
    """R-NW-03 使用の ルーター1 の更新の期限が空なら F_NO_SUPPORT_END(20.13 の 1)。"""
    assert findings(dunit, NOW, RB.F_NO_SUPPORT_END, "ルーター1") == [("ルーター1", RB.F_NO_SUPPORT_END, "")]
    dunit.change_device("ルーター1", reason="製造者の公表を見た", support_until="2027-03-31", **ADM)
    assert findings(dunit, NOW, RB.F_NO_SUPPORT_END, "ルーター1") == []


def test_r_nw_03_support_ended(dunit):
    """R-NW-03 期限 2027-03-31 で、at の日付が 2027-03-31 なら F_SUPPORT_ENDED は出ず(期限の日は内)、2027-04-01 なら出る
    (詳細 2027-03-31)(20.13 の 2)。"""
    dunit.change_device("ルーター1", reason="製造者の公表を見た", support_until="2027-03-31", **ADM)
    assert findings(dunit, "2027-03-31T12:00", RB.F_SUPPORT_ENDED, "ルーター1") == []
    assert findings(dunit, "2027-04-01T12:00", RB.F_SUPPORT_ENDED, "ルーター1") == \
        [("ルーター1", RB.F_SUPPORT_ENDED, "2027-03-31")]


def test_r_nw_03_support_near(dunit, refused):
    """R-NW-03 設定 更新の期限の知らせの日数 を 30 にすると、2027-03-01(残り 30 日)と 2027-03-31(残り 0 日)で F_SUPPORT_NEAR、
    2027-02-28(残り 31 日)と 2027-04-01 では出ない。設定が無ければどの日も出ない。0、-1、三十 は BAD_SETTING(20.13 の 3)。"""
    dunit.change_device("ルーター1", reason="製造者の公表を見た", support_until="2027-03-31", **ADM)
    days = ["2027-02-28T12:00", "2027-03-01T12:00", "2027-03-31T12:00", "2027-04-01T12:00"]
    for d in days:
        assert findings(dunit, d, RB.F_SUPPORT_NEAR, "ルーター1") == []
    dunit.set_setting("更新の期限の知らせの日数", "30", reason="管理者が決めた", **ADM)
    got = {d: findings(dunit, d, RB.F_SUPPORT_NEAR, "ルーター1") for d in days}
    near = [("ルーター1", RB.F_SUPPORT_NEAR, "2027-03-31")]
    assert got == {days[0]: [], days[1]: near, days[2]: near, days[3]: []}
    for v in ("0", "-1", "三十"):
        refused(dunit, reasons.BAD_SETTING, dunit.set_setting, "更新の期限の知らせの日数", v, reason="試し", **ADM)


def test_r_nw_03_install_needs_initial_password(dunit, refused):
    """R-NW-03 据え付けの確かめに 初めのパスワード が無いと NEED_ITEM(詳細 初めのパスワード)で、伝票は増えない。
    当たらない は通る(20.13 の 4)。"""
    refused(dunit, RB.NEED_ITEM, dunit.check_device, "ルーター1", "据え付け", {"外からの口": "良い"},
            at="2026-12-01T09:00", **ADM, detail="初めのパスワード")
    slip = dunit.check_device("ルーター1", "据え付け", {"初めのパスワード": "当たらない"}, at="2026-12-01T09:00", **ADM)
    assert slip.form == "機器_確かめ"


def test_r_nw_03_initial_password_bad_then_fixed(dunit):
    """R-NW-03 据え付けで 初めのパスワード を 悪い と書くと F_BAD_ITEM。替えた後の点検で 良い と書くと消える。
    据え付けの確かめが一枚も無い使用の機器は F_NO_INSTALL_CHECK(20.13 の 5)。"""
    assert findings(dunit, NOW, RB.F_NO_INSTALL_CHECK, "ルーター1") == [("ルーター1", RB.F_NO_INSTALL_CHECK, "")]
    dunit.check_device("ルーター1", "据え付け", {"初めのパスワード": "悪い"}, at="2026-12-01T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_NO_INSTALL_CHECK, "ルーター1") == []
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "ルーター1") == [("ルーター1", RB.F_BAD_ITEM, "初めのパスワード")]
    dunit.check_device("ルーター1", "点検", {**ROUTER_CHECK, "初めのパスワード": "良い"}, at="2026-12-02T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "ルーター1") == []


def test_r_nw_03_updates(dunit, refused):
    """R-NW-03 版 1.0 で登録し、record_update("1.1") の後 版 は 1.1、機器_更新の 前の版 は 1.0。当てられなかった の後も 1.1 で、
    理由が伝票に残る。当てられなかった で理由が空なら NEED_REASON(20.13 の 6)。"""
    dunit.register_device("ルーター2", "ルーター", "使用", version="1.0", **ADM)
    s = dunit.record_update("ルーター2", "1.1", at="2026-12-01T09:00", **ADM)
    assert dunit.device("ルーター2").版 == "1.1"
    assert (s["前の版"], s["後の版"], s["結果"]) == ("1.0", "1.1", "当てた")
    f = dunit.record_update("ルーター2", "1.2", result="当てられなかった", reason="製造者の更新が止まっている",
                            at="2026-12-02T09:00", **ADM)
    assert dunit.device("ルーター2").版 == "1.1"
    assert (f["前の版"], f["後の版"], f["結果"], f["理由"]) == ("1.1", "1.2", "当てられなかった", "製造者の更新が止まっている")
    refused(dunit, RB.NEED_REASON, dunit.record_update, "ルーター2", "1.2", result="当てられなかった",
            at="2026-12-03T09:00", **ADM)


# ---- R-NW-04 ----

def test_r_nw_04_router_inspection_needs_two_items(dunit, refused):
    """R-NW-04 種類 ルーター の点検で、外からの口 か 見覚えのない設定とアカウント が空なら NEED_ITEM(詳細に足りない最初の項目)。
    種類 プリンター の点検は、どちらが空でも通る(20.13 の 1)。"""
    ck = dunit.check_device
    refused(dunit, RB.NEED_ITEM, ck, "ルーター1", "点検", {"版": "良い"}, at="2026-12-01T09:00", **ADM,
            detail="外からの口")
    refused(dunit, RB.NEED_ITEM, ck, "ルーター1", "点検", {"外からの口": "良い"}, at="2026-12-01T09:00", **ADM,
            detail="見覚えのない設定とアカウント")
    assert ck("複合機1", "点検", {"版": "良い"}, at="2026-12-01T09:00", **ADM).form == "機器_確かめ"


def test_r_nw_04_inspection_is_recorded(dunit):
    """R-NW-04 二つを書いた点検は、checks の最後の Check に、場面 点検、日時、結果、書いた人 が残る(20.13 の 2)。"""
    dunit.check_device("ルーター1", "点検", ROUTER_CHECK, note="転送の設定なし", at="2026-12-01T09:00",
                       by="ops", role="運用")
    c = dunit.checks("ルーター1")[-1]
    assert (c.場面, c.日時, c.結果, c.書いた人, c.所見) == ("点検", "2026-12-01T09:00", ROUTER_CHECK, "ops", "転送の設定なし")


def test_r_nw_04_check_overdue(dunit):
    """R-NW-04 設定 点検の間の日数 を 90 にし、最後の点検の日時を 2026-12-01T09:00 にすると、2027-03-01T09:00(ちょうど 90 日)では
    F_CHECK_OVERDUE は出ず、2027-03-01T09:01 で出る(詳細 2026-12-01T09:00)。確かめの無い使用の機器は詳細 確かめなし(20.13 の 3)。"""
    dunit.set_setting("点検の間の日数", "90", reason="管理者が決めた", **ADM)
    dunit.check_device("ルーター1", "点検", ROUTER_CHECK, at="2026-12-01T09:00", **ADM)
    assert findings(dunit, "2027-03-01T09:00", RB.F_CHECK_OVERDUE, "ルーター1") == []
    assert findings(dunit, "2027-03-01T09:01", RB.F_CHECK_OVERDUE, "ルーター1") == \
        [("ルーター1", RB.F_CHECK_OVERDUE, "2026-12-01T09:00")]
    assert findings(dunit, "2027-03-01T09:00", RB.F_CHECK_OVERDUE, "複合機1") == \
        [("複合機1", RB.F_CHECK_OVERDUE, "確かめなし")]


def test_r_nw_04_no_setting_no_overdue(dunit):
    """R-NW-04 設定を書いていない置き場では、どれだけ間が空いても出ない。設定の値を空に書き直すと、また出なくなる(20.13 の 4)。"""
    dunit.check_device("ルーター1", "点検", ROUTER_CHECK, at="2026-12-01T09:00", **ADM)
    assert findings(dunit, "2030-01-01T00:00", RB.F_CHECK_OVERDUE) == []
    dunit.set_setting("点検の間の日数", "90", reason="管理者が決めた", **ADM)
    assert len(findings(dunit, "2030-01-01T00:00", RB.F_CHECK_OVERDUE, "ルーター1")) == 1
    dunit.set_setting("点検の間の日数", "", reason="知らせを止める", **ADM)
    assert findings(dunit, "2030-01-01T00:00", RB.F_CHECK_OVERDUE) == []


# ---- R-NW-05 ----

@pytest.mark.parametrize("state,listed", [("準備", True), ("使用", True), ("休止", True), ("紛失", True),
                                          ("廃棄", False)])
def test_r_nw_05_no_owner(dunit, state, listed):
    """R-NW-05 責任者のいない使用の ルーター1 に F_NO_OWNER。準備、休止、紛失の機器にも出る。廃棄の機器には出ない(20.13 の 1)。"""
    if state != "使用":
        dunit.change_device("ルーター1", reason="試し", state=state, at=dt(NOW), **ADM)
    want = [("ルーター1", RB.F_NO_OWNER, "")] if listed else []
    assert findings(dunit, NOW, RB.F_NO_OWNER, "ルーター1") == want


def test_r_nw_05_owner_set_inactive_and_replaced(dunit, refused):
    """R-NW-05 change_device(owner="t1") で F_NO_OWNER が消える。t1 を退出にすると F_OWNER_INACTIVE(詳細 t1)。
    退出の t1 を責任者にする change_device は SUBJECT_INACTIVE、名簿に無い識別は SUBJECT_UNKNOWN。t2 に替えると消える
    (20.13 の 2〜4)。

    要求の「ルーターが止まっても、乗っ取られても、扉の判断と記録は扉の側で続く」は扉のコントローラのテストの持ち分(20.13 の 5)。"""
    dunit.change_device("ルーター1", reason="持ち主を決めた", owner="t1", **ADM)
    assert findings(dunit, NOW, RB.F_NO_OWNER, "ルーター1") == []
    dunit.change_subject("t1", reason="退職", state="退出", **ROSTER)
    assert findings(dunit, NOW, RB.F_OWNER_INACTIVE, "ルーター1") == [("ルーター1", RB.F_OWNER_INACTIVE, "t1")]
    refused(dunit, reasons.SUBJECT_INACTIVE, dunit.change_device, "複合機1", reason="持ち主", owner="t1", **ADM)
    refused(dunit, reasons.SUBJECT_UNKNOWN, dunit.change_device, "複合機1", reason="持ち主", owner="x9", **ADM)
    dunit.change_device("ルーター1", reason="引き継ぎ", owner="t2", **ADM)
    assert findings(dunit, NOW, RB.F_OWNER_INACTIVE, "ルーター1") == []
    assert findings(dunit, NOW, RB.F_NO_OWNER, "ルーター1") == []


# ---- R-RS-04 ----

def test_r_rs_04_vendor_inventory(dunit, access, grant, forms):
    """R-RS-04 業者と連携先に渡している権限と鍵の目録を持つ(20.13 の 1〜4)。"""
    # 1. v1 に ルーター1 の機器の権限(終了つき)と管理者の口 maint。v1 に扉 玄関 の権限と門番を通す錠。t1 にも機器の権限と口
    p_dev, maint = access(dunit, "v1", account="maint")
    p_door, _, _ = grant(dunit, "v1")
    gate = dunit.issue_entity(p_door, GATE, **ADM, at=AT).実体
    p_t1, _ = access(dunit, "t1")
    # 2. 目録は v1 の二つの権限だけ(申請を書いた順)。機器の権限の 残り は管理者の口の行一つで 効いている
    inv = dunit.vendor_inventory(at=dt(IN))
    assert [h.権限 for h in inv] == [p_dev, p_door]
    assert p_t1 not in [h.権限 for h in inv]
    assert [(r.実体, r.経路, r.状態) for r in inv[0].残り] == [(maint.番号, ACCESS, "効いている")]
    assert [r.実体 for r in inv[1].残り] == [gate.番号]
    # 3. 失効の後: 扉の権限は門番の行が 止まった なので消える。機器の権限は 失効 で残り、残り の行は 人の作業が要る
    dunit.revoke_subject("v1", "契約の終了", **ADM, at=dt(REV))
    inv = dunit.vendor_inventory(at=dt(REV))
    assert [(h.権限, h.状態) for h in inv] == [(p_dev, "失効")]
    assert [(r.実体, r.状態) for r in inv[0].残り] == [(maint.番号, "人の作業が要る")]
    # 4. 運用の ops が削除を書くと、目録は空
    dunit.remove_access(maint.番号, by="ops", role="運用")
    assert dunit.vendor_inventory(at=dt(REV)) == []


def test_r_rs_04_vendor_physical_key_until_returned(dunit, grant):
    """R-RS-04 業者の物理キー(貸し出したまま)も、回収まで目録に残る(20.13 の 5)。"""
    pid, _, _ = grant(dunit, "v1")
    k = dunit.issue_entity(pid, PHYSICAL, key_number="K-9", **ADM, at=AT).実体
    dunit.revoke_subject("v1", "契約の終了", **ADM, at=dt(REV))
    h, = dunit.vendor_inventory(at=dt(REV))
    assert (h.権限, [r.実体 for r in h.残り]) == (pid, [k.番号])
    dunit.return_physical(k.番号, by="adm", role="名簿の係")
    assert dunit.vendor_inventory(at=dt(REV)) == []


def test_r_rs_04_shared_account(dunit, access):
    """R-RS-04 一つの口を複数の人が使う機器: 複合機1 の口 admin を t1 と t2 に出す(拒まない)と F_SHARED_ACCOUNT(詳細 admin)が
    一つ。一人の口に パスワードを替えた の削除を書くと、止まっていない admin の主体が一人になり、消える(20.13)。"""
    _, a = access(dunit, "t1", "複合機1")
    _, b = access(dunit, "t2", "複合機1")
    assert findings(dunit, NOW, RB.F_SHARED_ACCOUNT) == [("複合機1", RB.F_SHARED_ACCOUNT, "admin")]
    dunit.remove_access(b.番号, "パスワードを替えた", **ADM)
    assert findings(dunit, NOW, RB.F_SHARED_ACCOUNT) == []


# ---- R-AC-15 ----

def _cli(*argv):
    try:
        code = kanri_cli.main([str(a) for a in argv])
    except SystemExit as e:
        code = e.code
    return 0 if code is None else code


def test_r_ac_15_doors_and_devices_on_one_sheet(dfile_unit, grant, refused, forms, capsys):
    """R-AC-15 一人の全経路を一覧にし、一度の操作で全部を失効できる。機器の口も同じ一枚(20.13 の 1〜5、20.8 の例)。"""
    u = dfile_unit
    # 1. s1 に、扉 玄関 の権限から門番を通す錠(E1)、機器 ルーター1 の権限から管理者の口 admin(E2)
    p_door, _, _ = grant(u, "s1")
    e1 = u.issue_entity(p_door, GATE, **ADM, at=AT).実体
    p_dev, _, _ = grant(u, "s1", "機器", "ルーター1")
    e2 = u.issue_entity(p_dev, ACCESS, account="admin", **ADM, at=AT).実体
    ents = u.entities("s1")
    assert [(e.番号, e.機器) for e in ents] == [(e1.番号, ""), (e2.番号, "ルーター1")]
    # 2. revoke_subject を一度。権限_失効 2 枚、実体_失効 2 枚(E1 止まった、E2 人の作業が要る)
    before = forms(u)
    rev = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert forms(u) - before == {"権限_失効": 2, "実体_失効": 2}
    assert {s["実体"]: s["結果"] for s in rev.実体_失効} == {e1.番号: "止まった", e2.番号: "人の作業が要る"}
    # 3. 同じ一枚に二行。済み は偽。confirm_stopped は NOT_ALL_STOPPED(詳細に E2)。E2 の reconcile は 違い
    s = u.progress("s1", at=dt(REV))
    assert [(r.実体, r.状態, r.機器) for r in s.行] == [(e1.番号, "止まった", ""), (e2.番号, "人の作業が要る", "ルーター1")]
    assert s.済み is False
    refused(u, reasons.NOT_ALL_STOPPED, u.confirm_stopped, "s1", **ADM, at=dt(REV), detail=e2.番号)
    r = u.reconcile(e2.番号, at=dt(REV))
    assert (r.結果, r.詳細) == ("違い", "口が残ったまま")
    # 5. 命令 progress の人が読む行は 7 列(20.8 の例のとおり)。--json の行に鍵 機器
    capsys.readouterr()
    assert _cli("progress", "--dir", u.root, "--subject", "s1", "--at", REV) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        f"s1 {REV} 未了",
        f"止まった\t{GATE}\t{e1.識別}\t玄関\t\t\t",
        f"人の作業が要る\t{ACCESS}\tadmin\t\t\t{ACCESS_WORK.format('ルーター1', 'admin')}\tルーター1",
    ]
    assert all(len(line.split("\t")) == 7 for line in lines[1:])
    assert _cli("progress", "--dir", u.root, "--subject", "s1", "--at", REV, "--json") == 0
    rows = json.loads(capsys.readouterr().out)["実体"]
    assert [(x["実体"], x["機器"]) for x in rows] == [(e1.番号, ""), (e2.番号, "ルーター1")]
    # 4. remove_access の後、E2 は 止まった、済み は真、confirm_stopped が失効_確認を書く。二度目は ALREADY_REMOVED
    u.remove_access(e2.番号, "消した", **ADM)
    s = u.progress("s1", at=dt(REV))
    assert {r.実体: r.状態 for r in s.行}[e2.番号] == "止まった"
    assert s.済み is True
    assert u.confirm_stopped("s1", **ADM, at=dt(REV)).form == "失効_確認"
    refused(u, RB.ALREADY_REMOVED, u.remove_access, e2.番号, "消した", **ADM)
    assert Unit.open(u.root).progress("s1", at=dt(REV)).済み is True


# ---- R-AC-10 ----

def test_r_ac_10_device_permission_needs_end(dunit, refused):
    """R-AC-10 業者 v1 の、終了が空の機器の権限の request は VENDOR_NO_END(20.13 の 1)。"""
    refused(dunit, reasons.VENDOR_NO_END, dunit.request, "v1", "機器", "ルーター1", T0, "", "保守", **ROSTER)


def test_r_ac_10_device_permission_max_days(dunit, refused):
    """R-AC-10 設定 業者の最長の日数 を N にすると、N 日を 1 分超える機器の権限の申請は VENDOR_TOO_LONG、ちょうど N 日は通る
    (20.13 の 2)。"""
    dunit.set_setting("業者の最長の日数", "14", reason="保守の契約", **ADM)
    refused(dunit, reasons.VENDOR_TOO_LONG, dunit.request, "v1", "機器", "ルーター1", T0, plus(T0, days=14, minutes=1),
            "保守", **ROSTER)
    assert dunit.request("v1", "機器", "ルーター1", T0, plus(T0, days=14), "保守", **ROSTER).form == "権限_申請"


# ---- 機器の紛失と廃棄(R-AC-15、R-NW-05) ----

def test_r_ac_15_r_nw_05_lost_and_retired_device(dunit, access, grant, request_, approve, refused, forms):
    """R-AC-15 R-NW-05 機器の紛失と廃棄(20.13): 紛失で機器の権限を全部止め、扉の権限は止めない。口は削除まで知らせに残る。
    戻しても権限は戻らない。廃棄の後は確かめと知らせ、変更と更新と申請を拒む。"""
    # 1. t1 の発行済み(管理者の口 E1)、v1 の承認だけ、t2 の申請中。t1 には扉 玄関 の門番を通す錠の権限も
    p1, e1 = access(dunit, "t1")
    p2 = request_(dunit, "v1", "機器", "ルーター1")
    approve(dunit, p2.id)
    p3 = request_(dunit, "t2", "機器", "ルーター1")
    p_door, _, _ = grant(dunit, "t1")
    dunit.issue_entity(p_door, GATE, **ADM, at=AT)
    # 2. 紛失: 権限_失効 3 枚(機器の停止、理由 なくした)、実体_失効 1 枚(E1、人の作業が要る)。扉の権限はそのまま
    got = dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    assert [(s["権限"], s["範囲"], s["理由"]) for s in got.権限_失効] == \
        [(p1, "機器の停止", "なくした"), (p2.id, "機器の停止", "なくした"), (p3.id, "機器の停止", "なくした")]
    assert [(s["実体"], s["結果"]) for s in got.実体_失効] == [(e1.番号, "人の作業が要る")]
    for pid in (p1, p2.id, p3.id):
        assert dunit.permission(pid, at=dt(REV)).状態 == "失効"
    assert dunit.permission(p_door, at=dt(REV)).状態 == "発行済み"
    assert dunit.may_open("t1", "玄関", at=dt(REV)).ok is True
    # 3. F_ACCESS_LEFT(詳細に E1)。remove_access の後に消える
    assert findings(dunit, REV, RB.F_ACCESS_LEFT) == [("ルーター1", RB.F_ACCESS_LEFT, e1.番号)]
    dunit.remove_access(e1.番号, "機器が無い", **ADM)
    assert findings(dunit, REV, RB.F_ACCESS_LEFT) == []
    # 4. 使用 に戻しても三つは 失効 のまま。前の権限の issue_entity は REVOKED。新しい申請は通る
    dunit.change_device("ルーター1", reason="見つかった", state="使用", at=dt(REV), **ADM)
    for pid in (p1, p2.id, p3.id):
        assert dunit.permission(pid, at=dt(REV)).状態 == "失効"
    refused(dunit, reasons.REVOKED, dunit.issue_entity, p1, ACCESS, account="admin", **ADM, at=AT)
    p4, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    # 5. 廃棄: F_NO_DISPOSAL_CHECK。廃棄の確かめで消える。変更、更新、点検は DEVICE_RETIRED。申請は UNKNOWN_DEVICE。
    #    廃棄の時に失効した権限(p4)の issue_entity は REVOKED
    got = dunit.change_device("ルーター1", reason="壊れた", state="廃棄", at=dt(REV), **ADM)
    assert [s["権限"] for s in got.権限_失効] == [p4]
    assert findings(dunit, REV, RB.F_NO_DISPOSAL_CHECK) == [("ルーター1", RB.F_NO_DISPOSAL_CHECK, "")]
    dunit.check_device("ルーター1", "廃棄", {"中に残る物": "良い", "網から外した": "良い"}, at=REV, **ADM)
    assert findings(dunit, REV, RB.F_NO_DISPOSAL_CHECK) == []
    refused(dunit, RB.DEVICE_RETIRED, dunit.change_device, "ルーター1", reason="直し", place="倉庫", **ADM)
    refused(dunit, RB.DEVICE_RETIRED, dunit.record_update, "ルーター1", "9.9", at=REV, **ADM)
    refused(dunit, RB.DEVICE_RETIRED, dunit.check_device, "ルーター1", "点検", ROUTER_CHECK, at=REV, **ADM)
    refused(dunit, RB.UNKNOWN_DEVICE, dunit.request, "t1", "機器", "ルーター1", T0, T1, "保守", **ROSTER,
            detail="ルーター1")
    refused(dunit, reasons.REVOKED, dunit.issue_entity, p4, ACCESS, account="admin", **ADM, at=AT)


# ---- 拒否と役(R-NW-05、R-RS-04) ----

def test_r_nw_05_r_rs_04_refusals_leave_ledger_unchanged(dunit, access, grant, refused):
    """R-NW-05 R-RS-04 拒んだ時は台帳の伝票の数が変わらない(NAME_HAS_COMMA、DUPLICATE_DEVICE、UNKNOWN_SEGMENT、NEED_ITEM、
    NOT_OWNER、DEVICE_RETIRED、DEVICE_ONLY、NEED_ACCOUNT のそれぞれで)(20.13 の拒否と役 1)。"""
    u = dunit
    n0 = u.ledger.count()
    refused(u, reasons.NAME_HAS_COMMA, u.register_device, "PC,1", "PC", "使用", **ADM)
    refused(u, reasons.NAME_HAS_COMMA, u.register_segment, "設,備", **ADM)
    refused(u, RB.DUPLICATE_DEVICE, u.register_device, "ルーター1", "ルーター", "使用", **ADM)
    refused(u, RB.UNKNOWN_SEGMENT, u.register_device, "PC1", "PC", "使用", segment="設備", **ADM)
    refused(u, RB.NEED_ITEM, u.check_device, "ルーター1", "据え付け", {"版": "良い"}, at="2026-12-01T09:00", **ADM)
    refused(u, RB.NOT_OWNER, u.check_device, "ルーター1", "点検", ROUTER_CHECK, at="2026-12-01T09:00",
            by="t2", role="本人")
    assert u.ledger.count() == n0
    pd, _, _ = grant(u, "t1")
    refused(u, RB.DEVICE_ONLY, u.issue_entity, pd, ACCESS, account="admin", **ADM, at=AT)
    pdev, _, _ = grant(u, "t1", "機器", "ルーター1")
    refused(u, RB.NEED_ACCOUNT, u.issue_entity, pdev, ACCESS, **ADM, at=AT)
    u.change_device("複合機1", reason="入れ替え", state="廃棄", **ADM)
    n1 = u.ledger.count()
    refused(u, RB.DEVICE_RETIRED, u.record_update, "複合機1", "2.0", at="2026-12-01T09:00", **ADM)
    assert u.ledger.count() == n1


def test_r_nw_05_r_rs_04_owner_role(dunit, refused):
    """R-NW-05 R-RS-04 本人の役で、責任者でない t2 の check_device は NOT_OWNER。責任者の t1 は通る。運用の ops は責任者を問わず通る。
    register_device を運用の役で呼ぶと ROLE_NOT_ALLOWED(20.13 の拒否と役 2)。"""
    dunit.change_device("ルーター1", reason="持ち主", owner="t1", **ADM)
    refused(dunit, RB.NOT_OWNER, dunit.check_device, "ルーター1", "点検", ROUTER_CHECK, at="2026-12-01T09:00",
            by="t2", role="本人")
    assert dunit.check_device("ルーター1", "点検", ROUTER_CHECK, at="2026-12-01T09:00", by="t1", role="本人").issuer == "t1"
    assert dunit.check_device("ルーター1", "点検", ROUTER_CHECK, at="2026-12-02T09:00", by="ops", role="運用").issuer == "ops"
    refused(dunit, reasons.ROLE_NOT_ALLOWED, dunit.register_device, "PC1", "PC", "使用", by="ops", role="運用")


def test_r_nw_05_r_rs_04_route_and_permission_kind(dunit, grant, refused):
    """R-NW-05 R-RS-04 扉の権限から管理者の口は DEVICE_ONLY。機器の権限から門番を通す錠は NO_DOOR_FOR_ROUTE。
    機器の権限から account の無い管理者の口は NEED_ACCOUNT(20.13 の拒否と役 3)。"""
    pd, _, _ = grant(dunit, "t1")
    refused(dunit, RB.DEVICE_ONLY, dunit.issue_entity, pd, ACCESS, account="admin", **ADM, at=AT)
    pdev, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.NO_DOOR_FOR_ROUTE, dunit.issue_entity, pdev, GATE, **ADM, at=AT)
    refused(dunit, RB.NEED_ACCOUNT, dunit.issue_entity, pdev, ACCESS, **ADM, at=AT)


def test_r_nw_05_r_rs_04_access_only_by_admin(dunit, grant, refused):
    """R-NW-05 R-RS-04 機器の権限から実体を出すのは管理者だけ。名簿の係の役では ROLE_NOT_ALLOWED(20.13 の拒否と役 4)。"""
    pdev, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.ROLE_NOT_ALLOWED, dunit.issue_entity, pdev, ACCESS, account="admin", by="adm",
            role="名簿の係", at=AT)
    assert dunit.issue_entity(pdev, ACCESS, account="admin", **ADM, at=AT).実体.機器 == "ルーター1"
