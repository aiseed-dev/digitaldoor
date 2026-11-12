"""設計書 20.8: 機器を対象にした権限と、経路「管理者のインターフェース」(出す、止める、効くまで、照らす、削除、進み具合、失効)。

段 1b の実装より先に書いた。発行と実体は固定の時刻(ISSUE_AT)で出す。
"""
from __future__ import annotations

import pytest

from digitalkey.kanri import reasons, routes
from kanri_common import ACCESS, ACCESS_WORK, ALL3, GATE, ISSUE_AT, PHYSICAL, RB, T0, T1, dt, plus

AT = dt(ISSUE_AT)
IN = "2026-12-05T10:00"
REV = "2026-12-10T12:00"
ADM = dict(by="adm", role="管理者")
ROSTER = dict(by="adm", role="名簿の係")


def _issue_access(u, pid, account="admin", **kw):
    kw.setdefault("by", "adm")
    kw.setdefault("role", "管理者")
    kw.setdefault("at", AT)
    return u.issue_entity(pid, ACCESS, account=account, **kw)


def _lose_directly(u, device="ルーター1", state="紛失"):
    """試験だけ: change_device を通らずに、台帳へ直に 機器_変更(状態)を書く。権限は失効しない。
    (`change_device` で紛失にすると権限が先に失効して REVOKED になるため、UNKNOWN_DEVICE の確かめにはこの形を使う)"""
    u.ledger.roles = {"adm": {"管理者"}}
    return u.ledger.append("機器_変更", {"名前": device, "状態": state, "理由": "試し", "役": "管理者"}, issuer="adm")


# ---- 申請と発行 ----

@pytest.mark.parametrize("state", ["準備", "使用", "休止"])
def test_request_device(dunit, state):
    """20.8: request(subject, "機器", 機器の名前, ...) は、状態が 準備、使用、休止 の機器なら通る。権限_申請の対象の種類は 機器。"""
    dunit.register_device("PC1", "PC", state, **ADM)
    r = dunit.request("t1", "機器", "PC1", T0, T1, "保守", **ROSTER)
    assert (r["対象の種類"], r["対象"]) == ("機器", "PC1")
    assert dunit.permission(r.id, at=AT).対象の種類 == "機器"


def test_request_device_unknown(dunit, refused):
    """20.8: 対象が、準備、使用、休止の機器でなければ UNKNOWN_DEVICE(詳細に対象の名)。扉の名前も機器ではない。"""
    req = dunit.request
    refused(dunit, RB.UNKNOWN_DEVICE, req, "t1", "機器", "ルーター9", T0, T1, "保守", **ROSTER, detail="ルーター9")
    refused(dunit, RB.UNKNOWN_DEVICE, req, "t1", "機器", "玄関", T0, T1, "保守", **ROSTER, detail="玄関")
    dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    refused(dunit, RB.UNKNOWN_DEVICE, req, "t1", "機器", "ルーター1", T0, T1, "保守", **ROSTER, detail="ルーター1")
    dunit.change_device("複合機1", reason="入れ替え", state="廃棄", at=dt(REV), **ADM)
    refused(dunit, RB.UNKNOWN_DEVICE, req, "t1", "機器", "複合機1", T0, T1, "保守", **ROSTER, detail="複合機1")


def test_request_device_check_order(dunit, refused):
    """20.8、19.7: 機器の確かめは「扉か群」の所(主体の有効の後、期間の前)。"""
    req = dunit.request
    refused(dunit, reasons.SUBJECT_UNKNOWN, req, "x9", "機器", "ルーター9", T1, T0, "保守", **ROSTER)
    refused(dunit, RB.UNKNOWN_DEVICE, req, "t1", "機器", "ルーター9", T1, T0, "保守", **ROSTER)
    refused(dunit, reasons.BAD_PERIOD, req, "t1", "機器", "ルーター1", T1, T0, "保守", **ROSTER)
    dunit.change_subject("t2", reason="休職", state="停止", **ROSTER)
    refused(dunit, reasons.SUBJECT_INACTIVE, req, "t2", "機器", "ルーター9", T0, T1, "保守", **ROSTER)


def test_request_device_vendor(dunit, refused):
    """R-AC-10 20.8: 業者の確かめ(VENDOR_NO_END、VENDOR_TOO_LONG)は機器の権限にも同じに当たる。"""
    req = dunit.request
    refused(dunit, reasons.VENDOR_NO_END, req, "v1", "機器", "ルーター1", T0, "", "保守", **ROSTER)
    dunit.set_setting("業者の最長の日数", "7", reason="保守の契約", **ADM)
    refused(dunit, reasons.VENDOR_TOO_LONG, req, "v1", "機器", "ルーター1", T0, plus(T0, days=7, minutes=1), "保守",
            **ROSTER)
    assert req("v1", "機器", "ルーター1", T0, plus(T0, days=7), "保守", **ROSTER).form == "権限_申請"


def test_import_device_rows(dunit):
    """20.8: import_requests の行も 機器 を受け、同じ確かめをする(外れた行は UNKNOWN_DEVICE の言葉で飛ばす)。"""
    rows = [{"識別": "t1", "対象の種類": "機器", "対象": "ルーター1", "開始": T0, "終了": T1, "理由": "保守"},
            {"識別": "t1", "対象の種類": "機器", "対象": "ルーター9", "開始": T0, "終了": T1, "理由": "保守"}]
    res = dunit.import_requests(rows, "保守の予定", **ROSTER)
    slip, = res.申請
    assert (slip["対象の種類"], slip["対象"], slip["出どころ"]) == ("機器", "ルーター1", "外の一覧")
    assert res.飛ばした == [(2, RB.UNKNOWN_DEVICE)]


def test_issue_device_permission(dunit, request_, approve):
    """20.8: 機器の権限の issue は、権限_発行の 扉 が空、Permission.扉 は空の列。"""
    r = request_(dunit, "t1", "機器", "ルーター1")
    a = approve(dunit, r.id)
    i = dunit.issue(a.id, "t1", "機器", "ルーター1", T0, T1, **ADM, at=AT)
    assert i.form == "権限_発行" and i.basis == [r.id, a.id]
    assert (i["対象の種類"], i["対象"], i["扉"]) == ("機器", "ルーター1", "")
    p = dunit.permission(r.id, at=AT)
    assert (p.状態, p.扉, p.対象の種類, p.対象) == ("発行済み", [], "機器", "ルーター1")


def test_issue_device_unknown(dunit, request_, approve, refused):
    """20.8: issue の 19.7 の 8 で、対象の種類が 機器 なら同じ確かめ(準備、使用、休止の機器でなければ UNKNOWN_DEVICE)。"""
    r = request_(dunit, "t1", "機器", "ルーター1")
    a = approve(dunit, r.id)
    _lose_directly(dunit)
    refused(dunit, RB.UNKNOWN_DEVICE, dunit.issue, a.id, "t1", "機器", "ルーター1", T0, T1, **ADM, at=AT,
            detail="ルーター1")


def test_issue_device_vendor_rechecked(dunit, request_, approve, refused):
    """R-AC-10 20.8、19.7 の 7: 機器の権限の発行でも、業者の確かめを今の名簿と設定で確かめ直す。"""
    r = request_(dunit, "t1", "機器", "ルーター1", end="")
    a = approve(dunit, r.id)
    dunit.change_subject("t1", reason="業務委託に変わった", vendor=True, **ROSTER)
    refused(dunit, reasons.VENDOR_NO_END, dunit.issue, a.id, "t1", "機器", "ルーター1", T0, "", **ADM, at=AT)


def test_may_open_ignores_device_permissions(dunit, access):
    """20.8: may_open と trust_entry は機器の権限を見ない(機器の権限で開く扉は無い)。"""
    before = dunit.trust_entry()
    access(dunit, "t1")
    assert dunit.may_open("t1", "玄関", at=dt(IN)).理由 == reasons.GATE_NO_PERMISSION
    assert dunit.trust_entry() == before


# ---- 実体を出す ----

def test_issue_access_example(dunit, grant):
    """20.8 の例: t1 の機器 ルーター1 の発行済みの権限 P から issue_entity(P, "管理者のインターフェース", account="admin")
    → 実体_発行(経路 管理者のインターフェース、扉 空、機器 ルーター1、識別 admin)。Issued.資格情報 は None。"""
    pid, _, ps = grant(dunit, "t1", "機器", "ルーター1")
    got = _issue_access(dunit, pid)
    assert got.資格情報 is None
    s = got.伝票
    assert s.form == "実体_発行" and s.issuer == "発行者" and s.to == "t1" and s.basis == [ps.id]
    assert (s["権限"], s["主体"], s["経路"], s["扉"], s["機器"], s["識別"], s["発行者"], s["開始"], s["終了"],
            s["操作者"], s["役"]) == (pid, "t1", ACCESS, "", "ルーター1", "admin", "", T0, T1, "adm", "管理者")
    e = got.実体
    assert (e.番号, e.権限, e.主体, e.経路, e.扉, e.識別, e.発行者, e.開始, e.終了, e.一回限り, e.失効, e.回収, e.機器) == \
        (s.id, pid, "t1", ACCESS, [], "admin", "", T0, T1, False, "", "", "ルーター1")


def test_door_route_entity_has_empty_device(dunit, grant):
    """20.2: 扉の経路の実体の 機器 は空。実体_発行の 機器 も空。"""
    pid, _, _ = grant(dunit, "t1")
    got = dunit.issue_entity(pid, GATE, **ADM, at=AT)
    assert got.実体.機器 == "" and got.伝票["機器"] == ""


def test_issue_access_until(dunit, grant):
    """20.8、19.8: until があれば実体の終了は until。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    assert _issue_access(dunit, pid, until="2026-12-15T00:00").実体.終了 == "2026-12-15T00:00"


def test_issue_access_on_paused_device(dunit, grant):
    """20.8 の 2: 休止の機器にも出せる。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    dunit.change_device("ルーター1", reason="止めておく", state="休止", **ADM)
    assert _issue_access(dunit, pid).実体.機器 == "ルーター1"


def test_issue_access_role_admin_only(dunit, grant, refused):
    """R-NW-05 R-RS-04 20.3、20.13 の拒否と役 4: 機器の権限から実体を出すのは管理者だけ。名簿の係では ROLE_NOT_ALLOWED。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.ROLE_NOT_ALLOWED, _issue_access, dunit, pid, by="adm", role="名簿の係")
    refused(dunit, reasons.ROLE_NOT_ALLOWED, _issue_access, dunit, pid, by="ops", role="運用")


def test_issue_access_unknown_route_before_role(dunit, refused):
    """19.8: 管理者のインターフェースも登録簿にある経路。知らない経路は役より先に UNKNOWN_ROUTE で、管理者のインターフェースはそうならない。"""
    refused(dunit, reasons.ROLE_NOT_ALLOWED, dunit.issue_entity, "x", ACCESS, account="admin", by="nobody",
            role="発行者", at=AT)


def test_issue_access_permission_states(dunit, grant, request_, approve, refused):
    """20.8、19.8: PERMISSION_UNKNOWN、NOT_ISSUED、EXPIRED、REVOKED は 19.8 のまま。"""
    refused(dunit, reasons.PERMISSION_UNKNOWN, _issue_access, dunit, "x")
    r = request_(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.NOT_ISSUED, _issue_access, dunit, r.id)
    approve(dunit, r.id)
    refused(dunit, reasons.NOT_ISSUED, _issue_access, dunit, r.id)
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.EXPIRED, _issue_access, dunit, pid, at=dt(T1))
    dunit.revoke_permission(pid, "取り下げ", **ADM, at=AT)
    refused(dunit, reasons.REVOKED, _issue_access, dunit, pid)


def test_issue_access_subject_inactive_before_device_only(dunit, grant, refused):
    """20.8: 主体が有効でなければ SUBJECT_INACTIVE(DEVICE_ONLY より先)。"""
    pd, _, _ = grant(dunit, "t1")
    dunit.change_subject("t1", reason="休職", state="停止", **ROSTER)
    refused(dunit, reasons.SUBJECT_INACTIVE, _issue_access, dunit, pd)


def test_device_only(dunit, grant, refused):
    """R-NW-05 R-RS-04 20.8 の 1、20.13 の拒否と役 3: 扉の権限から管理者のインターフェースは DEVICE_ONLY。"""
    pid, _, _ = grant(dunit, "t1")
    refused(dunit, RB.DEVICE_ONLY, _issue_access, dunit, pid, detail="")
    dunit.register_group("事務所", ["玄関"], **ADM)
    pg, _, _ = grant(dunit, "t1", "群", "事務所")
    refused(dunit, RB.DEVICE_ONLY, _issue_access, dunit, pg)


def test_issue_access_unknown_device(dunit, grant, refused):
    """20.8 の 2: 対象の機器の状態が 準備、使用、休止 でなければ UNKNOWN_DEVICE(詳細に機器の名)。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    _lose_directly(dunit)
    refused(dunit, RB.UNKNOWN_DEVICE, _issue_access, dunit, pid, detail="ルーター1")


@pytest.mark.parametrize("route", ALL3)
def test_device_permission_with_door_routes(dunit, grant, refused, route):
    """R-NW-05 R-RS-04 20.8、20.13 の拒否と役 3: 機器の権限にほかの三つの経路を渡すと、扉の列が空なので NO_DOOR_FOR_ROUTE。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.NO_DOOR_FOR_ROUTE, dunit.issue_entity, pid, route, key_number="K-7", **ADM, at=AT)


def test_issue_access_later_checks(dunit, grant, refused):
    """20.8 の 4: その後は 19.8 のまま。ONCE_CREDENTIAL_ONLY、UNTIL_TOO_LATE、BAD_PERIOD、出す(NEED_ACCOUNT)の順。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.ONCE_CREDENTIAL_ONLY, _issue_access, dunit, pid, account="", once=True)
    refused(dunit, reasons.UNTIL_TOO_LATE, _issue_access, dunit, pid, account="", until=plus(T1, minutes=1))
    refused(dunit, reasons.BAD_PERIOD, _issue_access, dunit, pid, account="", until=T0)
    refused(dunit, RB.NEED_ACCOUNT, _issue_access, dunit, pid, account="", detail="")


def test_access_uses_account_not_key_number(dunit, grant, refused):
    """20.8、20.14: 管理者のインターフェースは account を使い、key_number を使わない。ほかの経路は account を使わない。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, RB.NEED_ACCOUNT, dunit.issue_entity, pid, ACCESS, key_number="K-1", **ADM, at=AT)
    got = dunit.issue_entity(pid, ACCESS, account="admin", key_number="K-9", **ADM, at=AT)
    assert got.実体.識別 == "admin"
    pd, _, _ = grant(dunit, "t1")
    k = dunit.issue_entity(pd, PHYSICAL, key_number="K-7", account="admin", **ADM, at=AT)
    assert k.実体.識別 == "K-7"
    g = dunit.issue_entity(pd, GATE, account="admin", **ADM, at=AT)
    assert g.実体.識別 != "admin"


def test_account_length_is_form(dunit, grant, refused):
    """20.8、20.14: 19.3 の 5 で account を実体_発行の 識別(varchar(64))に照らす。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    refused(dunit, reasons.FORM, _issue_access, dunit, pid, account="a" * 65)
    assert _issue_access(dunit, pid, account="a" * 64).実体.識別 == "a" * 64


def test_shared_account_is_not_refused(dunit, access):
    """R-RS-04 20.8: 同じ機器と同じインターフェースの名の実体を、何人分でも出せる(拒まない)。同じ人に二度も出せる。"""
    _, a = access(dunit, "t1", "複合機1")
    _, b = access(dunit, "t2", "複合機1")
    _, c = access(dunit, "t1", "複合機1")
    assert (a.識別, b.識別, c.識別) == ("admin", "admin", "admin")
    assert len({a.番号, b.番号, c.番号}) == 3


def test_entities_include_access(dunit, access, grant):
    """R-AC-15 20.8: entities(subject) は扉の鍵と機器のインターフェースを同じ一覧に並べる(実体_発行を台帳に書いた順)。"""
    pd, _, _ = grant(dunit, "t1")
    g = dunit.issue_entity(pd, GATE, **ADM, at=AT).実体
    _, a = access(dunit, "t1")
    assert [(e.番号, e.経路, e.機器) for e in dunit.entities("t1")] == \
        [(g.番号, GATE, ""), (a.番号, ACCESS, "ルーター1")]


# ---- 経路「管理者のインターフェース」の四つの働き ----

def test_route_issue_directly(dunit, grant):
    """20.8 の表の 出す: RouteIssue(識別=account, 発行者="", 資格情報=None)。key_number が空なら NEED_ACCOUNT。"""
    pid, _, _ = grant(dunit, "t1", "機器", "ルーター1")
    route = routes.available()[ACCESS]
    perm = dunit.permission(pid, at=AT)
    got = route.issue(dunit, perm, doors=[], start=T0, end=T1, once=False, key_number="admin")
    assert (got.識別, got.発行者, got.資格情報) == ("admin", "", None)
    with pytest.raises(reasons.Refused) as ei:
        route.issue(dunit, perm, doors=[], start=T0, end=T1, once=False, key_number="")
    assert ei.value.理由 == RB.NEED_ACCOUNT


def test_route_stop_and_effective_by(dunit, access):
    """20.8 の表の 止める と 効くまで: 削除の伝票が無ければ 人の作業が要る(作業の文)、あれば 止まった。効くまで は 人の作業まで。
    経路は伝票を書かない。"""
    route = routes.available()[ACCESS]
    _, e = access(dunit, "t1")
    n = dunit.ledger.count()
    s = route.stop(dunit, e, at=dt(REV))
    assert (s.結果, s.効くまで, s.作業) == ("人の作業が要る", "", ACCESS_WORK.format("ルーター1", "admin"))
    eb = route.effective_by(dunit, e)
    assert (eb.種類, eb.時刻) == ("人の作業まで", "")
    assert dunit.ledger.count() == n
    dunit.remove_access(e.番号, **ADM)
    removed = {x.番号: x for x in dunit.entities("t1")}[e.番号]
    s = route.stop(dunit, removed, at=dt(REV))
    assert (s.結果, s.効くまで, s.作業) == ("止まった", "", "")


def test_route_reconcile(dunit, access, refused):
    """20.8 の表の 照らす: 進み具合の状態が 人の作業が要る なら 違い(インターフェースが残ったまま)、ほかは 照らせない(段 6)。"""
    _, e = access(dunit, "t1")
    at = dt(REV)
    r = dunit.reconcile(e.番号, at=at)
    assert (r.結果, r.詳細) == ("照らせない", "機器の中との照らし合わせは段 6")
    dunit.revoke_subject("t1", "退職", **ADM, at=at)
    r = dunit.reconcile(e.番号, at=at)
    assert (r.結果, r.詳細) == ("違い", "インターフェースが残ったまま")
    dunit.remove_access(e.番号, **ADM)
    r = dunit.reconcile(e.番号, at=at)
    assert (r.結果, r.詳細) == ("照らせない", "機器の中との照らし合わせは段 6")


# ---- 進み具合 ----

def _row(u, subject, eid, at):
    return {r.実体: r for r in u.progress(subject, at=dt(at)).行}[eid]


def test_progress_access_rows(dunit, access, grant):
    """20.8 の進み具合の表: 削除の伝票 → 止まった。実体_失効、権限の失効、終了を過ぎた → 人の作業が要る(作業の文)。
    ほか → 効いている。Row.機器 は実体の 機器、扉の経路の行は空。"""
    pd, _, _ = grant(dunit, "t1")
    g = dunit.issue_entity(pd, GATE, **ADM, at=AT).実体
    _, e = access(dunit, "t1")
    r = _row(dunit, "t1", e.番号, IN)
    assert (r.状態, r.経路, r.識別, r.扉, r.効くまで, r.作業, r.機器) == ("効いている", ACCESS, "admin", [], "", "", "ルーター1")
    assert _row(dunit, "t1", g.番号, IN).機器 == ""
    # 終了を過ぎた(失効なし)
    r = _row(dunit, "t1", e.番号, T1)
    assert (r.状態, r.作業) == ("人の作業が要る", ACCESS_WORK.format("ルーター1", "admin"))
    assert _row(dunit, "t1", e.番号, plus(T1, minutes=-1)).状態 == "効いている"
    # 削除の伝票の後
    dunit.remove_access(e.番号, **ADM)
    r = _row(dunit, "t1", e.番号, T1)
    assert (r.状態, r.作業) == ("止まった", "")


def test_progress_access_after_revoke_permission(dunit, access):
    """20.8 の進み具合の表と 19.10: 権限が 失効 なら 人の作業が要る。revoke_permission は機器の権限にも同じに当たる。"""
    pid, e = access(dunit, "t1")
    rev = dunit.revoke_permission(pid, "保守の終わり", **ADM, at=dt(REV))
    p, = rev.権限_失効
    assert (p["範囲"], p["権限"]) == ("一つの権限", pid)
    x, = rev.実体_失効
    assert (x["実体"], x["経路"], x["結果"], x["作業"]) == \
        (e.番号, ACCESS, "人の作業が要る", ACCESS_WORK.format("ルーター1", "admin"))
    assert x.basis == [e.番号, p.id]
    assert _row(dunit, "t1", e.番号, REV).状態 == "人の作業が要る"


def test_revoke_skips_removed_access(dunit, access):
    """20.2 の 失効の 2: 削除の伝票がある管理者のインターフェースには、実体_失効を書かない。行は 止まった。"""
    pid, e = access(dunit, "t1")
    dunit.remove_access(e.番号, "消した", **ADM)
    rev = dunit.revoke_subject("t1", "退職", **ADM, at=dt(REV))
    assert [s["権限"] for s in rev.権限_失効] == [pid]
    assert rev.実体_失効 == []
    assert _row(dunit, "t1", e.番号, REV).状態 == "止まった"
    assert rev.進み具合.済み is True


def test_confirm_stopped_waits_for_removal(dunit, access, refused):
    """R-AC-15 20.8: 削除の伝票が出るまで 済み は偽で、confirm_stopped は NOT_ALL_STOPPED(詳細に実体の番号)。"""
    _, e = access(dunit, "t1")
    dunit.revoke_subject("t1", "退職", **ADM, at=dt(REV))
    refused(dunit, reasons.NOT_ALL_STOPPED, dunit.confirm_stopped, "t1", **ADM, at=dt(REV), detail=e.番号)
    dunit.remove_access(e.番号, **ADM)
    assert dunit.confirm_stopped("t1", **ADM, at=dt(REV))["実体"] == e.番号


# ---- 削除 ----

def test_remove_access_slip(dunit, access):
    """20.8、20.3: remove_access は 管理者のインターフェース_削除 を書く(発行者 by、宛先 実体の主体、根拠 [実体_発行])。
    書く値は 実体、機器、インターフェースの名、結果(既定 消した)、備考、役。Entity.回収 はその番号。"""
    _, e = access(dunit, "t1")
    slip = dunit.remove_access(e.番号, by="ops", role="運用")
    assert slip.form == "管理者のインターフェース_削除" and slip.issuer == "ops" and slip.to == "t1" and slip.basis == [e.番号]
    assert (slip["実体"], slip["機器"], slip["インターフェースの名"], slip["結果"], slip["備考"], slip["役"]) == \
        (e.番号, "ルーター1", "admin", "消した", "", "運用")
    assert {x.番号: x for x in dunit.entities("t1")}[e.番号].回収 == slip.id


@pytest.mark.parametrize("result", ["消した", "パスワードを替えた", "機器を初期化", "機器が無い"])
def test_remove_access_results(dunit, access, result):
    """20.8: result は 消した、パスワードを替えた、機器を初期化、機器が無い。note は 備考。"""
    _, e = access(dunit, "t1")
    slip = dunit.remove_access(e.番号, result, "共用のインターフェース", **ADM)
    assert (slip["結果"], slip["備考"]) == (result, "共用のインターフェース")


def test_remove_access_refusals(dunit, access, grant, refused):
    """20.8 の削除の確かめ: ENTITY_UNKNOWN、NOT_ACCESS、NOT_OWNER、ALREADY_REMOVED。result の外れは FORM。"""
    pd, _, _ = grant(dunit, "t1")
    g = dunit.issue_entity(pd, GATE, **ADM, at=AT).実体
    k = dunit.issue_entity(pd, PHYSICAL, key_number="K-7", **ADM, at=AT).実体
    _, e = access(dunit, "t1")
    rm = dunit.remove_access
    refused(dunit, reasons.ENTITY_UNKNOWN, rm, "x", **ADM)
    refused(dunit, RB.NOT_ACCESS, rm, g.番号, **ADM, detail="")
    refused(dunit, RB.NOT_ACCESS, rm, k.番号, **ADM)
    refused(dunit, reasons.FORM, rm, e.番号, "捨てた", **ADM)
    refused(dunit, reasons.FORM, rm, e.番号, "消した", "あ" * 201, **ADM)
    dunit.remove_access(e.番号, **ADM)
    refused(dunit, RB.ALREADY_REMOVED, rm, e.番号, **ADM, detail="")


def test_remove_access_owner_role(dunit, access, grant, refused):
    """20.3、20.8: 本人の役は、その機器の今の責任者だけ。実体の主体でも、責任者でなければ NOT_OWNER。管理者と運用は問わない。"""
    dunit.change_device("ルーター1", reason="責任者を決めた", owner="t1", **ADM)
    _, e2 = access(dunit, "t2")
    refused(dunit, RB.NOT_OWNER, dunit.remove_access, e2.番号, by="t2", role="本人", detail="")
    assert dunit.remove_access(e2.番号, by="t1", role="本人")["役"] == "本人"


def test_remove_access_refusal_order(dunit, access, grant, refused):
    """20.8 の削除の確かめの順: 実体 → 管理者のインターフェースか → 責任者 → 削除済み。"""
    pd, _, _ = grant(dunit, "t1")
    g = dunit.issue_entity(pd, GATE, **ADM, at=AT).実体
    _, e = access(dunit, "t1")
    refused(dunit, reasons.ENTITY_UNKNOWN, dunit.remove_access, "x", by="t2", role="本人")
    refused(dunit, RB.NOT_ACCESS, dunit.remove_access, g.番号, by="t2", role="本人")
    dunit.remove_access(e.番号, **ADM)
    refused(dunit, RB.NOT_OWNER, dunit.remove_access, e.番号, by="t2", role="本人")


def test_remove_access_before_revoke_and_on_retired(dunit, access):
    """20.8: 失効の前にも書ける(人が先にインターフェースを消した時)。機器が廃棄でも書ける。削除の後、その行は 止まった。"""
    _, a = access(dunit, "t1")
    dunit.remove_access(a.番号, **ADM)
    assert _row(dunit, "t1", a.番号, IN).状態 == "止まった"
    _, b = access(dunit, "t2", "複合機1")
    dunit.change_device("複合機1", reason="入れ替え", state="廃棄", at=dt(REV), **ADM)
    dunit.remove_access(b.番号, "機器を初期化", **ADM)
    assert _row(dunit, "t2", b.番号, REV).状態 == "止まった"


def test_return_physical_and_remove_access_do_not_cross(dunit, access, grant, refused):
    """20.8: 物理キーの実体に remove_access は NOT_ACCESS、管理者のインターフェースの実体に return_physical は NOT_PHYSICAL。"""
    pd, _, _ = grant(dunit, "t1")
    k = dunit.issue_entity(pd, PHYSICAL, key_number="K-7", **ADM, at=AT).実体
    _, e = access(dunit, "t1")
    refused(dunit, RB.NOT_ACCESS, dunit.remove_access, k.番号, **ADM)
    refused(dunit, reasons.NOT_PHYSICAL, dunit.return_physical, e.番号, **ADM)


# ---- 扉の経路に管理者のインターフェースは書けない ----

def test_register_door_refuses_access_route(dunit, refused):
    """20.2、20.14: register_door の routes に 管理者のインターフェース があれば、unit.routes にあっても UNKNOWN_ROUTE(詳細 管理者のインターフェース)。"""
    assert ACCESS in dunit.routes
    refused(dunit, reasons.UNKNOWN_ROUTE, dunit.register_door, "裏口", [GATE, ACCESS], **ADM, detail=ACCESS)
    refused(dunit, reasons.UNKNOWN_ROUTE, dunit.register_door, "裏口", [ACCESS], **ADM, detail=ACCESS)

