from datetime import datetime

import pytest

from digitalkey.entrance.keyring import DummyLock, Keyring


@pytest.fixture
def keyring(ledger):
    return Keyring(ledger, {"玄関": DummyLock("玄関")})


def test_issue_window_and_revoke(keyring):
    k = keyring.issue(subject="Anna", lock_id="玄関", start="2026-10-01 15:00", end="2026-10-05 10:00")
    assert k["開始"] == "2026-10-01T15:00" and k.to == "Anna"
    assert keyring.is_valid(k.id, datetime(2026, 10, 1, 14, 59)) == (False, "まだ有効ではありません")
    assert keyring.is_valid(k.id, datetime(2026, 10, 3)) == (True, "")
    assert keyring.is_valid(k.id, datetime(2026, 10, 5, 10)) == (False, "期限が切れています")
    assert [x.id for x in keyring.active(datetime(2026, 10, 3))] == [k.id]
    r = keyring.revoke(k.id, "退去")
    assert r.basis == [k.id]
    assert keyring.is_valid(k.id, datetime(2026, 10, 3)) == (False, "失効しています")
    assert keyring.active(datetime(2026, 10, 3)) == []
    assert keyring.is_valid("無い番号") == (False, "発行の記録がありません")


async def test_open_records_every_outcome(keyring):
    k = keyring.issue(subject="Anna", lock_id="玄関", start="2026-10-01T15:00", end="2026-10-05T10:00")
    ok = await keyring.open(k.id, at=datetime(2026, 10, 2))
    assert ok["結果"] == "成功" and keyring.locks["玄関"].actions == ["unlock"] and ok.basis == [k.id]
    no = await keyring.open(k.id, at=datetime(2026, 10, 9))
    assert no["結果"] == "拒否" and no["理由"] == "期限が切れています"
    assert keyring.locks["玄関"].actions == ["unlock"]
    k2 = keyring.issue(subject="Ben", lock_id="裏口", start="2026-10-01T15:00", end="2026-10-05T10:00")
    fail = await keyring.open(k2.id, at=datetime(2026, 10, 2))
    assert fail["結果"] == "失敗"
    assert keyring.ledger.verify_chain()[0]
