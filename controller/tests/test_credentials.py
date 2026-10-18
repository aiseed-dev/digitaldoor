"""発行者の署名つき資格情報(扉コントローラ仕様書 5.2a、試験の対 18〜25)の試験。

発行者の側の `credentials.issue` / `credentials.trust_change` で作り、扉の側の
`Controller.present` / `Controller.change_trust` で確かめる。時刻は `set_time` で与え、実時間は待たない。
主体は仮名("resident-101" など)。
"""
import json

import pytest

from digitaldoor_controller import credentials
from digitaldoor_controller.audit import Audit, Ed25519Signer, Ed25519Verifier, HmacSigner
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller
from digitaldoor_controller.slips import key_id

DOOR = "door-1"
NOW = 1000


# ---- 道具 ----
def trust_entry(signer, doors):
    pub = signer.public_key()
    return {key_id(pub): {"public_key": pub.hex(), "doors": list(doors)}}


def new_ctl(trust=None, *, name=DOOR, admin_key=None, audit=None, config=None, synced=True, now=NOW):
    ctl = Controller(SimContacts(), audit or Audit(HmacSigner(b"k" * 32)), config or Config(autolock_s=5),
                     name=name, admin_key=admin_key, trust=trust)
    ctl.set_time(now, synced)
    return ctl


def cred(signer, *, cid="c-1", doors=(DOOR,), nb=NOW - 100, na=NOW + 1000, once=False, subject="resident-101"):
    return credentials.issue(signer, cred_id=cid, subject=subject, doors=list(doors),
                             not_before=nb, not_after=na, once=once)


def forge(signer, *, issuer=None, drop=(), **over):
    """発行の関数を通さずに body を組み、signer で署名する(形式や署名の誤りを作るため)。"""
    b = {"format": "digitaldoor-credential/1", "issuer": issuer or key_id(signer.public_key()), "id": "f-1",
         "subject": "resident-101", "doors": [DOOR], "not_before": NOW - 100, "not_after": NOW + 1000,
         "once": False}
    b.update(over)
    for k in drop:
        b.pop(k)
    body = json.dumps(b, ensure_ascii=False)
    return {"body": body, "sig": signer.sign(body.encode("utf-8"))}


def edit_body(c, **over):
    """署名はそのままで body だけ書き換えた資格情報。"""
    b = json.loads(c["body"])
    b.update(over)
    return {"body": json.dumps(b, ensure_ascii=False), "sig": c["sig"]}


def auth_entries(ctl):
    return [e for e in ctl.audit.entries if e.kind == "認証"]


def assert_rejected(ctl, result, reason, *, check_lock=True):
    assert result == "拒否"
    assert auth_entries(ctl)[-1].result == reason
    if check_lock:
        assert ctl.locked is True


def is_reject(e):
    return "拒否" in e.event or "拒否" in e.result


def trust_changes(ctl):
    """受けた変更の記録(拒否の記録は除く)。"""
    return [e for e in ctl.audit.entries if e.kind == "設定" and e.event == "信頼する発行者" and not is_reject(e)]


def trust_rejections(ctl):
    return [e for e in ctl.audit.entries if e.kind == "設定" and is_reject(e)]


def change(admin, action, issuer, doors=None, req_id="req-1", door=DOOR):
    return credentials.trust_change(admin, door=door, action=action, issuer_public_key=issuer.public_key(),
                                    doors=doors, req_id=req_id)


# ---- 発行者の側 ----
def test_issue_shape():
    """R-AC-09: 発行した資格情報は body と sig を持ち、body は発行者の識別と指定の欄を持ち、公開鍵で署名が合う。"""
    issuer = Ed25519Signer.generate()
    c = cred(issuer, cid="c-9", doors=[DOOR, "door-2"], once=True)
    assert set(c) == {"body", "sig"} and isinstance(c["body"], str)
    body = json.loads(c["body"])
    assert body["format"] == "digitaldoor-credential/1"
    assert body["issuer"] == key_id(issuer.public_key())
    assert body["id"] == "c-9" and body["subject"] == "resident-101"
    assert body["doors"] == [DOOR, "door-2"]
    assert body["not_before"] == NOW - 100 and body["not_after"] == NOW + 1000 and body["once"] is True
    assert Ed25519Verifier(issuer.public_key()).verify(c["body"].encode("utf-8"), c["sig"])


def test_issue_once_defaults_to_false():
    """R-AC-10: once を指定しない資格情報は一回限りではない。"""
    c = cred(Ed25519Signer.generate())
    assert json.loads(c["body"])["once"] is False


# ---- 試験の対 18 ----
def test_pair18_trusted_issuer_unlocks_and_records_issuer_and_id():
    """R-AC-09: 発行者 A を信頼し、範囲にこの扉がある扉で A の資格情報を出すと解錠し、記録に発行者と識別が残る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    assert ctl.present(cred(a, cid="c-18"), route="遠隔") == "解錠"
    assert ctl.locked is False and ctl.contacts.outputs == ["unlock"]
    last = auth_entries(ctl)[-1]
    assert f"issuer={key_id(a.public_key())}" in last.detail and "id=c-18" in last.detail
    assert ctl.audit.verify()[0]


def test_pair18_route_defaults_to_remote():
    """R-AC-10: route を省くと経路は「遠隔」として記録に残る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    assert ctl.present(cred(a)) == "解錠"
    unlock = [e for e in ctl.audit.entries if e.kind == "操作" and e.event == "解錠"][-1]
    assert unlock.route == "遠隔"


def test_pair18_trust_given_at_install_is_readable():
    """R-AC-09: 据付で入れた信頼の一覧が ctl.trust に出る。入れなければ空である。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    assert ctl.trust[key_id(a.public_key())]["public_key"] == a.public_key().hex()
    assert ctl.trust[key_id(a.public_key())]["doors"] == [DOOR]
    assert not new_ctl().trust


def test_non_once_credential_can_be_used_again_without_use_record():
    """R-AC-10: 一回限りでない資格情報は何度でも通り、「使用」の記録は足されない。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a, cid="c-multi")
    assert ctl.present(c) == "解錠"
    assert ctl.present(c) == "解錠"
    assert not [e for e in ctl.audit.entries if e.kind == "資格情報"]


# ---- 試験の対 19 ----
def test_pair19_outside_issuer_range_by_trust_list():
    """R-AC-09: 資格情報の doors にこの扉があっても、信頼の一覧の範囲(発行者 A に許す扉)に無ければ「範囲の外」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, ["door-1"]), name="door-2")
    c = cred(a, cid="c-19a", doors=["door-1", "door-2"])
    assert_rejected(ctl, ctl.present(c), "範囲の外")
    assert "id=c-19a" in auth_entries(ctl)[-1].detail
    assert ctl.contacts.outputs == []


def test_pair19_outside_credential_doors():
    """R-AC-09: 一覧の範囲にこの扉があっても、資格情報の doors にこの扉が無ければ「範囲の外」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, ["door-1", "door-2"]), name="door-2")
    assert_rejected(ctl, ctl.present(cred(a, doors=["door-1"])), "範囲の外")


def test_pair19_unknown_issuer():
    """R-AC-09: A を信頼しない扉(別の顧客の扉)に A の資格情報を出すと「知らない発行者」で拒む。"""
    a, b = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(b, [DOOR]))
    assert_rejected(ctl, ctl.present(cred(a)), "知らない発行者")
    assert ctl.contacts.outputs == []


def test_pair19_same_vendor_separate_keys_per_customer():
    """R-AC-09: 顧客ごとに別の発行者の鍵とすれば、顧客 1 の鍵の資格情報で顧客 2 の扉は開かず、顧客 2 の鍵では開く。"""
    k1, k2 = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl2 = new_ctl(trust_entry(k2, [DOOR]))
    assert_rejected(ctl2, ctl2.present(cred(k1, cid="from-k1")), "知らない発行者")
    assert ctl2.present(cred(k2, cid="from-k2")) == "解錠"


def test_empty_trust_rejects_everything():
    """R-AC-09: 信頼する発行者が一つも無い扉は、どの資格情報も「知らない発行者」で拒む。"""
    ctl = new_ctl()
    assert_rejected(ctl, ctl.present(cred(Ed25519Signer.generate())), "知らない発行者")


# ---- 試験の対 20 ----
def test_pair20_edited_body_doors_widened():
    """R-AC-09: 署名のあと body の doors を書き足した資格情報は「署名が合わない」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a, doors=["door-2"])
    assert_rejected(ctl, ctl.present(edit_body(c, doors=["door-2", DOOR])), "署名が合わない")
    assert ctl.contacts.outputs == []


@pytest.mark.parametrize("over", [{"not_after": NOW + 10**9}, {"subject": "resident-102"}, {"once": False}])
def test_pair20_edited_body_other_fields(over):
    """R-AC-09: 期限・主体・一回限りの印を書き換えた資格情報も「署名が合わない」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a, once=True)
    assert_rejected(ctl, ctl.present(edit_body(c, **over)), "署名が合わない")


def test_pair20_body_bytes_are_what_is_signed():
    """R-AC-09: 意味を変えない書き直し(空白を足す)でも、署名したバイト列と違えば「署名が合わない」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a)
    assert_rejected(ctl, ctl.present({"body": c["body"] + " ", "sig": c["sig"]}), "署名が合わない")


def test_pair20_flipped_signature():
    """R-AC-09: sig の一文字を変えた資格情報は「署名が合わない」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a)
    flipped = ("0" if c["sig"][0] != "0" else "1") + c["sig"][1:]
    assert_rejected(ctl, ctl.present({"body": c["body"], "sig": flipped}), "署名が合わない")


def test_pair20_signed_by_other_key_claiming_issuer_a():
    """R-AC-09: 別の鍵で署名し、body の issuer に A を名乗った資格情報は「署名が合わない」で拒む。"""
    a, mallory = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = forge(mallory, issuer=key_id(a.public_key()))
    assert_rejected(ctl, ctl.present(c), "署名が合わない")
    assert ctl.contacts.outputs == []


# ---- 試験の対 21 ----
def test_pair21_once_credential_second_time_is_used():
    """R-AC-10: 一回限りの資格情報は、一度目は解錠し、二度目からは「使用済み」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a, cid="once-1", once=True)
    assert ctl.present(c) == "解錠"
    uses = [e for e in ctl.audit.entries if e.kind == "資格情報" and e.event == "使用"]
    assert len(uses) == 1 and uses[0].detail == "id=once-1"
    for _ in range(2):
        assert_rejected(ctl, ctl.present(c), "使用済み", check_lock=False)
    assert len([e for e in ctl.audit.entries if e.kind == "資格情報" and e.event == "使用"]) == 1


def test_pair21_once_used_list_rebuilt_from_record_after_restart(tmp_path):
    """R-AC-10: 再起動して記録を読み直した後でも、一度使った一回限りの資格情報は「使用済み」で拒み、未使用の物は通る。"""
    a = Ed25519Signer.generate()
    audit_signer = HmacSigner(b"k" * 32)
    path = tmp_path / "audit.jsonl"
    trust = trust_entry(a, [DOOR])
    first = new_ctl(trust, audit=Audit(audit_signer, path))
    used, unused = cred(a, cid="once-used", once=True), cred(a, cid="once-unused", once=True)
    assert first.present(used) == "解錠"

    second = new_ctl(trust, audit=Audit(audit_signer, path), now=NOW + 10)
    assert any(e.kind == "資格情報" and e.detail == "id=once-used" for e in second.audit.entries)
    assert_rejected(second, second.present(used), "使用済み")
    assert second.present(unused) == "解錠"
    assert second.audit.verify()[0]


# ---- 試験の対 22 ----
def test_pair22_remote_unlock_with_untrusted_vendor_key():
    """R-AC-10: 信頼していない業者の鍵の資格情報で遠隔の解錠を求めると拒み、記録に残る。"""
    a, vendor = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    n = len(ctl.audit.entries)
    assert_rejected(ctl, ctl.present(cred(vendor, cid="vendor-1"), route="遠隔"), "知らない発行者")
    assert len(ctl.audit.entries) > n
    last = auth_entries(ctl)[-1]
    assert last.route == "遠隔" and "id=vendor-1" in last.detail
    assert ctl.contacts.outputs == []


def test_pair22_trusted_issuer_unlocks_with_no_network():
    """R-AC-10: 網の無い状態でも、信頼する発行者の資格情報は扉が自分で確かめて解錠する(発行者のサーバーへ問い合わせない)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    # 扉に渡したのは公開鍵と範囲だけで、発行者への接続は持たない。この状態で通る。
    assert ctl.present(cred(a), route="遠隔") == "解錠"


# ---- 試験の対 23 ----
def test_pair23_admin_adds_then_removes_issuer():
    """R-AC-12: 管理者の署名つきの要求で発行者を足すと一覧が変わって資格情報が通り、消すと通らなくなる。設定の記録に前と新しい一覧が残る。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())
    iid = key_id(a.public_key())
    assert_rejected(ctl, ctl.present(cred(a, cid="before")), "知らない発行者")

    assert ctl.change_trust(change(admin, "add", a, [DOOR], "req-add")) == "受理"
    assert ctl.trust[iid]["public_key"] == a.public_key().hex() and ctl.trust[iid]["doors"] == [DOOR]
    added = trust_changes(ctl)
    assert len(added) == 1 and added[0].kind == "設定" and iid in added[0].detail
    assert "前" in added[0].detail and "新" in added[0].detail
    assert ctl.present(cred(a, cid="after")) == "解錠"

    n = len(trust_changes(ctl))
    assert ctl.change_trust(change(admin, "remove", a, None, "req-remove")) == "受理"
    assert iid not in ctl.trust
    removed = trust_changes(ctl)
    assert len(removed) == n + 1 and iid in removed[-1].detail
    assert "前" in removed[-1].detail and "新" in removed[-1].detail
    ctl.lock_request()
    assert_rejected(ctl, ctl.present(cred(a, cid="again")), "知らない発行者")
    assert ctl.audit.verify()[0]


def test_pair23_added_range_limits_the_issuer():
    """R-AC-09: 管理者が足した発行者の範囲(doors)は、その範囲の外の扉を開けさせない。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())
    assert ctl.change_trust(change(admin, "add", a, ["door-9"], "req-add")) == "受理"
    assert_rejected(ctl, ctl.present(cred(a)), "範囲の外")


def test_pair23_trust_is_kept_for_other_issuers_on_add_and_remove():
    """R-AC-12: 一人を足したり消したりしても、ほかの発行者の項目は変わらない。"""
    admin, a, b = (Ed25519Signer.generate() for _ in range(3))
    ctl = new_ctl(trust_entry(a, [DOOR]), admin_key=admin.public_key())
    before = json.loads(json.dumps(ctl.trust))
    assert ctl.change_trust(change(admin, "add", b, [DOOR], "req-b")) == "受理"
    assert ctl.trust[key_id(a.public_key())] == before[key_id(a.public_key())]
    assert ctl.change_trust(change(admin, "remove", b, None, "req-b-rm")) == "受理"
    assert ctl.trust == before


def _bad_requests(admin, other_admin, a):
    unsigned = change(admin, "add", a, [DOOR], "r-nosig")
    unsigned["sig"] = ""
    nokey = change(admin, "add", a, [DOOR], "r-nokey")
    del nokey["sig"]
    widened = change(admin, "add", a, [DOOR], "r-widen")
    body = json.loads(widened["body"])
    body["doors"] = [DOOR, "door-2"]
    widened = {"body": json.dumps(body, ensure_ascii=False), "sig": widened["sig"]}
    return {
        "署名が空": unsigned,
        "署名の欄が無い": nokey,
        "別の鍵の署名": change(other_admin, "add", a, [DOOR], "r-other"),
        "扉の名前が違う": change(admin, "add", a, [DOOR], "r-door", door="door-2"),
        "body を書き換えた": widened,
    }


@pytest.mark.parametrize("name", ["署名が空", "署名の欄が無い", "別の鍵の署名", "扉の名前が違う", "body を書き換えた"])
def test_pair23_bad_requests_are_rejected_and_recorded(name):
    """R-AC-12: 署名の無い、別の鍵の、扉の名前の違う、書き換えた要求は拒み、一覧は変わらず、拒否が設定の記録に残る。"""
    admin, other, a = (Ed25519Signer.generate() for _ in range(3))
    ctl = new_ctl(admin_key=admin.public_key())
    req = _bad_requests(admin, other, a)[name]
    changes_before = len(trust_changes(ctl))
    assert ctl.change_trust(req) == "拒否"
    assert not ctl.trust
    assert len(trust_changes(ctl)) == changes_before
    assert len(trust_rejections(ctl)) == 1
    assert_rejected(ctl, ctl.present(cred(a)), "知らない発行者")


def test_pair23_same_id_twice_is_rejected():
    """R-AC-12: 同じ id の要求を二度送ると、二度目は拒み、一覧を変えず、拒否を記録する。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())
    req = change(admin, "add", a, [DOOR], "req-dup")
    assert ctl.change_trust(req) == "受理"
    snapshot = json.loads(json.dumps(ctl.trust))
    assert ctl.change_trust(req) == "拒否"
    assert ctl.trust == snapshot
    assert len(trust_changes(ctl)) == 1 and len(trust_rejections(ctl)) == 1


def test_pair23_replay_of_old_add_after_remove_is_rejected():
    """R-AC-12: 消したあとに、前に受けた「足す」の要求をそのまま送り直しても、拒み、一覧に戻らない。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())
    add = change(admin, "add", a, [DOOR], "req-1")
    assert ctl.change_trust(add) == "受理"
    assert ctl.change_trust(change(admin, "remove", a, None, "req-2")) == "受理"
    assert ctl.change_trust(add) == "拒否"
    assert not ctl.trust


def test_pair23_same_id_with_different_action_is_rejected():
    """R-AC-12: 受けた id を別の操作(消す)で使い回した要求も拒む。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())
    assert ctl.change_trust(change(admin, "add", a, [DOOR], "req-x")) == "受理"
    assert ctl.change_trust(change(admin, "remove", a, None, "req-x")) == "拒否"
    assert key_id(a.public_key()) in ctl.trust


def test_pair23_no_admin_key_means_no_change():
    """R-AC-12: 管理者の公開鍵を入れていない扉は、どの要求も拒む。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl()
    assert ctl.change_trust(change(admin, "add", a, [DOOR], "req-1")) == "拒否"
    assert not ctl.trust and len(trust_rejections(ctl)) == 1


@pytest.mark.parametrize("request_", [None, "add", {}, {"body": "{", "sig": "00"}, {"body": "{}", "sig": "00"}])
def test_pair23_malformed_requests_are_rejected(request_):
    """R-AC-12: 形の合わない変更の要求は拒み、一覧を変えない。"""
    admin = Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())
    assert ctl.change_trust(request_) == "拒否"
    assert not ctl.trust


def test_pair23_wrong_format_with_valid_admin_signature_is_rejected():
    """R-AC-12: 管理者の署名が合っていても、format が digitaldoor-trust-change/1 でない要求は拒む。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key())
    good = change(admin, "add", a, [DOOR], "req-fmt")
    body = json.loads(good["body"])
    body["format"] = "digitaldoor-trust-change/2"
    text = json.dumps(body, ensure_ascii=False)
    assert ctl.change_trust({"body": text, "sig": admin.sign(text.encode("utf-8"))}) == "拒否"
    assert not ctl.trust


def test_pair23_credential_signer_cannot_change_trust():
    """R-AC-12: 信頼された発行者の鍵であっても、管理者の鍵でなければ信頼の一覧は変えられない。"""
    admin, a, b = (Ed25519Signer.generate() for _ in range(3))
    ctl = new_ctl(trust_entry(a, [DOOR]), admin_key=admin.public_key())
    assert ctl.change_trust(change(a, "add", b, [DOOR], "req-a")) == "拒否"
    assert key_id(b.public_key()) not in ctl.trust


# ---- 試験の対 24 ----
def test_pair24_unsynced_clock_rejects_credential():
    """R-AC-02: 時計が未同期のまま資格情報を出すと「時刻が未同期」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]), synced=False)
    assert_rejected(ctl, ctl.present(cred(a)), "時刻が未同期")
    assert ctl.contacts.outputs == []


def test_pair24_after_sync_the_same_credential_passes():
    """R-AC-02: 同期すれば、さっき「時刻が未同期」で拒んだ資格情報が通る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]), synced=False)
    c = cred(a)
    assert ctl.present(c) == "拒否"
    ctl.set_time(NOW + 1, True)
    assert ctl.present(c) == "解錠"


def test_pair24_expired_credential():
    """R-AC-02: not_after を過ぎた資格情報は「期限の外」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]), now=NOW + 5000)
    assert_rejected(ctl, ctl.present(cred(a, cid="c-old")), "期限の外")
    assert "id=c-old" in auth_entries(ctl)[-1].detail


def test_pair24_not_yet_valid_credential():
    """R-AC-02: not_before より前の資格情報は「期限の外」で拒む。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    assert_rejected(ctl, ctl.present(cred(a, nb=NOW + 500, na=NOW + 900)), "期限の外")


def test_pair24_expiry_follows_the_lock_clock():
    """R-AC-02: 期限は錠の時計で判定する。時計を進めると、同じ資格情報が通っていたものから拒む側に変わる。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a, na=NOW + 50)
    assert ctl.present(c) == "解錠"
    ctl.lock_request()
    ctl.set_time(NOW + 51, True)
    assert_rejected(ctl, ctl.present(c), "期限の外")


def test_pair24_trust_change_works_while_clock_unsynced():
    """R-AC-02: 管理者鍵による信頼の変更は期限付きの資格情報ではなく、時計が未同期でも受ける(管理者鍵は除く)。"""
    admin, a = Ed25519Signer.generate(), Ed25519Signer.generate()
    ctl = new_ctl(admin_key=admin.public_key(), synced=False)
    assert ctl.change_trust(change(admin, "add", a, [DOOR], "req-1")) == "受理"
    assert key_id(a.public_key()) in ctl.trust


# ---- 確かめる順番 ----
@pytest.mark.parametrize("name,expect", [
    ("形が先に外れる", "形式が違う"),
    ("発行者の次に署名", "知らない発行者"),
    ("署名の次に範囲", "署名が合わない"),
    ("範囲の次に時計", "範囲の外"),
    ("時計の次に期限", "時刻が未同期"),
])
def test_order_of_checks(name, expect):
    """R-AC-09: 確かめる順番(形、発行者、署名、範囲、時計、期限、使用済み)の最初に外れた所で拒む。"""
    a, unknown = Ed25519Signer.generate(), Ed25519Signer.generate()
    trust = trust_entry(a, [DOOR])
    if name == "形が先に外れる":
        ctl, c = new_ctl(trust, synced=False), forge(unknown, format="x/9", doors=["door-2"])
    elif name == "発行者の次に署名":
        ctl, c = new_ctl(trust, synced=False), forge(unknown, doors=["door-2"], not_after=1)
        c["sig"] = "00" * 64
    elif name == "署名の次に範囲":
        ctl, c = new_ctl(trust, synced=False), forge(unknown, issuer=key_id(a.public_key()), doors=["door-2"],
                                                     not_after=1)
    elif name == "範囲の次に時計":
        ctl, c = new_ctl(trust, synced=False), cred(a, doors=["door-2"], na=1)
    else:
        ctl, c = new_ctl(trust, synced=False), cred(a, na=1)
    assert_rejected(ctl, ctl.present(c), expect)


def test_order_expiry_before_used():
    """R-AC-10: 使った一回限りの資格情報が期限も過ぎているときは「期限の外」で拒む(期限が使用済みより先)。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    c = cred(a, cid="once-exp", once=True, na=NOW + 50)
    assert ctl.present(c) == "解錠"
    ctl.set_time(NOW + 100, True)
    assert_rejected(ctl, ctl.present(c), "期限の外", check_lock=False)


# ---- 形 ----
@pytest.mark.parametrize("name", [
    "none", "文字列", "空の辞書", "sig が無い", "body が無い", "body が JSON でない", "body が辞書でない",
    "format が違う", "欄が足りない:id", "欄が足りない:not_after", "doors が文字列", "not_after が文字列",
    "once が真偽でない",
])
def test_malformed_credentials_are_rejected_as_format(name):
    """R-AC-09: 形の合わない資格情報は「形式が違う」で拒み、錠は動かない。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    good = forge(a)
    cases = {
        "none": None,
        "文字列": "credential",
        "空の辞書": {},
        "sig が無い": {"body": good["body"]},
        "body が無い": {"sig": good["sig"]},
        "body が JSON でない": {"body": "{", "sig": good["sig"]},
        "body が辞書でない": {"body": "[1, 2]", "sig": good["sig"]},
        "format が違う": forge(a, format="digitaldoor-credential/2"),
        "欄が足りない:id": forge(a, drop=("id",)),
        "欄が足りない:not_after": forge(a, drop=("not_after",)),
        "doors が文字列": forge(a, doors=DOOR),
        "not_after が文字列": forge(a, not_after="2000"),
        "once が真偽でない": forge(a, once="yes"),
    }
    assert_rejected(ctl, ctl.present(cases[name]), "形式が違う")
    assert ctl.contacts.outputs == []


# ---- 通った後の判断 ----
def test_after_pass_fire_mode_keeps_release():
    """R-AC-09: 通った資格情報も読取機の判定結果と同じ判断を通る。火災中は「解放済み」で、記録に issuer と id が残る。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    ctl.on_fire(True)
    assert ctl.present(cred(a, cid="c-fire")) == "解放済み"
    last = auth_entries(ctl)[-1]
    assert "id=c-fire" in last.detail and f"issuer={key_id(a.public_key())}" in last.detail


def test_after_pass_two_person_rule():
    """R-AC-09: 二人同時の扉では、一人目の資格情報は「待ち」で、窓の中の二人目で解錠する。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]), config=Config(autolock_s=5, two_person=2, two_person_window_s=10))
    assert ctl.present(cred(a, cid="p1", subject="resident-101")) == "待ち"
    assert ctl.locked is True
    ctl.tick(NOW + 3)
    assert ctl.present(cred(a, cid="p2", subject="resident-102")) == "解錠"
    assert ctl.locked is False


def test_after_pass_autolock_follows():
    """R-AC-10: 資格情報で解錠したあとも、自動施錠の秒数が過ぎれば施錠する。"""
    a = Ed25519Signer.generate()
    ctl = new_ctl(trust_entry(a, [DOOR]))
    assert ctl.present(cred(a)) == "解錠"
    ctl.tick(NOW + 5)
    assert ctl.locked is True


# ---- 試験の対 25 ----
def test_pair25_condominium_association_and_owner():
    """R-AP-38 R-AP-39: 集合玄関は管理組合の資格情報で開き、住戸の扉は管理組合の資格情報では開かず、区分所有者の資格情報で開く。"""
    association, owner = Ed25519Signer.generate(), Ed25519Signer.generate()
    entrance = new_ctl(trust_entry(association, ["entrance"]), name="entrance")
    unit = new_ctl(trust_entry(owner, ["unit-101"]), name="unit-101")

    both = cred(association, cid="assoc-1", doors=["entrance", "unit-101"])
    assert entrance.present(both, route="系統1") == "解錠"
    assert_rejected(unit, unit.present(both, route="系統1"), "知らない発行者")
    assert unit.contacts.outputs == []

    mine = cred(owner, cid="owner-1", doors=["unit-101"])
    assert unit.present(mine, route="系統1") == "解錠"
    assert f"issuer={key_id(owner.public_key())}" in auth_entries(unit)[-1].detail


def test_pair25_management_company_and_vendor_cannot_open_unit():
    """R-AP-39: 管理会社と業者の発行者の鍵では住戸の扉は開かない。区分所有者の鍵が漏れても共用部の扉には届かない。"""
    association, owner, manager, vendor = (Ed25519Signer.generate() for _ in range(4))
    entrance = new_ctl(trust_entry(association, ["entrance"]), name="entrance")
    unit = new_ctl(trust_entry(owner, ["unit-101"]), name="unit-101")
    for who, cid in ((manager, "mgr-1"), (vendor, "vend-1")):
        assert_rejected(unit, unit.present(cred(who, cid=cid, doors=["unit-101"])), "知らない発行者")
    assert_rejected(entrance, entrance.present(cred(owner, cid="own-ent", doors=["entrance"])), "知らない発行者")


def test_pair25_one_user_two_credentials_each_checked_by_its_issuer():
    """R-AP-39: 一人が共用部の分(管理組合の署名)と住戸の分(区分所有者の署名)を持つとき、それぞれの扉でそれぞれの発行者の署名で確かめる。"""
    association, owner = Ed25519Signer.generate(), Ed25519Signer.generate()
    entrance = new_ctl(trust_entry(association, ["entrance"]), name="entrance")
    unit = new_ctl(trust_entry(owner, ["unit-101"]), name="unit-101")
    common = cred(association, cid="common-1", subject="resident-101", doors=["entrance"])
    private = cred(owner, cid="private-1", subject="resident-101", doors=["unit-101"])
    assert entrance.present(common) == "解錠" and unit.present(private) == "解錠"
    cross = new_ctl(trust_entry(owner, ["unit-101"]), name="unit-101")
    assert_rejected(cross, cross.present(common), "知らない発行者")
