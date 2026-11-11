"""設計書 21.1〜21.6、21.11: 段 2a の外に出す名前とデータの型、役と伝票の組、失効の一覧を作る・読む・組み立て直す、
発行時刻が世代の順に増える事、失効の操作の中で作る一覧。

段 2a の実装より先に書いた。まだ無い名前は関数の中で引く(収集は通り、試験は AttributeError などで落ちる)。
一覧の署名と中身は、扉の側の `digitaldoor_controller.credentials.verify_revocation` で確かめる(扉コントローラ仕様書 5.2c)。
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest
from digitaldoor_controller import credentials

import digitalkey.kanri as kanri
from digitalkey.kanri import Unit, reasons
from kanri_common import (ACCESS, CREDENTIAL, GATE, ISSUE_AT, PHYSICAL, R2A, REFUSALS_2A, T0, T1, dt, plus, unix)

AT = dt(ISSUE_AT)
REV = "2026-12-10T12:00"
PUB = "2026-12-10T09:00"
ADM = dict(by="adm", role="管理者")
OPS = dict(by="ops", role="運用")


def _body(lst: dict) -> dict:
    assert set(lst) == {"body", "sig"}
    return json.loads(lst["body"])


def _ids(u) -> list[list[str]]:
    return [r.識別 for r in u.revocations()]


# ---- 21.11 拒否の言葉 ----

@pytest.mark.parametrize("name", REFUSALS_2A)
def test_reason_words_2a(name):
    """R-AC-22 21.11: reasons に足す NO_REVOCATION_LIST(失効の一覧がありません)と UNKNOWN_GENERATION(その世代の一覧がありません)。"""
    assert getattr(reasons, name) == getattr(R2A, name)


# ---- 21.1、21.2、21.4 外に出す名前とデータの型 ----

TYPES_2A = {
    "Published": ["伝票", "世代", "一覧"],
    "RevList": ["番号", "世代", "発行時刻", "識別"],
}


@pytest.mark.parametrize("name", sorted(TYPES_2A))
def test_data_types_2a(name):
    """R-AC-22 21.4: 足す型は dataclass(frozen=True)、属性の名前と並びは表のとおり。`digitalkey.kanri` から出す(21.1)。"""
    cls = getattr(kanri, name)
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen
    assert [f.name for f in dataclasses.fields(cls)] == TYPES_2A[name]


@pytest.mark.parametrize("name,last", [("Door", "失効の最長の分数"), ("Revocation", "失効_一覧"),
                                       ("DeviceChange", "失効_一覧"), ("EffectiveBy", "分数")])
def test_types_get_attribute_last(name, last):
    """R-AC-22 21.2、21.4: Door、Revocation、DeviceChange、EffectiveBy に属性を最後に足す(前の並びを変えない)。"""
    names = [f.name for f in dataclasses.fields(getattr(kanri, name))]
    assert names[-1] == last


def test_door_and_revocation_keep_earlier_attributes():
    """R-AC-22 21.2: 段 1・1b の属性の並びはそのままで、その後に足す。"""
    names = {n: [f.name for f in dataclasses.fields(getattr(kanri, n))] for n in
             ("Door", "Revocation", "DeviceChange", "EffectiveBy")}
    assert names["Door"] == ["名前", "経路", "場所", "記録の公開鍵", "状態", "失効の最長の分数"]
    assert names["Revocation"] == ["権限_失効", "実体_失効", "進み具合", "失効_一覧"]
    assert names["DeviceChange"] == ["伝票", "権限_失効", "実体_失効", "失効_一覧"]
    assert names["EffectiveBy"] == ["種類", "時刻", "分数"]


def test_effective_by_minutes_default_none():
    """R-AC-14 21.2: EffectiveBy の 分数 は既定 None(段 1 の二つの引数で作れる)。"""
    e = kanri.EffectiveBy("期限まで", T1)
    assert (e.種類, e.時刻, e.分数) == ("期限まで", T1, None)
    e = kanri.EffectiveBy("最長の時間まで", "", 1440)
    assert e.分数 == 1440


def test_unit_has_six_mixins():
    """R-AC-22 21.1: Unit は RevocationsMixin を継ぐ(六つの Mixin)。"""
    from digitalkey.kanri.revocations import RevocationsMixin
    assert issubclass(kanri.Unit, RevocationsMixin)


def test_ledger_has_two_new_forms(make_unit):
    """R-AC-22 21.2、21.5: 台帳は 失効_一覧 と 失効_配布 を持つ(22 枚の数は test_devices の試験で見る)。"""
    u = make_unit()
    assert {"失効_一覧", "失効_配布"} <= set(u.ledger.form_set)


# ---- 21.6 publish_revocations ----

def test_publish_first_list_empty(runit):
    """R-AC-22 21.6、21.3: 最初の一覧は世代 1。失効が無くても作れる(識別は空)。伝票の発行者は 発行者、宛先は空、根拠なし。
    項目は 世代、発行時刻(unix 秒)、識別、操作者 by、役 role。一覧は発行者の鍵で署名した {"body", "sig"}。"""
    u = runit
    p = u.publish_revocations(**ADM, at=dt(PUB))
    assert isinstance(p, kanri.Published)
    s = p.伝票
    assert (s.form, s.issuer, s.to, s.basis) == ("失効_一覧", "発行者", "", [])
    assert (s["世代"], s["発行時刻"], s["識別"], s["操作者"], s["役"]) == ("1", str(unix(PUB)), "", "adm", "管理者")
    assert p.世代 == 1
    body = _body(p.一覧)
    assert body == {"format": "digitaldoor-revocation/1", "issuer": u.issuer, "generation": 1,
                    "issued_at": unix(PUB), "revoked": []}
    reason, got = credentials.verify_revocation(p.一覧, trust=u.trust_entry(), current={})
    assert (reason, got) == ("", body)


def test_publish_by_operations_role(runit):
    """R-AC-22 21.3: publish_revocations は 運用 の役でもできる。操作者と役は呼んだ人と役。"""
    p = runit.publish_revocations(**OPS, at=dt(PUB))
    assert (p.伝票["操作者"], p.伝票["役"]) == ("ops", "運用")


@pytest.mark.parametrize("by,role,why", [
    ("adm", "名簿の係", reasons.ROLE_NOT_ALLOWED),
    ("apr", "承認する人", reasons.ROLE_NOT_ALLOWED),
    ("adm", "発行者", reasons.ROLE_NOT_ALLOWED),
    ("s1", "本人", reasons.ROLE_NOT_ALLOWED),
    ("nobody", "名簿の係", reasons.ROLE_NOT_ALLOWED),     # 1 が 2 より先
    ("nobody", "運用", reasons.BY_UNKNOWN),
    ("s1", "運用", reasons.NO_ROLE),
])
def test_publish_checks_19_3(runit, refused, by, role, why):
    """R-AC-22 21.6: publish_revocations の確かめは 19.3 の 1〜5 だけ(できる役は 管理者、運用)。拒んだ時は何も書かない。"""
    refused(runit, why, runit.publish_revocations, by=by, role=role, at=dt(PUB))


def test_publish_by_inactive(runit, refused):
    """R-AC-22 19.3 の 3: 操作する人が有効でなければ BY_INACTIVE。"""
    runit.change_subject("ops", reason="休職", state="停止", by="adm", role="名簿の係")
    refused(runit, reasons.BY_INACTIVE, runit.publish_revocations, **OPS, at=dt(PUB))


def test_generation_increments(runit):
    """R-AC-22 21.6: 世代は台帳の最後の 失効_一覧 の世代 + 1。revocations() は台帳に書いた順に RevList で返す。"""
    u = runit
    got = [u.publish_revocations(**ADM, at=dt(plus(PUB, minutes=i))) for i in range(3)]
    assert [p.世代 for p in got] == [1, 2, 3]
    rl = u.revocations()
    assert all(isinstance(r, kanri.RevList) for r in rl)
    assert [(r.番号, r.世代, r.発行時刻, r.識別) for r in rl] == \
        [(p.伝票.id, i + 1, unix(plus(PUB, minutes=i)), []) for i, p in enumerate(got)]


def test_revocations_empty(runit):
    """R-AC-22 21.6: 一覧が一枚も無ければ revocations() は空の列。"""
    assert runit.revocations() == []


def test_issued_at_truncates_fraction(runit):
    """R-AC-22 21.6、21.14: 発行時刻は int(local(at).timestamp())。秒は残り、秒の端数は切り捨てる。"""
    at = datetime(2026, 12, 10, 9, 0, 30, 700000)
    p = runit.publish_revocations(**ADM, at=at)
    assert _body(p.一覧)["issued_at"] == unix(PUB) + 30
    assert runit.revocations()[-1].発行時刻 == unix(PUB) + 30


@pytest.mark.parametrize("tz", ["local", "utc"])
def test_issued_at_with_offset(runit, tz):
    """R-AC-22 21.6: 時差つきの at は地方時に直して読む(同じ瞬間なら同じ発行時刻)。"""
    at = dt(PUB).astimezone()
    if tz == "utc":
        at = at.astimezone(timezone.utc)
    assert _body(runit.publish_revocations(**ADM, at=at).一覧)["issued_at"] == unix(PUB)


def test_issued_at_increases_with_generation(runit):
    """R-AC-14 R-AC-22 21.6: 同じ at で二度作ると、二枚目の発行時刻は一枚目 + 1。前の一覧より古い at を渡しても拒まず、
    発行時刻は前の一覧 + 1。台帳にも同じ値を書く。"""
    u = runit
    a = u.publish_revocations(**ADM, at=dt(PUB))
    b = u.publish_revocations(**ADM, at=dt(PUB))
    c = u.publish_revocations(**ADM, at=dt(plus(PUB, days=-1)))
    t = [_body(x.一覧)["issued_at"] for x in (a, b, c)]
    assert t == [unix(PUB), unix(PUB) + 1, unix(PUB) + 2]
    assert [r.発行時刻 for r in u.revocations()] == t
    assert [x.伝票["発行時刻"] for x in (a, b, c)] == [str(v) for v in t]


# ---- 21.6 一覧の中身(識別) ----

def test_ids_are_stopped_unexpired_credentials_in_issue_order(runit, grant, cred):
    """R-AC-22 21.6: 識別は、署名つき資格情報の実体のうち、止めてあり、終了を過ぎていない物。主体を問わず、
    実体_発行を台帳に書いた順に並べる(止めた順ではない)。止めていない実体と、ほかの経路は入らない。"""
    u = runit
    p1, c1 = cred(u, "s1")
    gate = u.issue_entity(p1, GATE, **ADM, at=AT).実体       # ほかの経路(門番)の識別は入らない
    p2, c2 = cred(u, "s2")
    _, c3 = cred(u, "s2")                       # s2 の二つ目は止めない
    rev2 = u.revoke_permission(p2, "紛失", **ADM, at=dt(REV))     # 先に s2 の一つ目を止める
    assert rev2.失効_一覧 is not None
    assert _ids(u)[-1] == [c2.実体.識別]
    rev1 = u.revoke_subject("s1", "退職", **ADM, at=dt(plus(REV, minutes=1)))
    assert rev1.失効_一覧 is not None
    assert _ids(u)[-1] == [c1.実体.識別, c2.実体.識別]
    assert c3.実体.識別 not in _ids(u)[-1] and gate.識別 not in _ids(u)[-1]
    assert _body(u.revocation())["revoked"] == [c1.実体.識別, c2.実体.識別]


def test_ids_drop_ended_credentials(runit, cred):
    """R-AC-22 21.6: 終了を過ぎた実体(at >= 終了)は一覧から外れる。終了ちょうどで外れ、1 分前は入る。"""
    u = runit
    _, c1 = cred(u, "s1", until="2026-12-20T00:00")
    _, c2 = cred(u, "s2")
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    assert _ids(u)[-1] == [c1.実体.識別, c2.実体.識別]
    u.publish_revocations(**ADM, at=dt("2026-12-19T23:59"))
    assert _ids(u)[-1] == [c1.実体.識別, c2.実体.識別]
    u.publish_revocations(**ADM, at=dt("2026-12-20T00:00"))
    assert _ids(u)[-1] == [c2.実体.識別]


def test_ids_include_revoked_permission_without_entity_slip(runit, cred):
    """R-AC-22 21.6: 権限の状態が 失効 の実体も入る(実体_失効が無くても)。権限_失効はテストが台帳に直に書く。"""
    u = runit
    pid, c1 = cred(u, "s1")
    u.ledger.roles = {"adm": {"管理者"}}
    u.ledger.append("権限_失効", {"権限": pid, "主体": "s1", "範囲": "一つの権限", "理由": "試し", "役": "管理者"},
                    issuer="adm", to="s1", basis=[pid])
    p = u.publish_revocations(**ADM, at=dt(REV))
    assert _body(p.一覧)["revoked"] == [c1.実体.識別]
    assert p.伝票["識別"] == c1.実体.識別


def test_ids_joined_with_comma_space(runit, cred):
    """R-AC-22 21.5: 様式の 識別 は「, 」でつなぐ。RevList.識別 はそれを分けた列。"""
    u = runit
    _, c1 = cred(u, "s1")
    _, c2 = cred(u, "s2")
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    rev = u.revoke_subject("s2", "退職", **ADM, at=dt(plus(REV, minutes=1)))
    assert rev.失効_一覧["識別"] == f"{c1.実体.識別}, {c2.実体.識別}"
    assert u.revocations()[-1].識別 == [c1.実体.識別, c2.実体.識別]


# ---- 21.6 revocation(組み立て直し) ----

def test_revocation_rebuilds_same_dict(runit, cred):
    """R-AC-22 21.1、21.6: revocation(generation) は台帳の値から組み立て直し、作った時と同じ dict(同じ署名)。
    None は最後の世代。置き場を開き直しても同じ。"""
    u = runit
    a = u.publish_revocations(**ADM, at=dt(PUB))
    _, c1 = cred(u, "s1")
    rev = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert u.revocation(1) == a.一覧
    last = u.revocation()
    assert last == u.revocation(2)
    assert _body(last)["revoked"] == [c1.実体.識別]
    assert _body(last)["generation"] == 2 and rev.失効_一覧["世代"] == "2"
    again = Unit.open(u.root, signer=u.signer)
    assert again.revocation(1) == a.一覧 and again.revocation() == last


def test_revocation_refusals(runit, refused):
    """R-AC-22 21.6、21.11: 一覧が一枚も無い → NO_REVOCATION_LIST(詳細は空)。その世代が無い → UNKNOWN_GENERATION(詳細に世代の数)。"""
    u = runit
    refused(u, R2A.NO_REVOCATION_LIST, u.revocation, detail="")
    refused(u, R2A.NO_REVOCATION_LIST, u.revocation, 1, detail="")
    u.publish_revocations(**ADM, at=dt(PUB))
    refused(u, R2A.UNKNOWN_GENERATION, u.revocation, 9, detail="9")
    assert _body(u.revocation(1))["generation"] == 1


def test_read_methods_take_no_role(runit):
    """R-AC-22 21.3: 読むメソッド(revocations、revocation、door_generation)は役を取らない。"""
    u = runit
    u.publish_revocations(**ADM, at=dt(PUB))
    assert len(u.revocations()) == 1
    assert u.revocation()["sig"]
    assert u.door_generation("玄関") == 0


# ---- 21.6 失効の操作の中で作る一覧 ----

def _three(u, pid):
    kw = dict(by="adm", role="管理者", at=AT)
    got = u.issue_entity(pid, CREDENTIAL, **kw)
    e2 = u.issue_entity(pid, GATE, **kw).実体
    e3 = u.issue_entity(pid, PHYSICAL, key_number="K-7", **kw).実体
    return got.実体, e2, e3


def test_example_21_6_revoke_subject_makes_one_list(runit, grant, forms):
    """R-AC-15 R-AC-22 21.6 の例: 19.10 の例の revoke_subject で、権限_失効 1 枚、実体_失効 3 枚の後に 失効_一覧 1 枚
    (世代 1、発行時刻は at の unix 秒、識別は E1 の識別)。Revocation.失効_一覧 はその伝票。書く順は 権限_失効、実体_失効、失効_一覧。"""
    u = runit
    pid, _, _ = grant(u)
    e1, e2, e3 = _three(u, pid)
    before = forms(u)
    n = u.ledger.count()
    rev = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert forms(u) - before == {"権限_失効": 1, "実体_失効": 3, "失効_一覧": 1}
    s = rev.失効_一覧
    assert s.form == "失効_一覧"
    assert (s.issuer, s.to, s.basis) == ("発行者", "", [])
    assert (s["世代"], s["発行時刻"], s["識別"], s["操作者"], s["役"]) == \
        ("1", str(unix(REV)), e1.識別, "adm", "管理者")
    assert [x.form for x in u.ledger.select()[n:]] == ["権限_失効", "実体_失効", "実体_失効", "実体_失効", "失効_一覧"]
    assert u.revocations()[-1].番号 == s.id
    assert _body(u.revocation())["revoked"] == [e1.識別]


def test_revoke_progress_is_read_after_list(runit, grant):
    """R-AC-22 21.6: Revocation.進み具合 は一覧を書いた後の progress(subject, at=at)。"""
    u = runit
    pid, _, _ = grant(u)
    e1, _, _ = _three(u, pid)
    rev = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert rev.進み具合 == u.progress("s1", at=dt(REV))
    row = {r.実体: r for r in rev.進み具合.行}[e1.番号]
    assert (row.状態, row.効くまで) == ("待ち", T1)


def test_list_by_roster_role(runit, cred):
    """R-AC-22 21.3: 名簿の係が revoke_subject を呼んだ時も一覧を作る。一覧の 操作者 と 役 は失効を動かした人と役。"""
    u = runit
    cred(u, "s1")
    rev = u.revoke_subject("s1", "退職", by="adm", role="名簿の係", at=dt(REV))
    assert (rev.失効_一覧["操作者"], rev.失効_一覧["役"], rev.失効_一覧.issuer) == ("adm", "名簿の係", "発行者")


def test_revoke_permission_makes_list(runit, cred, forms):
    """R-AC-22 21.6: revoke_permission も、署名つき資格情報の実体に実体_失効を書いたら一覧を一枚作る。"""
    u = runit
    pid, c = cred(u, "s1")
    before = forms(u)
    rev = u.revoke_permission(pid, "紛失", **ADM, at=dt(REV))
    assert forms(u) - before == {"権限_失効": 1, "実体_失効": 1, "失効_一覧": 1}
    assert rev.失効_一覧.form == "失効_一覧" and rev.失効_一覧["識別"] == c.実体.識別


def test_no_list_when_nothing_written_on_credentials(runit, grant, cred, forms):
    """R-AC-22 21.6: 二度目の呼び出し、署名つき資格情報の実体が無い時、止めたのがほかの経路だけの時は一覧を作らず None。"""
    u = runit
    cred(u, "s1")
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    n = u.ledger.count()
    again = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert (again.失効_一覧, u.ledger.count()) == (None, n)
    # 門番だけの主体
    pid, _, _ = grant(u, "s2")
    u.issue_entity(pid, GATE, **ADM, at=AT)
    before = forms(u)
    rev = u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    assert rev.失効_一覧 is None
    assert forms(u) - before == {"権限_失効": 1, "実体_失効": 1}


def test_no_list_for_ended_credential(runit, cred, forms):
    """R-AC-22 21.6: 終了を過ぎた署名つき資格情報には実体_失効を書かない(19.10 の 2)ので、一覧も作らない。"""
    u = runit
    pid, _ = cred(u, "s1", until="2026-12-05T00:00")
    before = forms(u)
    rev = u.revoke_permission(pid, "紛失", **ADM, at=dt(REV))
    assert rev.失効_一覧 is None
    assert forms(u) - before == {"権限_失効": 1}


def test_list_in_revoke_uses_same_at(runit, cred):
    """R-AC-22 21.6: 失効の操作の中の一覧は同じ at で作る(秒の端数は切り捨て)。前の一覧があれば世代はその次。"""
    u = runit
    u.publish_revocations(**ADM, at=dt(PUB))
    cred(u, "s1")
    rev = u.revoke_subject("s1", "退職", **ADM, at=datetime(2026, 12, 10, 12, 0, 15, 900000))
    assert (rev.失効_一覧["世代"], rev.失効_一覧["発行時刻"]) == ("2", str(unix(REV) + 15))


def test_change_device_list_is_none(dunit, access):
    """R-AC-22 21.2、21.14: change_device(紛失)も同じ規則で、機器の権限には署名つき資格情報が無いので DeviceChange.失効_一覧 は None。"""
    _, e = access(dunit, "t1")
    got = dunit.change_device("ルーター1", reason="なくした", state="紛失", at=dt(REV), **ADM)
    assert [s["実体"] for s in got.実体_失効] == [e.番号]
    assert got.失効_一覧 is None
    assert e.経路 == ACCESS
