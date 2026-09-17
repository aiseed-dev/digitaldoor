import struct
import uuid

import pytest

from digitalkey.sesame import protocol as p


def test_cmac_rfc4493_vectors():
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    assert p.aes_cmac(key, b"") == bytes.fromhex("bb1d6929e95937287fa37d129b756746")
    msg = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    assert p.aes_cmac(key, msg) == bytes.fromhex("070a16b46b4d4144f79bdd9dd04a287c")


def test_ccm_roundtrip_and_counters():
    key = bytes(range(16))
    token = b"\x01\x02\x03\x04"
    a = p.SesameCipher(key, token)
    b = p.SesameCipher(key, token)
    for i in range(3):
        msg = bytes([i]) * (5 + i)
        ct = a.encrypt(msg)
        assert len(ct) == len(msg) + 4
        assert b.decrypt(ct) == msg
    assert a.encrypt_counter == 3 and b.decrypt_counter == 3
    # nonce must be counter-dependent: replaying an old ciphertext fails
    with pytest.raises(ValueError):
        b.decrypt(ct)


def test_ccm_matches_reference_construction():
    """Independent CCM construction with explicit nonce to pin the nonce layout."""
    from Crypto.Cipher import AES
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    token = bytes.fromhex("deadbeef")
    c = p.SesameCipher(key, token)
    c.encrypt_counter = 7
    ct = c.encrypt(b"\x53\x00\x0e")
    ref = AES.new(key, AES.MODE_CCM, nonce=struct.pack("<Q", 7) + b"\x00" + token, mac_len=4)
    ref.update(b"\x00")
    rct, rtag = ref.encrypt_and_digest(b"\x53\x00\x0e")
    assert ct == rct + rtag


def test_segment_split_and_reassemble():
    for n in (0, 1, 19, 20, 38, 39, 77, 100):
        data = bytes(range(256))[:n] if n <= 256 else bytes(n)
        chunks = p.split_segments(p.SegmentType.CIPHER, data)
        assert all(len(c) <= 20 for c in chunks)
        assert chunks[0][0] & 1 == 1
        assert all((c[0] & 1) == 0 for c in chunks[1:])
        assert chunks[-1][0] >> 1 == p.SegmentType.CIPHER
        assert all((c[0] >> 1) == 0 for c in chunks[:-1])
        rx = p.SegmentReceiver()
        out = None
        for c in chunks:
            r = rx.feed(c)
            assert out is None
            out = r
        assert out == (p.SegmentType.CIPHER, data)


def test_segment_plain_single():
    chunks = p.split_segments(p.SegmentType.PLAIN, b"\x02\xaa\xbb\xcc\xdd")
    assert chunks == [b"\x03\x02\xaa\xbb\xcc\xdd"]


def test_parse_advertisement_sesame6pro():
    dev = uuid.uuid4()
    mfg = {0x055A: bytes([21, 0, 0x00]) + dev.bytes}
    adv = p.parse_advertisement("AA:BB", "Sesame6Pro", -50, mfg)
    assert adv is not None
    assert adv.product_type == 21
    assert adv.model_id == "sesame_6_pro"
    assert adv.model_name == "Sesame 6 Pro"
    assert adv.is_registered is False
    assert adv.device_uuid == dev
    assert adv.is_lock
    mfg = {0x055A: bytes([21, 0, 0x03]) + dev.bytes}
    adv = p.parse_advertisement("AA:BB", None, -50, mfg)
    assert adv.is_registered and adv.has_history
    assert p.parse_advertisement("x", None, 0, {0x004C: b"\x00" * 20}) is None


def test_ecdh_secret_matches_between_peers():
    a = p.EphemeralKey()
    b = p.EphemeralKey()
    assert len(a.public_bytes_raw()) == 64
    sa = a.derive_device_secret(b.public_bytes_raw())
    sb = b.derive_device_secret(a.public_bytes_raw())
    assert sa == sb and len(sa) == 16


def test_command_builders():
    assert p.build_login(bytes.fromhex("0102030405060708090a0b0c0d0e0f10")) == b"\x02\x01\x02\x03\x04"
    assert p.build_timestamp_like() if hasattr(p, "build_timestamp_like") else True
    assert p.timestamp_le32(1605929466) == bytes.fromhex("fa89b85f")  # from SDK comment
    assert p.build_time(1605929466) == b"\x08" + bytes.fromhex("fa89b85f")
    reg = p.build_registration(b"\x11" * 64, 1605929466)
    assert reg[0] == 1 and len(reg) == 69 and reg[-4:] == bytes.fromhex("fa89b85f")
    assert p.build_history_tag("flet") == b"\x00\x0eflet"
    assert len(p.build_history_tag("x" * 40)) == 20
    assert p.build_lock("a")[0] == 82 and p.build_unlock("a")[0] == 83 and p.build_toggle()[0] == 88


def test_parse_notification():
    r = p.parse_notification(b"\x07\x02\x00\x01\x02\x03\x04")
    assert isinstance(r, p.Response) and r.item_code == 2 and r.ok and r.payload == b"\x01\x02\x03\x04"
    r = p.parse_notification(b"\x07\x01\x09")
    assert isinstance(r, p.Response) and r.result_code == p.ResultCode.INVALID_ACTION and not r.ok
    pub = p.parse_notification(b"\x08\x0e\xaa\xbb\xcc\xdd")
    assert isinstance(pub, p.Publish) and pub.item_code == p.ItemCode.INITIAL and pub.payload == b"\xaa\xbb\xcc\xdd"
    assert p.parse_notification(b"\x01") is None


def test_mech_status_and_setting():
    data = struct.pack("<Hhh", 2900, -32768, 123) + bytes([0x02 | 0x40])
    ms = p.MechStatus.parse(data)
    assert ms.battery_raw == 2900 and ms.target is None and ms.position == 123
    assert ms.is_in_lock_range and ms.is_clockwise and not ms.is_low_battery
    assert ms.state == "locked"
    assert ms.battery_voltage == pytest.approx(5.8)
    assert ms.battery_percent == 50
    st = p.MechSetting.parse(struct.pack("<hhh", 100, -50, 15))
    assert (st.lock_position, st.unlock_position, st.auto_lock_second) == (100, -50, 15)
    reg = p.RegistrationResult.parse(data + struct.pack("<hhh", 1, 2, 3) + b"\x55" * 64)
    assert reg.mech_setting.auto_lock_second == 3 and reg.device_public_key == b"\x55" * 64
