"""疑似の Sesame を錠のドライバの口で動かす FakeSesameLock の試験(実機にも Bluetooth にも触れない)。

要求仕様の番号は、実演の道具 tools/jitsuen.py の試験(tests/jitsuen)が確かめる。ここは部品の動きだけ。
"""
import pytest

from digitalkey_sesame import protocol as p
from digitalkey_sesame.fake import FakeSesameLock


async def test_unlock_and_lock_go_through_the_protocol():
    lock = FakeSesameLock("genkan")
    assert lock.locked
    await lock.unlock()
    assert not lock.locked
    await lock.lock()
    assert lock.locked
    items = [item for item, _ in lock.fake.received]
    assert items.count(p.ItemCode.LOGIN) == 2
    assert items.count(p.ItemCode.UNLOCK) == 1 and items.count(p.ItemCode.LOCK) == 1


async def test_unreachable_lock_raises_and_keeps_state():
    lock = FakeSesameLock("genkan", reachable=False)
    with pytest.raises(RuntimeError, match="見つかりません"):
        await lock.unlock()
    assert lock.locked
    assert lock.fake.received == []
