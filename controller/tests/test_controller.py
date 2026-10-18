import pytest

from digitaldoor_controller.audit import Audit, HmacSigner
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller


def outputs(ctl):
    return ctl.contacts.outputs


def test_auth_unlocks_and_autolocks(ctl):
    """R-CT-09 R-CT-10: 有効な資格情報で解錠し、秒数が過ぎたら施錠する。"""
    assert ctl.on_auth("A") == "解錠" and outputs(ctl) == ["unlock"] and not ctl.locked
    ctl.tick(1004.9)
    assert not ctl.locked
    ctl.tick(1005.0)
    assert ctl.locked and outputs(ctl) == ["unlock", "lock"]
    ok, n, _ = ctl.audit.verify()
    assert ok and n >= 4


def test_autolock_waits_while_door_open(ctl):
    """R-CT-09: 扉が開いている間は自動施錠せず、閉じた後に施錠する。"""
    ctl.on_auth("A")
    ctl.contacts._door_open = True
    ctl.on_door(True)
    ctl.tick(1010.0)
    assert not ctl.locked
    ctl.contacts._door_open = False
    ctl.on_door(False)
    ctl.tick(1010.5)
    assert ctl.locked


def test_fire_releases_and_blocks_lock(ctl):
    """R-CT-05: 火災信号で解放し、火災中の施錠要求を拒み、自動施錠しない。"""
    ctl.on_fire(True)
    assert not ctl.locked and ctl.mode == "火災" and outputs(ctl) == ["unlock"]
    assert ctl.lock_request("上位") == "拒否"
    assert ctl.on_auth("A") == "解放済み"
    ctl.tick(2000.0)
    assert not ctl.locked  # 火災中は自動施錠しない
    ctl.on_fire(False)
    assert ctl.mode == "通常"
    kinds = [e.event for e in ctl.audit.entries]
    assert "火災信号" in kinds


def test_power_loss_release_and_hold():
    """R-CT-06: 停電時の挙動を解放と保持のどちらにも設定でき、復電で通常に戻る。"""
    for policy, expect in (("release", False), ("hold", True)):
        c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(power_policy=policy))
        c.set_time(1.0, True)
        c.on_mains(False)
        assert c.locked is expect and c.mode == "停電"
        c.on_mains(True)
        assert c.mode == "通常"


def test_two_person_rule():
    """R-CT-08: 二人同時の人数と窓の秒数。揃ったときだけ解錠する。"""
    c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(two_person=2, two_person_window_s=10))
    c.set_time(100.0, True)
    assert c.on_auth("A") == "待ち" and c.locked
    assert c.on_auth("A") == "待ち"            # 同じ人は数えない
    c.now = 120.0
    assert c.on_auth("B") == "待ち"            # 窓を過ぎたので A は落ちる
    c.now = 121.0
    assert c.on_auth("A") == "解錠" and not c.locked
    assert any("二人同時" in e.detail for e in c.audit.entries if e.event == "解錠")


def test_duress_and_invalid(ctl):
    """R-CT-10: 無効は拒否して施錠のまま、強要は解錠して記録する。判定は上の層の結果を使う。"""
    assert ctl.on_auth("X", valid=False, reason="失効") == "拒否" and ctl.locked
    assert ctl.on_auth("A", duress=True) == "強要" and not ctl.locked
    assert any(e.event == "強要コード" for e in ctl.audit.entries)


def test_manual_release_and_tamper(ctl):
    """R-CT-07 R-CT-24: 非常手段の使用と改ざん検知が記録に残る。"""
    ctl.on_manual_release("機械キー")
    ctl.on_tamper("通信モジュール脱落")
    assert [e.result for e in ctl.audit.entries if e.event == "非常手段"] == ["機械キー"]
    assert any(e.kind == "異常" for e in ctl.audit.entries)


def test_unsynced_time_is_marked():
    """R-CT-16: 時刻が未同期の間の記録に印が付き、同期後は付かない。"""
    c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)))
    c.on_auth("A")
    assert all(not e.synced for e in c.audit.entries)
    c.set_time(5000.0, True)
    c.on_auth("B")
    assert c.audit.entries[-1].synced


def test_autolock_zero_never_locks(ctl):
    """R-CT-09: 自動施錠の秒数が 0 のときは自動施錠しない。"""
    ctl.configure("据付", autolock_s=0)
    ctl.on_auth("A")
    ctl.tick(1000.0)
    ctl.tick(99999.0)
    assert not ctl.locked and outputs(ctl) == ["unlock"]


def test_fire_refuses_lock_whatever_power_policy():
    """R-CT-05: 火災中の施錠要求は、停電の設定が保持でも受け付けない。"""
    c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(power_policy="hold"))
    c.set_time(1.0, True)
    c.on_fire(True)
    assert c.lock_request("上位") == "拒否" and not c.locked
    assert c.audit.entries[-1].detail == "火災中"


def test_power_loss_lock_request_follows_policy():
    """R-CT-06: 停電中の施錠要求は、解放の設定なら拒み、保持の設定なら受ける。"""
    for policy, expect in (("release", "拒否"), ("hold", "施錠")):
        c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(power_policy=policy))
        c.set_time(1.0, True)
        c.on_auth("A")
        c.on_mains(False)
        assert c.lock_request("上位") == expect


def test_sync_keeps_both_times_and_flags_jump(ctl):
    """R-CT-16 R-CT-24: 同期のたびに前と新しい時刻を残し、同期済みから許容を超えて動いたらずれを記録する。"""
    ctl.set_time(1100.0, True)
    sync = ctl.audit.entries[-1]
    assert (sync.kind, sync.event) == ("時刻", "同期") and "1000.0" in sync.detail and "1100.0" in sync.detail
    assert not [e for e in ctl.audit.entries if e.kind == "異常"]
    ctl.set_time(5000.0, True)
    jump = [e for e in ctl.audit.entries if e.kind == "異常"]
    assert [e.event for e in jump] == ["時刻のずれ"] and "1100.0" in jump[0].detail and "5000.0" in jump[0].detail
    ctl.set_time(4000.0, True)   # 戻る向きも同じ
    assert [e.event for e in ctl.audit.entries if e.kind == "異常"] == ["時刻のずれ", "時刻のずれ"]


def test_first_sync_is_not_a_jump():
    """R-CT-24: 未同期からの最初の同期は、ずれとして数えない。"""
    c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)))
    c.set_time(1_700_000_000.0, True)
    assert not [e for e in c.audit.entries if e.kind == "異常"]


def test_config_change_is_logged(ctl):
    """R-CT-22: 設定の変更は項目ごとに種別「設定」で記録し、前の値と新しい値の両方を残す。"""
    ctl.configure("据付", autolock_s=30, power_policy="hold")
    assert ctl.config.autolock_s == 30 and ctl.config.power_policy == "hold"
    rows = {e.event: e for e in ctl.audit.entries if e.kind == "設定"}
    assert set(rows) == {"autolock_s", "power_policy"}
    assert "5" in rows["autolock_s"].detail and "30" in rows["autolock_s"].detail
    assert "release" in rows["power_policy"].detail and "hold" in rows["power_policy"].detail
    assert rows["autolock_s"].subject == "据付"
    n = len(ctl.audit.entries)
    ctl.configure("据付", autolock_s=30)   # 同じ値は変更ではない
    assert len(ctl.audit.entries) == n


def test_config_rejects_unknown_and_bad_values(ctl):
    """R-CT-22: 無い項目と許されない値は拒み、どの項目も変えず、記録も増やさない。"""
    before, n = (ctl.config.autolock_s, ctl.config.fire_policy), len(ctl.audit.entries)
    for bad in ({"autolock_s": 10, "colour": "red"}, {"autolock_s": 10, "fire_policy": "open"},
                {"autolock_s": -1}, {"two_person": 0}, {"autolock_s": "10"}):
        with pytest.raises(ValueError):
            ctl.configure("据付", **bad)
    assert (ctl.config.autolock_s, ctl.config.fire_policy) == before and len(ctl.audit.entries) == n
