"""設計書 19.6: 名簿、役、扉と群、設定、trust_entry。"""
from __future__ import annotations

import re

from digitalkey.kanri import reasons
from kanri_common import ALL3, CREDENTIAL, GATE, PHYSICAL, T0, T1


# ---- 名簿 ----

def test_register_subject(unit):
    """19.6: register_subject は 主体_登録 を書き、宛先は識別。仮名は 16 桁の 16 進で、置き場の中で重ならない。"""
    slip = unit.register_subject("v1", "保守会社A", "業者", True, by="adm", role="名簿の係")
    assert slip.form == "主体_登録" and slip.issuer == "adm" and slip.to == "v1" and slip.basis == []
    assert slip["識別"] == "v1" and slip["呼び名"] == "保守会社A" and slip["種別"] == "業者"
    assert slip["役"] == "名簿の係"
    v = unit.subject("v1")
    assert (v.識別, v.呼び名, v.種別, v.業者, v.状態, v.持つ役) == ("v1", "保守会社A", "業者", True, "有効", {"本人"})
    assert re.fullmatch(r"[0-9a-f]{16}", v.仮名)
    assert slip["仮名"] == v.仮名
    names = [s.仮名 for s in unit.subjects()]
    assert len(set(names)) == len(names)


def test_subjects_in_registration_order(unit):
    """19.6: subjects() は登録の順。"""
    unit.register_subject("c1", "清掃A", "清掃", False, by="adm", role="名簿の係")
    assert [s.識別 for s in unit.subjects()] == ["adm", "apr", "s1", "c1"]


def test_register_subject_refusals(unit, refused):
    """19.6: 識別に「,」は NAME_HAS_COMMA、同じ識別は DUPLICATE_SUBJECT。"""
    refused(unit, reasons.NAME_HAS_COMMA, unit.register_subject, "a,b", "架空", "社員", False,
            by="adm", role="名簿の係")
    refused(unit, reasons.DUPLICATE_SUBJECT, unit.register_subject, "s1", "社員B", "社員", False,
            by="adm", role="名簿の係")


def test_subject_unknown(unit, refused):
    """19.6: subject(ident) は無ければ SUBJECT_UNKNOWN。"""
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.subject, "nobody")


def test_change_subject_items(unit):
    """19.6: change_subject は 主体_変更 を書き、項目ごとに空でない最後の値が今の値。"""
    slip = unit.change_subject("s1", reason="改姓", name="社員B", by="adm", role="名簿の係")
    assert slip.form == "主体_変更" and slip.to == "s1" and slip["理由"] == "改姓"
    unit.change_subject("s1", reason="契約の変更", kind="派遣", vendor=True, by="adm", role="名簿の係")
    unit.change_subject("s1", reason="休職", state="停止", by="adm", role="名簿の係")
    s = unit.subject("s1")
    assert (s.呼び名, s.種別, s.業者, s.状態) == ("社員B", "派遣", True, "停止")
    unit.change_subject("s1", reason="復職", state="有効", vendor=False, by="adm", role="名簿の係")
    s = unit.subject("s1")
    assert (s.呼び名, s.種別, s.業者, s.状態) == ("社員B", "派遣", False, "有効")


def test_change_subject_keeps_pseudonym(unit):
    """19.6: 主体_変更は仮名を変えない(様式に仮名の項目が無い)。"""
    before = unit.subject("s1").仮名
    unit.change_subject("s1", reason="改姓", name="社員B", by="adm", role="名簿の係")
    assert unit.subject("s1").仮名 == before


def test_change_subject_refusals(unit, refused):
    """19.6: 主体が無ければ SUBJECT_UNKNOWN、変える項目が全部 None なら NOTHING_TO_CHANGE、状態の値が様式に無ければ FORM。"""
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.change_subject, "nobody", reason="直し", name="架空",
            by="adm", role="名簿の係")
    refused(unit, reasons.NOTHING_TO_CHANGE, unit.change_subject, "s1", reason="直し", by="adm", role="名簿の係")
    refused(unit, reasons.FORM, unit.change_subject, "s1", reason="直し", state="休み", by="adm", role="名簿の係")


# ---- 役 ----

def test_assign_role(unit):
    """19.6: assign_role は 役_割当 を書き、宛先は主体。付与で持つ役に入る。"""
    slip = unit.assign_role("s1", "監査", reason="監査の当番", by="adm", role="管理者")
    assert slip.form == "役_割当" and slip.to == "s1" and slip.issuer == "adm"
    assert (slip["主体"], slip["割り当てる役"], slip["判断"], slip["理由"], slip["役"]) == \
        ("s1", "監査", "付与", "監査の当番", "管理者")
    assert unit.subject("s1").持つ役 == {"監査", "本人"}


def test_assign_role_remove_writes_kaijo(unit):
    """19.6: grant=False は判断 解除 の 役_割当。"""
    slip = unit.assign_role("s1", "運用", False, reason="当番の終わり", by="adm", role="管理者")
    assert slip["判断"] == "解除"
    assert "運用" not in unit.subject("s1").持つ役


def test_assign_role_last_decision_wins(unit):
    """19.3: 役_割当を台帳に書いた順に読み、主体と役の組ごとに最後の判断が 付与 の物を持つ。"""
    unit.assign_role("s1", "運用", reason="当番", by="adm", role="管理者")
    unit.assign_role("s1", "監査", reason="監査の当番", by="adm", role="管理者")
    unit.assign_role("s1", "運用", False, reason="当番の終わり", by="adm", role="管理者")
    assert unit.subject("s1").持つ役 == {"監査", "本人"}
    unit.assign_role("s1", "運用", reason="また当番", by="adm", role="管理者")
    assert unit.subject("s1").持つ役 == {"運用", "監査", "本人"}
    # 作った時の管理者も外せる
    unit.assign_role("adm", "管理者", False, reason="引き継ぎ", by="adm", role="管理者")
    assert unit.subject("adm").持つ役 == {"名簿の係", "本人"}


def test_removed_role_is_no_role(unit, refused):
    """19.3 の 4: 解除した役では NO_ROLE。"""
    unit.assign_role("apr", "承認する人", False, reason="異動", by="adm", role="管理者")
    r = unit.request("s1", "扉", "玄関", T0, T1, "業務", by="adm", role="名簿の係")
    refused(unit, reasons.NO_ROLE, unit.decide, r.id, "承認", by="apr", role="承認する人")


def test_assign_role_refusals(unit, refused):
    """19.6: 主体が無ければ SUBJECT_UNKNOWN、選べる値に無い役(発行者、本人、初期化)は FORM。"""
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.assign_role, "nobody", "運用", reason="当番", by="adm", role="管理者")
    for name in ("発行者", "本人", "初期化", "社長"):
        refused(unit, reasons.FORM, unit.assign_role, "s1", name, reason="当番", by="adm", role="管理者")


# ---- 扉と群 ----

def test_register_door_example(make_unit):
    """R-AC-09 19.6 の例: 扉 玄関 の伝票の項目 経路 は「, 」でつなぐ。宛先は空。"""
    u = make_unit()
    slip = u.register_door("玄関", ["署名つき資格情報", "門番を通す錠", "物理キー"], by="adm", role="管理者")
    assert slip.form == "扉" and slip.to == "" and slip.basis == []
    assert slip["経路"] == "署名つき資格情報, 門番を通す錠, 物理キー"
    assert slip["状態"] == "使用" and slip["役"] == "管理者"
    d, = u.doors()
    assert (d.名前, d.経路, d.場所, d.記録の公開鍵, d.状態) == ("玄関", list(ALL3), "", "", "使用")


def test_register_door_place_and_audit_key(unit):
    """19.6: place と audit_key は 場所 と 記録の公開鍵 に入る。"""
    key = "ab" * 32
    unit.register_door("倉庫", [PHYSICAL], place="一階の奥", audit_key=key, by="adm", role="管理者")
    d = [x for x in unit.doors() if x.名前 == "倉庫"][0]
    assert (d.経路, d.場所, d.記録の公開鍵) == ([PHYSICAL], "一階の奥", key)


def test_register_door_refusals(unit, refused):
    """19.6: 名前に「,」は NAME_HAS_COMMA、登録簿に無い経路は UNKNOWN_ROUTE(詳細に経路の名)、経路が空は FORM。"""
    refused(unit, reasons.NAME_HAS_COMMA, unit.register_door, "裏,口", [GATE], by="adm", role="管理者")
    refused(unit, reasons.UNKNOWN_ROUTE, unit.register_door, "裏口", [GATE, "暗証番号"], by="adm", role="管理者",
            detail="暗証番号")
    refused(unit, reasons.FORM, unit.register_door, "裏口", [], by="adm", role="管理者")


def test_door_second_slip_replaces_and_retired_doors_stay_listed(unit):
    """19.6: 同じ名前の二枚目は今の値を置き換える。doors() は撤去した扉も入れ、最初に登録した順。"""
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    unit.register_door("玄関", [GATE], state="撤去", by="adm", role="管理者")
    ds = unit.doors()
    assert [d.名前 for d in ds] == ["玄関", "倉庫"]
    assert (ds[0].経路, ds[0].状態) == ([GATE], "撤去")


def test_register_group(unit):
    """19.6: register_group は 群 を書く。groups() は今の値で最初に登録した順。"""
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    slip = unit.register_group("事務所", ["玄関", "倉庫"], by="adm", role="管理者")
    assert slip.form == "群" and slip.to == "" and slip["扉"] == "玄関, 倉庫" and slip["状態"] == "使用"
    unit.register_group("夜間", ["玄関"], by="adm", role="管理者")
    unit.register_group("事務所", ["倉庫"], state="廃止", by="adm", role="管理者")
    gs = unit.groups()
    assert [(g.名前, g.扉, g.状態) for g in gs] == [("事務所", ["倉庫"], "廃止"), ("夜間", ["玄関"], "使用")]


def test_register_group_example(make_unit, refused):
    """R-AC-09 19.6 の例: 倉庫が無ければ Refused(理由="この発行者の扉ではありません", 詳細="倉庫")。"""
    u = make_unit()
    u.register_door("玄関", list(ALL3), by="adm", role="管理者")
    refused(u, "この発行者の扉ではありません", u.register_group, "事務所", ["玄関", "倉庫"],
            by="adm", role="管理者", detail="倉庫")


def test_register_group_refusals(unit, refused):
    """19.6: 撤去した扉は UNKNOWN_DOOR、名前に「,」は NAME_HAS_COMMA、扉が空は FORM。"""
    unit.register_door("倉庫", [PHYSICAL], state="撤去", by="adm", role="管理者")
    refused(unit, reasons.UNKNOWN_DOOR, unit.register_group, "事務所", ["玄関", "倉庫"], by="adm", role="管理者",
            detail="倉庫")
    refused(unit, reasons.NAME_HAS_COMMA, unit.register_group, "事,務所", ["玄関"], by="adm", role="管理者")
    refused(unit, reasons.FORM, unit.register_group, "事務所", [], by="adm", role="管理者")


# ---- 設定 ----

def test_settings(unit):
    """19.6: set_setting は 設定 を書く。setting は今の値で、書いていなければ空(自己承認だけは 許さない。19.6 の setting)。"""
    assert unit.setting("業者の最長の日数") == ""
    assert unit.setting("自己承認") == "許さない"
    slip = unit.set_setting("業者の最長の日数", "30", reason="契約の上限", by="adm", role="管理者")
    assert slip.form == "設定" and slip.issuer == "adm" and slip.to == ""
    assert (slip["項目"], slip["値"], slip["理由"], slip["役"]) == ("業者の最長の日数", "30", "契約の上限", "管理者")
    assert unit.setting("業者の最長の日数") == "30"
    unit.set_setting("業者の最長の日数", "", reason="上限をなくす", by="adm", role="管理者")
    assert unit.setting("業者の最長の日数") == ""
    unit.set_setting("自己承認", "許す", reason="一人で回す", by="adm", role="管理者")
    assert unit.setting("自己承認") == "許す"
    unit.set_setting("自己承認", "許さない", reason="二人に戻った", by="adm", role="管理者")
    assert unit.setting("自己承認") == "許さない"


def test_setting_refusals(unit, refused):
    """19.6: 項目が選べる値に無ければ FORM、値が読めなければ BAD_SETTING、理由が空なら FORM。"""
    refused(unit, reasons.FORM, unit.set_setting, "扉の最長の時間", "10", reason="試し", by="adm", role="管理者")
    for v in ("abc", "0", "-1", "1.5"):
        refused(unit, reasons.BAD_SETTING, unit.set_setting, "業者の最長の日数", v, reason="試し",
                by="adm", role="管理者")
    for v in ("はい", "いいえ", ""):
        refused(unit, reasons.BAD_SETTING, unit.set_setting, "自己承認", v, reason="試し", by="adm", role="管理者")
    refused(unit, reasons.FORM, unit.set_setting, "自己承認", "許す", reason="", by="adm", role="管理者")


# ---- trust_entry ----

def test_trust_entry(unit):
    """R-AC-09 19.6: trust_entry は {発行者: {public_key, doors: 使用の扉、登録の順}}。"""
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    unit.register_door("裏口", [GATE], by="adm", role="管理者")
    unit.register_door("倉庫", [PHYSICAL], state="撤去", by="adm", role="管理者")
    assert unit.trust_entry() == {unit.issuer: {"public_key": unit.public_key.hex(), "doors": ["玄関", "裏口"]}}


def test_unit_reads_take_no_role(unit, grant):
    """19.3: 読むメソッドは役を取らない(by も role も渡さずに呼べる)。"""
    pid, _, _ = grant(unit)
    unit.subject("s1"), unit.subjects(), unit.doors(), unit.groups(), unit.trust_entry()
    unit.permission(pid, at=None), unit.permissions(), unit.entities(), unit.progress("s1", at=None)
    assert unit.may_open("s1", "玄関", at=None).ok is False


def test_dates_in_request_are_form_strings(unit):
    """19.5: 開始と終了は 年-月-日T時:分 で、和暦、全角、空白を直す。datetime は秒を落とす。"""
    from datetime import datetime
    a = unit.request("s1", "扉", "玄関", "令和8年12月1日 9:00", "２０２７－０１－０１Ｔ００：００", "業務",
                     by="adm", role="名簿の係")
    assert (a["開始"], a["終了"]) == (T0, T1)
    b = unit.request("s1", "扉", "玄関", datetime(2026, 12, 1, 9, 0, 30), datetime(2027, 1, 1).astimezone(), "業務",
                     by="adm", role="名簿の係")
    assert (b["開始"], b["終了"]) == (T0, T1)
    p = unit.permission(b.id, at=datetime(2026, 11, 20))
    assert (p.開始, p.終了) == (T0, T1)


def test_dates_with_offset_are_form_errors(unit, refused):
    """19.5: 時差つきの文字列は読めず FORM。"""
    refused(unit, reasons.FORM, unit.request, "s1", "扉", "玄関", "2026-12-01T09:00+09:00", T1, "業務",
            by="adm", role="名簿の係")


def test_credential_route_constants_used(unit):
    """19.6: 扉の経路は Door.経路 に list で入る。"""
    assert unit.doors()[0].経路 == [CREDENTIAL, GATE, PHYSICAL]
