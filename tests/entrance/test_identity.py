from datetime import datetime, timedelta

import pytest

from digitalkey.entrance.identity import Identity, Evidence, StubMatcher, distance_m

HOUSE = (35.0, 135.0)
FACE = b"face-bytes"
T0 = datetime(2026, 10, 1, 15, 0)


@pytest.fixture
def identity(ledger):
    return Identity(ledger, StubMatcher(), house_lat=HOUSE[0], house_lon=HOUSE[1])


def full(**kw):
    ev = Evidence(selfie=FACE, id_image=FACE, doorbell=FACE, lat=HOUSE[0] + 0.0005, lon=HOUSE[1],
                submitted_at=T0, doorbell_at=T0 - timedelta(seconds=30))
    for k, v in kw.items():
        setattr(ev, k, v)
    return ev


def test_distance():
    assert abs(distance_m(35.0, 135.0, 35.0009, 135.0) - 100) < 2


def test_approve(identity):
    v = identity.verify(subject="Anna", evidence=full(), reservation_id="R1")
    assert v["判定"] == "承認" and v["判定者"] == "AI社員" and v["理由"] == ""
    assert v["距離m"] == "55" and v["時刻差s"] == "30" and v["一致度"] == "1.0"
    assert len(v["自撮り"]) == 64 and (identity.ledger.root / "画像" / f"{v['自撮り']}.bin").exists()
    assert v.basis == ["R1"]


def test_hold_reasons(identity):
    far = identity.verify(subject="A", evidence=full(lat=35.01))
    assert far["判定"] == "保留" and "届出住宅から" in far["理由"]
    nobell = identity.verify(subject="A", evidence=full(doorbell=None))
    assert nobell["判定"] == "保留" and "ドアホンの映像がない" in nobell["理由"]
    late = identity.verify(subject="A", evidence=full(doorbell_at=T0 - timedelta(hours=1)))
    assert late["判定"] == "保留" and "時刻差" in late["理由"]
    nopos = identity.verify(subject="A", evidence=full(lat=None, lon=None))
    assert nopos["判定"] == "保留" and "位置情報がない" in nopos["理由"]


def test_reject_when_faces_differ(identity):
    v = identity.verify(subject="A", evidence=full(id_image=b"other"))
    assert v["判定"] == "拒否" and "顔が一致しない" in v["理由"]


def test_human_decides_with_new_slip(identity):
    held = identity.verify(subject="A", evidence=full(doorbell=None))
    d = identity.decide(held.id, "承認", by="オーナー", reason="電話で確認")
    assert d["判定"] == "承認" and d["判定者"] == "オーナー" and d.basis == [held.id]
    assert identity.ledger.get(held.id)["判定"] == "保留"  # 前の伝票は変わらない
    assert identity.latest("A").id == d.id


def test_kiosk_inside_house_stands_in_for_location_and_doorbell(ledger):
    h = Identity(ledger, StubMatcher(), house_lat=HOUSE[0], house_lon=HOUSE[1], kiosks={"受付1"})
    v = h.verify(subject="A", evidence=Evidence(selfie=FACE, id_image=FACE, kiosk="受付1"))
    assert v["判定"] == "承認" and v["位置"] == "受付端末:受付1" and v["距離m"] == "0" and v["時刻差s"] == "0"
    assert v["ドアホン"] == ""


def test_unknown_kiosk_is_held(ledger):
    h = Identity(ledger, StubMatcher(), house_lat=HOUSE[0], house_lon=HOUSE[1], kiosks={"受付1"})
    v = h.verify(subject="A", evidence=Evidence(selfie=FACE, id_image=FACE, kiosk="よその端末"))
    assert v["判定"] == "保留" and "登録のない受付端末" in v["理由"]
