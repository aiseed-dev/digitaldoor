"""錠のドライバで動く接点(digitalkey/contacts.py)の試験。

扉のコントローラ(digitaldoor_controller)の判断が、接点の口だけを通して錠のドライバを動かすこと。
"""
import asyncio

import pytest

from digitaldoor_controller.audit import Audit, HmacSigner
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller
from digitalkey.contacts import LockDriverContacts
from digitalkey.entrance.keyring import DummyLock
from digitalkey.locks import make_lock

CONTACT_NAMES = ("fire", "mains", "door_open", "bolt_out", "set_lock")


class BrokenLock:
    id = "broken"

    async def unlock(self):
        raise RuntimeError("錠が見つかりません")

    lock = unlock


class HangingLock:
    id = "hang"

    async def unlock(self):
        await asyncio.sleep(60)

    lock = unlock


def ctl_with(contacts, **cfg):
    ctl = Controller(contacts, Audit(HmacSigner(b"k" * 32)), Config(autolock_s=0, **cfg))
    ctl.set_time(1000.0, True)
    return ctl


def test_same_shape_as_the_simulated_contacts():
    """R-CT-03: 接点の口(fire、mains、door_open、bolt_out、set_lock)を SimContacts と同じ形で満たす。"""
    c = LockDriverContacts(DummyLock("genkan"))
    for name in CONTACT_NAMES:
        assert callable(getattr(c, name)) and callable(getattr(SimContacts(), name))
    assert (c.fire(), c.mains(), c.door_open(), c.bolt_out()) == (False, True, False, True)
    c._fire, c._mains, c._door_open = True, False, True
    assert (c.fire(), c.mains(), c.door_open()) == (True, False, True)
    c.close()


def test_controller_decisions_reach_the_lock_driver():
    """R-CT-03: 扉の判断(解錠、施錠、火災での解放)は、接点の下の錠のドライバを動かす。判断の側は錠の銘柄を知らない。"""
    lock = DummyLock("genkan")
    c = LockDriverContacts(lock)
    ctl = ctl_with(c)
    assert ctl.unlock_request("上位") == "解錠"
    assert lock.actions == ["unlock"] and c.bolt_out() is False
    assert ctl.lock_request("上位") == "施錠"
    assert lock.actions == ["unlock", "lock"] and c.bolt_out() is True
    ctl.on_fire(True)  # 火災信号: 既定は解放
    assert lock.actions == ["unlock", "lock", "unlock"]
    assert c.outputs == ["unlock", "lock", "unlock"]
    c.close()


def test_registry_driver_is_swapped_without_changing_the_controller():
    """R-CT-04: digitalkey.locks の登録簿で選んだ錠(dummy)に差し替えても、判断の側は同じ。"""
    lock = make_lock("genkan", "dummy")
    c = LockDriverContacts(lock)
    assert ctl_with(c).unlock_request("上位") == "解錠"
    assert lock.actions == ["unlock"]
    c.close()


def test_unreachable_lock_keeps_the_position_and_does_not_raise():
    """R-CT-04: 錠に届かない銘柄の事情は接点の中で閉じる。例外を出さず、位置の記録を動かさず、失敗を数える。"""
    c = LockDriverContacts(BrokenLock())
    ctl = ctl_with(c)
    assert ctl.unlock_request("上位") == "解錠"  # 判断は止まらず、扉は命令を出した側として記録する
    assert c.bolt_out() is True and c.outputs == []
    assert c.failures == 1 and "錠が見つかりません" in c.last_error
    assert ctl.state()["bolt_out"] is True
    assert ctl.audit.verify()[0]
    c.close()


def test_timeout_cancels_the_driver_and_keeps_the_position():
    """R-CT-04: 時間内に終わらない錠は、時間切れで取り消し、届かなかったものとして扱う。"""
    c = LockDriverContacts(HangingLock(), timeout_s=0.05)
    c.set_lock(False)
    assert c.failures == 1 and "秒の内に終わりませんでした" in c.last_error
    assert c.bolt_out() is True and c.outputs == []
    c.close()


def test_a_later_success_after_failure_updates_the_position():
    """R-CT-04: 一度届かなくても、次に届けば位置が動く。"""
    lock = DummyLock("genkan")
    c = LockDriverContacts(BrokenLock())
    c.set_lock(False)
    c.driver = lock
    c.set_lock(False)
    assert c.bolt_out() is False and c.outputs == ["unlock"] and c.failures == 1
    c.close()


def test_run_serves_other_coroutines_on_the_same_loop():
    """R-CT-04: 鍵の台帳など、同じ錠を非同期で動かす処理は run() で同じループに渡せる。"""
    lock = DummyLock("genkan")
    c = LockDriverContacts(lock)
    assert c.run(_unlock_and_return(lock)) == 7
    assert lock.actions == ["unlock"]
    with pytest.raises(TimeoutError):
        c.run(asyncio.sleep(60), timeout=0.05)
    c.close()
    assert c.run(_unlock_and_return(lock)) == 7  # close の後でも、新しいループが立つ
    c.close()


async def _unlock_and_return(lock):
    await lock.unlock()
    return 7
