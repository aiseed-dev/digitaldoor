"""設計書 21.8〜21.10: 署名つき資格情報の進み具合(五つの順)、経路の四つの働き(止める、効くまで、照らす)、
扉の最長の時間(register_door の revocation_max_min、Door.失効の最長の分数)、時刻の書き方と時刻の端。

扉の判定との突き合わせ(見立ての時刻に疑似の扉が本当に拒む事)は test_requirements_2a.py の R-AC-14 に置く。
段 2a の実装より先に書いた。まだ無い名前は関数の中で引く。

時刻は分ちょうどの固定の値を使う。秒のある時刻は、発行時刻と「止める」の切り上げの確かめだけに使う(21.14)。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from digitalkey.kanri import Unit, reasons, routes
from kanri_common import CREDENTIAL, GATE, ISSUE_AT, PHYSICAL, R2A, T0, T1, at_minute_up, dt, plus, unix

AT = dt(ISSUE_AT)
REV = "2026-12-10T12:00"
DAY = "2026-12-11T12:00"          # REV + 1440 分
ADM = dict(by="adm", role="管理者")
OPS = dict(by="ops", role="運用")


@pytest.fixture
def u(runit):
    """21.13 の下ごしらえに、最長の分数を持つ扉と群を足す。
    通用口(署名つき資格情報、1440 分)、裏口(署名つき資格情報、60 分)、群 事務所(玄関、通用口)、群 奥(裏口、通用口)。"""
    runit.register_door("通用口", [CREDENTIAL], revocation_max_min=1440, **ADM)
    runit.register_door("裏口", [CREDENTIAL], revocation_max_min=60, **ADM)
    runit.register_group("事務所", ["玄関", "通用口"], **ADM)
    runit.register_group("奥", ["裏口", "通用口"], **ADM)
    return runit


def _row(unit, subject, entity_id, at):
    s = unit.progress(subject, at=dt(at) if isinstance(at, str) else at)
    return {r.実体: r for r in s.行}[entity_id]


def _state(unit, subject, entity_id, at):
    r = _row(unit, subject, entity_id, at)
    return (r.状態, r.効くまで)


def _cred_route():
    return routes.available()[CREDENTIAL]


# ---- 21.9 扉の最長の分数 ----

def test_register_door_max_minutes(runit):
    """R-AC-14 21.9、21.5: revocation_max_min は様式の 失効の最長の分数 に書く。Door.失効の最長の分数 は今の値(int)。
    省いた扉は None。同じ名前の二枚目で省くと None に戻る。"""
    s = runit.register_door("通用口", [CREDENTIAL], revocation_max_min=1440, **ADM)
    assert (s.form, s["失効の最長の分数"]) == ("扉", "1440")
    doors = {d.名前: d for d in runit.doors()}
    assert doors["通用口"].失効の最長の分数 == 1440
    assert doors["玄関"].失効の最長の分数 is None
    s = runit.register_door("通用口", [CREDENTIAL], **ADM)
    assert s["失効の最長の分数"] == ""
    assert {d.名前: d for d in runit.doors()}["通用口"].失効の最長の分数 is None
    runit.register_door("通用口", [CREDENTIAL], revocation_max_min=1, **ADM)
    assert {d.名前: d for d in Unit.open(runit.root, signer=runit.signer).doors()}["通用口"].失効の最長の分数 == 1


@pytest.mark.parametrize("bad", [0, -1, "x", 1.5])
def test_register_door_max_minutes_form(runit, refused, bad):
    """R-AC-14 21.9: 1 より小さい値と整数でない値は 19.3 の 5 で FORM。"""
    refused(runit, reasons.FORM, runit.register_door, "通用口", [CREDENTIAL], revocation_max_min=bad, **ADM)


def test_issue_is_not_refused_by_max_minutes(u, cred):
    """R-AC-14 21.1、21.9: 発行の時には最長の分数で拒まない(長い資格情報も出せる)。"""
    _, got = cred(u, "s2", "通用口")
    assert got.資格情報 is not None and got.実体.扉 == ["通用口"]


# ---- 21.8 効くまで(effective_by) ----

def test_effective_by_door_without_minutes(u, cred):
    """R-AC-14 21.8: 扉の最長の分数が空なら EffectiveBy("期限まで", 終了)、分数は None(19.8 のまま)。"""
    _, got = cred(u, "s1", "玄関")
    e = _cred_route().effective_by(u, got.実体)
    assert (e.種類, e.時刻, e.分数) == ("期限まで", T1, None)


def test_effective_by_long_credential(u, cred):
    """R-AC-14 21.8 の例: 長さが 1440 分より長い 通用口 の資格情報は EffectiveBy("最長の時間まで", "", 1440)。"""
    _, got = cred(u, "s2", "通用口")
    e = _cred_route().effective_by(u, got.実体)
    assert (e.種類, e.時刻, e.分数) == ("最長の時間まで", "", 1440)


def test_effective_by_largest_minutes_of_doors(u, cred):
    """R-AC-14 21.8: 実体の扉の全部に最長の分数があり、長さがそのどれよりも長い時、分数はその中の一番大きい M。"""
    _, got = cred(u, "s2", "奥", "群")
    assert set(got.実体.扉) == {"裏口", "通用口"}
    e = _cred_route().effective_by(u, got.実体)
    assert (e.種類, e.時刻, e.分数) == ("最長の時間まで", "", 1440)


def test_effective_by_any_door_without_minutes(u, cred):
    """R-AC-14 21.8: 実体の扉に最長の分数の無い扉が一つでもあれば 期限まで(終了)。"""
    _, got = cred(u, "s2", "事務所", "群")
    e = _cred_route().effective_by(u, got.実体)
    assert (e.種類, e.時刻, e.分数) == ("期限まで", T1, None)


@pytest.mark.parametrize("minutes,kind", [(1440, "期限まで"), (1441, "最長の時間まで")])
def test_effective_by_length_edge(u, cred, minutes, kind):
    """R-AC-14 21.8、21.10: 長さがちょうど M × 60 秒は 期限まで(扉は古さで拒まない)。1 分長ければ 最長の時間まで。"""
    end = plus(T0, minutes=minutes)
    _, got = cred(u, "s2", "通用口", until=end)
    e = _cred_route().effective_by(u, got.実体)
    assert e.種類 == kind
    assert (e.時刻, e.分数) == ((end, None) if kind == "期限まで" else ("", 1440))


# ---- 21.8 止める(stop) ----

def test_stop_door_without_minutes_is_end(u, cred):
    """R-AC-22 21.8: 最長の分数の無い扉では、止めるは Stop("待ち", 終了, "")(段 1 と同じ)。"""
    _, got = cred(u, "s1", "玄関")
    s = _cred_route().stop(u, got.実体, at=dt(REV))
    assert (s.結果, s.効くまで, s.作業) == ("待ち", T1, "")


def test_stop_uses_max_minutes(u, cred):
    """R-AC-14 21.8: 止めるは、実体の扉の列の全部の T(扉, at) の一番遅い物。通用口(1440 分)は at + 1 日。"""
    _, got = cred(u, "s2", "通用口")
    s = _cred_route().stop(u, got.実体, at=dt(REV))
    assert (s.結果, s.効くまで, s.作業) == ("待ち", DAY, "")


def test_stop_latest_of_all_doors(u, cred):
    """R-AC-14 21.8: 扉の列に最長の分数の無い扉(玄関)があれば、その扉の T は終了で、一番遅いのは終了。
    裏口(60 分)と通用口(1440 分)なら 1440 分の方。"""
    _, a = cred(u, "s2", "事務所", "群")
    assert _cred_route().stop(u, a.実体, at=dt(REV)).効くまで == T1
    _, b = cred(u, "s2", "奥", "群")
    assert _cred_route().stop(u, b.実体, at=dt(REV)).効くまで == DAY


def test_stop_rounds_seconds_up(u, cred):
    """R-AC-14 21.8、21.14: 時刻の書き方。at に秒があれば、T を地方時に直し、次の分に切り上げる。"""
    _, got = cred(u, "s2", "通用口")
    s = _cred_route().stop(u, got.実体, at=datetime(2026, 12, 10, 12, 0, 30))
    assert s.効くまで == "2026-12-11T12:01"
    assert at_minute_up(unix(REV) + 30 + 1440 * 60) == "2026-12-11T12:01"


def test_stop_end_before_max_time(u, cred):
    """R-AC-14 21.8: T は min(終了, t + M × 60)。終了が先なら終了。"""
    end = "2026-12-11T00:00"            # T0 から 1440 分より長い。REV + 1 日より前
    _, got = cred(u, "s2", "通用口", until=end)
    assert _cred_route().effective_by(u, got.実体).種類 == "最長の時間まで"
    assert _cred_route().stop(u, got.実体, at=dt(REV)).効くまで == end
    rev = u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    assert _state(u, "s2", got.実体.番号, REV) == ("待ち", end)
    assert rev.実体_失効[0]["効くまで"] == end


def test_stop_after_end(u, cred):
    """R-AC-22 21.8: at >= 終了 なら Stop("止まった", "", "")(19.8 のまま)。"""
    _, got = cred(u, "s2", "通用口")
    s = _cred_route().stop(u, got.実体, at=dt(T1))
    assert (s.結果, s.効くまで, s.作業) == ("止まった", "", "")


def test_stop_writes_no_slip(u, cred):
    """R-AC-22 21.8、19.8: 経路は伝票を書かない。"""
    _, got = cred(u, "s2", "通用口")
    n = u.ledger.count()
    _cred_route().stop(u, got.実体, at=dt(REV))
    _cred_route().effective_by(u, got.実体)
    u.reconcile(got.実体.番号, at=dt(REV))
    assert u.ledger.count() == n


# ---- 21.8 の例 ----

def test_example_21_8_door_without_minutes(u, grant, forms):
    """R-AC-15 R-AC-22 21.8 の例(扉 玄関、最長の分数は空): revoke_subject の直後は E1 待ち(2027-01-01T00:00)、済み 偽。
    E3 の回収と record_delivery("玄関", 1) の後、2026-12-11T09:00 では E1 止まった、済み 真。confirm_stopped が通る。"""
    pid, _, _ = grant(u)
    kw = dict(by="adm", role="管理者", at=AT)
    e1 = u.issue_entity(pid, CREDENTIAL, **kw).実体
    u.issue_entity(pid, GATE, **kw)
    e3 = u.issue_entity(pid, PHYSICAL, key_number="K-7", **kw).実体
    rev = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert rev.失効_一覧["世代"] == "1"
    assert _state(u, "s1", e1.番号, REV) == ("待ち", T1)
    assert u.progress("s1", at=dt(REV)).済み is False
    u.return_physical(e3.番号, "回収", by="adm", role="名簿の係")
    u.record_delivery("玄関", 1, **OPS, at="2026-12-11T09:00")
    s = u.progress("s1", at=dt("2026-12-11T09:00"))
    assert ({r.実体: r.状態 for r in s.行}[e1.番号], s.済み) == ("止まった", True)
    assert _row(u, "s1", e1.番号, "2026-12-11T09:00").効くまで == ""
    assert u.confirm_stopped("s1", **ADM, at=dt("2026-12-11T09:00")).form == "失効_確認"


def test_example_21_8_door_with_minutes(u, cred):
    """R-AC-14 21.8 の例: 通用口(1440 分)の s2 の資格情報を 2026-12-10T12:00 に revoke_subject すると、実体_失効は 待ち、
    効くまで 2026-12-11T12:00。届いた記録が無い間、11:59 までは 待ち(2026-12-11T12:00)、12:00 からは 止まった。
    record_delivery("通用口", 1) の後は、その時刻の前でも 止まった。effective_by は ("最長の時間まで", "", 1440)。"""
    _, got = cred(u, "s2", "通用口")
    eid = got.実体.番号
    rev = u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    x, = rev.実体_失効
    assert (x["結果"], x["効くまで"]) == ("待ち", DAY)
    assert rev.失効_一覧["世代"] == "1"
    assert _state(u, "s2", eid, REV) == ("待ち", DAY)
    assert _state(u, "s2", eid, "2026-12-11T11:59") == ("待ち", DAY)
    assert _state(u, "s2", eid, DAY) == ("止まった", "")
    assert u.progress("s2", at=dt(DAY)).済み is True
    u.record_delivery("通用口", 1, **OPS, at="2026-12-10T15:00")
    assert _state(u, "s2", eid, REV) == ("止まった", "")
    e = _cred_route().effective_by(u, got.実体)
    assert (e.種類, e.時刻, e.分数) == ("最長の時間まで", "", 1440)


# ---- 21.8 進み具合の五つの順 ----

def test_order_1_end_comes_first(u, cred):
    """R-AC-22 21.8 の 1: 終了を過ぎた(at >= 終了)は、止めてあって届いていなくても 止まった。効くまでは空。"""
    _, got = cred(u, "s1", "玄関")
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert _state(u, "s1", got.実体.番号, T1) == ("止まった", "")
    assert _state(u, "s1", got.実体.番号, plus(T1, minutes=-1)) == ("待ち", T1)


def test_order_2_not_stopped(u, cred):
    """R-AC-22 21.8 の 2: 実体_失効が無く、権限も 失効 でなければ 効いている。一覧があっても、届いた記録があっても同じ。"""
    _, got = cred(u, "s2", "通用口")
    u.publish_revocations(**ADM, at=dt(REV))
    u.record_delivery("通用口", 1, **OPS, at=REV)
    assert _state(u, "s2", got.実体.番号, plus(REV, days=2)) == ("効いている", "")


def _revoke_permission_directly(unit, pid, subject):
    """権限_失効をテストが台帳に直に書く(実体_失効も一覧も書かない)。19.13 の「直に書く」と同じ形。"""
    unit.ledger.roles = {"adm": {"管理者"}}
    unit.ledger.append("権限_失効", {"権限": pid, "主体": subject, "範囲": "一つの権限", "理由": "試し", "役": "管理者"},
                       issuer="adm", to=subject, basis=[pid])


def test_order_3_stopped_but_in_no_list(u, cred):
    """R-AC-22 21.8 の 3: 止めてあるが、その識別を入れた一覧がまだ一枚も無い → 待ち、効くまでは実体の終了
    (扉に最長の分数があっても)。照らすは 違い「失効の一覧に入っていない」。一覧を作ると 5 に移る。"""
    pid, got = cred(u, "s2", "通用口")
    u.publish_revocations(**ADM, at=dt(plus(REV, minutes=-60)))      # その識別を入れていない一覧
    _revoke_permission_directly(u, pid, "s2")
    eid = got.実体.番号
    assert _state(u, "s2", eid, REV) == ("待ち", T1)
    r = u.reconcile(eid, at=dt(REV))
    assert (r.結果, r.詳細) == ("違い", "失効の一覧に入っていない")
    u.publish_revocations(**ADM, at=dt(REV))
    assert _state(u, "s2", eid, REV) == ("待ち", DAY)


def test_order_4_all_doors_delivered(u, cred):
    """R-AC-22 21.8 の 4: 実体の扉の列の全部で door_generation(扉) >= g0 なら、期限の前でも 止まった。"""
    _, got = cred(u, "s2", "事務所", "群")
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    for door in got.実体.扉:
        u.record_delivery(door, 1, **OPS, at=REV)
    assert _state(u, "s2", got.実体.番号, REV) == ("止まった", "")


@pytest.mark.parametrize("first,expect", [("玄関", DAY), ("通用口", T1)])
def test_order_5_latest_of_undelivered_doors(u, cred, first, expect):
    """R-AC-14 R-AC-22 21.8 の 5: 効くまでは、届いていない扉ごとの T(扉, t0) のうち一番遅い物。
    玄関(空)と通用口(1440 分)の実体: 届いた記録が無ければ終了。玄関だけ届けば通用口の T、通用口だけ届けば終了。"""
    _, got = cred(u, "s2", "事務所", "群")
    eid = got.実体.番号
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    assert _state(u, "s2", eid, REV) == ("待ち", T1)
    u.record_delivery(first, 1, **OPS, at=REV)
    assert _state(u, "s2", eid, REV) == ("待ち", expect)
    other = "通用口" if first == "玄関" else "玄関"
    u.record_delivery(other, 1, **OPS, at=REV)
    assert _state(u, "s2", eid, REV) == ("止まった", "")


def test_order_5_generation_before_g0_does_not_count(u, cred):
    """R-AC-22 21.8 の 4、5: g0 より前の世代の届いた記録は数えない。g0 より後の世代は数える(>= g0)。"""
    _, got = cred(u, "s2", "通用口")
    eid = got.実体.番号
    u.publish_revocations(**ADM, at=dt(plus(REV, minutes=-60)))      # 世代 1(識別は空)
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))                # 世代 2 = g0
    u.publish_revocations(**ADM, at=dt(plus(REV, minutes=60)))       # 世代 3
    u.record_delivery("通用口", 1, **OPS, at=REV)
    assert _state(u, "s2", eid, REV) == ("待ち", DAY)
    u.record_delivery("通用口", 3, **OPS, at=REV)
    assert _state(u, "s2", eid, REV) == ("止まった", "")


def test_order_5_t0_is_first_list_with_the_id(u, cred):
    """R-AC-14 21.8: g0 と t0 は、その識別を入れた一覧のうち台帳に書いた順で最初の一枚。後の一覧の発行時刻では読まない。"""
    _, got = cred(u, "s2", "通用口")
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    u.publish_revocations(**ADM, at=dt(plus(REV, minutes=120)))
    assert u.revocations()[-1].識別 == [got.実体.識別]
    assert _state(u, "s2", got.実体.番号, REV) == ("待ち", DAY)


def test_order_5_reads_current_door_minutes(u, cred):
    """R-AC-14 21.8、21.14: T は扉の今の最長の分数で読む。実体_失効の 効くまで は止めた時点の見立てとして残る。"""
    _, got = cred(u, "s2", "通用口")
    eid = got.実体.番号
    rev = u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    u.register_door("通用口", [CREDENTIAL], revocation_max_min=60, **ADM)
    assert _state(u, "s2", eid, REV) == ("待ち", "2026-12-10T13:00")
    assert u.ledger.get(rev.実体_失効[0].id)["効くまで"] == DAY
    u.register_door("通用口", [CREDENTIAL], **ADM)                  # 省くと空に戻る
    assert _state(u, "s2", eid, REV) == ("待ち", T1)


def test_order_5_retired_door_stays(u, cred, refused):
    """R-AC-22 21.8、21.14: 撤去した扉も実体の扉の列に残り、届いた記録が無ければ 5 に当たる。"""
    _, got = cred(u, "s2", "事務所", "群")
    eid = got.実体.番号
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    u.record_delivery("玄関", 1, **OPS, at=REV)
    u.register_door("通用口", [CREDENTIAL], revocation_max_min=1440, state="撤去", **ADM)
    refused(u, reasons.UNKNOWN_DOOR, u.record_delivery, "通用口", 1, **OPS, at=REV)
    assert _state(u, "s2", eid, REV) == ("待ち", DAY)
    assert _state(u, "s2", eid, DAY) == ("止まった", "")


def test_progress_reads_ledger_issued_at_same_second(u, cred):
    """R-AC-14 21.6、21.8: 同じ at で二人を続けて止めると、二枚目の一覧の発行時刻は 1 秒進み、二人目の進み具合は
    その発行時刻から読む(秒があるので次の分に切り上げ)。二人目の実体_失効の 効くまで は止めた時点の見立て(at から)のまま。"""
    _, a = cred(u, "s1", "通用口")
    _, b = cred(u, "s2", "通用口")
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    rev = u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    assert [r.発行時刻 for r in u.revocations()] == [unix(REV), unix(REV) + 1]
    assert rev.実体_失効[0]["効くまで"] == DAY
    assert _state(u, "s1", a.実体.番号, REV) == ("待ち", DAY)
    assert _state(u, "s2", b.実体.番号, REV) == ("待ち", "2026-12-11T12:01")
    assert _state(u, "s2", b.実体.番号, DAY) == ("待ち", "2026-12-11T12:01")
    assert _state(u, "s2", b.実体.番号, "2026-12-11T12:01") == ("止まった", "")


def test_done_before_end_after_delivery(u, cred, refused):
    """R-AC-22 21.8: 全部の扉に届いた記録がそろえば、期限の前でも 済み になり、confirm_stopped が通る。"""
    _, got = cred(u, "s2", "事務所", "群")
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    refused(u, reasons.NOT_ALL_STOPPED, u.confirm_stopped, "s2", **ADM, at=dt(REV), detail=got.実体.番号)
    u.record_delivery("玄関", 1, **OPS, at=REV)
    u.record_delivery("通用口", 1, **OPS, at=REV)
    assert u.progress("s2", at=dt(REV)).済み is True
    assert u.confirm_stopped("s2", **ADM, at=dt(REV)).form == "失効_確認"


# ---- 21.8 照らす(reconcile) ----

def test_reconcile_not_stopped(u, cred):
    """R-AC-22 21.8: 止めていない物は 照らせない(段 1 の言葉のまま)。"""
    _, got = cred(u, "s2", "通用口")
    r = u.reconcile(got.実体.番号, at=dt(REV))
    assert (r.結果, r.詳細) == ("照らせない", "扉の記録の受け取りは段 2")


def test_reconcile_not_delivered_lists_doors_in_entity_order(u, cred):
    """R-AC-22 21.8、21.14: 止めてあって 待ち、一覧がある → 違い「扉に届いていない: <届いていない扉を実体の扉の列の順に「, 」で>」。
    届いて止まった → 一致。"""
    _, got = cred(u, "s2", "事務所", "群")
    e = got.実体
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    r = u.reconcile(e.番号, at=dt(REV))
    assert (r.結果, r.詳細) == ("違い", "扉に届いていない: " + ", ".join(e.扉))
    u.record_delivery(e.扉[0], 1, **OPS, at=REV)
    r = u.reconcile(e.番号, at=dt(REV))
    assert (r.結果, r.詳細) == ("違い", f"扉に届いていない: {e.扉[1]}")
    u.record_delivery(e.扉[1], 1, **OPS, at=REV)
    r = u.reconcile(e.番号, at=dt(REV))
    assert (r.結果, r.詳細) == ("一致", "")


def test_reconcile_stopped_by_time_or_end(u, cred):
    """R-AC-22 21.8: 止めてあって、効くまでの時刻を過ぎて 止まった、または終了を過ぎて 止まった → 一致。
    止めていなくて終了を過ぎた物は 照らせない。"""
    _, a = cred(u, "s2", "通用口")
    _, b = cred(u, "s1", "通用口")
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    r = u.reconcile(a.実体.番号, at=dt(DAY))
    assert (r.結果, r.詳細) == ("一致", "")
    r = u.reconcile(a.実体.番号, at=dt(T1))
    assert (r.結果, r.詳細) == ("一致", "")
    r = u.reconcile(b.実体.番号, at=dt(T1))
    assert (r.結果, r.詳細) == ("照らせない", "扉の記録の受け取りは段 2")


def test_reconcile_unknown_entity(u, refused):
    """R-AC-22 19.8: 照らすの相手が無ければ ENTITY_UNKNOWN(段 1 のまま)。"""
    refused(u, reasons.ENTITY_UNKNOWN, u.reconcile, "x", at=dt(REV))
