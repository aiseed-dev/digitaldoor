"""設計書 20.9: 台帳だけでできる照らし合わせ(知らせ)。表の一行ずつ、見る状態、境目の値、並び。

知らせは一つの機器に複数出るので、言葉と機器で絞って比べる(20.13)。`at` は固定の値を渡す。
"""
from __future__ import annotations

from datetime import timezone

import pytest

from kanri_common import RB, dt, findings

ADM = dict(by="adm", role="管理者")
NOW = "2026-12-20T12:00"
ALL_STATES = ["準備", "使用", "休止", "紛失", "廃棄"]


def _device_in(u, name, state, kind="PC", **kw):
    """状態 state の機器を作る(紛失と廃棄は、登録の後に change_device で変える)。"""
    first = state if state in ("準備", "使用", "休止") else "使用"
    u.register_device(name, kind, first, **kw, **ADM)
    if first != state:
        u.change_device(name, reason="試し", state=state, at=dt(NOW), **ADM)


def _words(u, at, device):
    return [w for _, w, _ in findings(u, at, device=device)]


def test_example_20_9(make_unit):
    """R-NW-02 R-NW-03 R-NW-05 20.9 の例: 設定の無い置き場で、使用のルーター(責任者、区画、更新の期限が空、確かめなし)の知らせは
    F_NO_OWNER、F_NO_SEGMENT、F_NO_SUPPORT_END、F_NO_INSTALL_CHECK の 4 件で、この順。"""
    u = make_unit()
    u.register_device("ルーター1", "ルーター", "使用", **ADM)
    assert findings(u, NOW) == [("ルーター1", RB.F_NO_OWNER, ""), ("ルーター1", RB.F_NO_SEGMENT, ""),
                                ("ルーター1", RB.F_NO_SUPPORT_END, ""), ("ルーター1", RB.F_NO_INSTALL_CHECK, "")]


def test_order_by_device_registration_then_table(dunit):
    """20.9: 並びは機器_登録を台帳に書いた順、同じ機器の中では表の順。Finding の型で返す。"""
    got = dunit.device_findings(at=dt(NOW))
    assert all(type(f).__name__ == "Finding" for f in got)
    assert [(f.機器, f.知らせ, f.詳細) for f in got] == [
        ("ルーター1", RB.F_NO_OWNER, ""), ("ルーター1", RB.F_NO_SEGMENT, ""),
        ("ルーター1", RB.F_NO_SUPPORT_END, ""), ("ルーター1", RB.F_NO_INSTALL_CHECK, ""),
        ("玄関の錠", RB.F_NO_OWNER, ""), ("玄関の錠", RB.F_NO_SUPPORT_END, ""), ("玄関の錠", RB.F_NO_INSTALL_CHECK, ""),
        ("複合機1", RB.F_NO_OWNER, ""), ("複合機1", RB.F_NO_SEGMENT, ""),
        ("複合機1", RB.F_NO_SUPPORT_END, ""), ("複合機1", RB.F_NO_INSTALL_CHECK, ""),
    ]


def test_no_devices_no_findings(make_unit):
    """20.9: 機器が無ければ空の列。"""
    assert make_unit().device_findings(at=dt(NOW)) == []


def test_findings_do_not_write(dunit):
    """20.9: 知らせは拒否ではなく、台帳に何も書かない。"""
    n = dunit.ledger.count()
    dunit.device_findings(at=dt(NOW))
    assert dunit.ledger.count() == n


# ---- 責任者 ----

@pytest.mark.parametrize("state", ALL_STATES)
def test_no_owner_states(dunit, state):
    """R-NW-05 20.9 F_NO_OWNER: 責任者が空。見る状態は 準備、使用、休止、紛失(廃棄には出さない)。詳細は空。"""
    _device_in(dunit, "PC1", state)
    want = [("PC1", RB.F_NO_OWNER, "")] if state != "廃棄" else []
    assert findings(dunit, NOW, RB.F_NO_OWNER, "PC1") == want


@pytest.mark.parametrize("state", ALL_STATES)
@pytest.mark.parametrize("subject_state", ["停止", "退出"])
def test_owner_inactive_states(dunit, state, subject_state):
    """R-NW-05 20.9 F_OWNER_INACTIVE: 責任者の主体の状態が 有効 でない。詳細は責任者の識別。廃棄には出さない。"""
    _device_in(dunit, "PC1", state, owner="t1")
    assert findings(dunit, NOW, RB.F_OWNER_INACTIVE, "PC1") == []
    dunit.change_subject("t1", reason="試し", state=subject_state, by="adm", role="名簿の係")
    want = [("PC1", RB.F_OWNER_INACTIVE, "t1")] if state != "廃棄" else []
    assert findings(dunit, NOW, RB.F_OWNER_INACTIVE, "PC1") == want
    assert findings(dunit, NOW, RB.F_NO_OWNER, "PC1") == []


# ---- 区画 ----

@pytest.mark.parametrize("state", ALL_STATES)
def test_no_segment_only_in_use(dunit, state):
    """R-NW-02 20.9 F_NO_SEGMENT: 区画が空。見る状態は 使用 だけ。"""
    _device_in(dunit, "PC1", state)
    want = [("PC1", RB.F_NO_SEGMENT, "")] if state == "使用" else []
    assert findings(dunit, NOW, RB.F_NO_SEGMENT, "PC1") == want


def test_no_segment_retired_segment(dunit):
    """R-NW-02 20.9 F_NO_SEGMENT: 区画が 廃止 なら、詳細に区画の名。"""
    _device_in(dunit, "PC1", "使用", segment="来客")
    assert findings(dunit, NOW, RB.F_NO_SEGMENT, "PC1") == []
    dunit.register_segment("来客", state="廃止", **ADM)
    assert findings(dunit, NOW, RB.F_NO_SEGMENT, "PC1") == [("PC1", RB.F_NO_SEGMENT, "来客")]


def test_mixed_segment(dunit):
    """R-NW-02 20.9 F_MIXED_SEGMENT: 扉の系統が いいえ で、同じ区画に、使用で扉の系統が はい の機器がある。詳細は区画の名。
    扉の系統の機器には出さない。"""
    dunit.change_device("複合機1", reason="移した", segment="扉", **ADM)
    assert findings(dunit, NOW, RB.F_MIXED_SEGMENT) == [("複合機1", RB.F_MIXED_SEGMENT, "扉")]


@pytest.mark.parametrize("state", ["準備", "休止", "紛失", "廃棄"])
def test_mixed_segment_needs_door_device_in_use(dunit, state):
    """R-NW-02 20.9 F_MIXED_SEGMENT: 扉の系統の機器が 使用 でなければ挙げない。"""
    dunit.change_device("複合機1", reason="移した", segment="扉", **ADM)
    dunit.change_device("玄関の錠", reason="試し", state=state, at=dt(NOW), **ADM)
    assert findings(dunit, NOW, RB.F_MIXED_SEGMENT) == []


@pytest.mark.parametrize("state", ["準備", "休止", "紛失"])
def test_mixed_segment_only_for_device_in_use(dunit, state):
    """R-NW-02 20.9 F_MIXED_SEGMENT: 見る状態は 使用 だけ(ほかの機器の側)。"""
    _device_in(dunit, "PC1", state, segment="扉")
    assert findings(dunit, NOW, RB.F_MIXED_SEGMENT, "PC1") == []


def test_mixed_segment_compares_retired_segment_by_name(dunit):
    """R-NW-02 20.14: F_MIXED_SEGMENT は区画の状態(使用、廃止)を問わず、同じ区画の名で比べる。"""
    dunit.change_device("複合機1", reason="移した", segment="扉", **ADM)
    dunit.register_segment("扉", state="廃止", **ADM)
    assert findings(dunit, NOW, RB.F_MIXED_SEGMENT) == [("複合機1", RB.F_MIXED_SEGMENT, "扉")]


# ---- 更新の期限 ----

@pytest.mark.parametrize("state", ALL_STATES)
def test_no_support_end_only_in_use(dunit, state):
    """R-NW-03 20.9 F_NO_SUPPORT_END: 更新の期限が空。見る状態は 使用 だけ。"""
    _device_in(dunit, "PC1", state)
    want = [("PC1", RB.F_NO_SUPPORT_END, "")] if state == "使用" else []
    assert findings(dunit, NOW, RB.F_NO_SUPPORT_END, "PC1") == want


@pytest.mark.parametrize("at,ended", [
    ("2027-03-31T00:00", False), ("2027-03-31T23:59", False), ("2027-04-01T00:00", True), ("2028-01-01T00:00", True),
])
def test_support_ended_boundary(dunit, at, ended):
    """R-NW-03 20.9 F_SUPPORT_ENDED: 今日 > 期限(期限の日は内)。詳細は期限。"""
    _device_in(dunit, "PC1", "使用", support_until="2027-03-31")
    want = [("PC1", RB.F_SUPPORT_ENDED, "2027-03-31")] if ended else []
    assert findings(dunit, at, RB.F_SUPPORT_ENDED, "PC1") == want


@pytest.mark.parametrize("state", ["準備", "休止", "紛失", "廃棄"])
def test_support_ended_only_in_use(dunit, state):
    """R-NW-03 20.9: 期限を過ぎた知らせも、見る状態は 使用 だけ。"""
    _device_in(dunit, "PC1", state, support_until="2027-03-31")
    dunit.set_setting("更新の期限の知らせの日数", "30", reason="試し", **ADM)
    assert findings(dunit, "2027-04-01T00:00", RB.F_SUPPORT_ENDED, "PC1") == []
    assert findings(dunit, "2027-03-31T00:00", RB.F_SUPPORT_NEAR, "PC1") == []


def test_today_is_local_date_of_aware_at(dunit):
    """R-NW-03 20.9: 「今日」は at の地方時の日付(時差つきは地方時に直す)。"""
    _device_in(dunit, "PC1", "使用", support_until="2027-03-31")
    after = dt("2027-04-01T00:30").astimezone().astimezone(timezone.utc)
    within = dt("2027-03-31T23:30").astimezone().astimezone(timezone.utc)
    assert findings(dunit, after, RB.F_SUPPORT_ENDED, "PC1") == [("PC1", RB.F_SUPPORT_ENDED, "2027-03-31")]
    assert findings(dunit, within, RB.F_SUPPORT_ENDED, "PC1") == []


@pytest.mark.parametrize("at,near", [
    ("2027-02-28T12:00", False),   # 残り 31 日
    ("2027-03-01T00:00", True),    # 残り 30 日
    ("2027-03-15T12:00", True),
    ("2027-03-31T23:59", True),    # 残り 0 日
    ("2027-04-01T00:00", False),   # 過ぎた
])
def test_support_near_boundary(dunit, at, near):
    """R-NW-03 20.9 F_SUPPORT_NEAR: 設定 更新の期限の知らせの日数 が N で、0 <= 期限 - 今日 <= N 日。詳細は期限。"""
    _device_in(dunit, "PC1", "使用", support_until="2027-03-31")
    dunit.set_setting("更新の期限の知らせの日数", "30", reason="管理者が決めた", **ADM)
    want = [("PC1", RB.F_SUPPORT_NEAR, "2027-03-31")] if near else []
    assert findings(dunit, at, RB.F_SUPPORT_NEAR, "PC1") == want


def test_support_near_needs_setting(dunit):
    """R-NW-03 20.9: 設定を書いていない間(最後の設定の値が空の時を含む)は F_SUPPORT_NEAR を出さない。"""
    _device_in(dunit, "PC1", "使用", support_until="2027-03-31")
    assert findings(dunit, "2027-03-31T12:00", RB.F_SUPPORT_NEAR) == []
    dunit.set_setting("更新の期限の知らせの日数", "30", reason="管理者が決めた", **ADM)
    assert len(findings(dunit, "2027-03-31T12:00", RB.F_SUPPORT_NEAR)) == 1
    dunit.set_setting("更新の期限の知らせの日数", "", reason="知らせを止める", **ADM)
    assert findings(dunit, "2027-03-31T12:00", RB.F_SUPPORT_NEAR) == []


# ---- 確かめ ----

@pytest.mark.parametrize("state", ALL_STATES)
def test_no_install_check_only_in_use(dunit, state):
    """R-NW-03 20.9 F_NO_INSTALL_CHECK: 場面が 据え付け の確かめが一枚も無い。見る状態は 使用 だけ。"""
    _device_in(dunit, "PC1", state)
    want = [("PC1", RB.F_NO_INSTALL_CHECK, "")] if state == "使用" else []
    assert findings(dunit, NOW, RB.F_NO_INSTALL_CHECK, "PC1") == want


def test_install_check_clears(dunit):
    """R-NW-03 20.9: 据え付けの確かめを書くと消える。点検の確かめでは消えない。"""
    _device_in(dunit, "PC1", "使用")
    dunit.check_device("PC1", "点検", {"版": "良い"}, at="2026-12-01T09:00", **ADM)
    assert len(findings(dunit, NOW, RB.F_NO_INSTALL_CHECK, "PC1")) == 1
    dunit.check_device("PC1", "据え付け", {"初めのパスワード": "良い"}, at="2026-12-02T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_NO_INSTALL_CHECK, "PC1") == []


def test_bad_item_per_item_in_form_order(dunit):
    """R-NW-02 R-NW-03 R-NW-04 20.9 F_BAD_ITEM: 確かめの項目の今の値が 悪い。項目ごとに一つ、様式の項目の順。詳細は項目の名。"""
    _device_in(dunit, "PC1", "使用")
    dunit.check_device("PC1", "点検", {"網から外した": "悪い", "版": "悪い", "初めのパスワード": "悪い", "区画": "良い"},
                       at="2026-12-01T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "PC1") == [
        ("PC1", RB.F_BAD_ITEM, "初めのパスワード"), ("PC1", RB.F_BAD_ITEM, "版"), ("PC1", RB.F_BAD_ITEM, "網から外した")]


def test_bad_item_current_value_is_last_nonempty(dunit):
    """R-NW-02 20.7、20.9: 項目の今の値は、その項目が空でない最後の確かめ(台帳に書いた順)の値。"""
    _device_in(dunit, "PC1", "使用")
    dunit.check_device("PC1", "点検", {"区画": "悪い", "版": "悪い"}, at="2026-12-02T09:00", **ADM)
    # 日時が前でも、台帳に後に書いた物が今の値
    dunit.check_device("PC1", "点検", {"区画": "良い", "版": ""}, at="2026-12-01T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "PC1") == [("PC1", RB.F_BAD_ITEM, "版")]
    dunit.check_device("PC1", "点検", {"版": "当たらない"}, at="2026-12-03T09:00", **ADM)
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "PC1") == []


@pytest.mark.parametrize("state", ["準備", "休止", "紛失"])
def test_bad_item_only_in_use(dunit, state):
    """20.9: F_BAD_ITEM の見る状態は 使用 だけ。"""
    _device_in(dunit, "PC1", "使用")
    dunit.check_device("PC1", "点検", {"版": "悪い"}, at="2026-12-01T09:00", **ADM)
    dunit.change_device("PC1", reason="試し", state=state, at=dt(NOW), **ADM)
    assert findings(dunit, NOW, RB.F_BAD_ITEM, "PC1") == []


def test_check_overdue_boundary(dunit):
    """R-NW-04 20.9 F_CHECK_OVERDUE: 設定 点検の間の日数 が N で、一番遅い据え付けか点検の日時から at まで N 日を超える
    (ちょうど N 日は挙げない)。詳細はその日時。"""
    _device_in(dunit, "PC1", "使用")
    dunit.set_setting("点検の間の日数", "90", reason="管理者が決めた", **ADM)
    dunit.check_device("PC1", "据え付け", {"初めのパスワード": "良い"}, at="2026-12-01T09:00", **ADM)
    assert findings(dunit, "2027-03-01T09:00", RB.F_CHECK_OVERDUE, "PC1") == []
    assert findings(dunit, "2027-03-01T09:01", RB.F_CHECK_OVERDUE, "PC1") == \
        [("PC1", RB.F_CHECK_OVERDUE, "2026-12-01T09:00")]


def test_check_overdue_uses_latest_datetime_not_ledger_order(dunit):
    """R-NW-04 20.14: 「最後の確かめ」は据え付けと点検の日時のうち一番遅い物(台帳に書いた順ではない)。廃棄の確かめは数えない。"""
    _device_in(dunit, "PC1", "使用")
    dunit.set_setting("点検の間の日数", "30", reason="管理者が決めた", **ADM)
    dunit.check_device("PC1", "点検", {"版": "良い"}, at="2027-01-10T09:00", **ADM)
    dunit.check_device("PC1", "据え付け", {"初めのパスワード": "良い"}, at="2026-12-01T09:00", **ADM)
    dunit.check_device("PC1", "廃棄", {"中に残る物": "良い", "網から外した": "良い"}, at="2027-03-01T09:00", **ADM)
    assert findings(dunit, "2027-02-09T09:00", RB.F_CHECK_OVERDUE, "PC1") == []
    assert findings(dunit, "2027-02-09T09:01", RB.F_CHECK_OVERDUE, "PC1") == \
        [("PC1", RB.F_CHECK_OVERDUE, "2027-01-10T09:00")]


def test_check_overdue_without_checks(dunit):
    """R-NW-04 20.9: 据え付けか点検の確かめが一枚も無い時も挙げる(詳細 確かめなし)。設定が無ければ出さない。"""
    _device_in(dunit, "PC1", "使用")
    assert findings(dunit, NOW, RB.F_CHECK_OVERDUE, "PC1") == []
    dunit.set_setting("点検の間の日数", "90", reason="管理者が決めた", **ADM)
    assert findings(dunit, NOW, RB.F_CHECK_OVERDUE, "PC1") == [("PC1", RB.F_CHECK_OVERDUE, "確かめなし")]


@pytest.mark.parametrize("state", ["準備", "休止", "紛失", "廃棄"])
def test_check_overdue_only_in_use(dunit, state):
    """R-NW-04 20.9: F_CHECK_OVERDUE の見る状態は 使用 だけ。"""
    dunit.set_setting("点検の間の日数", "90", reason="管理者が決めた", **ADM)
    _device_in(dunit, "PC1", state)
    assert findings(dunit, NOW, RB.F_CHECK_OVERDUE, "PC1") == []


# ---- 管理者の口 ----

def test_shared_account(dunit, access):
    """R-RS-04 20.9 F_SHARED_ACCOUNT: 止まっていない管理者の口のうち、同じ口の名で主体が二人以上。口の名ごとに一つ、
    その口の名の最初の実体_発行を書いた順。同じ人の二つは数えない。"""
    access(dunit, "t1", "複合機1", "service")
    access(dunit, "t1", "複合機1", "admin")
    access(dunit, "t1", "複合機1", "admin")
    assert findings(dunit, NOW, RB.F_SHARED_ACCOUNT) == []
    access(dunit, "t2", "複合機1", "admin")
    access(dunit, "v1", "複合機1", "service")
    assert findings(dunit, NOW, RB.F_SHARED_ACCOUNT) == [
        ("複合機1", RB.F_SHARED_ACCOUNT, "service"), ("複合機1", RB.F_SHARED_ACCOUNT, "admin")]


def test_shared_account_counts_until_removed(dunit, access):
    """R-RS-04 20.9: 進み具合の状態が 止まった でない物を数える。失効しても削除の伝票が出るまでは数える。"""
    _, a = access(dunit, "t1", "複合機1")
    _, b = access(dunit, "t2", "複合機1")
    dunit.revoke_subject("t2", "異動", **ADM, at=dt(NOW))
    assert len(findings(dunit, NOW, RB.F_SHARED_ACCOUNT)) == 1
    dunit.remove_access(b.番号, "パスワードを替えた", **ADM)
    assert findings(dunit, NOW, RB.F_SHARED_ACCOUNT) == []


@pytest.mark.parametrize("state", ["準備", "休止"])
def test_shared_account_only_in_use(dunit, access, state):
    """R-RS-04 20.9: F_SHARED_ACCOUNT の見る状態は 使用 だけ。"""
    access(dunit, "t1", "複合機1")
    access(dunit, "t2", "複合機1")
    dunit.change_device("複合機1", reason="試し", state=state, **ADM)
    assert findings(dunit, NOW, RB.F_SHARED_ACCOUNT) == []


@pytest.mark.parametrize("state", ALL_STATES)
def test_no_disposal_check_only_retired(dunit, state):
    """20.9 F_NO_DISPOSAL_CHECK: 場面が 廃棄 の確かめが一枚も無い。見る状態は 廃棄 だけ。詳細は空。"""
    _device_in(dunit, "PC1", state)
    want = [("PC1", RB.F_NO_DISPOSAL_CHECK, "")] if state == "廃棄" else []
    assert findings(dunit, NOW, RB.F_NO_DISPOSAL_CHECK, "PC1") == want


def test_retired_device_findings(dunit):
    """20.9: 廃棄の機器に出るのは F_NO_DISPOSAL_CHECK と F_ACCESS_LEFT だけ。廃棄の確かめの後は何も出ない。"""
    _device_in(dunit, "PC1", "廃棄")
    assert _words(dunit, NOW, "PC1") == [RB.F_NO_DISPOSAL_CHECK]
    dunit.check_device("PC1", "廃棄", {"中に残る物": "良い", "網から外した": "良い"}, at="2026-12-21T09:00", **ADM)
    assert _words(dunit, NOW, "PC1") == []


def test_access_left(dunit, access):
    """R-AC-15 20.9 F_ACCESS_LEFT: 紛失か廃棄の機器の管理者の口に、止まった でない物がある。詳細は実体の番号を、
    実体_発行を書いた順に「, 」でつなぐ。使用の機器には出さない。"""
    _, a = access(dunit, "t1")
    _, b = access(dunit, "t2")
    access(dunit, "t1", "複合機1")   # 使用の機器の口は挙がらない
    assert findings(dunit, NOW, RB.F_ACCESS_LEFT) == []
    dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(NOW), **ADM)
    assert findings(dunit, NOW, RB.F_ACCESS_LEFT) == [("ルーター1", RB.F_ACCESS_LEFT, f"{a.番号}, {b.番号}")]
    dunit.remove_access(a.番号, **ADM)
    assert findings(dunit, NOW, RB.F_ACCESS_LEFT) == [("ルーター1", RB.F_ACCESS_LEFT, b.番号)]
    dunit.change_device("ルーター1", reason="見つからない", state="廃棄", at=dt(NOW), **ADM)
    assert findings(dunit, NOW, RB.F_ACCESS_LEFT) == [("ルーター1", RB.F_ACCESS_LEFT, b.番号)]
    dunit.remove_access(b.番号, "機器が無い", **ADM)
    assert findings(dunit, NOW, RB.F_ACCESS_LEFT) == []


def test_access_left_includes_ended_access(dunit, access):
    """R-AC-15 20.9: 終了を過ぎて、実体_失効の無い管理者の口も、削除の伝票が出るまで 止まった ではない(進み具合の表)。"""
    _, a = access(dunit, "t1", end="2026-12-05T00:00")
    got = dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(NOW), **ADM)
    assert got.実体_失効 == []
    assert findings(dunit, NOW, RB.F_ACCESS_LEFT) == [("ルーター1", RB.F_ACCESS_LEFT, a.番号)]


@pytest.mark.parametrize("state", ["紛失", "廃棄"])
def test_lost_or_retired_findings(dunit, state):
    """20.9: 紛失の機器には責任者の知らせと F_ACCESS_LEFT、廃棄の機器には F_NO_DISPOSAL_CHECK と F_ACCESS_LEFT だけ。"""
    _device_in(dunit, "PC1", state)
    want = [RB.F_NO_OWNER] if state == "紛失" else [RB.F_NO_DISPOSAL_CHECK]
    assert _words(dunit, NOW, "PC1") == want

