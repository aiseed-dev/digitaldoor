"""設計書 19.8: 経路の差し替えのインターフェース、登録簿、実体を出す(issue_entity)、効くまで、照らす。"""
from __future__ import annotations

import json
import re

from digitalkey.kanri import reasons, routes
from kanri_common import ALL3, ALL4, CREDENTIAL, GATE, PHYSICAL, T0, T1, dt, plus, unix

AT = dt("2026-11-20T00:00")


def _issue(u, pid, route, **kw):
    kw.setdefault("by", "adm")
    kw.setdefault("role", "管理者")
    kw.setdefault("at", AT)
    return u.issue_entity(pid, route, **kw)


def _all3(u, pid, key_number="K-7"):
    """一つの権限から三つの経路の実体を出す。{経路: Entity}。"""
    return {r: _issue(u, pid, r, **({"key_number": key_number} if r == PHYSICAL else {})).実体 for r in ALL3}


def test_registry_has_four_routes():
    """19.8、20.2: 登録簿 available() は組み込みで四つ(段 1 の三つと 管理者のインターフェース)を持つ。"""
    got = routes.available()
    assert set(ALL4) <= set(got)
    for name in ALL4:
        assert got[name].name == name


def test_credential_entity(unit, grant):
    """19.8: 署名つき資格情報は credentials.issue で作り、not_after は終了の 1 秒前、subject は主体の仮名。"""
    pid, _, ps = grant(unit)
    got = _issue(unit, pid, CREDENTIAL)
    body = json.loads(got.資格情報["body"])
    assert set(got.資格情報) == {"body", "sig"}
    assert body["format"] == "digitaldoor-credential/1"
    assert body["issuer"] == unit.issuer
    assert re.fullmatch(r"[0-9a-f]{16}", body["id"])
    assert body["subject"] == unit.subject("s1").仮名
    assert body["doors"] == ["玄関"]
    assert body["not_before"] == unix(T0)
    assert body["not_after"] == unix(T1) - 1
    assert body["once"] is False
    e = got.実体
    assert (e.番号, e.権限, e.主体, e.経路, e.扉, e.識別, e.発行者, e.開始, e.終了, e.一回限り, e.失効, e.回収) == \
        (got.伝票.id, pid, "s1", CREDENTIAL, ["玄関"], body["id"], unit.issuer, T0, T1, False, "", "")
    s = got.伝票
    assert s.form == "実体_発行" and s.issuer == "発行者" and s.to == "s1" and s.basis == [ps.id]
    assert (s["権限"], s["主体"], s["経路"], s["扉"], s["識別"], s["発行者"], s["開始"], s["終了"], s["操作者"],
            s["役"]) == (pid, "s1", CREDENTIAL, "玄関", body["id"], unit.issuer, T0, T1, "adm", "管理者")


def test_credential_is_not_written_to_ledger(unit, grant):
    """19.8: 資格情報そのもの(署名)は台帳に書かず、Issued.資格情報 で返すだけ。"""
    pid, _, _ = grant(unit)
    got = _issue(unit, pid, CREDENTIAL)
    sig = got.資格情報["sig"]
    for p in (unit.root / "台帳").rglob("*"):
        if p.is_file():
            assert sig.encode() not in p.read_bytes()


def test_credential_verifies_at_door_until_one_second_before_end(unit, grant, door):
    """19.8 の例: 終了 2027-01-01T00:00 の資格情報は 2026-12-31 23:59:59 まで通り、2027-01-01 00:00:00 に 期限の外。"""
    pid, _, _ = grant(unit)
    cred = _issue(unit, pid, CREDENTIAL).資格情報
    ctl = door(unit, "玄関")
    ctl.set_time(unix("2026-12-31T23:59") + 59, synced=True)
    assert ctl.present(cred) == "解錠"
    ctl.set_time(unix(T1), synced=True)
    assert ctl.present(cred) == "拒否"
    assert ctl.audit.entries[-1].result == "期限の外"


def test_once_credential(unit, grant):
    """19.8: once=True は資格情報の once が真、実体の 一回限り が真。"""
    pid, _, _ = grant(unit)
    got = _issue(unit, pid, CREDENTIAL, once=True)
    assert json.loads(got.資格情報["body"])["once"] is True
    assert got.実体.一回限り is True


def test_until_shortens_entity(unit, grant):
    """19.8: until があれば実体の終了は until、開始は権限の開始。"""
    pid, _, _ = grant(unit)
    until = "2026-12-15T00:00"
    got = _issue(unit, pid, CREDENTIAL, until=until)
    assert (got.実体.開始, got.実体.終了) == (T0, until)
    assert json.loads(got.資格情報["body"])["not_after"] == unix(until) - 1


def test_gate_entity(unit, grant):
    """19.8: 門番を通す錠は識別だけを作り、本人には何も渡さない(発行者 空、資格情報 None)。"""
    pid, _, _ = grant(unit)
    got = _issue(unit, pid, GATE)
    assert got.資格情報 is None
    assert re.fullmatch(r"[0-9a-f]{16}", got.実体.識別)
    assert got.実体.発行者 == "" and got.伝票["発行者"] == ""
    assert got.実体.扉 == ["玄関"]


def test_physical_entity(unit, grant):
    """19.8: 物理キーは識別に鍵の番号。"""
    pid, _, _ = grant(unit)
    got = _issue(unit, pid, PHYSICAL, key_number="K-7")
    assert got.資格情報 is None
    assert (got.実体.識別, got.実体.発行者, got.実体.経路) == ("K-7", "", PHYSICAL)


def test_group_example_doors_filtered_by_route(unit, grant):
    """19.8 の例: 玄関(三つの経路)と倉庫(物理キーだけ)の群の権限から、署名つき資格情報の doors は ["玄関"]。"""
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    unit.register_group("事務所", ["玄関", "倉庫"], by="adm", role="管理者")
    pid, _, _ = grant(unit, kind="群", target="事務所")
    got = _issue(unit, pid, CREDENTIAL)
    assert json.loads(got.資格情報["body"])["doors"] == ["玄関"]
    assert got.伝票["扉"] == "玄関"
    key = _issue(unit, pid, PHYSICAL, key_number="K-8")
    assert key.実体.扉 == ["玄関", "倉庫"] and key.伝票["扉"] == "玄関, 倉庫"


def test_entities_list(unit, grant):
    """R-AC-15 19.8: entities(subject) は一人の全経路の一覧。"""
    unit.register_subject("c1", "清掃A", "清掃", False, by="adm", role="名簿の係")
    pid, _, _ = grant(unit)
    pc, _, _ = grant(unit, "c1")
    ids = {e.番号 for e in _all3(unit, pid).values()}
    other = _issue(unit, pc, GATE).実体.番号
    assert {e.番号 for e in unit.entities("s1")} == ids
    assert {e.経路 for e in unit.entities("s1")} == set(ALL3)
    assert {e.番号 for e in unit.entities()} == ids | {other}


def test_entities_in_ledger_order(unit, grant):
    """19.8: entities() は実体_発行を台帳に書いた順。"""
    pid, _, _ = grant(unit)
    a = _issue(unit, pid, PHYSICAL, key_number="K-3").実体
    b = _issue(unit, pid, GATE).実体
    c = _issue(unit, pid, CREDENTIAL).実体
    assert [e.番号 for e in unit.entities("s1")] == [a.番号, b.番号, c.番号]


class _PinRoute:
    """試験だけの経路(段 6 の経路の代わり)。出す以外は呼ばれない。"""
    name = "暗証番号"


def test_routes_given_to_unit_are_the_registry(make_unit, refused):
    """19.2: routes を渡した Unit では、register_door と issue_entity の経路の確かめの相手は unit.routes。"""
    reg = routes.available()
    u = make_unit("R", routes={GATE: reg[GATE]})
    assert set(u.routes) == {GATE}
    u.assign_role("adm", "名簿の係", reason="兼務", by="adm", role="管理者")
    u.assign_role("adm", "承認する人", reason="一人で回す", by="adm", role="管理者")
    refused(u, reasons.UNKNOWN_ROUTE, u.register_door, "玄関", [GATE, CREDENTIAL], by="adm", role="管理者",
            detail=CREDENTIAL)
    u.register_door("玄関", [GATE], by="adm", role="管理者")
    refused(u, reasons.UNKNOWN_ROUTE, u.issue_entity, "x", CREDENTIAL, by="adm", role="管理者", at=AT)
    v = make_unit("V", routes={**reg, "暗証番号": _PinRoute()})
    assert v.register_door("通用口", ["暗証番号"], by="adm", role="管理者")["経路"] == "暗証番号"
    w = make_unit("W")
    refused(w, reasons.UNKNOWN_ROUTE, w.register_door, "通用口", ["暗証番号"], by="adm", role="管理者",
            detail="暗証番号")


# ---- issue_entity の確かめ(この順) ----

def test_unknown_route_comes_first(unit, refused):
    """19.8: route が登録簿に無ければ、役より先に UNKNOWN_ROUTE。"""
    refused(unit, reasons.UNKNOWN_ROUTE, unit.issue_entity, "x", "暗証番号", by="nobody", role="発行者", at=AT)


def test_route_roles(unit, grant, refused):
    """19.3、19.8: 物理キーは 管理者 か 名簿の係、ほかの経路は 管理者 だけ。"""
    unit.register_subject("k1", "受付A", "社員", False, by="adm", role="名簿の係")
    unit.assign_role("k1", "名簿の係", reason="受付", by="adm", role="管理者")
    pid, _, _ = grant(unit)
    got = _issue(unit, pid, PHYSICAL, key_number="K-1", by="k1", role="名簿の係")
    assert got.伝票["操作者"] == "k1" and got.伝票["役"] == "名簿の係"
    refused(unit, reasons.ROLE_NOT_ALLOWED, _issue, unit, pid, CREDENTIAL, by="k1", role="名簿の係")
    refused(unit, reasons.ROLE_NOT_ALLOWED, _issue, unit, pid, GATE, by="k1", role="名簿の係")


def test_permission_state_checks(unit, refused, request_, approve, grant):
    """19.8: 申請でなければ PERMISSION_UNKNOWN、失効は REVOKED、申請中・承認・却下は NOT_ISSUED、期限切れは EXPIRED。"""
    refused(unit, reasons.PERMISSION_UNKNOWN, _issue, unit, "x", GATE)
    r = request_(unit)
    refused(unit, reasons.NOT_ISSUED, _issue, unit, r.id, GATE)
    approve(unit, r.id)
    refused(unit, reasons.NOT_ISSUED, _issue, unit, r.id, GATE)
    rj = request_(unit)
    unit.decide(rj.id, "却下", by="apr", role="承認する人")
    refused(unit, reasons.NOT_ISSUED, _issue, unit, rj.id, GATE)
    pid, _, _ = grant(unit)
    refused(unit, reasons.EXPIRED, _issue, unit, pid, GATE, at=dt(T1))
    unit.revoke_permission(pid, "取り下げ", by="adm", role="管理者", at=AT)
    refused(unit, reasons.REVOKED, _issue, unit, pid, GATE)


def test_subject_inactive(unit, refused, grant):
    """19.8: 主体が有効でなければ SUBJECT_INACTIVE。"""
    pid, _, _ = grant(unit)
    unit.change_subject("s1", reason="休職", state="停止", by="adm", role="名簿の係")
    refused(unit, reasons.SUBJECT_INACTIVE, _issue, unit, pid, GATE)


def test_no_door_for_route(unit, refused, grant):
    """19.8: 権限の扉のうち、今使用で、この経路を持つ扉が無ければ NO_DOOR_FOR_ROUTE。"""
    unit.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    pid, _, _ = grant(unit, target="倉庫")
    refused(unit, reasons.NO_DOOR_FOR_ROUTE, _issue, unit, pid, CREDENTIAL)
    refused(unit, reasons.NO_DOOR_FOR_ROUTE, _issue, unit, pid, GATE)
    p2, _, _ = grant(unit)
    unit.register_door("玄関", list(ALL3), state="撤去", by="adm", role="管理者")
    refused(unit, reasons.NO_DOOR_FOR_ROUTE, _issue, unit, p2, GATE)


def test_once_only_for_credential(unit, refused, grant):
    """19.8: once が真で経路が署名つき資格情報でなければ ONCE_CREDENTIAL_ONLY。"""
    pid, _, _ = grant(unit)
    refused(unit, reasons.ONCE_CREDENTIAL_ONLY, _issue, unit, pid, GATE, once=True)
    refused(unit, reasons.ONCE_CREDENTIAL_ONLY, _issue, unit, pid, PHYSICAL, once=True, key_number="K-7")


def test_until_checks(unit, refused, grant):
    """19.8: until > 権限の終了 は UNTIL_TOO_LATE、実体の終了 <= 開始 は BAD_PERIOD。until = 権限の終了は通る。"""
    pid, _, _ = grant(unit)
    refused(unit, reasons.UNTIL_TOO_LATE, _issue, unit, pid, GATE, until=plus(T1, minutes=1))
    refused(unit, reasons.BAD_PERIOD, _issue, unit, pid, GATE, until=T0)
    refused(unit, reasons.BAD_PERIOD, _issue, unit, pid, GATE, until=plus(T0, minutes=-1))
    assert _issue(unit, pid, GATE, until=T1).実体.終了 == T1


def test_credential_needs_end(unit, refused, grant):
    """19.8: 期限なしの権限から署名つき資格情報を出す時、until が無ければ NEED_END。until を渡せば出せる。門番は期限なしで出せる。"""
    pid, _, _ = grant(unit, end="")
    refused(unit, reasons.NEED_END, _issue, unit, pid, CREDENTIAL)
    assert _issue(unit, pid, CREDENTIAL, until="2026-12-31T00:00").実体.終了 == "2026-12-31T00:00"
    assert _issue(unit, pid, GATE).実体.終了 == ""


def test_physical_key_number(unit, refused, grant):
    """19.8: 鍵の番号が空なら NEED_KEY_NUMBER、回収の無い同じ鍵の番号があれば KEY_LENT。回収の後は貸せる。"""
    pid, _, _ = grant(unit)
    refused(unit, reasons.NEED_KEY_NUMBER, _issue, unit, pid, PHYSICAL)
    first = _issue(unit, pid, PHYSICAL, key_number="K-7")
    refused(unit, reasons.KEY_LENT, _issue, unit, pid, PHYSICAL, key_number="K-7")
    unit.return_physical(first.実体.番号, "回収", by="adm", role="名簿の係")
    assert _issue(unit, pid, PHYSICAL, key_number="K-7").実体.識別 == "K-7"


def test_key_lent_across_subjects(unit, refused, grant):
    """19.8: 同じ鍵の番号の貸し出しは、主体が違っても KEY_LENT。"""
    unit.register_subject("c1", "清掃A", "清掃", False, by="adm", role="名簿の係")
    pid, _, _ = grant(unit)
    pc, _, _ = grant(unit, "c1")
    _issue(unit, pid, PHYSICAL, key_number="K-7")
    refused(unit, reasons.KEY_LENT, _issue, unit, pc, PHYSICAL, key_number="K-7")


# ---- 効くまで、止める、照らす ----

def test_effective_by(unit, grant):
    """19.8 の 効くまで: 署名つきは 期限まで(終了)、門番は 即時、物理キーは 人の作業まで。
    21.2: 扉 玄関 に最長の分数が無いので、署名つきは 期限まで のままで、分数 は None。"""
    pid, _, _ = grant(unit)
    reg = routes.available()
    ents = _all3(unit, pid)
    e = reg[CREDENTIAL].effective_by(unit, ents[CREDENTIAL])
    assert (e.種類, e.時刻, e.分数) == ("期限まで", T1, None)
    e = reg[GATE].effective_by(unit, ents[GATE])
    assert (e.種類, e.時刻) == ("即時", "")
    e = reg[PHYSICAL].effective_by(unit, ents[PHYSICAL])
    assert (e.種類, e.時刻) == ("人の作業まで", "")


def test_route_stop(unit, grant):
    """19.8 の 止める: 経路は結果を返し、伝票は書かない。"""
    pid, _, _ = grant(unit)
    reg = routes.available()
    ents = _all3(unit, pid)
    n = unit.ledger.count()
    s = reg[CREDENTIAL].stop(unit, ents[CREDENTIAL], at=dt("2026-12-10T12:00"))
    assert (s.結果, s.効くまで, s.作業) == ("待ち", T1, "")
    s = reg[CREDENTIAL].stop(unit, ents[CREDENTIAL], at=dt(T1))
    assert (s.結果, s.効くまで, s.作業) == ("止まった", "", "")
    s = reg[GATE].stop(unit, ents[GATE], at=dt("2026-12-10T12:00"))
    assert (s.結果, s.効くまで, s.作業) == ("止まった", "", "")
    s = reg[PHYSICAL].stop(unit, ents[PHYSICAL], at=dt("2026-12-10T12:00"))
    assert (s.結果, s.効くまで, s.作業) == ("人の作業が要る", "", "鍵番号 K-7 を回収する。戻らない時は錠を交換して回収の伝票に書く")
    assert unit.ledger.count() == n


def test_reconcile(unit, grant, refused):
    """19.8 の 照らす: 署名つきは 照らせない、門番は 一致、物理キーは 人の作業が要る なら 違い(貸し出したまま)。"""
    pid, _, _ = grant(unit)
    ents = _all3(unit, pid)
    at = dt("2026-12-10T12:00")
    r = unit.reconcile(ents[CREDENTIAL].番号, at=at)
    assert (r.結果, r.詳細) == ("照らせない", "扉の記録の受け取りは段 2")
    r = unit.reconcile(ents[GATE].番号, at=at)
    assert (r.結果, r.詳細) == ("一致", "")
    r = unit.reconcile(ents[PHYSICAL].番号, at=at)
    assert (r.結果, r.詳細) == ("一致", "")
    unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=at)
    r = unit.reconcile(ents[PHYSICAL].番号, at=at)
    assert (r.結果, r.詳細) == ("違い", "貸し出したまま")
    unit.return_physical(ents[PHYSICAL].番号, "錠を交換", by="adm", role="管理者")
    r = unit.reconcile(ents[PHYSICAL].番号, at=at)
    assert (r.結果, r.詳細) == ("一致", "")
    refused(unit, reasons.ENTITY_UNKNOWN, unit.reconcile, "x", at=at)


def test_unit_routes_default_to_registry(make_unit):
    """19.2、20.2: routes を省けば登録簿の組み込みの四つ。"""
    u = make_unit("R")
    assert set(u.routes) == set(ALL4)
    for name in ALL4:
        assert u.routes[name].name == name
