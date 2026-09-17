from digitalkey.door.audit import Audit, HmacSigner


def test_chain_and_signature(tmp_path):
    a = Audit(HmacSigner(b"s" * 32), tmp_path / "a.jsonl")
    a.append(at=1, synced=True, kind="操作", event="解錠", subject="A")
    a.append(at=2, synced=True, kind="操作", event="施錠")
    assert a.verify() == (True, 2, "")
    assert a.entries[1].prev_hash == a.entries[0].hash and a.entries[1].seq == 2
    b = Audit(HmacSigner(b"s" * 32), tmp_path / "a.jsonl")   # 再読込
    assert b.verify() == (True, 2, "") and b.since(1)[0].seq == 2
    wrong = Audit(HmacSigner(b"x" * 32), tmp_path / "a.jsonl")
    assert wrong.verify()[2] == "署名が合いません"


def test_tamper_detected(tmp_path):
    a = Audit(HmacSigner(b"s" * 32), tmp_path / "a.jsonl")
    a.append(at=1, synced=True, kind="操作", event="解錠", subject="A")
    a.append(at=2, synced=True, kind="操作", event="施錠")
    a.entries[0].subject = "B"
    assert a.verify()[2] == "内容が変わっています"
    a.entries[0].subject = "A"
    del a.entries[0]
    ok, _, why = a.verify()
    assert not ok and "欠番" in why
