"""設計書 21.7: 一覧を扉へ配った記録(record_delivery、失効_配布)と、扉に届いた世代(door_generation)。

段 2a の実装より先に書いた。まだ無い名前は関数の中で引く。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from digitalkey.kanri import Unit, reasons
from kanri_common import CREDENTIAL, GATE, R2A, dt

PUB = "2026-12-10T09:00"
GOT = "2026-12-11T09:00"
ADM = dict(by="adm", role="管理者")
OPS = dict(by="ops", role="運用")


@pytest.fixture
def listed(runit):
    """一覧を二枚(世代 1、2)作った置き場。"""
    runit.publish_revocations(**ADM, at=dt(PUB))
    runit.publish_revocations(**ADM, at=dt("2026-12-10T10:00"))
    return runit


def test_example_21_7(listed):
    """R-AC-22 21.7 の例: record_delivery("玄関", 1, note=..., by="ops", role="運用") は 失効_配布 を書き、根拠は世代 1 の
    失効_一覧。伝票の発行者は by、宛先は空。door_generation("玄関") == 1。"""
    u = listed
    first = u.revocations()[0].番号
    s = u.record_delivery("玄関", 1, note="受付の端末で運んだ", **OPS, at=GOT)
    assert (s.form, s.issuer, s.to, s.basis) == ("失効_配布", "ops", "", [first])
    assert (s["扉"], s["世代"], s["日時"], s["備考"], s["役"]) == ("玄関", "1", GOT, "受付の端末で運んだ", "運用")
    assert u.door_generation("玄関") == 1


def test_delivery_at_datetime_and_admin(listed):
    """R-AC-22 21.7: at は datetime も受け、様式の 日時 に 19.5 の形で書く。管理者の役でもできる。note は省けば空。"""
    s = listed.record_delivery("玄関", 2, **ADM, at=datetime(2026, 12, 11, 9, 0, 45))
    assert (s["日時"], s["備考"], s["役"], s.issuer) == (GOT, "", "管理者", "adm")
    assert s.basis == [listed.revocations()[1].番号]
    assert listed.door_generation("玄関") == 2


@pytest.mark.parametrize("by,role,why", [
    ("adm", "名簿の係", reasons.ROLE_NOT_ALLOWED),
    ("apr", "承認する人", reasons.ROLE_NOT_ALLOWED),
    ("s1", "本人", reasons.ROLE_NOT_ALLOWED),
    ("adm", "発行者", reasons.ROLE_NOT_ALLOWED),
    ("nobody", "運用", reasons.BY_UNKNOWN),
    ("s1", "運用", reasons.NO_ROLE),
])
def test_delivery_roles(listed, refused, by, role, why):
    """R-AC-22 21.7、19.3: できる役は 管理者、運用。19.3 の 1〜4 の順に拒む。"""
    refused(listed, why, listed.record_delivery, "玄関", 1, by=by, role=role, at=GOT)


def test_delivery_form_check(listed, refused):
    """R-AC-22 19.3 の 5: 備考は varchar(200)。長すぎれば FORM。"""
    refused(listed, reasons.FORM, listed.record_delivery, "玄関", 1, note="あ" * 201, **OPS, at=GOT)


def test_delivery_unknown_door_before_generation(listed, refused):
    """R-AC-22 21.7: 確かめは 扉(UNKNOWN_DOOR、詳細に扉の名)、世代(UNKNOWN_GENERATION、詳細に世代の数)の順。"""
    u = listed
    refused(u, reasons.UNKNOWN_DOOR, u.record_delivery, "倉庫", 9, **OPS, at=GOT, detail="倉庫")
    refused(u, R2A.UNKNOWN_GENERATION, u.record_delivery, "玄関", 3, **OPS, at=GOT, detail="3")


def test_delivery_retired_door(listed, refused):
    """R-AC-22 21.7、21.14: 撤去した扉への record_delivery は UNKNOWN_DOOR。"""
    u = listed
    u.register_door("通用口", [CREDENTIAL], **ADM)
    u.register_door("通用口", [CREDENTIAL], state="撤去", **ADM)
    refused(u, reasons.UNKNOWN_DOOR, u.record_delivery, "通用口", 1, **OPS, at=GOT, detail="通用口")


def test_delivery_without_any_list(runit, refused):
    """R-AC-22 21.7: 一覧が一枚も無い置き場では、どの世代も UNKNOWN_GENERATION。"""
    refused(runit, R2A.UNKNOWN_GENERATION, runit.record_delivery, "玄関", 1, **OPS, at=GOT, detail="1")


def test_smaller_generation_is_recorded_but_does_not_lower(listed):
    """R-AC-22 21.7: 前に記録した世代より小さい世代も書ける(拒まない)。door_generation は記録の中の一番大きい世代。"""
    u = listed
    u.record_delivery("玄関", 2, **OPS, at=GOT)
    s = u.record_delivery("玄関", 1, **OPS, at="2026-12-11T10:00")
    assert s.form == "失効_配布"
    assert u.door_generation("玄関") == 2
    assert Unit.open(u.root, signer=u.signer).door_generation("玄関") == 2


def test_door_generation_defaults(listed):
    """R-AC-22 21.7、21.14: 記録の無い扉、台帳に無い扉の名前は 0(拒まない)。扉ごとに別に読む。"""
    u = listed
    u.register_door("裏口", [GATE, CREDENTIAL], **ADM)
    assert u.door_generation("玄関") == 0
    assert u.door_generation("倉庫") == 0
    u.record_delivery("裏口", 1, **OPS, at=GOT)
    assert (u.door_generation("裏口"), u.door_generation("玄関")) == (1, 0)


def test_door_generation_of_retired_door(listed):
    """R-AC-22 21.7: 撤去した扉にも、記録の中の一番大きい世代を返す。"""
    u = listed
    u.register_door("裏口", [CREDENTIAL], **ADM)
    u.record_delivery("裏口", 2, **OPS, at=GOT)
    u.register_door("裏口", [CREDENTIAL], state="撤去", **ADM)
    assert u.door_generation("裏口") == 2
