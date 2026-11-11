"""設計書 19.10: 失効、進み具合の一枚、物理キーの回収、失効の確認。"""
from __future__ import annotations

import pytest

from digitalkey.kanri import reasons
from kanri_common import CREDENTIAL, GATE, PHYSICAL, PHYSICAL_WORK, T0, T1, dt, plus

AT = dt("2026-11-20T00:00")
REV = "2026-12-10T12:00"


def _three(u, pid, key_number="K-7"):
    """一つの権限から E1(署名つき)、E2(門番)、E3(物理キー)を出す。"""
    kw = dict(by="adm", role="管理者", at=AT)
    e1 = u.issue_entity(pid, CREDENTIAL, **kw).実体
    e2 = u.issue_entity(pid, GATE, **kw).実体
    e3 = u.issue_entity(pid, PHYSICAL, key_number=key_number, **kw).実体
    return e1, e2, e3


def _rows(sheet):
    return {r.実体: r for r in sheet.行}


def _ids(text):
    return set(text.split(", "))


@pytest.fixture
def three(unit, grant):
    pid, _, _ = grant(unit)
    return (pid, *_three(unit, pid))


def test_example_19_10(unit, three, forms):
    """R-AC-15 19.10 の例: 2026-12-10T12:00 の revoke_subject("s1", "退職") の後の進み具合の表。
    21.2: 実体_失効 3 枚の後に 失効_一覧 が 1 枚増え、Revocation.失効_一覧 はその伝票。"""
    pid, e1, e2, e3 = three
    before = forms(unit)
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    after = forms(unit)
    assert after - before == {"権限_失効": 1, "実体_失効": 3, "失効_一覧": 1}
    assert len(rev.権限_失効) == 1 and len(rev.実体_失効) == 3
    assert rev.失効_一覧.form == "失効_一覧"
    by_entity = {s["実体"]: s for s in rev.実体_失効}
    assert set(by_entity) == {e1.番号, e2.番号, e3.番号}
    assert (by_entity[e1.番号]["結果"], by_entity[e1.番号]["効くまで"]) == ("待ち", T1)
    assert (by_entity[e2.番号]["結果"], by_entity[e2.番号]["効くまで"]) == ("止まった", "")
    assert by_entity[e3.番号]["結果"] == "人の作業が要る"

    rows = _rows(rev.進み具合)
    assert (rows[e1.番号].状態, rows[e1.番号].効くまで) == ("待ち", T1)
    assert rows[e2.番号].状態 == "止まった"
    assert rows[e3.番号].状態 == "人の作業が要る"
    assert rev.進み具合.済み is False
    assert rev.進み具合 == unit.progress("s1", at=dt(REV))

    unit.return_physical(e3.番号, "回収", by="adm", role="名簿の係")
    s = unit.progress("s1", at=dt("2026-12-20T00:00"))
    rows = _rows(s)
    assert [rows[e.番号].状態 for e in (e1, e2, e3)] == ["待ち", "止まった", "止まった"]
    assert s.済み is False

    s = unit.progress("s1", at=dt(T1))
    assert [_rows(s)[e.番号].状態 for e in (e1, e2, e3)] == ["止まった", "止まった", "止まった"]
    assert s.済み is True


def test_revoke_slips(unit, three):
    """19.4、19.10: 権限_失効(範囲 一人の全経路、発行者 by、根拠 [申請])と、実体_失効(発行者 発行者、根拠 [実体_発行, 権限_失効])。"""
    pid, e1, e2, e3 = three
    rev = unit.revoke_subject("s1", "退職", by="adm", role="名簿の係", at=dt(REV))
    p, = rev.権限_失効
    assert p.form == "権限_失効" and p.issuer == "adm" and p.to == "s1" and p.basis == [pid]
    assert (p["権限"], p["主体"], p["範囲"], p["理由"], p["役"]) == (pid, "s1", "一人の全経路", "退職", "名簿の係")
    by_entity = {s["実体"]: s for s in rev.実体_失効}
    s1 = by_entity[e1.番号]
    assert s1.form == "実体_失効" and s1.issuer == "発行者" and s1.to == "s1"
    assert s1.basis == [e1.番号, p.id]
    assert (s1["主体"], s1["経路"], s1["結果"], s1["効くまで"], s1["作業"], s1["理由"], s1["操作者"], s1["役"]) == \
        ("s1", CREDENTIAL, "待ち", T1, "", "退職", "adm", "名簿の係")
    s2 = by_entity[e2.番号]
    assert (s2["経路"], s2["結果"], s2["効くまで"], s2["作業"], s2.issuer, s2.basis) == \
        (GATE, "止まった", "", "", "発行者", [e2.番号, p.id])
    s3 = by_entity[e3.番号]
    assert (s3["経路"], s3["結果"], s3["効くまで"], s3["作業"]) == \
        (PHYSICAL, "人の作業が要る", "", PHYSICAL_WORK.format("K-7"))
    assert s3.basis == [e3.番号, p.id]


def test_entity_attributes_after_revoke(unit, three):
    """19.2: Entity.失効 は実体_失効の番号、Permission.失効 は権限_失効の番号。"""
    pid, e1, e2, e3 = three
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    ents = {e.番号: e for e in unit.entities("s1")}
    by_entity = {s["実体"]: s.id for s in rev.実体_失効}
    assert ents[e1.番号].失効 == by_entity[e1.番号]
    assert ents[e3.番号].失効 == by_entity[e3.番号]
    assert unit.permission(pid, at=dt(REV)).失効 == rev.権限_失効[0].id


def test_second_revoke_writes_nothing(unit, three):
    """R-AC-15 19.10: 二度目の呼び出しは伝票を増やさず、Revocation.権限_失効 と .実体_失効 は空の列、進み具合は今の物。
    21.2: 失効_一覧 も増やさず、Revocation.失効_一覧 は None。"""
    unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    n = unit.ledger.count()
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    assert unit.ledger.count() == n
    assert rev.権限_失効 == [] and rev.実体_失効 == []
    assert rev.失効_一覧 is None
    assert rev.進み具合 == unit.progress("s1", at=dt(REV))
    pid = three[0]
    rev = unit.revoke_permission(pid, "退職", by="adm", role="管理者", at=dt(REV))
    assert unit.ledger.count() == n
    assert rev.権限_失効 == [] and rev.実体_失効 == []
    assert rev.失効_一覧 is None


def test_revoke_subject_covers_open_permissions_only(unit, grant, request_, approve):
    """19.10 の 1: 状態が 申請中、承認、発行済み の権限に一枚ずつ。却下と期限切れには書かない。"""
    issued, _, _ = grant(unit)
    pending = request_(unit)
    approved = request_(unit)
    approve(unit, approved.id)
    rejected = request_(unit)
    unit.decide(rejected.id, "却下", by="apr", role="承認する人")
    expired, _, _ = grant(unit, end="2026-12-05T00:00")
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    assert {s["権限"] for s in rev.権限_失効} == {issued, pending.id, approved.id}
    at = dt(REV)
    assert unit.permission(rejected.id, at=at).状態 == "却下"
    assert unit.permission(expired, at=at).状態 == "期限切れ"


def test_revoke_skips_ended_and_returned_entities(unit, grant):
    """19.10 の 2: 終了を過ぎた実体と、回収済みの物理キーには実体_失効を書かない。"""
    short, _, _ = grant(unit, end="2026-12-05T00:00")           # 失効の時(REV)には終了を過ぎている
    ended = _three(unit, short, key_number="K-1")
    pid, _, _ = grant(unit)
    live = _three(unit, pid, key_number="K-2")
    unit.return_physical(live[2].番号, by="adm", role="名簿の係")
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    written = {s["実体"] for s in rev.実体_失効}
    assert written == {live[0].番号, live[1].番号}
    assert not written & {e.番号 for e in ended}
    rows = _rows(rev.進み具合)
    # 終了を過ぎた物理キーは、実体_失効が無くても 人の作業が要る(19.10 の表)
    assert rows[ended[2].番号].状態 == "人の作業が要る"
    assert (rows[ended[0].番号].状態, rows[ended[1].番号].状態) == ("止まった", "止まった")


def test_revoke_subject_refusals_and_inactive(unit, grant, refused):
    """19.10 の 1: 主体が名簿に無ければ SUBJECT_UNKNOWN。停止、退出の主体も止められる。"""
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.revoke_subject, "nobody", "退職", by="adm", role="管理者",
            at=dt(REV))
    pid, _, _ = grant(unit)
    unit.change_subject("s1", reason="退職", state="退出", by="adm", role="名簿の係")
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    assert [s["権限"] for s in rev.権限_失効] == [pid]


def test_revoke_subject_without_anything(unit):
    """19.10: 権限も実体も無い主体は、何も書かずに済みの一枚を返す。"""
    n = unit.ledger.count()
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    assert unit.ledger.count() == n
    assert (rev.進み具合.主体, rev.進み具合.行, rev.進み具合.済み) == ("s1", [], True)


def test_revoke_permission(unit, grant):
    """19.10: revoke_permission は一つの権限の分だけ(範囲 一つの権限)。ほかの権限の実体は効いたまま。"""
    p1, _, _ = grant(unit)
    p2, _, _ = grant(unit)
    a1 = unit.issue_entity(p1, GATE, by="adm", role="管理者", at=AT).実体
    a2 = unit.issue_entity(p2, GATE, by="adm", role="管理者", at=AT).実体
    k1 = unit.issue_entity(p1, PHYSICAL, key_number="K-1", by="adm", role="管理者", at=AT).実体
    rev = unit.revoke_permission(p1, "紛失", by="adm", role="管理者", at=dt(REV))
    p, = rev.権限_失効
    assert (p["権限"], p["範囲"]) == (p1, "一つの権限")
    assert {s["実体"] for s in rev.実体_失効} == {a1.番号, k1.番号}
    rows = _rows(rev.進み具合)
    assert rows[a1.番号].状態 == "止まった"
    assert rows[a2.番号].状態 == "効いている"
    assert rows[k1.番号].状態 == "人の作業が要る"
    assert unit.may_open("s1", "玄関", at=dt(REV)).ok is True


def test_revoke_permission_on_closed_states_writes_no_permission_slip(unit, grant, request_, refused):
    """19.10: 状態が 却下、期限切れ、失効 の権限に revoke_permission を呼ぶと、権限_失効は書かない。"""
    rj = request_(unit)
    unit.decide(rj.id, "却下", by="apr", role="承認する人")
    n = unit.ledger.count()
    rev = unit.revoke_permission(rj.id, "取り下げ", by="adm", role="管理者", at=dt(REV))
    assert (rev.権限_失効, rev.実体_失効) == ([], [])
    assert unit.ledger.count() == n
    assert unit.permission(rj.id, at=dt(REV)).状態 == "却下"
    pid, _, _ = grant(unit)
    unit.issue_entity(pid, GATE, by="adm", role="管理者", at=AT)
    rev = unit.revoke_permission(pid, "期限の後", by="adm", role="管理者", at=dt(T1))
    assert (rev.権限_失効, rev.実体_失効) == ([], [])   # 門番の実体は終了を過ぎているので 2 にも当たらない
    assert unit.permission(pid, at=dt(T1)).状態 == "期限切れ"
    # 失効の権限への二度目は、何も書かない
    p2, _, _ = grant(unit)
    unit.revoke_permission(p2, "紛失", by="adm", role="管理者", at=dt(REV))
    n = unit.ledger.count()
    rev = unit.revoke_permission(p2, "紛失", by="adm", role="管理者", at=dt(REV))
    assert (rev.権限_失効, rev.実体_失効, unit.ledger.count()) == ([], [], n)


def test_revoke_permission_unknown(unit, refused):
    """19.10: 番号が申請でなければ PERMISSION_UNKNOWN。"""
    refused(unit, reasons.PERMISSION_UNKNOWN, unit.revoke_permission, "x", "紛失", by="adm", role="管理者",
            at=dt(REV))


def test_revoke_pending_request_blocks_issue(unit, request_, approve, refused):
    """19.10 の 1: 承認を待つ申請も、失効の後は承認も発行もできない。"""
    pending = request_(unit)
    approved = request_(unit)
    a = approve(unit, approved.id)
    unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    refused(unit, reasons.REVOKED, unit.decide, pending.id, "承認", by="apr", role="承認する人")
    refused(unit, reasons.REVOKED, unit.issue, a.id, "s1", "扉", "玄関", T0, T1, by="adm", role="管理者", at=AT)


# ---- 進み具合の状態の表 ----

def test_progress_before_revoke(unit, three):
    """19.10 の表: 失効の前で期間の内は、三つとも 効いている。効くまで と 作業 は空。"""
    pid, e1, e2, e3 = three
    s = unit.progress("s1", at=dt("2026-12-05T10:00"))
    assert (s.主体, s.時刻, s.済み) == ("s1", "2026-12-05T10:00", False)
    for r in s.行:
        assert (r.状態, r.効くまで, r.作業) == ("効いている", "", "")
    rows = _rows(s)
    assert (rows[e1.番号].経路, rows[e1.番号].識別, rows[e1.番号].扉) == (CREDENTIAL, e1.識別, ["玄関"])
    assert (rows[e3.番号].経路, rows[e3.番号].識別) == (PHYSICAL, "K-7")
    assert [r.実体 for r in s.行] == [e1.番号, e2.番号, e3.番号]   # 実体_発行を台帳に書いた順(19.2)


def test_progress_after_end_without_revoke(unit, three):
    """19.10 の表: 終了を過ぎた時、門番と署名つきは 止まった、物理キーは 人の作業が要る(作業は 19.8 の文)。"""
    pid, e1, e2, e3 = three
    s = unit.progress("s1", at=dt(T1))
    rows = _rows(s)
    assert rows[e1.番号].状態 == "止まった"
    assert rows[e2.番号].状態 == "止まった"
    assert (rows[e3.番号].状態, rows[e3.番号].作業) == ("人の作業が要る", PHYSICAL_WORK.format("K-7"))
    assert s.済み is False
    # 終了の 1 分前は、まだ効いている
    s = unit.progress("s1", at=dt(plus(T1, minutes=-1)))
    assert {r.状態 for r in s.行} == {"効いている"}


def test_progress_wait_until_entity_end(unit, grant):
    """19.10 の表: 署名つきの 待ち の 効くまで は実体の終了(until)。"""
    pid, _, _ = grant(unit)
    e = unit.issue_entity(pid, CREDENTIAL, until="2026-12-15T00:00", by="adm", role="管理者", at=AT).実体
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    row = _rows(rev.進み具合)[e.番号]
    assert (row.状態, row.効くまで) == ("待ち", "2026-12-15T00:00")
    assert _rows(unit.progress("s1", at=dt("2026-12-15T00:00")))[e.番号].状態 == "止まった"


def test_progress_unknown_subject(unit, refused):
    """19.10 の progress: 名簿に無い主体は SUBJECT_UNKNOWN。"""
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.progress, "nobody", at=dt(REV))


def test_progress_empty_is_done(unit):
    """19.2: 行が無ければ 済み は真。"""
    s = unit.progress("s1", at=dt(REV))
    assert (s.行, s.済み) == ([], True)


# ---- 物理キーの回収 ----

def test_return_physical(unit, three):
    """R-AC-15 19.10: return_physical は 物理キー_回収 を書き(根拠 [実体_発行])、その行は 止まった。失効の前にも使える。"""
    pid, e1, e2, e3 = three
    slip = unit.return_physical(e3.番号, by="adm", role="名簿の係")
    assert slip.form == "物理キー_回収" and slip.issuer == "adm" and slip.to == "s1" and slip.basis == [e3.番号]
    assert (slip["実体"], slip["鍵番号"], slip["結果"], slip["備考"], slip["役"]) == \
        (e3.番号, "K-7", "回収", "", "名簿の係")
    assert _rows(unit.progress("s1", at=dt(REV)))[e3.番号].状態 == "止まった"
    assert {e.番号: e for e in unit.entities("s1")}[e3.番号].回収 == slip.id
    # 回収済みの物理キーは、失効の時に実体_失効を書かない
    rev = unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    assert e3.番号 not in {s["実体"] for s in rev.実体_失効}


def test_return_physical_lock_changed_with_note(unit, three):
    """19.10: result は 回収 か 錠を交換。note は 備考。"""
    pid, e1, e2, e3 = three
    slip = unit.return_physical(e3.番号, "錠を交換", "戻らなかった", by="adm", role="管理者")
    assert (slip["結果"], slip["備考"], slip["役"]) == ("錠を交換", "戻らなかった", "管理者")


def test_return_physical_refusals(unit, three, refused):
    """19.10: 実体が無ければ ENTITY_UNKNOWN、物理キーでなければ NOT_PHYSICAL、回収済みは ALREADY_RETURNED、結果が様式に無ければ FORM。"""
    pid, e1, e2, e3 = three
    kw = dict(by="adm", role="名簿の係")
    refused(unit, reasons.ENTITY_UNKNOWN, unit.return_physical, "x", **kw)
    refused(unit, reasons.NOT_PHYSICAL, unit.return_physical, e2.番号, **kw)
    refused(unit, reasons.FORM, unit.return_physical, e3.番号, "紛失", **kw)
    unit.return_physical(e3.番号, **kw)
    refused(unit, reasons.ALREADY_RETURNED, unit.return_physical, e3.番号, **kw)


# ---- 失効の確認 ----

def test_confirm_stopped(unit, three, refused):
    """19.10: 済みでなければ NOT_ALL_STOPPED(詳細に止まっていない実体の番号)。済みなら 失効_確認 に実体の番号の列。"""
    pid, e1, e2, e3 = three
    unit.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    e = refused(unit, reasons.NOT_ALL_STOPPED, unit.confirm_stopped, "s1", by="adm", role="管理者", at=dt(REV))
    assert _ids(e.詳細) == {e1.番号, e3.番号}
    unit.return_physical(e3.番号, by="adm", role="名簿の係")
    e = refused(unit, reasons.NOT_ALL_STOPPED, unit.confirm_stopped, "s1", by="adm", role="管理者", at=dt(REV))
    assert _ids(e.詳細) == {e1.番号}
    slip = unit.confirm_stopped("s1", by="adm", role="管理者", at=dt(T1))
    assert slip.form == "失効_確認" and slip.issuer == "adm" and slip.to == "s1"
    assert _ids(slip["実体"]) == {e1.番号, e2.番号, e3.番号}
    assert set(slip.basis) == {e1.番号, e2.番号, e3.番号}
    assert slip["役"] == "管理者"


def test_confirm_stopped_without_entities(unit, refused):
    """19.10: 実体が無ければ 実体 は なし。主体が無ければ SUBJECT_UNKNOWN。"""
    refused(unit, reasons.SUBJECT_UNKNOWN, unit.confirm_stopped, "nobody", by="adm", role="管理者", at=dt(REV))
    slip = unit.confirm_stopped("s1", by="adm", role="管理者", at=dt(REV))
    assert slip["実体"] == "なし" and slip.basis == []
