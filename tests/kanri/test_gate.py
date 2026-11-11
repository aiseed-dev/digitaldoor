"""設計書 19.9: 門番を通す錠(may_open と relay)。"""
from __future__ import annotations

from digitalkey.kanri import reasons
from kanri_common import CREDENTIAL, GATE, PHYSICAL, T0, T1, dt, plus, unix

AT = dt("2026-11-20T00:00")
IN = dt("2026-12-05T10:00")


def _gate(u, pid, **kw):
    return u.issue_entity(pid, GATE, by="adm", role="管理者", at=AT, **kw)


def test_may_open_ok(unit, grant):
    """19.9: 通れば GateVerdict(True, "", 通した実体の番号)。"""
    pid, _, _ = grant(unit)
    e = _gate(unit, pid).実体
    v = unit.may_open("s1", "玄関", at=IN)
    assert (v.ok, v.理由, v.実体) == (True, "", e.番号)


def test_may_open_unknown_door(unit, grant):
    """R-AC-09 19.9 の 1: この置き場の使用の扉でなければ UNKNOWN_DOOR(撤去した扉も)。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    v = unit.may_open("s1", "倉庫", at=IN)
    assert (v.ok, v.理由, v.実体) == (False, reasons.UNKNOWN_DOOR, "")
    unit.register_door("玄関", [GATE], state="撤去", by="adm", role="管理者")
    assert unit.may_open("s1", "玄関", at=IN).理由 == reasons.UNKNOWN_DOOR


def test_may_open_subject_checks(unit, grant):
    """19.9 の 2: 主体が名簿に無ければ SUBJECT_UNKNOWN、有効でなければ SUBJECT_INACTIVE。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    assert unit.may_open("nobody", "玄関", at=IN).理由 == reasons.SUBJECT_UNKNOWN
    assert unit.may_open("nobody", "倉庫", at=IN).理由 == reasons.UNKNOWN_DOOR   # 1 が 2 より先
    unit.change_subject("s1", reason="休職", state="停止", by="adm", role="名簿の係")
    assert unit.may_open("s1", "玄関", at=IN).理由 == reasons.SUBJECT_INACTIVE


def test_may_open_no_permission(unit, grant):
    """19.9 の 3: 門番を通す錠の実体で扉の列に door を持つ物が無ければ GATE_NO_PERMISSION(権限がありません)。"""
    assert unit.may_open("s1", "玄関", at=IN).理由 == "権限がありません"
    pid, _, _ = grant(unit)
    unit.issue_entity(pid, CREDENTIAL, by="adm", role="管理者", at=AT)
    unit.issue_entity(pid, PHYSICAL, key_number="K-7", by="adm", role="管理者", at=AT)
    assert unit.may_open("s1", "玄関", at=IN).理由 == reasons.GATE_NO_PERMISSION
    unit.register_door("裏口", [GATE], by="adm", role="管理者")
    _gate(unit, pid)
    assert unit.may_open("s1", "裏口", at=IN).理由 == reasons.GATE_NO_PERMISSION


def test_may_open_revoked(unit, grant):
    """19.9 の 4: 実体の全部が、権限の失効か実体_失効ありなら GATE_REVOKED(失効しています)。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    unit.revoke_permission(pid, "紛失", by="adm", role="管理者", at=IN)
    v = unit.may_open("s1", "玄関", at=IN)
    assert (v.ok, v.理由) == (False, "失効しています")


def test_may_open_one_live_entity_is_enough(unit, grant):
    """19.9 の 4、5: 失効していない実体が一つでも期間の内なら通り、それを返す。"""
    p1, _, _ = grant(unit)
    _gate(unit, p1)
    p2, _, _ = grant(unit)
    e2 = _gate(unit, p2).実体
    unit.revoke_permission(p1, "紛失", by="adm", role="管理者", at=IN)
    v = unit.may_open("s1", "玄関", at=IN)
    assert (v.ok, v.実体) == (True, e2.番号)


def test_may_open_returns_first_written_entity(unit, grant):
    """19.9: 通した実体は、当てはまる中で台帳に書いた順で最初。"""
    p1, _, _ = grant(unit)
    p2, _, _ = grant(unit)
    first = _gate(unit, p2).実体
    _gate(unit, p1)
    _gate(unit, p2)
    assert unit.may_open("s1", "玄関", at=IN).実体 == first.番号


def test_may_open_period_edges(unit, grant):
    """19.5、19.9 の 5: 開始 <= at < 終了 の間だけ通る。外れたら GATE_OUT_OF_PERIOD(期間の外)。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    assert unit.may_open("s1", "玄関", at=dt(plus(T0, minutes=-1))).理由 == "期間の外"
    assert unit.may_open("s1", "玄関", at=dt(T0)).ok is True
    assert unit.may_open("s1", "玄関", at=dt(plus(T1, minutes=-1))).ok is True
    assert unit.may_open("s1", "玄関", at=dt(T1)).理由 == reasons.GATE_OUT_OF_PERIOD


def test_may_open_until_and_no_end(unit, grant):
    """19.9 の 5: 実体の終了(until)で切れ、終了が空なら 開始 <= at だけ。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid, until="2026-12-10T00:00")
    assert unit.may_open("s1", "玄関", at=dt("2026-12-10T00:00")).理由 == reasons.GATE_OUT_OF_PERIOD
    p2, _, _ = grant(unit, end="")
    e = _gate(unit, p2).実体
    v = unit.may_open("s1", "玄関", at=dt("2099-01-01T00:00"))
    assert (v.ok, v.実体) == (True, e.番号)


def test_may_open_offset_aware_at(unit, grant):
    """19.5: at が時差つきなら地方時に直して比べる。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    assert unit.may_open("s1", "玄関", at=dt(T0).astimezone()).ok is True
    assert unit.may_open("s1", "玄関", at=dt(T1).astimezone()).ok is False


def test_may_open_writes_nothing(unit, grant):
    """19.3: may_open は読むメソッドで、台帳に書かない。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    n = unit.ledger.count()
    unit.may_open("s1", "玄関", at=IN)
    unit.may_open("nobody", "玄関", at=IN)
    assert unit.ledger.count() == n


def test_relay_ok_and_refused(unit, grant, door, auth_entry):
    """19.9: relay は may_open の結果で ctl.on_auth を呼ぶ。通れば 解錠、記録に仮名と 門番 と 実体=番号。
    拒めば 拒否 で、記録に種別 認証、事象 拒否、結果に理由。"""
    pid, _, _ = grant(unit)
    e = _gate(unit, pid).実体
    ctl = door(unit, "玄関", at="2026-12-05T10:00")
    assert unit.relay(ctl, "s1") == "解錠"
    a = auth_entry(ctl)
    assert (a.event, a.subject, a.route, a.detail) == ("成功", unit.subject("s1").仮名, "門番", f"実体={e.番号}")
    ctl.set_time(unix(T1), synced=True)
    assert unit.relay(ctl, "s1") == "拒否"
    last = ctl.audit.entries[-1]
    assert (last.kind, last.event, last.result, last.route, last.detail) == ("認証", "拒否", "期間の外", "門番", "")


def test_relay_unknown_subject_has_empty_pseudonym(unit, door):
    """19.9: 名簿に無い主体は、仮名を空にして拒否を記録する。"""
    ctl = door(unit, "玄関", at="2026-12-05T10:00")
    assert unit.relay(ctl, "nobody") == "拒否"
    last = ctl.audit.entries[-1]
    assert (last.kind, last.event, last.result, last.subject) == ("認証", "拒否", "名簿にありません", "")


def test_relay_at_overrides_door_clock(unit, grant, door):
    """19.9: at を渡せば ctl.now ではなく at で may_open を聞く。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    ctl = door(unit, "玄関", at="2026-12-05T10:00")
    assert unit.relay(ctl, "s1", at=dt(T1)) == "拒否"
    assert ctl.audit.entries[-1].result == reasons.GATE_OUT_OF_PERIOD


def test_relay_door_name_is_ctl_name(unit, grant, door):
    """19.9: relay は may_open(subject, ctl.name) を聞く。"""
    pid, _, _ = grant(unit)
    _gate(unit, pid)
    ctl = door(unit, "倉庫", at="2026-12-05T10:00")
    assert unit.relay(ctl, "s1") == "拒否"
    assert ctl.audit.entries[-1].result == reasons.UNKNOWN_DOOR

