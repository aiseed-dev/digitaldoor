"""設計書 20.1〜20.6、20.11: 段 1b の外に出す名前、データの型、役と伝票の組、区画、機器の台帳(登録、変更、紛失と廃棄)。

段 1b の実装より先に書いた。まだ無い名前は関数の中で引く(収集は通り、試験は AttributeError で落ちる)。
"""
from __future__ import annotations

import dataclasses
import inspect
from datetime import date

import pytest

import digitalkey.kanri as kanri
from digitalkey.kanri import reasons, routes
from kanri_common import (ACCESS, ACCESS_WORK, ALL4, FINDINGS_1B, FORMS_5A, GATE, ISSUE_AT, REFUSALS_1B, RB, dt)

AT = dt(ISSUE_AT)
REV = "2026-12-10T12:00"
ADM = dict(by="adm", role="管理者")


# ---- 20.11 拒否の言葉と知らせの言葉 ----

@pytest.mark.parametrize("name", REFUSALS_1B + FINDINGS_1B)
def test_reason_and_finding_words(name):
    """20.11: reasons.py に足す拒否の言葉と、知らせの言葉(F_ で始まる定数)。"""
    assert getattr(reasons, name) == getattr(RB, name)


# ---- 20.1、20.2、20.4 外に出す名前とデータの型 ----

TYPES_1B = {
    "Segment": ["名前", "説明", "状態"],
    "Device": ["名前", "種類", "責任者", "置き場", "区画", "扉の系統", "扉", "製造者", "型", "製造番号", "網の番号", "版",
               "更新の期限", "更新の当て方", "秘密の在庫の番号", "記録の送り先", "中に残る物", "状態"],
    "DeviceChange": ["伝票", "権限_失効", "実体_失効", "失効_一覧"],   # 21.2: 失効_一覧 を最後に足す
    "Check": ["番号", "機器", "場面", "日時", "結果", "所見", "書いた人"],
    "Finding": ["機器", "知らせ", "詳細"],
    "Held": ["主体", "権限", "対象の種類", "対象", "開始", "終了", "状態", "残り"],
}


@pytest.mark.parametrize("name", sorted(TYPES_1B))
def test_data_types_1b(name):
    """20.4: 足す型は dataclass(frozen=True)、属性の名前と並びは表のとおり。`digitalkey.kanri` から出す(20.1)。"""
    cls = getattr(kanri, name)
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen
    assert [f.name for f in dataclasses.fields(cls)] == TYPES_1B[name]


@pytest.mark.parametrize("name", ["Entity", "Row"])
def test_entity_and_row_get_device_last(name):
    """20.2、20.4、20.14: Entity と Row に属性 機器 を最後に足す(段 1 の並びを変えない)。"""
    names = [f.name for f in dataclasses.fields(getattr(kanri, name))]
    assert names[-1] == "機器"


def test_access_constant_and_registry():
    """20.1、20.2: 経路の名の定数 ACCESS = "管理者の口" を外に出し、登録簿 available() の組み込みは四つ。"""
    assert kanri.ACCESS == ACCESS
    assert routes.ACCESS == ACCESS
    got = routes.available()
    assert set(ALL4) <= set(got)
    assert got[ACCESS].name == ACCESS


def test_unit_has_five_mixins():
    """20.1: Unit は DevicesMixin を継ぐ。"""
    from digitalkey.kanri.devices import DevicesMixin
    assert issubclass(kanri.Unit, DevicesMixin)


def test_ledger_opens_with_twenty_three_forms(make_unit):
    """20.2、21.2、22.2: 台帳は 23 枚の様式で開く(20.2 で 区画、機器_登録、機器_変更、機器_確かめ、機器_更新、管理者の口_削除、
    21.2 で 失効_一覧、失効_配布、22.2 で 主体_結び を足す)。"""
    u = make_unit()
    assert set(u.ledger.form_set) == set(FORMS_5A)
    assert len(u.ledger.form_set) == 23


def test_default_routes_are_four(make_unit):
    """20.2: Unit の routes を省いた時も、組み込みの四つ。"""
    u = make_unit("R")
    assert set(u.routes) == set(ALL4)


# ---- 20.3 役と操作 ----

def _write_calls_1b(u):
    """20.3 の表の書くメソッドを一つずつ呼ぶ(by と role は後で足す)。"""
    return {
        "register_segment": lambda **kw: u.register_segment("設備", **kw),
        "register_device": lambda **kw: u.register_device("ルーター9", "ルーター", "使用", **kw),
        "change_device": lambda **kw: u.change_device("ルーター1", reason="直し", place="二階", **kw),
        "check_device": lambda **kw: u.check_device("複合機1", "点検", {"版": "良い"}, at="2026-12-01T09:00", **kw),
        "record_update": lambda **kw: u.record_update("ルーター1", "1.1", at="2026-12-01T09:00", **kw),
        "remove_access": lambda **kw: u.remove_access("x", **kw),
    }


METHODS_1B = ["register_segment", "register_device", "change_device", "check_device", "record_update", "remove_access"]

NOT_ALLOWED_1B = {
    "register_segment": "運用",
    "register_device": "運用",
    "change_device": "名簿の係",
    "check_device": "名簿の係",
    "record_update": "承認する人",
    "remove_access": "名簿の係",
}

ALLOWED_1B = {
    "register_segment": "管理者",
    "register_device": "管理者",
    "change_device": "管理者",
    "check_device": "運用",
    "record_update": "運用",
    "remove_access": "運用",
}


@pytest.mark.parametrize("method", METHODS_1B)
@pytest.mark.parametrize("role", ["発行者", "初期化"])
def test_machine_and_init_roles_never_allowed_1b(dunit, refused, method, role):
    """20.3、19.3: role に 発行者 か 初期化 を渡すと ROLE_NOT_ALLOWED。"""
    refused(dunit, reasons.ROLE_NOT_ALLOWED, _write_calls_1b(dunit)[method], by="adm", role=role)


@pytest.mark.parametrize("method", METHODS_1B)
def test_role_not_in_table_1b(dunit, refused, method):
    """20.3、19.3 の 1: できる役の表に無い役は、by が名簿に無くても ROLE_NOT_ALLOWED。"""
    calls = _write_calls_1b(dunit)
    refused(dunit, reasons.ROLE_NOT_ALLOWED, calls[method], by="adm", role=NOT_ALLOWED_1B[method])
    refused(dunit, reasons.ROLE_NOT_ALLOWED, calls[method], by="nobody", role=NOT_ALLOWED_1B[method])


@pytest.mark.parametrize("method", METHODS_1B)
def test_by_unknown_1b(dunit, refused, method):
    """20.3、19.3 の 2: by が名簿に無ければ BY_UNKNOWN。"""
    refused(dunit, reasons.BY_UNKNOWN, _write_calls_1b(dunit)[method], by="nobody", role=ALLOWED_1B[method])


@pytest.mark.parametrize("method", METHODS_1B)
def test_by_inactive_1b(dunit, refused, method):
    """20.3、19.3 の 3: by が有効でなければ BY_INACTIVE。"""
    dunit.change_subject("t2", reason="休職", state="停止", by="adm", role="名簿の係")
    refused(dunit, reasons.BY_INACTIVE, _write_calls_1b(dunit)[method], by="t2", role=ALLOWED_1B[method])


@pytest.mark.parametrize("method", METHODS_1B)
def test_no_role_1b(dunit, refused, method):
    """20.3、19.3 の 4: by が role を持たなければ NO_ROLE(t1 は本人だけを持つ)。"""
    refused(dunit, reasons.NO_ROLE, _write_calls_1b(dunit)[method], by="t1", role=ALLOWED_1B[method])


def test_reads_take_no_role_1b(dunit):
    """20.3: 読むメソッド(segments、device、devices、checks、device_findings、vendor_inventory)は役を取らない。"""
    dunit.segments(), dunit.device("ルーター1"), dunit.devices(), dunit.checks("ルーター1")
    dunit.device_findings(at=dt(REV)), dunit.vendor_inventory(at=dt(REV))


# ---- 20.5 区画 ----

def test_register_segment_example(make_unit):
    """20.5 の例: register_segment("扉", note=...) → 伝票 区画、状態 使用。宛先は空、根拠なし。"""
    u = make_unit()
    slip = u.register_segment("扉", note="錠、扉のコントローラ、小さなサーバー", by="adm", role="管理者")
    assert slip.form == "区画" and slip.issuer == "adm" and slip.to == "" and slip.basis == []
    assert (slip["名前"], slip["説明"], slip["状態"], slip["役"]) == ("扉", "錠、扉のコントローラ、小さなサーバー", "使用", "管理者")
    s, = u.segments()
    assert (s.名前, s.説明, s.状態) == ("扉", "錠、扉のコントローラ、小さなサーバー", "使用")


def test_segments_replace_and_order(dunit):
    """20.5: 同じ名前の二枚目は今の値を置き換える。segments() は廃止も入れ、最初の伝票を台帳に書いた順。"""
    dunit.register_segment("事務", note="事務の網", state="廃止", **ADM)
    dunit.register_segment("設備", **ADM)
    got = [(s.名前, s.説明, s.状態) for s in dunit.segments()]
    assert got == [("扉", "錠、扉のコントローラ、小さなサーバー", "使用"), ("事務", "事務の網", "廃止"),
                   ("来客", "来客の無線", "使用"), ("設備", "", "使用")]


def test_register_segment_refusals(dunit, refused):
    """20.5: 名前に「,」は NAME_HAS_COMMA。state が 使用 / 廃止 のほかは FORM。"""
    refused(dunit, reasons.NAME_HAS_COMMA, dunit.register_segment, "事,務", **ADM)
    refused(dunit, reasons.FORM, dunit.register_segment, "設備", state="撤去", **ADM)


def test_retired_segment_keeps_devices(dunit):
    """20.5: 廃止した区画を載せている機器は、そのまま残る。"""
    dunit.register_segment("扉", state="廃止", **ADM)
    assert dunit.device("玄関の錠").区画 == "扉"


# ---- 20.6 登録 ----

def test_register_device_example(dunit):
    """20.6 の例: register_device("ルーター1", "ルーター", "使用", owner="t1", segment="事務", support_until="2027-03-31")
    → 伝票 機器_登録、扉の系統 は偽、版 は空。"""
    slip = dunit.register_device("ルーター2", "ルーター", "使用", owner="t1", segment="事務", support_until="2027-03-31",
                                 **ADM)
    assert slip.form == "機器_登録" and slip.issuer == "adm" and slip.to == "" and slip.basis == []
    d = dunit.device("ルーター2")
    assert d.扉の系統 is False and d.版 == ""
    assert (d.名前, d.種類, d.責任者, d.区画, d.更新の期限, d.状態) == ("ルーター2", "ルーター", "t1", "事務", "2027-03-31", "使用")


def test_register_device_all_items(dunit):
    """20.6: 引数と様式の項目の対応。Device の属性は様式の項目の名前。"""
    slip = dunit.register_device(
        "録画1", "そのほか", "準備", owner="t2", place="二階の倉庫", segment="扉", door_system=True, door="玄関",
        maker="製造者A", model="型A", serial="SN-001", net_id="00:11:22:33:44:55", version="2.0",
        support_until=date(2028, 3, 31), update_mode="人", secret_ref="kagi-12", log_to="記録のサーバー",
        holds="録画、設定", **ADM)
    assert (slip["名前"], slip["種類"], slip["状態"], slip["責任者"], slip["置き場"], slip["区画"], slip["扉"]) == \
        ("録画1", "そのほか", "準備", "t2", "二階の倉庫", "扉", "玄関")
    assert (slip["製造者"], slip["型"], slip["製造番号"], slip["網の番号"], slip["版"], slip["更新の期限"]) == \
        ("製造者A", "型A", "SN-001", "00:11:22:33:44:55", "2.0", "2028-03-31")
    assert (slip["更新の当て方"], slip["秘密の在庫の番号"], slip["記録の送り先"], slip["中に残る物"], slip["役"]) == \
        ("人", "kagi-12", "記録のサーバー", "録画、設定", "管理者")
    d = dunit.device("録画1")
    assert d == kanri.Device("録画1", "そのほか", "t2", "二階の倉庫", "扉", True, "玄関", "製造者A", "型A", "SN-001",
                             "00:11:22:33:44:55", "2.0", "2028-03-31", "人", "kagi-12", "記録のサーバー", "録画、設定",
                             "準備")


def test_register_device_date_repair(dunit):
    """20.6: support_until は 年-月-日 の文字列か date で、様式の修復(和暦、全角)を通す。"""
    dunit.register_device("PC1", "PC", "使用", support_until="令和9年3月31日", **ADM)
    dunit.register_device("PC2", "PC", "使用", support_until="２０２７－０３－３１", **ADM)
    assert dunit.device("PC1").更新の期限 == "2027-03-31"
    assert dunit.device("PC2").更新の期限 == "2027-03-31"


def test_register_device_state_has_no_default():
    """20.6: state は既定を持たない(登録の時に必ず渡す)。"""
    p = inspect.signature(kanri.Unit.register_device).parameters["state"]
    assert p.default is inspect.Parameter.empty


@pytest.mark.parametrize("state", ["紛失", "廃棄", "撤去"])
def test_register_device_state_choices(dunit, refused, state):
    """20.6: 登録の state は 準備、使用、休止 だけ。ほか(紛失、廃棄を含む)は FORM。"""
    refused(dunit, reasons.FORM, dunit.register_device, "PC1", "PC", state, **ADM)


@pytest.mark.parametrize("state", ["準備", "使用", "休止"])
def test_register_device_states_ok(dunit, state):
    """20.6: 登録の state は 準備、使用、休止。"""
    dunit.register_device("PC1", "PC", state, **ADM)
    assert dunit.device("PC1").状態 == state


def test_register_device_refusals(dunit, refused):
    """20.6 の登録の確かめ: NAME_HAS_COMMA、DUPLICATE_DEVICE、SUBJECT_UNKNOWN、SUBJECT_INACTIVE、UNKNOWN_SEGMENT(詳細に区画)、
    UNKNOWN_DOOR(詳細に扉)。長すぎる名前は FORM。"""
    reg = dunit.register_device
    refused(dunit, reasons.NAME_HAS_COMMA, reg, "PC,1", "PC", "使用", **ADM)
    refused(dunit, RB.DUPLICATE_DEVICE, reg, "ルーター1", "ルーター", "使用", **ADM, detail="")
    refused(dunit, reasons.SUBJECT_UNKNOWN, reg, "PC1", "PC", "使用", owner="x9", **ADM)
    dunit.change_subject("t2", reason="退職", state="退出", by="adm", role="名簿の係")
    refused(dunit, reasons.SUBJECT_INACTIVE, reg, "PC1", "PC", "使用", owner="t2", **ADM)
    refused(dunit, RB.UNKNOWN_SEGMENT, reg, "PC1", "PC", "使用", segment="設備", **ADM, detail="設備")
    refused(dunit, reasons.UNKNOWN_DOOR, reg, "PC1", "PC", "使用", door="倉庫", **ADM, detail="倉庫")
    refused(dunit, reasons.FORM, reg, "あ" * 41, "PC", "使用", **ADM)
    refused(dunit, reasons.FORM, reg, "PC1", "冷蔵庫", "使用", **ADM)


def test_register_device_refusal_order(dunit, refused):
    """20.6 の登録の確かめの順: 名前の「,」→ 同じ名前 → 責任者 → 区画 → 扉。"""
    reg = dunit.register_device
    refused(dunit, reasons.NAME_HAS_COMMA, reg, "ルー,ター1", "ルーター", "使用", owner="x9", segment="設備", door="倉庫",
            **ADM)
    refused(dunit, RB.DUPLICATE_DEVICE, reg, "ルーター1", "ルーター", "使用", owner="x9", segment="設備", door="倉庫", **ADM)
    refused(dunit, reasons.SUBJECT_UNKNOWN, reg, "PC1", "PC", "使用", owner="x9", segment="設備", door="倉庫", **ADM)
    refused(dunit, RB.UNKNOWN_SEGMENT, reg, "PC1", "PC", "使用", owner="t1", segment="設備", door="倉庫", **ADM,
            detail="設備")


def test_register_device_retired_segment_and_door(dunit, refused):
    """R-NW-02 20.6: 廃止の区画は UNKNOWN_SEGMENT、撤去の扉は UNKNOWN_DOOR(どちらも 使用 でない)。"""
    dunit.register_segment("来客", state="廃止", **ADM)
    refused(dunit, RB.UNKNOWN_SEGMENT, dunit.register_device, "PC1", "PC", "使用", segment="来客", **ADM, detail="来客")
    dunit.register_door("裏口", [GATE], state="撤去", **ADM)
    refused(dunit, reasons.UNKNOWN_DOOR, dunit.register_device, "裏口の錠", "錠", "使用", door="裏口", **ADM,
            detail="裏口")


def test_register_device_no_combination_checks(dunit):
    """20.6: 種類と扉の系統、種類と扉の組み合わせは確かめない(台帳は事実を写す)。"""
    dunit.register_device("PC1", "PC", "使用", door_system=True, door="玄関", **ADM)
    d = dunit.device("PC1")
    assert (d.扉の系統, d.扉) == (True, "玄関")


def test_duplicate_includes_retired_device(dunit, refused):
    """20.6、20.14: 廃棄した機器の名前も使い直さない(DUPLICATE_DEVICE)。"""
    dunit.change_device("複合機1", reason="入れ替え", state="廃棄", **ADM)
    refused(dunit, RB.DUPLICATE_DEVICE, dunit.register_device, "複合機1", "プリンター", "準備", **ADM)


def test_device_unknown(dunit, refused):
    """20.6、20.11: device(name) は無ければ UNKNOWN_DEVICE(詳細に機器の名)。"""
    refused(dunit, RB.UNKNOWN_DEVICE, dunit.device, "ルーター9", detail="ルーター9")


def test_devices_order_includes_retired(dunit):
    """20.6: devices() は機器_登録を台帳に書いた順。廃棄も入る。"""
    dunit.change_device("ルーター1", reason="入れ替え", state="廃棄", **ADM)
    dunit.register_device("ルーター2", "ルーター", "準備", **ADM)
    assert [(d.名前, d.状態) for d in dunit.devices()] == \
        [("ルーター1", "廃棄"), ("玄関の錠", "使用"), ("複合機1", "使用"), ("ルーター2", "準備")]


# ---- 20.6 変更 ----

def test_change_device_only_given_items(dunit):
    """20.6、20.4: 渡した項目だけが変わる。今の値は登録の値に、変更の空でない値を書いた順に重ねた物。"""
    dunit.change_device("ルーター1", reason="置き直し", place="二階", segment="事務", **ADM)
    dunit.change_device("ルーター1", reason="責任者を決めた", owner="t1", **ADM)
    dunit.change_device("ルーター1", reason="置き直し", place="三階", **ADM)
    d = dunit.device("ルーター1")
    assert (d.置き場, d.区画, d.責任者, d.種類, d.状態) == ("三階", "事務", "t1", "ルーター", "使用")


def test_change_device_slip(dunit):
    """20.6、20.3: 機器_変更を一枚書く(発行者 by、宛先 空、根拠なし)。紛失と廃棄のほかでは、DeviceChange の列は空。"""
    got = dunit.change_device("ルーター1", reason="責任者を決めた", owner="t1", **ADM)
    s = got.伝票
    assert s.form == "機器_変更" and s.issuer == "adm" and s.to == "" and s.basis == []
    assert (s["名前"], s["責任者"], s["理由"], s["役"], s["置き場"], s["状態"]) == \
        ("ルーター1", "t1", "責任者を決めた", "管理者", "", "")
    assert (got.権限_失効, got.実体_失効) == ([], [])


def test_change_device_empty_string_is_none(dunit, refused):
    """20.6、20.14: 空の文字列は None と同じ(前のまま)。空の文字列だけなら NOTHING_TO_CHANGE。"""
    dunit.change_device("ルーター1", reason="置いた", place="二階", **ADM)
    dunit.change_device("ルーター1", reason="直し", place="", owner="t1", **ADM)
    assert dunit.device("ルーター1").置き場 == "二階"
    refused(dunit, reasons.NOTHING_TO_CHANGE, dunit.change_device, "ルーター1", reason="直し", place="", **ADM)


def test_change_device_door_system(dunit):
    """20.6: door_system は None なら前のまま、bool なら はい か いいえ を書く。False も変える項目。"""
    dunit.change_device("ルーター1", reason="扉の系統に入れた", door_system=True, **ADM)
    assert dunit.device("ルーター1").扉の系統 is True
    dunit.change_device("ルーター1", reason="置いた", place="二階", **ADM)
    assert dunit.device("ルーター1").扉の系統 is True
    dunit.change_device("ルーター1", reason="外した", door_system=False, **ADM)
    assert dunit.device("ルーター1").扉の系統 is False


def test_change_device_nothing_to_change(dunit, refused):
    """20.6 の変更の 3: reason と at のほかに変える項目が無ければ NOTHING_TO_CHANGE。"""
    refused(dunit, reasons.NOTHING_TO_CHANGE, dunit.change_device, "ルーター1", reason="直し", **ADM)
    refused(dunit, reasons.NOTHING_TO_CHANGE, dunit.change_device, "ルーター1", reason="直し", at=dt(REV), **ADM)


def test_change_device_refusals(dunit, refused):
    """20.6 の変更の確かめ: UNKNOWN_DEVICE(詳細に機器の名)、DEVICE_RETIRED、責任者、区画、扉。state の外れは FORM。"""
    ch = dunit.change_device
    refused(dunit, RB.UNKNOWN_DEVICE, ch, "ルーター9", reason="直し", place="二階", **ADM, detail="ルーター9")
    refused(dunit, reasons.SUBJECT_UNKNOWN, ch, "ルーター1", reason="直し", owner="x9", **ADM)
    dunit.change_subject("t2", reason="退職", state="退出", by="adm", role="名簿の係")
    refused(dunit, reasons.SUBJECT_INACTIVE, ch, "ルーター1", reason="直し", owner="t2", **ADM)
    refused(dunit, RB.UNKNOWN_SEGMENT, ch, "ルーター1", reason="直し", segment="設備", **ADM, detail="設備")
    refused(dunit, reasons.UNKNOWN_DOOR, ch, "ルーター1", reason="直し", door="倉庫", **ADM, detail="倉庫")
    refused(dunit, reasons.FORM, ch, "ルーター1", reason="直し", state="撤去", **ADM)
    refused(dunit, reasons.FORM, ch, "ルーター1", reason="", place="二階", **ADM)
    dunit.change_device("ルーター1", reason="入れ替え", state="廃棄", **ADM)
    refused(dunit, RB.DEVICE_RETIRED, ch, "ルーター1", reason="直し", place="二階", **ADM, detail="")
    refused(dunit, RB.DEVICE_RETIRED, ch, "ルーター1", reason="戻す", state="使用", **ADM)


def test_change_device_refusal_order(dunit, refused):
    """20.6 の変更の確かめの順: 機器 → 廃棄 → 変える項目 → 責任者 → 区画 → 扉。"""
    ch = dunit.change_device
    refused(dunit, RB.UNKNOWN_DEVICE, ch, "ルーター9", reason="直し", owner="x9", segment="設備", **ADM)
    refused(dunit, reasons.SUBJECT_UNKNOWN, ch, "ルーター1", reason="直し", owner="x9", segment="設備", door="倉庫",
            **ADM)
    refused(dunit, RB.UNKNOWN_SEGMENT, ch, "ルーター1", reason="直し", owner="t1", segment="設備", door="倉庫", **ADM)
    dunit.change_device("複合機1", reason="入れ替え", state="廃棄", **ADM)
    refused(dunit, RB.DEVICE_RETIRED, ch, "複合機1", reason="直し", **ADM)    # 変える項目が無くても先に DEVICE_RETIRED


def test_change_device_to_retired_segment(dunit, refused):
    """R-NW-02 20.6: 廃止の区画への change_device は UNKNOWN_SEGMENT。"""
    dunit.register_segment("来客", state="廃止", **ADM)
    refused(dunit, RB.UNKNOWN_SEGMENT, dunit.change_device, "複合機1", reason="移す", segment="来客", **ADM,
            detail="来客")


def test_lost_device_may_return_to_use(dunit):
    """20.6: 紛失から 使用 などへ戻せる。"""
    dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    dunit.change_device("ルーター1", reason="見つかった", state="使用", at=dt(REV), **ADM)
    assert dunit.device("ルーター1").状態 == "使用"


# ---- 20.6 紛失と廃棄 ----

def test_lost_example(dunit, access, request_, forms):
    """R-AC-15 20.6 の例: t1 の発行済みの機器の権限 P1(管理者の口 E1)と v1 の申請中の P2 がある時、紛失にすると
    機器_変更 1 枚、権限_失効 2 枚(範囲 機器の停止)、実体_失効 1 枚(E1、人の作業が要る)。"""
    p1, e1 = access(dunit, "t1")
    p2 = request_(dunit, "v1", "機器", "ルーター1")
    before = forms(dunit)
    got = dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    assert forms(dunit) - before == {"機器_変更": 1, "権限_失効": 2, "実体_失効": 1}
    assert got.伝票.form == "機器_変更" and got.伝票["状態"] == "紛失"
    # 権限_失効: 権限_申請を台帳に書いた順。発行者 by、宛先 主体、根拠 [申請]、範囲 機器の停止、理由 reason、役 role
    assert [s["権限"] for s in got.権限_失効] == [p1, p2.id]
    for s, who, pid in zip(got.権限_失効, ("t1", "v1"), (p1, p2.id)):
        assert s.form == "権限_失効" and s.issuer == "adm" and s.to == who and s.basis == [pid]
        assert (s["主体"], s["範囲"], s["理由"], s["役"]) == (who, "機器の停止", "なくした", "管理者")
    # 実体_失効: 発行者 発行者、宛先 主体、根拠 [実体_発行, この呼び出しで書いた権限_失効]
    x, = got.実体_失効
    assert x.form == "実体_失効" and x.issuer == "発行者" and x.to == "t1"
    assert x.basis == [e1.番号, got.権限_失効[0].id]
    assert (x["実体"], x["主体"], x["経路"], x["結果"], x["効くまで"], x["作業"], x["理由"], x["操作者"], x["役"]) == \
        (e1.番号, "t1", ACCESS, "人の作業が要る", "", ACCESS_WORK.format("ルーター1", "admin"), "なくした", "adm",
         "管理者")
    assert dunit.permission(p1, at=dt(REV)).状態 == "失効"
    assert dunit.permission(p2.id, at=dt(REV)).状態 == "失効"


def test_lost_does_not_touch_other_devices_or_doors(dunit, access, grant):
    """20.6: その機器を対象にした権限だけを止める。ほかの機器の権限と、同じ人の扉の権限は止めない。"""
    p_router, _ = access(dunit, "t1", "ルーター1")
    p_printer, _ = access(dunit, "t1", "複合機1")
    p_door, _, _ = grant(dunit, "t1")
    dunit.issue_entity(p_door, GATE, **ADM, at=AT)
    got = dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    assert [s["権限"] for s in got.権限_失効] == [p_router]
    assert dunit.permission(p_printer, at=dt(REV)).状態 == "発行済み"
    assert dunit.permission(p_door, at=dt(REV)).状態 == "発行済み"
    assert dunit.may_open("t1", "玄関", at=dt(REV)).ok is True


def test_lost_skips_closed_permissions(dunit, access, request_, grant):
    """20.6 の 1: 状態(at で読む)が 申請中、承認、発行済み の物だけ。却下と期限切れには書かない。
    期限切れの権限の実体は終了を過ぎているので、実体_失効も書かない(19.10 の 2 の条件)。"""
    rej = request_(dunit, "t2", "機器", "ルーター1")
    dunit.decide(rej.id, "却下", by="apr", role="承認する人")
    p_old, e_old = access(dunit, "t1", end="2026-12-05T00:00")
    got = dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    assert (got.権限_失効, got.実体_失効) == ([], [])
    assert dunit.permission(p_old, at=dt(REV)).状態 == "期限切れ"


def test_lost_then_retired_writes_only_change(dunit, access, forms):
    """20.6、20.14: 紛失から廃棄へ変える時も同じ事をするが、二度目は伝票を増やさない(機器_変更の一枚だけ)。"""
    access(dunit, "t1")
    dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    before = forms(dunit)
    got = dunit.change_device("ルーター1", reason="見つからない", state="廃棄", at=dt(REV), **ADM)
    assert forms(dunit) - before == {"機器_変更": 1}
    assert (got.権限_失効, got.実体_失効) == ([], [])


def test_lost_skips_removed_access(dunit, access):
    """20.6 の 2、20.2: 削除の伝票がある管理者の口には、実体_失効を書かない(回収の伝票が無い物だけ)。"""
    p1, e1 = access(dunit, "t1")
    dunit.remove_access(e1.番号, "消した", **ADM)
    got = dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    assert [s["権限"] for s in got.権限_失効] == [p1]
    assert got.実体_失効 == []


def test_retired_example_states_and_revokes(dunit, access):
    """20.6: 状態を廃棄にした時も、開いた権限に権限_失効、実体に実体_失効を書く。廃棄からは変えられない。"""
    p1, e1 = access(dunit, "t1", "複合機1")
    got = dunit.change_device("複合機1", reason="入れ替え", state="廃棄", at=dt(REV), **ADM)
    assert [s["権限"] for s in got.権限_失効] == [p1]
    assert [s["実体"] for s in got.実体_失効] == [e1.番号]
    assert dunit.device("複合機1").状態 == "廃棄"


def test_lost_does_not_stop_people(dunit, access):
    """20.6: 機器を紛失にしても、責任者や権限の主体は止めない(名簿の状態は変わらない)。"""
    dunit.change_device("ルーター1", reason="責任者", owner="t2", **ADM)
    access(dunit, "t1")
    dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    assert dunit.subject("t1").状態 == "有効" and dunit.subject("t2").状態 == "有効"


def test_settings_1b_items(dunit, refused):
    """20.2: 設定の項目に 点検の間の日数、更新の期限の知らせの日数 を足す。値は 1 以上の整数か空で、外れは BAD_SETTING。
    書いていなければ空。"""
    for item in ("点検の間の日数", "更新の期限の知らせの日数"):
        assert dunit.setting(item) == ""
        slip = dunit.set_setting(item, "30", reason="管理者が決めた", **ADM)
        assert (slip["項目"], slip["値"]) == (item, "30")
        assert dunit.setting(item) == "30"
        dunit.set_setting(item, "", reason="知らせを止める", **ADM)
        assert dunit.setting(item) == ""
        for v in ("0", "-1", "三十", "1.5"):
            refused(dunit, reasons.BAD_SETTING, dunit.set_setting, item, v, reason="試し", **ADM)
