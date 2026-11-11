"""設計書 22.10: 段 5a の要求ごとの確かめ方(R-EB-37、R-EB-36、R-LG-07、R-AC-13)。R-AC-19 は test_cli_portal.py。

段 5a の実装より先に書いた。下ごしらえは 19.13 の adm、apr、s1 に ros(名簿の係)を足した物(conftest の punit)。
呼び名、理由、作業の文は、写しに出ていないことを文字列で確かめられるよう、ほかの値と重ならない架空の文字列にする。
窓口の番号は、英数字の架空の値をテストが選ぶ。
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

from digitalkey.kanri import reasons
from kanri_common import (ACCESS, ACCESS_WORK, CREDENTIAL, ELLIPSIS, EXPORT_KEYS, GATE, ISSUE_AT, KEY_KEYS, PERM_KEYS,
                          PHYSICAL, PHYSICAL_WORK, R5A, SHEET_KEYS, T0, T1, dt)

AT = dt(ISSUE_AT)
REV = "2026-12-10T12:00"
A1 = "m4n8b2v6c1x5z9a"   # 架空の窓口の番号
A2 = "p0o9i8u7y6t5r4e"   # 架空
ROS = dict(by="ros", role="名簿の係")
ADM = dict(by="adm", role="管理者")

# R-LG-07 の筋書きの、ほかの値と重ならない架空の文字列
NAME = "写しに出ない呼び名ホ"
KIND = "写しに出ない種別ヘ"
REASON = "写しに出ない理由ト"
DEV_REASON = "写しに出ない機器の理由チ"
ACCOUNT = "kuchinonaQ7"
SUBJ = "hito-ri-07"


def _dump(items) -> str:
    return json.dumps(items, ensure_ascii=False)


def _diff(after: Counter, before: Counter) -> dict:
    d = Counter(after)
    d.subtract(before)
    return {k: v for k, v in d.items() if v}


# ---- R-EB-37 ----

def test_r_eb_37_1_unknown_subject_adds_nothing(punit, refused):
    """R-EB-37 22.10 の 1: 名簿に無い識別への link_portal は SUBJECT_UNKNOWN で、台帳の伝票の数と subjects() の数は変わらない。"""
    u = punit
    n = len(u.subjects())
    refused(u, reasons.SUBJECT_UNKNOWN, u.link_portal, "madoguchi-nomi", A1, "対面", **ROS)
    assert len(u.subjects()) == n


def test_r_eb_37_2_export_only_linked(punit, forms):
    """R-EB-37 22.10 の 2: 写しに出るのは結んだ主体だけ。結んだだけで権限の無い主体は 権限 と 鍵 が空の列。
    結ぶ事で、権限_申請ほかの伝票は増えない(増えるのは 主体_結び の一枚だけ)。"""
    u = punit
    before = forms(u)
    u.link_portal("s2", A2, "対面", **ROS)
    assert _diff(forms(u), before) == {"主体_結び": 1}
    got = u.portal_export(at=dt(REV))
    assert [x["owner"] for x in got] == [A2]
    assert (got[0]["sheet"]["権限"], got[0]["sheet"]["鍵"]) == ([], [])


@pytest.mark.parametrize("role", ["本人", "管理者"])
def test_r_eb_37_3_roster_role_only(punit, refused, role):
    """R-EB-37 22.10 の 3: link_portal と unlink_portal は名簿の係の役だけ。role="本人"、role="管理者" は ROLE_NOT_ALLOWED。"""
    u = punit
    refused(u, reasons.ROLE_NOT_ALLOWED, u.link_portal, "s1", A1, "対面", by="s1", role=role)
    refused(u, reasons.ROLE_NOT_ALLOWED, u.link_portal, "s1", A1, "対面", by="adm", role=role)
    u.link_portal("s1", A1, "対面", **ROS)
    refused(u, reasons.ROLE_NOT_ALLOWED, u.unlink_portal, "s1", reason="本人の申し出", by="s1", role=role)
    refused(u, reasons.ROLE_NOT_ALLOWED, u.unlink_portal, "s1", reason="本人の申し出", by="adm", role=role)


def test_r_eb_37_4_one_number_one_subject(punit, refused):
    """R-EB-37 22.10 の 4: 同じ番号を二人目に結ぶと ACCOUNT_LINKED(詳細は一人目の識別)。一人目を外すと二人目に結べる。
    一人目を 退出 にしただけでは、二人目は ACCOUNT_LINKED のまま。"""
    u = punit
    u.link_portal("s1", A1, "対面", **ROS)
    refused(u, R5A.ACCOUNT_LINKED, u.link_portal, "s2", A1, "対面", detail="s1", **ROS)
    u.change_subject("s1", reason="退職", state="退出", **ROS)
    refused(u, R5A.ACCOUNT_LINKED, u.link_portal, "s2", A1, "対面", detail="s1", **ROS)
    u.unlink_portal("s1", reason="退職の後の片づけ", **ROS)
    u.link_portal("s2", A1, "対面", **ROS)
    assert u.portal_account("s2") == A1


def test_r_eb_37_5_states(punit, refused):
    """R-EB-37 22.10 の 5: 結んだ主体を 退出 にすると写しから外れる。停止 は写しに残る。停止と退出の主体への link_portal は
    SUBJECT_INACTIVE。退出の主体の unlink_portal は通る。"""
    u = punit
    u.link_portal("s1", A1, "対面", **ROS)
    u.link_portal("s2", A2, "ビデオ通話", **ROS)
    u.change_subject("s2", reason="休職", state="停止", **ROS)
    assert [x["owner"] for x in u.portal_export(at=dt(REV))] == [A1, A2]
    u.change_subject("s1", reason="退職", state="退出", **ROS)
    assert [x["owner"] for x in u.portal_export(at=dt(REV))] == [A2]
    refused(u, reasons.SUBJECT_INACTIVE, u.link_portal, "s2", "aa11", "対面", **ROS)
    refused(u, reasons.SUBJECT_INACTIVE, u.link_portal, "s1", "bb22", "対面", **ROS)
    s = u.unlink_portal("s1", reason="退職の後の片づけ", **ROS)
    assert s["状態"] == "外す"


# ---- R-EB-36 ----

def test_r_eb_36_1_how_required(punit, refused):
    """R-EB-36 22.10 の 1: how="" の link_portal は FORM(詳細 `確かめ方: 必須です`)、how="メール" は FORM。伝票は増えない。"""
    u = punit
    refused(u, reasons.FORM, u.link_portal, "s1", A1, "", detail="確かめ方: 必須です", **ROS)
    refused(u, reasons.FORM, u.link_portal, "s1", A1, "メール", **ROS)
    assert u.portal_account("s1") == ""


@pytest.mark.parametrize("how", ["対面", "ビデオ通話"])
def test_r_eb_36_2_slip_keeps_how_role_issuer(punit, how):
    """R-EB-36 22.10 の 2: 書いた 主体_結び に、確かめ方と、役 名簿の係と、伝票の発行者(by)が残る。"""
    u = punit
    s = u.link_portal("s1", A1, how, **ROS)
    got = u.ledger.get(s.id)
    assert (got.form, got.issuer, got["確かめ方"], got["役"]) == ("主体_結び", "ros", how, "名簿の係")


def test_r_eb_36_3_relink(punit):
    """R-EB-36 22.10 の 3: 同じ主体に別の番号で結び直すと、portal_account は後の番号、前の番号は portal_links から消え、
    ほかの主体に結べる。"""
    u = punit
    u.link_portal("s1", A1, "対面", **ROS)
    u.link_portal("s1", A2, "ビデオ通話", **ROS)
    assert u.portal_account("s1") == A2
    assert A1 not in [x.窓口の番号 for x in u.portal_links()]
    u.link_portal("s2", A1, "対面", **ROS)
    assert u.portal_account("s2") == A1


# ---- R-LG-07 ----

@pytest.fixture
def lg07(punit):
    """R-LG-07 の筋書き: 架空の呼び名と種別の主体 SUBJ に、扉の権限から三つの経路、機器の権限から管理者の口を出し、
    結んで、失効させる。(unit, 扉の権限, 機器の権限, 実体の列, 署名つき資格情報の Issued)。"""
    u = punit
    u.register_subject(SUBJ, NAME, KIND, False, **ROS)
    u.register_device("ルーター1", "ルーター", "使用", **ADM)
    r = u.request(SUBJ, "扉", "玄関", T0, T1, REASON, by="adm", role="名簿の係")
    a = u.decide(r.id, "承認", "確かめた", by="apr", role="承認する人")
    u.issue(a.id, SUBJ, "扉", "玄関", T0, T1, **ADM, at=AT)
    r2 = u.request(SUBJ, "機器", "ルーター1", T0, T1, DEV_REASON, by="adm", role="名簿の係")
    a2 = u.decide(r2.id, "承認", "確かめた", by="apr", role="承認する人")
    u.issue(a2.id, SUBJ, "機器", "ルーター1", T0, T1, **ADM, at=AT)
    i1 = u.issue_entity(r.id, CREDENTIAL, **ADM, at=AT)
    i2 = u.issue_entity(r.id, GATE, **ADM, at=AT)
    i3 = u.issue_entity(r.id, PHYSICAL, key_number="K-7", **ADM, at=AT)
    i4 = u.issue_entity(r2.id, ACCESS, account=ACCOUNT, **ADM, at=AT)
    u.link_portal(SUBJ, A1, "対面", **ROS)
    u.revoke_subject(SUBJ, "退職", **ADM, at=dt(REV))
    return u, r.id, r2.id, [i1.実体, i2.実体, i3.実体, i4.実体], i1


def test_r_lg_07_1_no_names_in_export(lg07):
    """R-LG-07 22.10 の 1: 写しの文に、結んだ主体の呼び名、仮名、主体の識別、権限の理由、申請者と操作者の識別、
    19.8 と 20.8 の作業の文が現れない。"""
    u, *_ = lg07
    text = _dump(u.portal_export(at=dt(REV)))
    sub = u.subject(SUBJ)
    for s in (NAME, KIND, sub.仮名, SUBJ, REASON, DEV_REASON, "退職", "確かめた",
              PHYSICAL_WORK.format("K-7"), ACCESS_WORK.format("ルーター1", ACCOUNT),
              '"adm"', '"apr"', '"ros"', "管理者A", "名簿係の架空の呼び名ロ"):
        assert s not in text, s
    # 申請者と操作者(adm、apr、ros)は、値としても部分の文字列としても出ない
    for who in ("adm", "apr", "ros"):
        assert who not in text, who


def test_r_lg_07_2_identifiers(lg07):
    """R-LG-07 22.10 の 2: 署名つき資格情報と門番を通す錠の行の 識別 は Entity.識別 の先頭 4 文字と「…」で、全部は文に現れない。
    物理キーの行は鍵の番号、管理者の口の行は口の名がそのまま出る。"""
    u, _, _, ents, _ = lg07
    got = u.portal_export(at=dt(REV))
    text = _dump(got)
    ids = {k["経路"]: k["識別"] for k in got[0]["sheet"]["鍵"]}
    e1, e2, e3, e4 = ents
    assert ids[CREDENTIAL] == e1.識別[:4] + ELLIPSIS
    assert ids[GATE] == e2.識別[:4] + ELLIPSIS
    assert ids[PHYSICAL] == "K-7"
    assert ids[ACCESS] == ACCOUNT
    assert e1.識別 not in text and e2.識別 not in text


# ---- R-AC-13 ----

def test_r_ac_13_1_keys_only(lg07):
    """R-AC-13 22.10 の 1: 一人分の鍵は owner、issuer、made_at、sheet の四つだけ。sheet は 形式、時刻、権限、鍵 の四つだけ。
    権限 と 鍵 の一件の鍵は 22.7 の表のとおり。"""
    u, *_ = lg07
    for item in u.portal_export(at=dt(REV)):
        assert set(item) == set(EXPORT_KEYS)
        assert set(item["sheet"]) == set(SHEET_KEYS)
        assert item["sheet"]["権限"] and item["sheet"]["鍵"]
        assert all(set(p) == set(PERM_KEYS) for p in item["sheet"]["権限"])
        assert all(set(k) == set(KEY_KEYS) for k in item["sheet"]["鍵"])


def test_r_ac_13_2_no_secrets(lg07):
    """R-AC-13 22.10 の 2: 写しの文に、issue_entity が返した資格情報の sig の値と body の識別の全部、
    発行者の公開鍵の 16 進が現れない。"""
    u, _, _, ents, i1 = lg07
    text = _dump(u.portal_export(at=dt(REV)))
    cred = i1.資格情報
    assert cred and "sig" in cred
    assert str(cred["sig"]) not in text
    body = json.loads(cred["body"])
    assert body["id"] not in text
    assert body["subject"] not in text          # 仮名
    assert ents[0].識別 not in text
    assert u.public_key.hex() not in text


def test_r_ac_13_2_no_key_file_path(file_unit):
    """R-AC-13 22.10 の 2: 鍵のファイルの道(置き場の 鍵/発行者.key、kanri.json の 鍵のファイル)が写しに現れない。"""
    u = file_unit
    u.register_subject("ros", "名簿係の架空の呼び名ロ", "社員", False, by="adm", role="名簿の係")
    u.assign_role("ros", "名簿の係", reason="名簿の当番", by="adm", role="管理者")
    r = u.request("s1", "扉", "玄関", T0, T1, "業務", by="adm", role="名簿の係")
    a = u.decide(r.id, "承認", "確かめた", by="apr", role="承認する人")
    u.issue(a.id, "s1", "扉", "玄関", T0, T1, **ADM, at=AT)
    u.issue_entity(r.id, CREDENTIAL, **ADM, at=AT)
    u.link_portal("s1", A1, "対面", **ROS)
    info = json.loads((u.root / "kanri.json").read_text(encoding="utf-8"))
    text = _dump(u.portal_export(at=dt(REV)))
    assert info["鍵のファイル"] and info["鍵のファイル"] not in text
    assert str(u.root) not in text
    assert info["公開鍵"] not in text


def test_r_ac_13_3_export_writes_nothing(lg07):
    """R-AC-13 22.10 の 3: portal_export は台帳の伝票の数を変えない。"""
    u, *_ = lg07
    n = u.ledger.count()
    u.portal_export(at=dt(REV))
    assert u.ledger.count() == n


# ---- 働きの確かめ(22.10 の働きの 4) ----

def test_example_states_after_revoke_and_delivery(punit):
    """R-AC-13 22.10 の働きの 4: 19.10 の例で、失効の後の写しの 鍵 は 22.7 の例と同じ状態と効くまで。
    record_delivery の後は、署名つき資格情報の行が 止まった(21.8)。"""
    u = punit
    r = u.request("s1", "扉", "玄関", T0, T1, "業務", by="adm", role="名簿の係")
    a = u.decide(r.id, "承認", "確かめた", by="apr", role="承認する人")
    u.issue(a.id, "s1", "扉", "玄関", T0, T1, **ADM, at=AT)
    u.issue_entity(r.id, CREDENTIAL, **ADM, at=AT)
    u.issue_entity(r.id, GATE, **ADM, at=AT)
    u.issue_entity(r.id, PHYSICAL, key_number="K-7", **ADM, at=AT)
    u.link_portal("s1", A1, "対面", **ROS)
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    keys = u.portal_export(at=dt(REV))[0]["sheet"]["鍵"]
    assert [(k["経路"], k["状態"], k["効くまで"]) for k in keys] == [
        (CREDENTIAL, "待ち", T1), (GATE, "止まった", ""), (PHYSICAL, "人の作業が要る", "")]
    u.record_delivery("玄関", 1, **ADM, at="2026-12-11T09:00")
    keys = u.portal_export(at=dt("2026-12-11T09:00"))[0]["sheet"]["鍵"]
    assert keys[0]["経路"] == CREDENTIAL and keys[0]["状態"] == "止まった"
