"""設計書 19.7: 権限の状態の導き方、申請、承認、発行、外の一覧の取り込み。19.4 の伝票の組。"""
from __future__ import annotations

from datetime import datetime

import pytest

from digitalkey.kanri import reasons
from kanri_common import ALL3, GATE, PHYSICAL, T0, T1, dt, plus

AT = dt("2026-11-20T00:00")   # 開始より前の固定の時刻


def _direct_approval(u, request_id, by, mark="いいえ"):
    """19.13 の (a)(c): テストが Ledger.append で直に書く承認(decide を通らない)。"""
    subject = u.ledger.get(request_id)["主体"]
    u.ledger.roles = {by: {"承認する人"}}
    return u.ledger.append("権限_承認", {"申請": request_id, "判断": "承認", "理由": "", "自己承認": mark,
                                        "役": "承認する人"}, issuer=by, to=subject, basis=[request_id])


# ---- 状態の導き方 ----

def test_state_table(unit, request_, approve):
    """19.7 の状態の表: 申請中、承認、却下、発行済み(開始の前も含む)、期限切れ、失効。"""
    r = request_(unit)
    assert unit.permission(r.id, at=AT).状態 == "申請中"
    a = approve(unit, r.id)
    assert unit.permission(r.id, at=AT).状態 == "承認"
    unit.issue(a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)
    assert unit.permission(r.id, at=AT).状態 == "発行済み"
    assert unit.permission(r.id, at=dt(plus(T1, minutes=-1))).状態 == "発行済み"
    assert unit.permission(r.id, at=dt(T1)).状態 == "期限切れ"
    rej = request_(unit)
    unit.decide(rej.id, "却下", "必要が無い", by="apr", role="承認する人")
    assert unit.permission(rej.id, at=AT).状態 == "却下"
    unit.revoke_permission(r.id, "退職", by="adm", role="管理者", at=AT)
    assert unit.permission(r.id, at=AT).状態 == "失効"


def test_revoked_wins_over_expired_and_any_at(unit, grant):
    """19.7 の表の 1 と 19.5: 失効は書いた時から効き、at に失効より前の時刻を渡しても失効。"""
    pid, _, _ = grant(unit)
    unit.revoke_permission(pid, "退職", by="adm", role="管理者", at=dt("2026-12-10T12:00"))
    assert unit.permission(pid, at=dt(T1)).状態 == "失効"
    assert unit.permission(pid, at=dt("2026-11-01T00:00")).状態 == "失効"


def test_first_decision_in_ledger_order_counts(unit, request_, refused):
    """19.7 の表の 2、5 と発行の 1: 判断は台帳に書いた順で最初の一枚だけが効く。二枚目の承認では発行できない。"""
    r = request_(unit)
    first = _direct_approval(unit, r.id, "apr")
    unit.ledger.roles = {"apr": {"承認する人"}}
    second = unit.ledger.append("権限_承認", {"申請": r.id, "判断": "却下", "理由": "", "自己承認": "いいえ",
                                              "役": "承認する人"}, issuer="apr", to="s1", basis=[r.id])
    p = unit.permission(r.id, at=AT)
    assert (p.状態, p.承認) == ("承認", first.id)
    refused(unit, reasons.ALREADY_DECIDED, unit.decide, r.id, "承認", by="apr", role="承認する人")
    refused(unit, reasons.NO_APPROVAL, unit.issue, second.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者",
            at=AT)
    assert unit.issue(first.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT).form == "権限_発行"


def test_first_decision_reject_wins(unit, request_):
    """19.7 の表の 2: 最初の判断が 却下 なら、後に承認の伝票があっても 却下。"""
    r = request_(unit)
    unit.ledger.roles = {"apr": {"承認する人"}}
    unit.ledger.append("権限_承認", {"申請": r.id, "判断": "却下", "理由": "", "自己承認": "いいえ", "役": "承認する人"},
                       issuer="apr", to="s1", basis=[r.id])
    _direct_approval(unit, r.id, "apr")
    assert unit.permission(r.id, at=AT).状態 == "却下"


def test_no_end_never_expires(unit, request_, approve):
    """19.7 の表の 3: 終了が空なら期限切れにならない。"""
    r = request_(unit, end="")
    a = approve(unit, r.id)
    unit.issue(a.id, "s1", "扉", "玄関", T0, "", by="adm", role="管理者", at=AT)
    assert unit.permission(r.id, at=datetime(2099, 1, 1)).状態 == "発行済み"


def test_permission_attributes(unit, grant):
    """19.2 の Permission: 番号は申請の番号、申請者は申請の発行者、承認と発行は伝票の番号、扉は発行の列。"""
    pid, aid, issued = grant(unit)
    p = unit.permission(pid, at=AT)
    assert (p.番号, p.主体, p.対象の種類, p.対象, p.開始, p.終了) == (pid, "s1", "扉", "玄関", T0, T1)
    assert (p.理由, p.出どころ, p.申請者) == ("業務", "手入力", "adm")
    assert (p.承認, p.発行, p.扉, p.失効) == (aid, issued.id, ["玄関"], "")


def test_permission_before_issue_has_empty_doors(unit, request_):
    """19.2: 発行前の扉は空の列、承認と発行と失効は空。"""
    r = request_(unit)
    p = unit.permission(r.id, at=AT)
    assert (p.承認, p.発行, p.扉, p.失効) == ("", "", [], "")


def test_permission_unknown(unit, refused, grant):
    """19.7: permission は申請の伝票でなければ PERMISSION_UNKNOWN。"""
    _, aid, _ = grant(unit)
    refused(unit, reasons.PERMISSION_UNKNOWN, unit.permission, "x", at=AT)
    refused(unit, reasons.PERMISSION_UNKNOWN, unit.permission, aid, at=AT)


def test_permissions_filter_and_order(unit, request_):
    """19.7: permissions(subject) は申請の順、主体で絞れる。"""
    unit.register_subject("c1", "清掃A", "清掃", False, by="adm", role="名簿の係")
    a = request_(unit, "s1")
    b = request_(unit, "c1")
    c = request_(unit, "s1", start="2026-12-02T09:00")
    assert [p.番号 for p in unit.permissions(at=AT)] == [a.id, b.id, c.id]
    assert [p.番号 for p in unit.permissions("s1", at=AT)] == [a.id, c.id]


# ---- 申請 ----

def test_request_slip(unit, request_):
    """19.7、19.4: 権限_申請、出どころ 手入力、宛先は主体、根拠なし。"""
    r = request_(unit)
    assert r.form == "権限_申請" and r.issuer == "adm" and r.to == "s1" and r.basis == []
    assert (r["主体"], r["対象の種類"], r["対象"], r["開始"], r["終了"], r["理由"], r["出どころ"], r["一覧の名"]) == \
        ("s1", "扉", "玄関", T0, T1, "業務", "手入力", "")


def test_request_refusals(unit, refused):
    """19.7 の申請の確かめ。"""
    req = unit.request
    kw = dict(by="adm", role="名簿の係")
    refused(unit, reasons.SUBJECT_UNKNOWN, req, "nobody", "扉", "玄関", T0, T1, "業務", **kw)
    refused(unit, reasons.UNKNOWN_DOOR, req, "s1", "扉", "倉庫", T0, T1, "業務", **kw)
    refused(unit, reasons.UNKNOWN_GROUP, req, "s1", "群", "事務所", T0, T1, "業務", **kw)
    refused(unit, reasons.BAD_PERIOD, req, "s1", "扉", "玄関", T0, T0, "業務", **kw)
    refused(unit, reasons.BAD_PERIOD, req, "s1", "扉", "玄関", T1, T0, "業務", **kw)
    unit.change_subject("s1", reason="休職", state="停止", **kw)
    refused(unit, reasons.SUBJECT_INACTIVE, req, "s1", "扉", "玄関", T0, T1, "業務", **kw)


def test_request_to_retired_door_or_group(unit, refused):
    """19.7: 撤去した扉は UNKNOWN_DOOR、廃止した群は UNKNOWN_GROUP、群に使用の扉が無ければ UNKNOWN_DOOR。"""
    kw = dict(by="adm", role="名簿の係")
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    unit.register_group("物置", ["倉庫"], by="adm", role="管理者")
    unit.register_group("夜間", ["玄関"], by="adm", role="管理者")
    unit.register_group("夜間", ["玄関"], state="廃止", by="adm", role="管理者")
    unit.register_door("倉庫", [PHYSICAL], state="撤去", by="adm", role="管理者")
    refused(unit, reasons.UNKNOWN_DOOR, unit.request, "s1", "扉", "倉庫", T0, T1, "業務", **kw)
    refused(unit, reasons.UNKNOWN_GROUP, unit.request, "s1", "群", "夜間", T0, T1, "業務", **kw)
    refused(unit, reasons.UNKNOWN_DOOR, unit.request, "s1", "群", "物置", T0, T1, "業務", **kw)


def test_request_vendor(unit, refused):
    """R-AC-10 19.7: 業者は終了が空なら VENDOR_NO_END、上限の日数を超えれば VENDOR_TOO_LONG、ちょうどは通る。"""
    kw = dict(by="adm", role="名簿の係")
    unit.register_subject("v1", "保守会社A", "業者", True, **kw)
    refused(unit, reasons.VENDOR_NO_END, unit.request, "v1", "扉", "玄関", T0, "", "保守", **kw)
    # 上限を書いていなければ、終了が要るだけ
    assert unit.request("v1", "扉", "玄関", T0, plus(T0, days=400), "保守", **kw).form == "権限_申請"
    unit.set_setting("業者の最長の日数", "7", reason="保守の契約", by="adm", role="管理者")
    refused(unit, reasons.VENDOR_TOO_LONG, unit.request, "v1", "扉", "玄関", T0, plus(T0, days=7, minutes=1),
            "保守", **kw)
    assert unit.request("v1", "扉", "玄関", T0, plus(T0, days=7), "保守", **kw).form == "権限_申請"
    # 社員には上限を当てない
    assert unit.request("s1", "扉", "玄関", T0, "", "業務", **kw).form == "権限_申請"


def test_request_checks_order(unit, refused):
    """19.7: 確かめの順(主体 → 有効 → 扉 → 期間 → 業者)。"""
    kw = dict(by="adm", role="名簿の係")
    unit.register_subject("v1", "保守会社A", "業者", True, **kw)
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.request, "nobody", "扉", "倉庫", T1, T0, "業務", **kw)
    refused(unit, reasons.UNKNOWN_DOOR, unit.request, "v1", "扉", "倉庫", T1, T0, "保守", **kw)
    refused(unit, reasons.BAD_PERIOD, unit.request, "v1", "扉", "玄関", T1, T0, "保守", **kw)
    unit.change_subject("v1", reason="契約の終わり", state="退出", **kw)
    refused(unit, reasons.SUBJECT_INACTIVE, unit.request, "v1", "扉", "倉庫", T0, "", "保守", **kw)


# ---- 承認 ----

def test_decide_slip(unit, request_):
    """19.7、19.4: 権限_承認、宛先は主体、根拠は [申請]。申請者でも主体でもなければ 自己承認 いいえ。"""
    r = request_(unit)
    a = unit.decide(r.id, "承認", "確かめた", by="apr", role="承認する人")
    assert a.form == "権限_承認" and a.issuer == "apr" and a.to == "s1" and a.basis == [r.id]
    assert (a["申請"], a["判断"], a["理由"], a["自己承認"], a["役"]) == (r.id, "承認", "確かめた", "いいえ", "承認する人")


def test_decide_reason_may_be_empty(unit, request_):
    """19.7: decide の reason は省ける(様式で 理由 は not null でない)。"""
    r = request_(unit)
    assert unit.decide(r.id, "却下", by="apr", role="承認する人")["理由"] == ""


def test_decide_refusals(unit, refused, request_, approve):
    """19.7 の承認の確かめ: NO_REQUEST、REVOKED、ALREADY_DECIDED、判断の値が様式に無ければ FORM。"""
    kw = dict(by="apr", role="承認する人")
    r = request_(unit)
    refused(unit, reasons.NO_REQUEST, unit.decide, "x", "承認", **kw)
    door_slip, = unit.ledger.select("扉")
    refused(unit, reasons.NO_REQUEST, unit.decide, door_slip.id, "承認", **kw)
    refused(unit, reasons.FORM, unit.decide, r.id, "保留", **kw)
    approve(unit, r.id)
    refused(unit, reasons.ALREADY_DECIDED, unit.decide, r.id, "却下", **kw)
    r2 = request_(unit)
    unit.revoke_permission(r2.id, "取り下げ", by="adm", role="管理者", at=AT)
    refused(unit, reasons.REVOKED, unit.decide, r2.id, "承認", **kw)


def test_decide_self_without_setting(unit, refused):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 3(a): 自己承認 を書いていない置き場で、申請者は SELF_REQUEST、主体は SELF_SUBJECT。"""
    # apr が本人の役で s1 の分を申請し、apr が承認しようとする
    r1 = unit.request("s1", "扉", "玄関", T0, T1, "業務", by="apr", role="本人")
    refused(unit, reasons.SELF_REQUEST, unit.decide, r1.id, "承認", by="apr", role="承認する人")
    # adm が apr の分を申請し、主体の apr が承認しようとする
    r2 = unit.request("apr", "扉", "玄関", T0, T1, "業務", by="adm", role="名簿の係")
    refused(unit, reasons.SELF_SUBJECT, unit.decide, r2.id, "承認", by="apr", role="承認する人")
    # 却下も同じ
    refused(unit, reasons.SELF_SUBJECT, unit.decide, r2.id, "却下", by="apr", role="承認する人")
    # 申請者で主体でもある時は、申請者の確かめが先
    r3 = unit.request("apr", "扉", "玄関", T0, T1, "業務", by="apr", role="本人")
    refused(unit, reasons.SELF_REQUEST, unit.decide, r3.id, "承認", by="apr", role="承認する人")


def test_decide_self_with_setting_example(make_unit, refused):
    """R-EB-37 R-AC-10 19.7 の例: 自己承認 許す の置き場で adm が自分の申請を承認すると、自己承認 はい、役 承認する人。
    設定の無い置き場では SELF_REQUEST。"""
    for name, allow in (("A", True), ("B", False)):
        u = make_unit(name)
        u.assign_role("adm", "名簿の係", reason="一人で回す", by="adm", role="管理者")
        u.assign_role("adm", "承認する人", reason="一人で回す", by="adm", role="管理者")
        u.register_door("玄関", list(ALL3), by="adm", role="管理者")
        if allow:
            u.set_setting("自己承認", "許す", reason="一人で回す", by="adm", role="管理者")
        r = u.request("adm", "扉", "玄関", T0, T1, "業務", by="adm", role="名簿の係")
        if allow:
            a = u.decide(r.id, "承認", by="adm", role="承認する人")
            assert a.form == "権限_承認" and a["自己承認"] == "はい" and a["役"] == "承認する人"
        else:
            refused(u, reasons.SELF_REQUEST, u.decide, r.id, "承認", by="adm", role="承認する人")


def test_decide_self_mark_on_reject(make_unit):
    """19.7: 自己承認 の印は 却下 の時も同じ。"""
    u = make_unit()
    u.assign_role("adm", "承認する人", reason="一人で回す", by="adm", role="管理者")
    u.register_door("玄関", list(ALL3), by="adm", role="管理者")
    u.set_setting("自己承認", "許す", reason="一人で回す", by="adm", role="管理者")
    r = u.request("adm", "扉", "玄関", T0, T1, "業務", by="adm", role="本人")
    assert u.decide(r.id, "却下", by="adm", role="承認する人")["自己承認"] == "はい"


# ---- 発行 ----

def test_issue_slip(unit, request_, approve):
    """19.7、19.4: 権限_発行、発行者 発行者、操作者 by、根拠は [申請, 承認]、扉は対象から作った列。"""
    r = request_(unit)
    a = approve(unit, r.id)
    i = unit.issue(a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)
    assert i.form == "権限_発行" and i.issuer == "発行者" and i.to == "s1" and i.basis == [r.id, a.id]
    assert (i["権限"], i["承認"], i["主体"], i["対象の種類"], i["対象"], i["扉"], i["開始"], i["終了"],
            i["操作者"], i["役"]) == (r.id, a.id, "s1", "扉", "玄関", "玄関", T0, T1, "adm", "管理者")


def test_issue_no_approval_and_rejected(unit, refused, request_, approve):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 1: issue に空、無い番号、申請の番号は NO_APPROVAL、却下の判断の番号は REJECTED。"""
    kw = dict(by="adm", role="管理者", at=AT)
    r = request_(unit)
    approve(unit, r.id)
    for bad in ("", "20261111T101400-0f0f0f", r.id):
        refused(unit, reasons.NO_APPROVAL, unit.issue, bad, "s1", "扉", "玄関", T0, T1, **kw)
    r2 = request_(unit)
    rej = unit.decide(r2.id, "却下", "必要が無い", by="apr", role="承認する人")
    refused(unit, reasons.REJECTED, unit.issue, rej.id, "s1", "扉", "玄関", T0, T1, **kw)
    # 承認でない伝票(扉)の番号も NO_APPROVAL
    door_slip, = unit.ledger.select("扉")
    refused(unit, reasons.NO_APPROVAL, unit.issue, door_slip.id, "s1", "扉", "玄関", T0, T1, **kw)


@pytest.mark.parametrize("item,args", [
    ("主体", ("apr", "扉", "玄関", T0, T1)),
    ("対象の種類", ("s1", "群", "玄関", T0, T1)),
    ("対象", ("s1", "扉", "裏口", T0, T1)),
    ("開始", ("s1", "扉", "玄関", "2026-12-01T09:01", T1)),
    ("終了", ("s1", "扉", "玄関", T0, "2027-01-02T00:00")),
    ("終了", ("s1", "扉", "玄関", T0, "")),
])
def test_issue_mismatch(unit, refused, request_, approve, item, args):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 2: 承認と違う主体、対象、開始、終了で issue すると MISMATCH(詳細に項目の名)。"""
    unit.register_door("裏口", [GATE], by="adm", role="管理者")
    r = request_(unit)
    a = approve(unit, r.id)
    refused(unit, reasons.MISMATCH, unit.issue, a.id, *args, by="adm", role="管理者", at=AT, detail=item)


def test_issue_mismatch_order(unit, refused, request_, approve):
    """19.7 の発行の 4: 主体、対象の種類、対象、開始、終了の順に比べ、最初に違う項目を詳細にする。"""
    r = request_(unit)
    a = approve(unit, r.id)
    refused(unit, reasons.MISMATCH, unit.issue, a.id, "apr", "群", "裏口", "2026-12-02T09:00", "", by="adm",
            role="管理者", at=AT, detail="主体")
    refused(unit, reasons.MISMATCH, unit.issue, a.id, "s1", "扉", "裏口", "2026-12-02T09:00", "", by="adm",
            role="管理者", at=AT, detail="対象")


def test_issue_compares_normalized_dates(unit, request_, approve):
    """19.7 の発行の 4: 日時は 19.5 の形に直してから比べる。"""
    r = request_(unit)
    a = approve(unit, r.id)
    i = unit.issue(a.id, "s1", "扉", "玄関", datetime(2026, 12, 1, 9, 0), "２０２７－０１－０１Ｔ００：００",
                   by="adm", role="管理者", at=AT)
    assert (i["開始"], i["終了"]) == (T0, T1)


def test_issue_revoked_and_already_issued(unit, refused, request_, approve):
    """19.7 の発行の 5: 失効は REVOKED、二度目は ALREADY_ISSUED。"""
    kw = dict(by="adm", role="管理者", at=AT)
    r = request_(unit)
    a = approve(unit, r.id)
    unit.issue(a.id, "s1", "扉", "玄関", T0, T1, **kw)
    refused(unit, reasons.ALREADY_ISSUED, unit.issue, a.id, "s1", "扉", "玄関", T0, T1, **kw)
    r2 = request_(unit)
    a2 = approve(unit, r2.id)
    unit.revoke_permission(r2.id, "取り下げ", by="adm", role="管理者", at=AT)
    refused(unit, reasons.REVOKED, unit.issue, a2.id, "s1", "扉", "玄関", T0, T1, **kw)


def test_issue_subject_inactive(unit, refused, request_, approve):
    """19.7 の発行の 6: 主体が有効でなければ SUBJECT_INACTIVE。"""
    r = request_(unit)
    a = approve(unit, r.id)
    unit.change_subject("s1", reason="休職", state="停止", by="adm", role="名簿の係")
    refused(unit, reasons.SUBJECT_INACTIVE, unit.issue, a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者",
            at=AT)


def test_issue_vendor_rechecked(unit, refused, request_, approve):
    """R-AC-10 19.7 の発行の 7: 業者の確かめは今の名簿と設定で確かめ直す。"""
    kw = dict(by="adm", role="名簿の係")
    unit.register_subject("v1", "保守会社A", "業者", True, **kw)
    long_end = plus(T0, days=60)
    r = request_(unit, "v1", end=long_end)
    a = approve(unit, r.id)
    unit.set_setting("業者の最長の日数", "30", reason="契約の上限", by="adm", role="管理者")
    refused(unit, reasons.VENDOR_TOO_LONG, unit.issue, a.id, "v1", "扉", "玄関", T0, long_end, by="adm",
            role="管理者", at=AT)


def test_issue_doors_from_group_fixed_at_issue(unit, request_, approve):
    """19.7 の発行の 8: 群は今の群の使用の扉。権限の扉の列は発行の時に決まり、後で群を変えても変わらない。"""
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    unit.register_door("裏口", [GATE], by="adm", role="管理者")
    unit.register_group("事務所", ["玄関", "倉庫", "裏口"], by="adm", role="管理者")
    r = request_(unit, kind="群", target="事務所")
    a = approve(unit, r.id)
    unit.register_door("裏口", [GATE], state="撤去", by="adm", role="管理者")
    i = unit.issue(a.id, "s1", "群", "事務所", T0, T1, by="adm", role="管理者", at=AT)
    assert i["扉"] == "玄関, 倉庫"
    unit.register_group("事務所", ["玄関"], by="adm", role="管理者")
    assert unit.permission(r.id, at=AT).扉 == ["玄関", "倉庫"]


def test_issue_unknown_door_or_group(unit, refused, request_, approve):
    """19.7 の発行の 8: 扉が使用でなければ UNKNOWN_DOOR、群が使用でなければ UNKNOWN_GROUP、群に使用の扉が無ければ UNKNOWN_DOOR。"""
    kw = dict(by="adm", role="管理者", at=AT)
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    unit.register_door("裏口", [GATE], by="adm", role="管理者")
    unit.register_group("物置", ["倉庫"], by="adm", role="管理者")
    unit.register_group("夜間", ["裏口"], by="adm", role="管理者")
    r1 = request_(unit, target="倉庫")
    a1 = approve(unit, r1.id)
    r2 = request_(unit, kind="群", target="夜間")
    a2 = approve(unit, r2.id)
    r3 = request_(unit, kind="群", target="物置")
    a3 = approve(unit, r3.id)
    unit.register_door("倉庫", [PHYSICAL], state="撤去", by="adm", role="管理者")
    unit.register_group("夜間", ["裏口"], state="廃止", by="adm", role="管理者")
    refused(unit, reasons.UNKNOWN_DOOR, unit.issue, a1.id, "s1", "扉", "倉庫", T0, T1, **kw)
    refused(unit, reasons.UNKNOWN_GROUP, unit.issue, a2.id, "s1", "群", "夜間", T0, T1, **kw)
    refused(unit, reasons.UNKNOWN_DOOR, unit.issue, a3.id, "s1", "群", "物置", T0, T1, **kw)


def test_issue_expired(unit, refused, request_, approve):
    """19.7 の発行の 9: 終了が空でなく at >= 終了 なら EXPIRED。終了の 1 分前は通る。"""
    r = request_(unit)
    a = approve(unit, r.id)
    refused(unit, reasons.EXPIRED, unit.issue, a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者",
            at=dt(T1))
    unit.issue(a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=dt(plus(T1, minutes=-1)))


def test_issue_self_approval_by_third_party_ignores_mark(unit, request_):
    """19.7 の発行の 3: 承認の発行者が申請者でも主体でもない時は、項目 自己承認 を見ずに通る。"""
    r = request_(unit)
    a = _direct_approval(unit, r.id, "apr", mark="はい")
    i = unit.issue(a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)
    assert i.form == "権限_発行"


def test_issue_self_approval_unmarked_without_setting(unit, refused, request_):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 3(a): 設定の無い置き場で、直に書いた申請者と同じ人の承認は SELF_REQUEST。
    主体と同じ人の承認は SELF_SUBJECT。"""
    r = request_(unit)                      # 申請者は adm
    a = _direct_approval(unit, r.id, "adm")
    refused(unit, reasons.SELF_REQUEST, unit.issue, a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)
    r2 = request_(unit, "apr")              # 主体は apr
    a2 = _direct_approval(unit, r2.id, "apr")
    refused(unit, reasons.SELF_SUBJECT, unit.issue, a2.id, "apr", "扉", "玄関", T0, T1, by="adm", role="管理者",
            at=AT)


# ---- 6 章の原則 3(b)〜(e): 一人で回す置き場 ----

@pytest.fixture
def solo(make_unit):
    """19.13 の (b): adm 一人に、管理者(作った時の物)、名簿の係、承認する人を割り当て、自己承認 許す を書く。"""
    u = make_unit("solo")
    u.assign_role("adm", "名簿の係", reason="一人で回す", by="adm", role="管理者")
    u.assign_role("adm", "承認する人", reason="一人で回す", by="adm", role="管理者")
    u.register_door("玄関", list(ALL3), by="adm", role="管理者")
    u.set_setting("自己承認", "許す", reason="一人で回す", by="adm", role="管理者")
    return u


def test_solo_unit_runs_without_refusal(solo):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 3(b): 一人で回す置き場は、申請、承認、発行、実体まで拒否なしに進む。
    権限_承認の 自己承認 は はい、役 は 承認する人。設定の伝票に発行者 adm と理由が残る。"""
    u = solo
    r = u.request("adm", "扉", "玄関", T0, T1, "自宅", by="adm", role="名簿の係")
    a = u.decide(r.id, "承認", by="adm", role="承認する人")
    assert a["自己承認"] == "はい" and a["役"] == "承認する人"
    u.issue(a.id, "adm", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)
    got = u.issue_entity(r.id, GATE, by="adm", role="管理者", at=AT)
    assert got.伝票.form == "実体_発行"
    setting, = u.ledger.select("設定")
    assert setting.issuer == "adm" and setting["理由"] == "一人で回す" and setting["項目"] == "自己承認"


def test_solo_unmarked_direct_approval_is_refused(solo, refused):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 3(c): 許す の置き場でも、直に書いた 自己承認 いいえ の自己承認は SELF_REQUEST。"""
    r = solo.request("adm", "扉", "玄関", T0, T1, "自宅", by="adm", role="名簿の係")
    a = _direct_approval(solo, r.id, "adm", mark="いいえ")
    refused(solo, reasons.SELF_REQUEST, solo.issue, a.id, "adm", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)


def test_solo_setting_checked_again_at_issue(solo, refused):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 3(d): 自己承認の後、発行の前に 許さない に戻すと SELF_REQUEST。もう一度 許す で通る。"""
    r = solo.request("adm", "扉", "玄関", T0, T1, "自宅", by="adm", role="名簿の係")
    a = solo.decide(r.id, "承認", by="adm", role="承認する人")
    solo.set_setting("自己承認", "許さない", reason="二人に戻った", by="adm", role="管理者")
    refused(solo, reasons.SELF_REQUEST, solo.issue, a.id, "adm", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)
    solo.set_setting("自己承認", "許す", reason="また一人", by="adm", role="管理者")
    assert solo.issue(a.id, "adm", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT).form == "権限_発行"


def test_self_approval_setting_value_must_be_allow_or_deny(solo, refused):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 3(e): set_setting("自己承認", "はい") は BAD_SETTING。"""
    refused(solo, reasons.BAD_SETTING, solo.set_setting, "自己承認", "はい", reason="試し", by="adm", role="管理者")


def test_refusals_leave_ledger_unchanged(unit, refused, request_):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 4: 拒んだ時は台帳の伝票の数が変わらない。"""
    r = request_(unit)
    n = unit.ledger.count()
    refused(unit, reasons.NO_APPROVAL, unit.issue, r.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)
    refused(unit, reasons.ROLE_NOT_ALLOWED, unit.decide, r.id, "承認", by="s1", role="本人")
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.request, "nobody", "扉", "玄関", T0, T1, "業務",
            by="adm", role="名簿の係")
    assert unit.ledger.count() == n


def test_revoke_needs_no_approval(unit, forms, request_):
    """R-EB-37 R-AC-10 19.13 の 6 章の原則 5: 止めるのは承認なしで済む。revoke_subject は承認の伝票を要らない。"""
    request_(unit)
    before = forms(unit)
    rev = unit.revoke_subject("s1", "退職", by="adm", role="名簿の係", at=AT)
    after = forms(unit)
    assert len(rev.権限_失効) == 1
    assert after["権限_承認"] == before["権限_承認"]
    assert after["権限_失効"] == before["権限_失効"] + 1


# ---- 外の一覧の取り込み ----

def _row(ident, target="玄関", kind="扉", start=T0, end=T1, reason="人事の届け"):
    return {"識別": ident, "対象の種類": kind, "対象": target, "開始": start, "終了": end, "理由": reason}


def test_import_writes_only_requests(unit, forms):
    """R-EB-37 19.7: 通った行は権限_申請(出どころ 外の一覧、一覧の名)を一枚書き、外れた行は飛ばした に入る(理由は 19.11 の言葉だけ。19.2)。"""
    before = forms(unit)
    res = unit.import_requests([_row("x9"), _row("s1"), _row("s1", target="倉庫")], "人事の届け 2026-11",
                               by="adm", role="名簿の係")
    assert len(res.申請) == 1
    slip = res.申請[0]
    assert slip.form == "権限_申請" and slip.issuer == "adm" and slip.to == "s1"
    assert (slip["出どころ"], slip["一覧の名"], slip["役"]) == ("外の一覧", "人事の届け 2026-11", "名簿の係")
    assert res.飛ばした == [(1, "名簿にありません"), (3, "この発行者の扉ではありません")]
    after = forms(unit)
    assert after - before == {"権限_申請": 1}


def test_import_row_missing_keys_is_form(unit):
    """19.7、19.2 の ImportResult: 鍵の足りない行は 様式に合いません で飛ばす(理由は言葉だけ)。"""
    row = _row("s1")
    del row["理由"]
    res = unit.import_requests([row, _row("s1")], "名簿", by="adm", role="名簿の係")
    assert res.飛ばした == [(1, "様式に合いません")]
    assert len(res.申請) == 1


def test_import_whole_refusals(unit, refused):
    """R-EB-37 19.7: 19.3 の 1〜4 は全体に一度。source_name が空なら FORM。どちらも何も書かない。"""
    refused(unit, reasons.FORM, unit.import_requests, [_row("s1")], "", by="adm", role="名簿の係")
    refused(unit, reasons.ROLE_NOT_ALLOWED, unit.import_requests, [_row("s1")], "名簿", by="s1", role="本人")
    refused(unit, reasons.NO_ROLE, unit.import_requests, [_row("s1")], "名簿", by="apr", role="名簿の係")


def test_import_applies_request_checks(unit):
    """19.7: 行ごとに request と同じ確かめ(業者、期間、有効)。"""
    kw = dict(by="adm", role="名簿の係")
    unit.register_subject("v1", "保守会社A", "業者", True, **kw)
    unit.register_subject("c1", "清掃A", "清掃", False, **kw)
    unit.change_subject("c1", reason="契約の終わり", state="退出", **kw)
    res = unit.import_requests([_row("v1", end=""), _row("s1", start=T1, end=T0), _row("c1")], "予約", **kw)
    assert res.申請 == []
    assert res.飛ばした == [(1, reasons.VENDOR_NO_END), (2, reasons.BAD_PERIOD), (3, reasons.SUBJECT_INACTIVE)]
