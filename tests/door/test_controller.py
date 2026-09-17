from digitalkey.door.audit import Audit, HmacSigner
from digitalkey.door.contacts import SimContacts
from digitalkey.door.controller import Config, Controller


def outputs(ctl):
    return ctl.contacts.outputs


def test_auth_unlocks_and_autolocks(ctl):
    assert ctl.on_auth("A") == "解錠" and outputs(ctl) == ["unlock"] and not ctl.locked
    ctl.tick(1004.9)
    assert not ctl.locked
    ctl.tick(1005.0)
    assert ctl.locked and outputs(ctl) == ["unlock", "lock"]
    ok, n, _ = ctl.audit.verify()
    assert ok and n >= 4


def test_autolock_waits_while_door_open(ctl):
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
    for policy, expect in (("release", False), ("hold", True)):
        c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(power_policy=policy))
        c.set_time(1.0, True)
        c.on_mains(False)
        assert c.locked is expect and c.mode == "停電"
        c.on_mains(True)
        assert c.mode == "通常"


def test_two_person_rule():
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
    assert ctl.on_auth("X", valid=False, reason="失効") == "拒否" and ctl.locked
    assert ctl.on_auth("A", duress=True) == "強要" and not ctl.locked
    assert any(e.event == "強要コード" for e in ctl.audit.entries)


def test_manual_release_and_tamper(ctl):
    ctl.on_manual_release("機械キー")
    ctl.on_tamper("通信モジュール脱落")
    assert [e.result for e in ctl.audit.entries if e.event == "非常手段"] == ["機械キー"]
    assert any(e.kind == "異常" for e in ctl.audit.entries)


def test_unsynced_time_is_marked():
    c = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)))
    c.on_auth("A")
    assert all(not e.synced for e in c.audit.entries)
    c.set_time(5000.0, True)
    c.on_auth("B")
    assert c.audit.entries[-1].synced
