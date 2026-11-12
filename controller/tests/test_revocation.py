"""失効の一覧(扉コントローラ仕様書 5.2c、第 8 章の試験の対 33〜43、46)の試験。

期待値は仕様書 5.2a と 5.2c から決めている。一覧は二つの道で作る。
- `make_list`: 仕様の形(`body` の欄)をここで組み立て、発行者の鍵で署名する。実装の誤りに引きずられないため
- `issued_list`: 発行者の側のインターフェース `credentials.revocation_list` を使う。発行者の側のインターフェースの確かめ

試験の値のファイル `vectors/revocation-1.json` は、使い捨ての鍵を `Ed25519Signer.generate()` で二つ(信頼する A と、
信頼しない X)作り、`make_list` と同じ組み立てで一覧に、`credentials.issue` で資格情報に署名して書いた。
鍵は書き終えたら捨て、ファイルには公開鍵だけを入れた。作り直すと署名が変わるので、形を変えるときは revocation-2.json にする。

時刻は `set_time` で固定の値を入れ、実時間は待たない。主体は仮名("resident-012" など)。
"""
import copy
import json
import re
from pathlib import Path

import pytest

from digitaldoor_controller import credentials
from digitaldoor_controller.audit import Audit, Ed25519Signer, Ed25519Verifier, HmacSigner
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller
from digitaldoor_controller.slips import key_id

DOOR = "door-1"
T0 = 1_794_700_800          # 仕様書 5.2c の例の issued_at
EVENT = "失効の一覧"
VECTORS = Path(__file__).parent / "vectors" / "revocation-1.json"
DAY = 86_400


# ---- 道具 ----
def iid(signer):
    return key_id(signer.public_key())


def trust_entry(*pairs):
    """trust_entry((signer, doors), ...) で 5.2a の信頼の一覧を作る。"""
    out = {}
    for signer, doors in pairs:
        out[iid(signer)] = {"public_key": signer.public_key().hex(), "doors": list(doors)}
    return out


def new_ctl(trust=None, *, max_age=None, audit=None, admin_key=None, now=T0, synced=True, autolock_s=0):
    config = Config(autolock_s=autolock_s) if max_age is None else Config(autolock_s=autolock_s,
                                                                            revocation_max_age_s=max_age)
    ctl = Controller(SimContacts(), audit or Audit(HmacSigner(b"k" * 32)), config, name=DOOR,
                     admin_key=admin_key, trust=trust)
    ctl.set_time(now, synced)
    return ctl


def make_list(signer, *, generation=1, issued_at=T0, revoked=(), issuer=None, drop=(), **extra):
    """仕様の形(5.2c の body の欄)をここで組み立てて、signer で署名した一覧。形の誤りもこれで作る。"""
    b = {"format": "digitaldoor-revocation/1", "issuer": issuer if issuer is not None else iid(signer),
         "generation": generation, "issued_at": issued_at,
         "revoked": list(revoked) if isinstance(revoked, (list, tuple)) else revoked}
    b.update(extra)
    for k in drop:
        b.pop(k)
    body = json.dumps(b, ensure_ascii=False)
    return {"body": body, "sig": signer.sign(body.encode("utf-8"))}


def issued_list(signer, *, generation=1, issued_at=T0, revoked=()):
    """発行者の側のインターフェースで作った一覧。"""
    return credentials.revocation_list(signer, generation=generation, issued_at=issued_at, revoked=list(revoked))


def edit_body(lst, **over):
    """署名はそのままで body だけ書き換えた一覧。"""
    b = json.loads(lst["body"])
    b.update(over)
    return {"body": json.dumps(b, ensure_ascii=False), "sig": lst["sig"]}


def cred(signer, cid, *, nb=T0 - DAY, na=T0 + 30 * DAY, doors=(DOOR,), once=False, subject=None):
    return credentials.issue(signer, cred_id=cid, subject=subject or f"resident-{cid}", doors=list(doors),
                             not_before=nb, not_after=na, once=once)


def state(generation, issued_at, revoked):
    return {"generation": generation, "issued_at": issued_at, "revoked": list(revoked)}


def auth_entries(ctl):
    return [e for e in ctl.audit.entries if e.kind == "認証"]


def list_entries(ctl):
    return [e for e in ctl.audit.entries if e.kind == "設定" and e.event == EVENT]


def assert_cred_rejected(ctl, result, reason):
    assert result == "拒否"
    assert auth_entries(ctl)[-1].result == reason


def assert_list_rejected(ctl, result, reason):
    assert result == "拒否"
    last = list_entries(ctl)[-1]
    assert last.result == "拒否" and last.detail == reason and last.subject == ""


def expected_detail(issuer, generation, issued_at, revoked):
    """仕様書 5.2c の記録の形。配列は ASCII 以外の文字をそのまま、区切りは既定で書く。"""
    return (f"issuer={issuer} generation={generation} issued_at={issued_at} "
            f"revoked={json.dumps(list(revoked), ensure_ascii=False)}")


def restart(ctl, *, trust="same", max_age=None):
    """同じ記録のファイルで扉を作り直す。時計は未同期で始まる。"""
    audit = Audit(ctl.audit.signer, ctl.audit.path)
    config = copy.deepcopy(ctl.config) if max_age is None else Config(autolock_s=0, revocation_max_age_s=max_age)
    return Controller(SimContacts(), audit, config, name=ctl.name, admin_key=ctl.admin_key,
                      trust=copy.deepcopy(ctl.trust) if trust == "same" else trust)


def file_audit(tmp_path):
    return Audit(HmacSigner(b"k" * 32), tmp_path / "audit.jsonl")


# ---- 発行者の側: credentials.revocation_list ----
def test_revocation_list_shape_and_signature():
    """R-AC-22: revocation_list は {body, sig} を作り、body は仕様の五つの欄を持ち、発行者の公開鍵で署名が合う。"""
    a = Ed25519Signer.generate()
    lst = issued_list(a, generation=7, issued_at=T0, revoked=["c-0012", "c-0031"])
    assert set(lst) == {"body", "sig"} and isinstance(lst["body"], str) and isinstance(lst["sig"], str)
    body = json.loads(lst["body"])
    assert body == {"format": "digitaldoor-revocation/1", "issuer": iid(a), "generation": 7, "issued_at": T0,
                    "revoked": ["c-0012", "c-0031"]}
    assert credentials.REVOCATION_FORMAT == "digitaldoor-revocation/1"
    assert Ed25519Verifier(a.public_key()).verify(lst["body"].encode("utf-8"), lst["sig"])


def test_revocation_list_may_be_empty():
    """R-AC-22: 失効が無くても、世代を上げた空の一覧を作れる(通知を新しくするため)。"""
    a = Ed25519Signer.generate()
    assert json.loads(issued_list(a, generation=1, revoked=[])["body"])["revoked"] == []


@pytest.mark.parametrize("generation", [0, -1])
def test_revocation_list_generation_below_one_is_value_error(generation):
    """R-AC-22: 世代が 1 より小さい一覧は作らない(ValueError)。"""
    with pytest.raises(ValueError):
        issued_list(Ed25519Signer.generate(), generation=generation)


def test_revocation_list_from_issuer_side_is_accepted_by_door():
    """R-AC-22: 発行者の側のインターフェースで作った一覧を、扉が受理する。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(issued_list(a, generation=3, revoked=["c-1"])) == "受理"
    assert ctl.revocations == {iid(a): state(3, T0, ["c-1"])}


def test_reason_constants():
    """R-AC-22 R-AC-14: 理由の言葉の定数は仕様の言葉と同じ。"""
    assert credentials.REVOKED == "失効済み"
    assert credentials.STALE_REVOCATION == "失効の通知が古い"
    assert credentials.OLD_GENERATION == "古い世代"


# ---- 扉の側: verify_revocation、記録の詳細 ----
def test_verify_revocation_results_and_current_is_not_changed():
    """R-AC-22: verify_revocation は、受理なら空、同じ世代なら「同じ」、小さい世代なら「古い世代」を返し、current を変えない。"""
    a = Ed25519Signer.generate()
    trust = trust_entry((a, [DOOR]))
    current = {iid(a): state(5, T0, ["c-1"])}
    snapshot = copy.deepcopy(current)
    lst = make_list(a, generation=6, revoked=["c-1", "c-2"])
    reason, body = credentials.verify_revocation(lst, trust=trust, current=current)
    assert reason == "" and body == json.loads(lst["body"])
    assert credentials.verify_revocation(make_list(a, generation=5), trust=trust, current=current)[0] == "同じ"
    assert credentials.verify_revocation(make_list(a, generation=4), trust=trust, current=current)[0] == "古い世代"
    assert credentials.verify_revocation(make_list(a, generation=1), trust=trust, current={})[0] == ""
    assert current == snapshot


def test_verify_revocation_reasons_in_order():
    """R-AC-22: verify_revocation の拒否の理由は、形、発行者、署名、世代の順に最初に外れた所。"""
    a, x = Ed25519Signer.generate(), Ed25519Signer.generate()
    trust = trust_entry((a, [DOOR]))
    current = {iid(a): state(5, T0, [])}
    v = credentials.verify_revocation
    assert v(make_list(x, generation=0), trust=trust, current=current)[0] == "形式が違う"
    assert v(make_list(x, generation=1), trust=trust, current=current)[0] == "知らない発行者"
    assert v(make_list(x, issuer=iid(a), generation=1), trust=trust, current=current)[0] == "署名が合わない"


def test_revocation_detail_exact_form_and_roundtrip():
    """R-AC-22: 記録の詳細は issuer=.. generation=.. issued_at=.. revoked=<JSON の配列> で、読み戻せる。"""
    body = {"format": "digitaldoor-revocation/1", "issuer": "0123456789abcdef", "generation": 7,
            "issued_at": T0, "revoked": ["c-0012", "鍵 1", 'q"x']}
    detail = credentials.revocation_detail(body)
    assert detail == expected_detail("0123456789abcdef", 7, T0, ["c-0012", "鍵 1", 'q"x'])
    assert credentials.parse_revocation_detail(detail) == {
        "issuer": "0123456789abcdef", "generation": 7, "issued_at": T0, "revoked": ["c-0012", "鍵 1", 'q"x']}
    empty = dict(body, revoked=[])
    assert credentials.parse_revocation_detail(credentials.revocation_detail(empty))["revoked"] == []


@pytest.mark.parametrize("text", ["古い世代", "", "issuer=x generation=y", "形式が違う"])
def test_parse_revocation_detail_returns_none_for_other_text(text):
    """R-AC-22: 受理の詳細でない文は読み戻さない(None)。"""
    assert credentials.parse_revocation_detail(text) is None


# ---- 試験の対 33 ----
def test_pair33_offline_door_accepts_list_and_rejects_revoked():
    """R-AC-22: 試験の対 33。ネットワークの無い扉が一覧を受理し、一覧にある資格情報は「失効済み」、無い資格情報(同じ発行者)は解錠する。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    lst = make_list(a, generation=7, issued_at=T0, revoked=["c-0012", "c-0031"])
    assert ctl.accept_revocation(lst) == "受理"
    assert ctl.revocations == {iid(a): state(7, T0, ["c-0012", "c-0031"])}
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012")), "失効済み")
    assert ctl.locked is True and ctl.contacts.outputs == []
    assert ctl.present(cred(a, "c-0040")) == "解錠"
    assert ctl.audit.verify()[0]


def test_pair33_accept_record_form():
    """R-AC-22: 試験の対 33。受理は 設定/失効の一覧 受理、主体は発行者の識別、詳細は仕様の形で一件だけ残る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    n = len(ctl.audit.entries)
    assert ctl.accept_revocation(make_list(a, generation=7, revoked=["c-0012", "鍵-0031"])) == "受理"
    assert len(ctl.audit.entries) == n + 1
    e = ctl.audit.entries[-1]
    assert (e.kind, e.event, e.result, e.subject) == ("設定", EVENT, "受理", iid(a))
    assert e.detail == expected_detail(iid(a), 7, T0, ["c-0012", "鍵-0031"])


def test_pair33_revoked_rejection_record_has_issuer_and_id():
    """R-AC-22: 試験の対 33。失効済みの拒否は 5.2a の認証の拒否の記録で、詳細に issuer と id が入る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    ctl.accept_revocation(make_list(a, revoked=["c-0012"]))
    ctl.present(cred(a, "c-0012"))
    e = auth_entries(ctl)[-1]
    assert e.result == "失効済み"
    assert f"issuer={iid(a)}" in e.detail and "id=c-0012" in e.detail


def test_pair33_accepting_needs_no_clock_sync():
    """R-AC-22: 試験の対 33。一覧を受けるのに時計の同期は要らない(未同期でも受理する)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), synced=False)
    assert ctl.accept_revocation(make_list(a, generation=2, revoked=["c-1"])) == "受理"
    assert ctl.revocations[iid(a)]["generation"] == 2


def test_pair33_list_of_one_issuer_does_not_touch_other_issuer():
    """R-AC-22: 試験の対 33。状態は発行者ごとで、A の一覧は B の資格情報を止めない(同じ id でも)。"""
    a, b = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR]), (b, [DOOR])))
    assert ctl.accept_revocation(make_list(a, revoked=["c-1"])) == "受理"
    assert ctl.present(cred(b, "c-1")) == "解錠"
    assert set(ctl.revocations) == {iid(a)}


def test_pair33_extra_fields_are_not_read_or_kept():
    """R-AC-22: 試験の対 33。body のほかの欄は読まず、扉の状態には仕様の三つの欄だけを持つ。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(make_list(a, generation=3, revoked=["c-1"], note="extra", door="door-9")) == "受理"
    assert ctl.revocations == {iid(a): state(3, T0, ["c-1"])}


def test_revocations_is_empty_before_any_list():
    """R-AC-22: 一覧を一度も受けていない扉の状態は空である。"""
    ctl = new_ctl(trust_entry((Ed25519Signer.generate(), [DOOR])))
    assert ctl.revocations == {}


# ---- 試験の対 34 ----
def test_pair34_list_carried_with_another_persons_credential():
    """R-AC-22: 試験の対 34。一覧を持たない扉に、ほかの人の資格情報と一緒に一覧を運ぶと、ほかの人は解錠し一覧は受理。以後一覧にある資格情報は「失効済み」。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    lst = make_list(a, generation=7, revoked=["c-0012", "c-0031"])
    assert ctl.present(cred(a, "c-0040"), revocation=lst) == "解錠"
    assert ctl.revocations == {iid(a): state(7, T0, ["c-0012", "c-0031"])}
    accepted = list_entries(ctl)
    assert len(accepted) == 1 and accepted[0].result == "受理"
    ctl.lock_request()
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012")), "失効済み")
    assert ctl.locked is True


def test_pair34_list_is_checked_before_the_credential():
    """R-AC-22: 試験の対 34。一緒に運んだ一覧は資格情報より先に確かめ、記録も一覧の方が先に残る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    n = len(ctl.audit.entries)
    ctl.present(cred(a, "c-0040"), revocation=make_list(a, revoked=["c-0012"]))
    added = ctl.audit.entries[n:]
    kinds = [(e.kind, e.event) for e in added]
    assert kinds.index(("設定", EVENT)) < next(i for i, k in enumerate(kinds) if k[0] == "認証")


def test_pair34_carried_list_applies_to_the_same_credential_and_stays():
    """R-AC-22: 試験の対 34。受理した一覧はその資格情報の確かめに使い、資格情報が拒まれても一覧は残る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    lst = make_list(a, generation=4, revoked=["c-0012"])
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012"), revocation=lst), "失効済み")
    assert ctl.revocations == {iid(a): state(4, T0, ["c-0012"])}
    assert [e.result for e in list_entries(ctl)] == ["受理"]


def test_pair34_rejected_carried_list_does_not_stop_the_credential():
    """R-AC-22: 試験の対 34。一緒に運んだ一覧が拒否でも、続けて資格情報を確かめる(正しい資格情報は解錠)。拒否は記録に残る。"""
    a, x = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    bad = make_list(x, issuer=iid(a), revoked=["c-0040"])
    assert ctl.present(cred(a, "c-0040"), revocation=bad) == "解錠"
    assert ctl.revocations == {}
    assert_list_rejected(ctl, "拒否", "署名が合わない")


def test_pair34_same_list_carried_every_time_is_not_recorded():
    """R-AC-22: 試験の対 34。端末がかざすたびに同じ一覧を運んでも「同じ」で、一覧の記録は増えない。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    lst = make_list(a, generation=2, revoked=["c-0012"])
    for _ in range(3):
        assert ctl.present(cred(a, "c-0040"), revocation=lst) == "解錠"
    assert [e.result for e in list_entries(ctl)] == ["受理"]


def test_pair34_present_without_revocation_is_as_before():
    """R-AC-22: 試験の対 34。revocation を渡さない present は一覧の記録を作らない。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.present(cred(a, "c-0040")) == "解錠"
    assert list_entries(ctl) == [] and ctl.revocations == {}


# ---- 試験の対 35 ----
def test_pair35_older_generation_cannot_roll_back():
    """R-AC-22: 試験の対 35。世代 2 の後に世代 1(失効が少ない)は「古い世代」で拒み、状態は世代 2 のまま、失効した資格情報は拒まれたまま。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(make_list(a, generation=2, issued_at=T0, revoked=["c-1", "c-2"])) == "受理"
    n = len(ctl.audit.entries)
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(a, generation=1, issued_at=T0 + 10, revoked=["c-1"])),
                         "古い世代")
    assert len(ctl.audit.entries) == n + 1
    assert ctl.revocations == {iid(a): state(2, T0, ["c-1", "c-2"])}
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-2")), "失効済み")


def test_pair35_same_generation_is_same_and_not_recorded():
    """R-AC-22: 試験の対 35。世代 2 をもう一度送ると「同じ」で、記録は増えない。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    lst = make_list(a, generation=2, revoked=["c-1"])
    assert ctl.accept_revocation(lst) == "受理"
    n = len(ctl.audit.entries)
    assert ctl.accept_revocation(lst) == "同じ"
    assert len(ctl.audit.entries) == n


def test_pair35_same_generation_with_other_content_is_same_and_ignored():
    """R-AC-22: 試験の対 35。同じ世代で中身の違う一覧も「同じ」で、中身は見ない(状態も記録も変わらない)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(make_list(a, generation=2, revoked=["c-1"])) == "受理"
    n = len(ctl.audit.entries)
    assert ctl.accept_revocation(make_list(a, generation=2, issued_at=T0 + 99, revoked=[])) == "同じ"
    assert len(ctl.audit.entries) == n
    assert ctl.revocations == {iid(a): state(2, T0, ["c-1"])}
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-1")), "失効済み")


def test_pair35_newer_generation_replaces_the_whole_list():
    """R-AC-22: 試験の対 35。一覧は全部であり差分ではない。新しい世代を受理すると前の一覧を丸ごと置き換える。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(make_list(a, generation=2, revoked=["c-1"])) == "受理"
    assert ctl.accept_revocation(make_list(a, generation=5, issued_at=T0 + 60, revoked=["c-2"])) == "受理"
    assert ctl.revocations == {iid(a): state(5, T0 + 60, ["c-2"])}
    assert ctl.present(cred(a, "c-1")) == "解錠"
    ctl.lock_request()
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-2")), "失効済み")


def test_pair35_first_list_of_an_issuer_may_have_any_generation():
    """R-AC-22: 試験の対 35。まだ一覧を持たない発行者は、どの世代(1 以上)も受理する。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(make_list(a, generation=1)) == "受理"
    b = Ed25519Signer.generate()
    ctl2 = new_ctl(trust_entry((b, [DOOR])))
    assert ctl2.accept_revocation(make_list(b, generation=10**6)) == "受理"


# ---- 試験の対 36 ----
def _bad_lists(a, x):
    good = make_list(a, generation=3, revoked=["c-1"])
    return {
        "別の鍵で署名": (make_list(x, issuer=iid(a), generation=9, revoked=[]), "署名が合わない"),
        "body を書き換え": (edit_body(good, generation=9, revoked=[]), "署名が合わない"),
        "body に空白を足す": ({"body": good["body"] + " ", "sig": good["sig"]}, "署名が合わない"),
        "信頼していない発行者": (make_list(x, generation=9, revoked=["c-2"]), "知らない発行者"),
        "世代が 0": (make_list(a, generation=0), "形式が違う"),
        "世代が負": (make_list(a, generation=-3), "形式が違う"),
        "世代が真偽": (make_list(a, generation=True), "形式が違う"),
        "世代が文字列": (make_list(a, generation="9"), "形式が違う"),
        "世代が小数": (make_list(a, generation=9.0), "形式が違う"),
        "世代が無い": (make_list(a, drop=("generation",)), "形式が違う"),
        "revoked が文字列": (make_list(a, generation=9, revoked="c-1"), "形式が違う"),
        "revoked に数": (make_list(a, generation=9, revoked=["c-1", 2]), "形式が違う"),
        "revoked が無い": (make_list(a, generation=9, drop=("revoked",)), "形式が違う"),
        "issued_at が文字列": (make_list(a, generation=9, issued_at="1794700800"), "形式が違う"),
        "issued_at が真偽": (make_list(a, generation=9, issued_at=True), "形式が違う"),
        "issued_at が無い": (make_list(a, generation=9, drop=("issued_at",)), "形式が違う"),
        "format が違う": (make_list(a, generation=9, format="digitaldoor-credential/1"), "形式が違う"),
        "issuer が無い": (make_list(a, generation=9, drop=("issuer",)), "形式が違う"),
        "issuer が数": (make_list(a, generation=9, issuer=12), "形式が違う"),
        "body が JSON でない": ({"body": "{", "sig": "00"}, "形式が違う"),
        "body が辞書でない": ({"body": "[1, 2]", "sig": "00"}, "形式が違う"),
        "sig が無い": ({"body": good["body"]}, "形式が違う"),
        "body が無い": ({"sig": good["sig"]}, "形式が違う"),
        "包みが辞書でない": ("not a list", "形式が違う"),
        "None": (None, "形式が違う"),
    }


@pytest.mark.parametrize("name", list(_bad_lists(Ed25519Signer.generate(), Ed25519Signer.generate())))
def test_pair36_bad_lists_are_rejected_and_change_nothing(name):
    """R-AC-22: 試験の対 36。別の鍵、書き換え、信頼していない発行者、形の違う一覧は、仕様の理由で拒み、状態は変わらず、拒否が記録に残る。"""
    a, x = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(make_list(a, generation=3, revoked=["c-1"])) == "受理"
    before = copy.deepcopy(ctl.revocations)
    lst, reason = _bad_lists(a, x)[name]
    n = len(ctl.audit.entries)
    assert_list_rejected(ctl, ctl.accept_revocation(lst), reason)
    assert len(ctl.audit.entries) == n + 1
    assert ctl.revocations == before
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-1")), "失効済み")


def test_pair36_check_order_form_issuer_signature_generation():
    """R-AC-22: 試験の対 36。受ける時の順番は 形、発行者、署名、世代。最初に外れた所の理由になる。"""
    a, x = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.accept_revocation(make_list(a, generation=5)) == "受理"
    # 形が先: 知らない発行者の、世代 0 の一覧
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(x, generation=0)), "形式が違う")
    # 発行者が署名より先: 知らない発行者の、署名の壊れた古い世代
    bad = make_list(x, generation=1)
    bad["sig"] = "00" * 64
    assert_list_rejected(ctl, ctl.accept_revocation(bad), "知らない発行者")
    # 署名が世代より先: A を名乗り別の鍵で署名した古い世代
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(x, issuer=iid(a), generation=1)), "署名が合わない")
    # 署名が世代より先: A を名乗り別の鍵で署名した同じ世代も「同じ」ではなく拒否
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(x, issuer=iid(a), generation=5)), "署名が合わない")
    assert ctl.revocations[iid(a)]["generation"] == 5


def test_pair36_list_signed_by_admin_key_is_not_an_issuer_list():
    """R-AC-22: 試験の対 36。一覧に署名するのは発行者の鍵で、管理者の鍵ではない。管理者の鍵で A を名乗った一覧は「署名が合わない」。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), admin_key=admin.public_key())
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(admin, issuer=iid(a), generation=1)), "署名が合わない")
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(admin, generation=1)), "知らない発行者")
    assert ctl.revocations == {}


# ---- 試験の対 37 ----
def test_pair37_state_is_rebuilt_from_record_after_restart(tmp_path):
    """R-AC-22: 試験の対 37。一覧を受けた後に再起動して記録を読み直すと、失効した資格情報は「失効済み」、前より小さい世代は「古い世代」。"""
    a = Ed25519Signer.generate()
    first = new_ctl(trust_entry((a, [DOOR])), audit=file_audit(tmp_path))
    assert first.accept_revocation(make_list(a, generation=3, issued_at=T0, revoked=["c-0012"])) == "受理"
    assert first.accept_revocation(make_list(a, generation=4, issued_at=T0 + 60, revoked=["c-0012", "c-0031"])) == "受理"
    assert first.accept_revocation(make_list(a, generation=2)) == "拒否"

    second = restart(first)
    assert second.revocations == {iid(a): state(4, T0 + 60, ["c-0012", "c-0031"])}
    second.set_time(T0 + 100, True)
    assert_cred_rejected(second, second.present(cred(a, "c-0031")), "失効済み")
    assert_list_rejected(second, second.accept_revocation(make_list(a, generation=3, revoked=[])), "古い世代")
    assert second.present(cred(a, "c-0040")) == "解錠"
    assert second.audit.verify()[0]


def test_pair37_rebuild_includes_issuers_not_in_trust(tmp_path):
    """R-AC-22: 試験の対 37。作り直しは信頼の一覧とは別に行い、信頼の一覧に無い発行者の状態も作り直す。"""
    a, b = Ed25519Signer.generate(), Ed25519Signer.generate()
    first = new_ctl(trust_entry((a, [DOOR]), (b, [DOOR])), audit=file_audit(tmp_path))
    assert first.accept_revocation(make_list(a, generation=2, revoked=["c-a"])) == "受理"
    assert first.accept_revocation(make_list(b, generation=6, revoked=["鍵-b"])) == "受理"
    second = restart(first, trust=trust_entry((b, [DOOR])))
    assert second.revocations == {iid(a): state(2, T0, ["c-a"]), iid(b): state(6, T0, ["鍵-b"])}


def test_pair37_restart_without_any_list_has_empty_state(tmp_path):
    """R-AC-22: 試験の対 37。拒否の記録しか無い記録から作り直すと、状態は空のまま。"""
    a, x = Ed25519Signer.generate(), Ed25519Signer.generate()
    first = new_ctl(trust_entry((a, [DOOR])), audit=file_audit(tmp_path))
    assert first.accept_revocation(make_list(x, generation=3)) == "拒否"
    assert restart(first).revocations == {}


# ---- 試験の対 38 ----
def test_pair38_state_survives_removing_and_re_adding_the_issuer():
    """R-AC-22: 試験の対 38。一覧を受けた発行者を管理者の鍵で外して足し直しても、小さい世代は「古い世代」、失効した資格情報は「失効済み」。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())

    def change(action, req_id):
        return credentials.trust_change(admin, door=DOOR, action=action, issuer_public_key=a.public_key(),
                                        doors=[DOOR] if action == "add" else None, req_id=req_id)

    assert ctl.change_trust(change("add", "r-1")) == "受理"
    assert ctl.accept_revocation(make_list(a, generation=5, revoked=["c-0012"])) == "受理"
    assert ctl.change_trust(change("remove", "r-2")) == "受理"
    assert ctl.revocations == {iid(a): state(5, T0, ["c-0012"])}
    # 外している間: 資格情報は順番の 2 で拒み、一覧も知らない発行者として拒む
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012")), "知らない発行者")
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(a, generation=9)), "知らない発行者")
    assert ctl.change_trust(change("add", "r-3")) == "受理"
    assert_list_rejected(ctl, ctl.accept_revocation(make_list(a, generation=4, revoked=[])), "古い世代")
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012")), "失効済み")
    assert ctl.revocations == {iid(a): state(5, T0, ["c-0012"])}


# ---- 試験の対 39 ----
def test_pair39_revoked_and_expired_is_revoked():
    """R-AC-22: 試験の対 39。失効済みで期限の外の資格情報は「失効済み」(順番の 5 は期限より先)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    ctl.accept_revocation(make_list(a, revoked=["c-old"]))
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-old", nb=T0 - 2 * DAY, na=T0 - DAY)), "失効済み")
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-future", nb=T0 + DAY, na=T0 + 2 * DAY)), "期限の外")


def test_pair39_revoked_while_clock_unsynced_is_revoked():
    """R-AC-22: 試験の対 39。時計が未同期のままの失効済みの資格情報は「失効済み」(順番の 5 は時計より先)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), synced=False)
    ctl.accept_revocation(make_list(a, revoked=["c-0012"]))
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012")), "失効済み")
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0040")), "時刻が未同期")


def test_pair39_revoked_and_out_of_range_is_out_of_range():
    """R-AC-22: 試験の対 39。失効済みで範囲の外の資格情報は「範囲の外」(順番の 4 が 5 より先)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    ctl.accept_revocation(make_list(a, revoked=["c-0012"]))
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012", doors=["door-2"])), "範囲の外")


def test_pair39_revoked_with_bad_signature_is_bad_signature():
    """R-AC-22: 試験の対 39。失効済みの id で body を書き換えた資格情報は「署名が合わない」(順番の 3 が 5 より先)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    ctl.accept_revocation(make_list(a, revoked=["c-0012"]))
    c = cred(a, "c-0012")
    b = json.loads(c["body"])
    b["subject"] = "resident-999"
    assert_cred_rejected(ctl, ctl.present({"body": json.dumps(b, ensure_ascii=False), "sig": c["sig"]}),
                         "署名が合わない")


def test_pair39_revoked_once_credential_is_revoked_not_used():
    """R-AC-22: 試験の対 39。使った一回限りの資格情報を後から失効させると「失効済み」(順番の 5 は 9 より先)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    c = cred(a, "c-once", once=True)
    assert ctl.present(c) == "解錠"
    ctl.accept_revocation(make_list(a, revoked=["c-once"]))
    assert_cred_rejected(ctl, ctl.present(c), "失効済み")


def test_pair39_revoked_before_stale():
    """R-AC-22 R-AC-14: 試験の対 39。失効済みで通知も古いときは「失効済み」(順番の 5 は 8 より先)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=3600)
    ctl.accept_revocation(make_list(a, issued_at=T0, revoked=["c-0012"]))
    ctl.set_time(T0 + 7200, True)
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-0012")), "失効済み")


def test_verify_credential_new_arguments_are_optional():
    """R-AC-22 R-AC-14: verify_credential は revocations が None なら順番の 5 を、max_age が 0 なら順番の 8 を見ない。"""
    a = Ed25519Signer.generate()
    trust = trust_entry((a, [DOOR]))
    c = cred(a, "c-0012")
    kw = dict(door=DOOR, trust=trust, now=T0, synced=True, used_ids=set())
    assert credentials.verify_credential(c, **kw).ok
    revs = {iid(a): state(1, T0, ["c-0012"])}
    assert credentials.verify_credential(c, **kw, revocations=revs).reason == "失効済み"
    assert credentials.verify_credential(c, **kw, revocations={}, max_age=0.0).ok
    assert credentials.verify_credential(c, **kw, revocations={}, max_age=60.0).reason == "失効の通知が古い"
    fresh = {iid(a): state(1, T0, [])}
    assert credentials.verify_credential(c, **kw, revocations=fresh, max_age=60.0).ok


# ---- 試験の対 40 ----
def test_pair40_stale_after_max_age_long_rejected_short_passes_then_new_list():
    """R-AC-14: 試験の対 40。issued_at から設定の秒数を過ぎると長い資格情報は「失効の通知が古い」、短い物は解錠、新しい世代を受けると長い物も解錠。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=3600)
    assert ctl.accept_revocation(make_list(a, generation=1, issued_at=T0)) == "受理"
    long_ = cred(a, "c-long")
    short = cred(a, "c-short", nb=T0 + 3000, na=T0 + 3000 + 3600)
    assert ctl.present(long_) == "解錠"
    ctl.lock_request()
    ctl.set_time(T0 + 3601, True)
    assert_cred_rejected(ctl, ctl.present(long_), "失効の通知が古い")
    assert ctl.locked is True
    assert ctl.present(short) == "解錠"
    ctl.lock_request()
    assert ctl.accept_revocation(make_list(a, generation=2, issued_at=T0 + 3500)) == "受理"
    assert ctl.present(long_) == "解錠"


def test_pair40_boundaries():
    """R-AC-14: 試験の対 40。古いのは now - issued_at が設定を超えたとき。長さが設定と等しい資格情報は通す。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=3600)
    ctl.accept_revocation(make_list(a, issued_at=T0))
    long_ = cred(a, "c-long")
    equal = cred(a, "c-equal", nb=T0 + 3000, na=T0 + 3000 + 3600)
    ctl.set_time(T0 + 3600, True)
    assert ctl.present(long_) == "解錠"                       # ちょうど設定の秒数はまだ古くない
    ctl.lock_request()
    ctl.set_time(T0 + 3600 + 1, True)
    assert_cred_rejected(ctl, ctl.present(long_), "失効の通知が古い")
    assert ctl.present(equal) == "解錠"                       # 長さが設定以下は通す


def test_pair40_age_is_measured_by_issued_at_not_by_receipt():
    """R-AC-14: 試験の対 40。古さは扉が受け取った時刻ではなく一覧の issued_at で量る。古い issued_at の一覧を今受けても通知は古い。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=3600, now=T0 + 10_000)
    assert ctl.accept_revocation(make_list(a, generation=1, issued_at=T0)) == "受理"
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-long")), "失効の通知が古い")


def test_pair40_stale_record_has_issuer_and_id():
    """R-AC-14: 試験の対 40。「失効の通知が古い」の拒否は認証の拒否の記録で、詳細に issuer と id が入る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=60)
    ctl.present(cred(a, "c-long"))
    e = auth_entries(ctl)[-1]
    assert e.result == "失効の通知が古い" and f"issuer={iid(a)}" in e.detail and "id=c-long" in e.detail


def test_pair40_stale_is_checked_after_clock_and_period():
    """R-AC-14: 試験の対 40。順番の 8 は 6(時計)と 7(期限)の後。未同期なら「時刻が未同期」、期限の外なら「期限の外」。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=60, synced=False)
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-long")), "時刻が未同期")
    ctl.set_time(T0, True)
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-exp", nb=T0 - 2 * DAY, na=T0 - DAY)), "期限の外")


def test_pair40_stale_is_checked_before_used():
    """R-AC-14: 試験の対 40。順番の 8 は 9(使用済み)より先。使った一回限りの長い資格情報は、通知が古くなると「失効の通知が古い」。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=3600)
    ctl.accept_revocation(make_list(a, issued_at=T0))
    c = cred(a, "c-once", once=True)
    assert ctl.present(c) == "解錠"
    ctl.lock_request()
    ctl.set_time(T0 + 7200, True)
    assert_cred_rejected(ctl, ctl.present(c), "失効の通知が古い")


# ---- 試験の対 41 ----
def test_pair41_resending_old_list_does_not_refresh():
    """R-AC-14: 試験の対 41。古くなった扉に前に受けた一覧(同じ世代)を送り直すと「同じ」で、長い資格情報は「失効の通知が古い」のまま。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=3600)
    lst = make_list(a, generation=3, issued_at=T0)
    assert ctl.accept_revocation(lst) == "受理"
    ctl.set_time(T0 + 5000, True)
    assert ctl.accept_revocation(lst) == "同じ"
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-long")), "失効の通知が古い")
    assert ctl.revocations[iid(a)]["issued_at"] == T0
    # 端末が一緒に運んでも同じ
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-long"), revocation=lst), "失効の通知が古い")


# ---- 試験の対 42 ----
def test_pair42_never_received_long_rejected_short_passes():
    """R-AC-14: 試験の対 42。設定を入れ、一覧を一度も受けていない扉は、長い資格情報を「失効の通知が古い」で拒み、短い物は解錠する。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=3600)
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-long")), "失効の通知が古い")
    assert ctl.present(cred(a, "c-short", nb=T0 - 600, na=T0 + 3000)) == "解錠"


def test_pair42_zero_setting_does_not_use_the_rule():
    """R-AC-14: 試験の対 42。設定が 0 の扉は、一覧を受けずに長い資格情報を出しても解錠する。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])), max_age=0)
    assert ctl.present(cred(a, "c-long", nb=T0 - 365 * DAY, na=T0 + 365 * DAY)) == "解錠"


def test_pair42_default_setting_is_zero():
    """R-AC-14: 試験の対 42。revocation_max_age_s の既定は 0 で、今までの動きを変えない。"""
    assert Config().revocation_max_age_s == 0
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.config.revocation_max_age_s == 0
    assert ctl.present(cred(a, "c-long")) == "解錠"


def test_pair42_age_is_per_issuer():
    """R-AC-14: 試験の対 42。通知の古さは発行者ごと。B の新しい一覧は、一覧を受けていない A の長い資格情報を通さない。"""
    a, b = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR]), (b, [DOOR])), max_age=3600)
    assert ctl.accept_revocation(make_list(b, issued_at=T0)) == "受理"
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-long")), "失効の通知が古い")
    assert ctl.present(cred(b, "c-long-b")) == "解錠"


# ---- 試験の対 43 ----
def test_pair43_configure_records_before_and_after():
    """R-AC-14 R-CT-22: 試験の対 43。configure で revocation_max_age_s を変えると、前の値と新しい値が設定の記録に残り、すぐ効く。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry((a, [DOOR])))
    assert ctl.present(cred(a, "c-long")) == "解錠"
    ctl.lock_request()
    ctl.configure("管理者", revocation_max_age_s=3600)
    assert ctl.config.revocation_max_age_s == 3600
    rows = [e for e in ctl.audit.entries if e.kind == "設定" and e.event == "revocation_max_age_s"]
    assert len(rows) == 1
    assert re.search(r"\b0(\.0)?\b", rows[0].detail) and "3600" in rows[0].detail
    assert rows[0].subject == "管理者"
    assert_cred_rejected(ctl, ctl.present(cred(a, "c-long")), "失効の通知が古い")
    ctl.configure("管理者", revocation_max_age_s=0)
    assert ctl.present(cred(a, "c-long")) == "解錠"


@pytest.mark.parametrize("bad", [-1, -0.5, "3600", True, None, [3600]])
def test_pair43_bad_values_are_rejected_and_change_nothing(bad):
    """R-AC-14 R-CT-22: 試験の対 43。負の値と数でない値は拒み(ValueError)、設定も記録も変えない。"""
    ctl = new_ctl(max_age=60)
    n = len(ctl.audit.entries)
    with pytest.raises(ValueError):
        ctl.configure("管理者", revocation_max_age_s=bad)
    assert ctl.config.revocation_max_age_s == 60 and len(ctl.audit.entries) == n


# ---- 試験の対 46: 試験の値のファイル ----
def load_vectors():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_pair46_vector_file_has_the_specified_shape_and_no_secret():
    """R-AC-22 R-AC-14: 試験の対 46。試験の値のファイルは仕様の欄を持ち、秘密鍵を持たない(64 桁の 16 進は公開鍵だけ)。"""
    doc = load_vectors()
    assert doc["format"] == "digitaldoor-test-vectors/1"
    assert doc["requirements"] == ["R-AC-22", "R-AC-14"]
    assert {"door", "keys", "trust", "config", "start", "steps"} <= set(doc)
    publics = set()
    for k in doc["keys"].values():
        assert set(k) == {"public_key", "issuer"}
        assert key_id(bytes.fromhex(k["public_key"])) == k["issuer"]
        publics.add(k["public_key"])
    assert any(k["issuer"] not in doc["trust"] for k in doc["keys"].values())   # 信頼しない発行者も入れる
    text = VECTORS.read_text(encoding="utf-8")
    assert "private" not in text.lower() and "secret" not in text.lower()
    for hex64 in re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text):
        assert hex64 in publics
    ops = {s["op"] for s in doc["steps"]}
    assert {"revocation", "present", "time", "restart", "configure"} <= ops


def test_pair46_vector_steps_give_the_written_results(tmp_path):
    """R-AC-22 R-AC-14: 試験の対 46。試験の値のファイルの操作を順に行うと、どの操作もファイルに書いた結果になる。"""
    doc = load_vectors()
    audit_signer = HmacSigner(b"k" * 32)
    path = tmp_path / "audit.jsonl"

    def boot():
        return Controller(SimContacts(), Audit(audit_signer, path), Config(**doc["config"]), name=doc["door"],
                          trust=copy.deepcopy(doc["trust"]))

    ctl = boot()
    ctl.set_time(doc["start"]["now"], doc["start"]["synced"])
    for i, step in enumerate(doc["steps"]):
        where = f"steps[{i}] {step['op']} {step.get('note', '')}"
        n = len(ctl.audit.entries)
        op = step["op"]
        if op == "revocation":
            got = ctl.accept_revocation(step["list"])
            assert got == step["expect"], where
            if got == "拒否":
                assert list_entries(ctl)[-1].detail == step["reason"], where
        elif op == "present":
            kw = {}
            if "revocation" in step:
                kw["revocation"] = step["revocation"]
            got = ctl.present(step["credential"], step.get("route", "遠隔"), **kw)
            assert got == step["expect"], where
            if got == "拒否":
                assert auth_entries(ctl)[-1].result == step["reason"], where
            if "revocation" in step:
                listed = [e for e in ctl.audit.entries[n:] if e.kind == "設定" and e.event == EVENT]
                want = step["revocation_expect"]
                if want == "同じ":
                    assert listed == [], where
                else:
                    assert [e.result for e in listed] == [want], where
        elif op == "time":
            ctl.set_time(step["now"], step["synced"])
        elif op == "configure":
            ctl.configure("試験の値", **step["set"])
        elif op == "restart":
            ctl = boot()
            assert ctl.synced is False, where
        else:
            pytest.fail(f"知らない操作: {where}")
        if "audit_added" in step:
            assert len(ctl.audit.entries) - (0 if op == "restart" else n) == step["audit_added"], where
        if "revocations" in step:
            assert ctl.revocations == step["revocations"], where
    assert ctl.audit.verify()[0]
