"""設計書 22.1〜22.8: 段 5a の外に出す名前とデータの型、役と伝票の組、窓口の番号を結ぶ・外す、今の結び、照会用のコピー。

段 5a の実装より先に書いた。まだ無い名前(Link、PortalMixin、link_portal など)は関数の中で引く
(収集は通り、試験は AttributeError などで落ちる)。時刻は固定の値を `at` に渡す。
呼び名、理由、作業の文は、コピーに出ていないことを文字列で確かめられるよう、ほかの値と重ならない架空の文字列にする。
"""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timezone

import pytest

import digitalkey.kanri as kanri
from digitalkey.kanri import reasons
from kanri_common import (ACCESS, CREDENTIAL, ELLIPSIS, FORMS_5A, GATE, ISSUE_AT, KEY_KEYS, PERM_KEYS, PHYSICAL, R5A,
                          REFUSALS_5A, SHEET_FORMAT, SHEET_KEYS, EXPORT_KEYS, T0, T1, dt, plus)

AT = dt(ISSUE_AT)
REV = "2026-12-10T12:00"      # 19.10 の例の失効の時刻
A1 = "k3x9m2p7q1w8e5r"        # 22.6 の例の窓口の番号(架空)
A2 = "q7w2e9r4t1y8u3i"        # 架空
A3 = "z0x1c2v3b4n5m6a"        # 架空
ROS = dict(by="ros", role="名簿の係")
ADM = dict(by="adm", role="管理者")


def _dump(items) -> str:
    return json.dumps(items, ensure_ascii=False)


def _grant_r(u, subject, kind, target, reason, *, start=T0, end=T1):
    """申請(adm、名簿の係。理由は呼び出しが選ぶ)、承認(apr)、発行(adm)。権限の番号を返す。"""
    r = u.request(subject, kind, target, start, end, reason, by="adm", role="名簿の係")
    a = u.decide(r.id, "承認", "確かめた", by="apr", role="承認する人")
    u.issue(a.id, subject, kind, target, start, end, **ADM, at=AT)
    return r.id


def _three_routes(u, subject="s1", reason="業務"):
    """19.10 の例: 扉 玄関 の権限から 署名つき資格情報(E1)、門番を通す錠(E2)、物理キー K-7(E3)。(pid, E1, E2, E3, Issued1)。"""
    pid = _grant_r(u, subject, "扉", "玄関", reason)
    i1 = u.issue_entity(pid, CREDENTIAL, **ADM, at=AT)
    i2 = u.issue_entity(pid, GATE, **ADM, at=AT)
    i3 = u.issue_entity(pid, PHYSICAL, key_number="K-7", **ADM, at=AT)
    return pid, i1.実体, i2.実体, i3.実体, i1


# ---- 22.8 拒否の言葉 ----

@pytest.mark.parametrize("name", REFUSALS_5A)
def test_reason_words_5a(name):
    """R-EB-37 22.8: reasons に足す ACCOUNT_LINKED(その番号は、ほかの主体に結ばれています)と NOT_LINKED(結ばれていません)。"""
    assert getattr(reasons, name) == getattr(R5A, name)


# ---- 22.1、22.2、22.4 外に出す名前とデータの型 ----

def test_link_type():
    """R-EB-37 22.4: Link は dataclass(frozen=True)、属性は 主体、窓口の番号、確かめ方、伝票。`digitalkey.kanri` から出す(22.1)。"""
    cls = kanri.Link
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen
    assert [f.name for f in dataclasses.fields(cls)] == ["主体", "窓口の番号", "確かめ方", "伝票"]
    assert "Link" in kanri.__all__


def test_unit_has_seven_mixins():
    """R-EB-37 22.1、22.2: Unit は PortalMixin を継ぐ(七つの Mixin)。"""
    from digitalkey.kanri.devices import DevicesMixin
    from digitalkey.kanri.portal import PortalMixin
    from digitalkey.kanri.revocations import RevocationsMixin
    assert issubclass(kanri.Unit, PortalMixin)
    assert issubclass(kanri.Unit, DevicesMixin) and issubclass(kanri.Unit, RevocationsMixin)


def test_ledger_has_link_form(make_unit):
    """R-EB-37 22.2、22.5: 台帳は 主体_結び を足した 23 枚の様式で開く。"""
    u = make_unit()
    assert set(u.ledger.form_set) == set(FORMS_5A)
    assert "主体_結び" in u.ledger.form_set


def test_link_form_spec():
    """R-EB-36 22.5: 様式 主体_結び の書けるは 名簿の係。項目は 主体、窓口の番号、状態、確かめ方、理由、役。"""
    from digitalkey.entrance.forms import load_file
    from pathlib import Path
    p = Path(kanri.__file__).parent / "forms_data" / "主体_結び.adoc"
    y = load_file(p)
    assert y.name == "主体_結び"
    assert y.attrs.get("書ける") == "名簿の係"
    assert y.names == ["主体", "窓口の番号", "状態", "確かめ方", "理由", "役"]


# ---- 22.3、22.6 結ぶ ----

def test_link_example(punit, forms):
    """R-EB-36 R-EB-37 22.6 の例、22.3: link_portal は 主体_結び を一枚書く。伝票の発行者は by、宛先は主体、根拠なし。
    項目は 主体、窓口の番号、状態 結ぶ、確かめ方、理由 空、役。portal_account は結んだ番号。"""
    u = punit
    before = forms(u)
    s = u.link_portal("s1", A1, "対面", **ROS)
    assert (s.form, s.issuer, s.to, s.basis) == ("主体_結び", "ros", "s1", [])
    assert (s["主体"], s["窓口の番号"], s["状態"], s["確かめ方"], s["理由"], s["役"]) == \
        ("s1", A1, "結ぶ", "対面", "", "名簿の係")
    assert u.portal_account("s1") == A1
    after = forms(u)
    after.subtract(before)
    assert +after == {"主体_結び": 1}


def test_link_video(punit):
    """R-EB-36 22.5、22.6: 確かめ方 ビデオ通話 も選べる。"""
    s = punit.link_portal("s2", A2, "ビデオ通話", **ROS)
    assert s["確かめ方"] == "ビデオ通話"
    assert punit.portal_account("s2") == A2


def test_portal_links_shape_and_order(punit, link):
    """R-EB-37 22.6: portal_links は今の結びの全部を Link で返す。並びは 主体_登録 を台帳に書いた順(結んだ順ではない)。
    Link.伝票 は最後の一枚の番号。結んでいない主体は入らない。"""
    u = punit
    s2 = link(u, "s2", A2, "ビデオ通話")
    s1 = link(u, "s1", A1)
    got = u.portal_links()
    assert all(isinstance(x, kanri.Link) for x in got)
    assert got == [kanri.Link("s1", A1, "対面", s1.id), kanri.Link("s2", A2, "ビデオ通話", s2.id)]


def test_portal_account_empty_and_unknown(punit, refused):
    """R-EB-37 22.6: 結んでいない主体の portal_account は空。名簿に無い主体は SUBJECT_UNKNOWN。"""
    assert punit.portal_account("s1") == ""
    refused(punit, reasons.SUBJECT_UNKNOWN, punit.portal_account, "nobody")


def test_portal_links_empty(punit):
    """R-EB-37 22.6: 一枚も無ければ空の列。"""
    assert punit.portal_links() == []


def test_example_account_linked(punit, link, refused):
    """R-EB-37 22.6 の例: 同じ番号を s2 に結ぶと ACCOUNT_LINKED(詳細は結ばれている主体 s1)。"""
    link(punit, "s1", A1)
    e = refused(punit, R5A.ACCOUNT_LINKED, punit.link_portal, "s2", A1, "ビデオ通話", detail="s1", **ROS)
    assert str(e) == "その番号は、ほかの主体に結ばれています(s1)"


def test_relink_same_subject_new_number(punit, link):
    """R-EB-36 22.6: 同じ主体に別の番号で結び直すと新しい一枚を書き、それが今の結び。前の番号は外れ、ほかの主体に結べる。"""
    u = punit
    link(u, "s1", A1)
    s = link(u, "s1", A2, "ビデオ通話")
    assert u.portal_account("s1") == A2
    assert u.portal_links() == [kanri.Link("s1", A2, "ビデオ通話", s.id)]
    link(u, "s2", A1)
    assert u.portal_account("s2") == A1


def test_relink_same_number_writes_new_slip(punit, link, forms):
    """R-EB-36 22.6: 番号が同じでも、結び直すと新しい一枚を書く(拒まない)。Link.伝票 は後の一枚。"""
    u = punit
    link(u, "s1", A1)
    n = forms(u)["主体_結び"]
    s = link(u, "s1", A1, "ビデオ通話")
    assert forms(u)["主体_結び"] == n + 1
    assert u.portal_links() == [kanri.Link("s1", A1, "ビデオ通話", s.id)]


def test_case_is_compared_as_is(punit, link):
    """R-EB-37 22.6: 窓口の番号の大文字と小文字はそのまま比べる。abc123 と ABC123 は別の番号。"""
    u = punit
    link(u, "s1", "abc123")
    link(u, "s2", "ABC123")
    assert (u.portal_account("s1"), u.portal_account("s2")) == ("abc123", "ABC123")


# ---- 19.3 の 1〜4 の確かめ(役) ----

@pytest.mark.parametrize("role", ["本人", "管理者", "承認する人", "運用", "監査", "発行者", "初期化"])
def test_link_unlink_roles_not_allowed(punit, link, refused, role):
    """R-EB-37 22.3、19.3 の 1: link_portal と unlink_portal は 名簿の係 だけ。ほかの役は ROLE_NOT_ALLOWED。"""
    u = punit
    refused(u, reasons.ROLE_NOT_ALLOWED, u.link_portal, "s1", A1, "対面", by="adm", role=role)
    link(u, "s1", A1)
    refused(u, reasons.ROLE_NOT_ALLOWED, u.unlink_portal, "s1", reason="本人の申し出", by="adm", role=role)


def test_link_check_order_by(punit, refused):
    """R-EB-37 19.3 の 1〜4: 役の次に、by が名簿に無い(BY_UNKNOWN)、有効でない(BY_INACTIVE)、役を持たない(NO_ROLE)。
    役が違う時は、by が名簿に無くても ROLE_NOT_ALLOWED が先。"""
    u = punit
    refused(u, reasons.ROLE_NOT_ALLOWED, u.link_portal, "s1", A1, "対面", by="nobody", role="管理者")
    refused(u, reasons.BY_UNKNOWN, u.link_portal, "s1", A1, "対面", by="nobody", role="名簿の係")
    refused(u, reasons.NO_ROLE, u.link_portal, "s1", A1, "対面", by="s2", role="名簿の係")
    u.change_subject("ros", reason="休職", state="停止", by="adm", role="名簿の係")
    refused(u, reasons.BY_INACTIVE, u.link_portal, "s1", A1, "対面", **ROS)


def test_unlink_check_order_by(punit, link, refused):
    """R-EB-37 19.3 の 1〜4: unlink_portal も同じ順。"""
    u = punit
    link(u, "s1", A1)
    refused(u, reasons.BY_UNKNOWN, u.unlink_portal, "s1", reason="本人の申し出", by="nobody", role="名簿の係")
    refused(u, reasons.NO_ROLE, u.unlink_portal, "s1", reason="本人の申し出", by="s2", role="名簿の係")
    u.change_subject("ros", reason="休職", state="停止", by="adm", role="名簿の係")
    refused(u, reasons.BY_INACTIVE, u.unlink_portal, "s1", reason="本人の申し出", **ROS)


def test_admin_with_roster_role_can_link(punit):
    """R-EB-36 22.11: 管理者が兼ねる時は、名簿の係の役を割り当てて入る(adm は 名簿の係 を持つ)。伝票の発行者は adm。"""
    s = punit.link_portal("s1", A1, "対面", by="adm", role="名簿の係")
    assert (s.issuer, s["役"]) == ("adm", "名簿の係")


# ---- 22.6 結ぶの確かめ(様式、主体、番号) ----

def test_link_how_required(punit, refused):
    """R-EB-36 22.6 の 2: 確かめ方が空なら FORM(詳細 `確かめ方: 必須です`)。"""
    refused(punit, reasons.FORM, punit.link_portal, "s1", A1, "", detail="確かめ方: 必須です", **ROS)


@pytest.mark.parametrize("how", ["メール", "電話", "対面で"])
def test_link_how_choices(punit, refused, how):
    """R-EB-36 22.5、22.6 の 2: 確かめ方は 対面 か ビデオ通話(様式の選べる値)。ほかは FORM。"""
    refused(punit, reasons.FORM, punit.link_portal, "s1", A1, how, **ROS)


@pytest.mark.parametrize("account", ["abc-123", "abc 123", "abc_123", "番号123", "abc.123"])
def test_link_account_alnum_only(punit, refused, account):
    """R-EB-37 22.6 の 2、22.10 の働きの 1: 直した窓口の番号が英数字([A-Za-z0-9])だけでなければ FORM
    (詳細 `窓口の番号: 英数字だけで書いてください`)。"""
    refused(punit, reasons.FORM, punit.link_portal, "s1", account, "対面",
            detail="窓口の番号: 英数字だけで書いてください", **ROS)


def test_link_account_empty(punit, refused):
    """R-EB-37 22.5、22.6 の 2: 空の窓口の番号は FORM(様式の not null か、英数字だけでない)。"""
    refused(punit, reasons.FORM, punit.link_portal, "s1", "", "対面", **ROS)


def test_link_account_length(punit, refused, link):
    """R-EB-37 22.5、22.10 の働きの 1: 窓口の番号は 40 文字まで。41 文字は FORM。"""
    u = punit
    refused(u, reasons.FORM, u.link_portal, "s1", "a" * 41, "対面", **ROS)
    link(u, "s1", "a" * 40)
    assert u.portal_account("s1") == "a" * 40


def test_link_fullwidth_account_is_normalized(punit, link):
    """R-EB-37 22.5、22.10 の働きの 1: 全角の英数字の番号は半角に直して書く(NFKC)。portal_account も直した値。"""
    u = punit
    s = link(u, "s1", "ｋ３ｘ９ｍ２ｐ７")
    assert s["窓口の番号"] == "k3x9m2p7"
    assert u.portal_account("s1") == "k3x9m2p7"


def test_link_fullwidth_conflicts_with_halfwidth(punit, link, refused):
    """R-EB-37 22.5、22.6: 直した値で確かめる。全角で書いた同じ番号は、半角で結んだ番号と同じとして ACCOUNT_LINKED。"""
    u = punit
    link(u, "s1", "k3x9m2p7")
    refused(u, R5A.ACCOUNT_LINKED, u.link_portal, "s2", "ｋ３ｘ９ｍ２ｐ７", "対面", detail="s1", **ROS)


def test_link_form_before_subject(punit, refused):
    """R-EB-37 22.6 の確かめの順: 様式(2)が主体(3)より先。名簿に無い主体でも、番号が英数字だけでなければ FORM。
    確かめ方が空の確かめは、英数字の確かめより先。"""
    u = punit
    refused(u, reasons.FORM, u.link_portal, "nobody", "abc-123", "対面", **ROS)
    refused(u, reasons.FORM, u.link_portal, "s1", "abc-123", "", detail="確かめ方: 必須です", **ROS)
    refused(u, reasons.FORM, u.link_portal, "s" * 41, A1, "対面", **ROS)


def test_link_subject_unknown(punit, refused):
    """R-EB-37 22.6 の 3: 名簿に無い主体は SUBJECT_UNKNOWN。"""
    refused(punit, reasons.SUBJECT_UNKNOWN, punit.link_portal, "nobody", A1, "対面", **ROS)


@pytest.mark.parametrize("state", ["停止", "退出"])
def test_link_subject_inactive(punit, refused, state):
    """R-EB-37 22.6 の 3: 主体の状態が 有効 でなければ SUBJECT_INACTIVE。"""
    u = punit
    u.change_subject("s2", reason="試し", state=state, **ROS)
    refused(u, reasons.SUBJECT_INACTIVE, u.link_portal, "s2", A1, "対面", **ROS)


def test_link_inactive_before_account_linked(punit, link, refused):
    """R-EB-37 22.6 の確かめの順: 主体の状態(3)が番号の重なり(4)より先。"""
    u = punit
    link(u, "s1", A1)
    u.change_subject("s2", reason="試し", state="停止", **ROS)
    refused(u, reasons.SUBJECT_INACTIVE, u.link_portal, "s2", A1, "対面", **ROS)


def test_account_linked_to_retired_subject(punit, link, refused):
    """R-EB-37 22.6 の 4: 退出の主体に結ばれたままの番号も ACCOUNT_LINKED。先に外せば結べる。"""
    u = punit
    link(u, "s1", A1)
    u.change_subject("s1", reason="退職", state="退出", **ROS)
    refused(u, R5A.ACCOUNT_LINKED, u.link_portal, "s2", A1, "対面", detail="s1", **ROS)
    u.unlink_portal("s1", reason="退職の後の片づけ", **ROS)
    link(u, "s2", A1)
    assert u.portal_account("s2") == A1


# ---- 22.6 外す ----

def test_unlink_example(punit, link, refused):
    """R-EB-37 22.6 の例: unlink_portal は 主体_結び(状態 外す、窓口の番号 = 今の番号、確かめ方 空、理由、役)を書く。
    伝票の発行者は by、宛先は主体。外した後の portal_account は空、二度目は NOT_LINKED(詳細は空)。"""
    u = punit
    link(u, "s1", A1)
    s = u.unlink_portal("s1", reason="本人の申し出", **ROS)
    assert (s.form, s.issuer, s.to, s.basis) == ("主体_結び", "ros", "s1", [])
    assert (s["主体"], s["窓口の番号"], s["状態"], s["確かめ方"], s["理由"], s["役"]) == \
        ("s1", A1, "外す", "", "本人の申し出", "名簿の係")
    assert u.portal_account("s1") == ""
    assert u.portal_links() == []
    e = refused(u, R5A.NOT_LINKED, u.unlink_portal, "s1", reason="二度目", detail="", **ROS)
    assert str(e) == "結ばれていません"


def test_unlink_never_linked(punit, refused):
    """R-EB-37 22.6 の 4、22.10 の働きの 2: 結んでいない主体の unlink_portal は NOT_LINKED。"""
    refused(punit, R5A.NOT_LINKED, punit.unlink_portal, "s1", reason="本人の申し出", **ROS)


def test_unlink_need_reason(punit, link, refused):
    """R-EB-37 22.6 の 5、22.10 の働きの 2: 結びがある時、理由が空なら NEED_REASON(詳細は空)。"""
    link(punit, "s1", A1)
    refused(punit, reasons.NEED_REASON, punit.unlink_portal, "s1", reason="", detail="", **ROS)


def test_unlink_not_linked_before_need_reason(punit, refused):
    """R-EB-37 22.6 の確かめの順: 結びが無い(4)が、理由が空(5)より先。"""
    refused(punit, R5A.NOT_LINKED, punit.unlink_portal, "s1", reason="", **ROS)


def test_unlink_form_and_unknown(punit, link, refused):
    """R-EB-37 22.6 の 2〜3: 理由が 200 文字を超えれば FORM。名簿に無い主体は SUBJECT_UNKNOWN。様式が主体より先。"""
    u = punit
    link(u, "s1", A1)
    refused(u, reasons.FORM, u.unlink_portal, "s1", reason="長" * 201, **ROS)
    refused(u, reasons.FORM, u.unlink_portal, "nobody", reason="長" * 201, **ROS)
    refused(u, reasons.SUBJECT_UNKNOWN, u.unlink_portal, "nobody", reason="本人の申し出", **ROS)


@pytest.mark.parametrize("state", ["停止", "退出"])
def test_unlink_inactive_subject(punit, link, state):
    """R-EB-37 22.6 の 3: 外す時は主体の状態を問わない(停止、退出の主体も外せる)。"""
    u = punit
    link(u, "s1", A1)
    u.change_subject("s1", reason="試し", state=state, **ROS)
    u.unlink_portal("s1", reason="片づけ", **ROS)
    assert u.portal_account("s1") == ""


def test_unlink_then_other_subject_can_link(punit, link, refused):
    """R-EB-37 22.10 の R-EB-37 の 4: 一人目を外すと、二人目に結べる。"""
    u = punit
    link(u, "s1", A1)
    refused(u, R5A.ACCOUNT_LINKED, u.link_portal, "s2", A1, "対面", detail="s1", **ROS)
    u.unlink_portal("s1", reason="本人の申し出", **ROS)
    link(u, "s2", A1)
    assert u.portal_links() == [kanri.Link("s2", A1, "対面", u.ledger.select("主体_結び")[-1].id)]


def test_relink_after_unlink(punit, link):
    """R-EB-37 22.6 の導き方: 外した後に結び直すと、最後の一枚(結ぶ)が今の結び。"""
    u = punit
    link(u, "s1", A1)
    u.unlink_portal("s1", reason="本人の申し出", **ROS)
    link(u, "s1", A3)
    assert u.portal_account("s1") == A3


# ---- 22.2 退出と結び ----

def test_retire_keeps_link_slip(punit, link, forms):
    """R-EB-37 22.2: change_subject で 退出 にしても、結びの伝票は残り、結びは自分では外れない(portal_links に残る)。"""
    u = punit
    s = link(u, "s1", A1)
    n = forms(u)["主体_結び"]
    u.change_subject("s1", reason="退職", state="退出", **ROS)
    assert forms(u)["主体_結び"] == n
    assert u.portal_account("s1") == A1
    assert u.portal_links() == [kanri.Link("s1", A1, "対面", s.id)]


def test_reopen_reads_same_links(punit, link):
    """R-EB-37 19.1 の台帳: 結びは台帳から導く。同じ置き場を開き直すと、同じ結びが読める。"""
    u = punit
    link(u, "s1", A1)
    link(u, "s2", A2)
    u.unlink_portal("s2", reason="本人の申し出", **ROS)
    v = kanri.Unit.open(u.root, signer=u.signer)
    assert v.portal_links() == u.portal_links()
    assert v.portal_account("s1") == A1 and v.portal_account("s2") == ""


# ---- 22.7 コピー ----

def test_export_empty(punit):
    """R-EB-37 22.7: 結んだ人が居なければ空の列。"""
    assert punit.portal_export(at=dt(REV)) == []


def test_export_linked_without_permissions(punit, link):
    """R-EB-37 22.7: 結んだだけで権限の無い主体は、権限 と 鍵 が空の列で出る。issuer は unit.issuer、made_at と 時刻 は at。"""
    u = punit
    link(u, "s2", A2)
    assert u.portal_export(at=dt(REV)) == [
        {"owner": A2, "issuer": u.issuer, "made_at": REV,
         "sheet": {"形式": SHEET_FORMAT, "時刻": REV, "権限": [], "鍵": []}}]


def test_export_only_linked_in_register_order(punit, link):
    """R-EB-37 22.7: 出すのは結んだ主体だけ。並びは 主体_登録 を台帳に書いた順。"""
    u = punit
    u.register_subject("s3", "照会の架空の呼び名サ", "社員", False, **ROS)
    link(u, "s3", A3)
    link(u, "s1", A1)
    assert [x["owner"] for x in u.portal_export(at=dt(REV))] == [A1, A3]


def test_export_states(punit, link):
    """R-EB-37 22.7: 主体の状態が 有効 か 停止 の物を出す。退出 の主体は出さない。"""
    u = punit
    link(u, "s1", A1)
    link(u, "s2", A2)
    u.change_subject("s1", reason="休職", state="停止", **ROS)
    assert [x["owner"] for x in u.portal_export(at=dt(REV))] == [A1, A2]
    u.change_subject("s2", reason="退職", state="退出", **ROS)
    assert [x["owner"] for x in u.portal_export(at=dt(REV))] == [A1]


def test_export_example_22_7(punit, link):
    """R-AC-13 R-LG-07 22.7 の例: 19.10 の例の s1 を結び、2026-12-10T12:00 の revoke_subject の後、同じ時刻でコピーを作る。
    権限は 失効、鍵は 署名つき資格情報 待ち(効くまで 2027-01-01T00:00)、門番を通す錠 止まった、物理キー 人の作業が要る。
    署名つき資格情報と門番を通す錠の識別は先頭 4 文字と「…」、物理キーは鍵の番号そのまま。"""
    u = punit
    pid, e1, e2, e3, _ = _three_routes(u)
    link(u, "s1", A1)
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    got = u.portal_export(at=dt(REV))
    assert got == [{
        "owner": A1, "issuer": u.issuer, "made_at": REV,
        "sheet": {"形式": SHEET_FORMAT, "時刻": REV,
                  "権限": [{"番号": pid, "対象の種類": "扉", "対象": "玄関", "開始": T0, "終了": T1, "状態": "失効"}],
                  "鍵": [{"権限": pid, "経路": CREDENTIAL, "扉": ["玄関"], "機器": "",
                         "識別": e1.識別[:4] + ELLIPSIS, "状態": "待ち", "効くまで": T1},
                        {"権限": pid, "経路": GATE, "扉": ["玄関"], "機器": "",
                         "識別": e2.識別[:4] + ELLIPSIS, "状態": "止まった", "効くまで": ""},
                        {"権限": pid, "経路": PHYSICAL, "扉": ["玄関"], "機器": "",
                         "識別": "K-7", "状態": "人の作業が要る", "効くまで": ""}]}}]


def test_export_before_revoke_all_effective(punit, link):
    """R-EB-37 22.7: 失効の前、期間の内のコピーは、権限 発行済み、鍵は三つとも 効いている(効くまで 空)。"""
    u = punit
    pid, *_ = _three_routes(u)
    link(u, "s1", A1)
    sheet = u.portal_export(at=dt("2026-12-05T10:00"))[0]["sheet"]
    assert [p["状態"] for p in sheet["権限"]] == ["発行済み"]
    assert [(k["状態"], k["効くまで"]) for k in sheet["鍵"]] == [("効いている", "")] * 3


def test_export_after_delivery(punit, link):
    """R-AC-13 22.10 の働きの 4、21.8: 失効の後、record_delivery("玄関", 1) の後のコピーでは、署名つき資格情報の行が 止まった。"""
    u = punit
    _three_routes(u)
    link(u, "s1", A1)
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    u.record_delivery("玄関", 1, **ADM, at="2026-12-11T09:00")
    keys = u.portal_export(at=dt("2026-12-11T09:00"))[0]["sheet"]["鍵"]
    assert keys[0]["経路"] == CREDENTIAL
    assert (keys[0]["状態"], keys[0]["効くまで"]) == ("止まった", "")


def test_export_after_end(punit, link):
    """R-EB-37 22.10 の働きの 3: at を権限の終了の後にしたコピーでは、権限の状態が 期限切れ(失効していない時)、
    署名つき資格情報の行が 止まった。"""
    u = punit
    _three_routes(u)
    link(u, "s1", A1)
    sheet = u.portal_export(at=dt(plus(T1, minutes=1)))[0]["sheet"]
    assert [p["状態"] for p in sheet["権限"]] == ["期限切れ"]
    rows = {k["経路"]: k for k in sheet["鍵"]}
    assert rows[CREDENTIAL]["状態"] == "止まった"


def test_export_end_exactly(punit, link):
    """R-EB-37 19.5、22.7: 終了の時刻ちょうどで切れる。at = 終了 のコピーは 期限切れ と 止まった。"""
    u = punit
    _three_routes(u)
    link(u, "s1", A1)
    sheet = u.portal_export(at=dt(T1))[0]["sheet"]
    assert sheet["権限"][0]["状態"] == "期限切れ"
    assert {k["経路"]: k["状態"] for k in sheet["鍵"]}[CREDENTIAL] == "止まった"


def test_export_permissions_follow_permissions_order(punit, link):
    """R-EB-37 22.7: 権限 は u.permissions(主体, at=at) の順で、値は Permission の同じ名前の属性(終了の空は空の文字列)。
    発行していない申請も、その状態で並ぶ。"""
    u = punit
    p1 = _grant_r(u, "s1", "扉", "玄関", "業務")
    p2 = u.request("s1", "扉", "玄関", T0, "", "期限なしの申請", by="adm", role="名簿の係").id
    link(u, "s1", A1)
    at = dt("2026-12-05T10:00")
    sheet = u.portal_export(at=at)[0]["sheet"]
    want = [{k: getattr(p, k) for k in PERM_KEYS} for p in u.permissions("s1", at=at)]
    assert sheet["権限"] == want
    assert [p["番号"] for p in sheet["権限"]] == [p1, p2]
    assert sheet["権限"][1]["終了"] == "" and sheet["権限"][1]["状態"] == "申請中"


def test_export_keys_follow_progress_order(punit, link):
    """R-EB-37 22.7: 鍵 は u.progress(主体, at=at).行 の順で、扉、機器、状態、効くまで は Row の同じ名前の属性、
    権限 は実体の Entity.権限。"""
    u = punit
    _three_routes(u)
    link(u, "s1", A1)
    at = dt(REV)
    keys = u.portal_export(at=at)[0]["sheet"]["鍵"]
    rows = u.progress("s1", at=at).行
    ents = {e.番号: e for e in u.entities("s1")}
    assert [(k["経路"], k["扉"], k["機器"], k["状態"], k["効くまで"], k["権限"]) for k in keys] == \
        [(r.経路, r.扉, r.機器, r.状態, r.効くまで, ents[r.実体].権限) for r in rows]


def test_export_access_row(punit, link):
    """R-LG-07 22.7: 管理者のインターフェースの行は、インターフェースの名をそのまま出す。扉は空の列、機器は機器の名前。権限は機器の権限。"""
    u = punit
    u.register_device("ルーター1", "ルーター", "使用", **ADM)
    pid = _grant_r(u, "s1", "機器", "ルーター1", "業務")
    u.issue_entity(pid, ACCESS, account="kanrinokuchi9", **ADM, at=AT)
    link(u, "s1", A1)
    sheet = u.portal_export(at=dt("2026-12-05T10:00"))[0]["sheet"]
    assert sheet["権限"][0]["対象の種類"] == "機器" and sheet["権限"][0]["対象"] == "ルーター1"
    assert sheet["鍵"] == [{"権限": pid, "経路": ACCESS, "扉": [], "機器": "ルーター1", "識別": "kanrinokuchi9",
                           "状態": "効いている", "効くまで": ""}]


def test_export_identifier_truncation(punit, link):
    """R-LG-07 22.7 の識別の出し方: 署名つき資格情報と門番を通す錠は先頭 4 文字に「…」(U+2026)。全部は出さない。"""
    u = punit
    _, e1, e2, _, _ = _three_routes(u)
    link(u, "s1", A1)
    text = _dump(u.portal_export(at=dt(REV)))
    for e in (e1, e2):
        assert len(e.識別) > 4
        assert e.識別[:4] + ELLIPSIS in text
        assert e.識別 not in text


def test_export_shape_keys(punit, link):
    """R-AC-13 22.7、22.10 の R-AC-13 の 1: 一人分の鍵は owner、issuer、made_at、sheet の四つ。sheet は 形式、時刻、権限、鍵。
    権限と鍵の一件の鍵は表のとおり。"""
    u = punit
    _three_routes(u)
    link(u, "s1", A1)
    for item in u.portal_export(at=dt(REV)):
        assert sorted(item) == sorted(EXPORT_KEYS)
        assert sorted(item["sheet"]) == sorted(SHEET_KEYS)
        for p in item["sheet"]["権限"]:
            assert sorted(p) == sorted(PERM_KEYS)
        for k in item["sheet"]["鍵"]:
            assert sorted(k) == sorted(KEY_KEYS)
            assert isinstance(k["扉"], list)


def test_export_does_not_write(punit, link):
    """R-AC-13 22.7、22.10 の R-AC-13 の 3: portal_export は台帳に何も書かない。"""
    u = punit
    _three_routes(u)
    link(u, "s1", A1)
    n = u.ledger.count()
    u.portal_export(at=dt(REV))
    u.portal_export()
    assert u.ledger.count() == n


def test_export_is_json_serializable(punit, link):
    """R-AC-13 22.7: コピーは JSON にできる形(dict、list、str)だけ。"""
    u = punit
    _three_routes(u)
    link(u, "s1", A1)
    got = u.portal_export(at=dt(REV))
    assert json.loads(_dump(got)) == got


def test_export_at_aware_datetime(punit, link):
    """R-EB-37 22.7、19.5: 時差つきの at は地方時に直す。made_at と 時刻 は同じ文字列。"""
    u = punit
    link(u, "s1", A1)
    aware = dt(REV).astimezone(timezone.utc)
    item = u.portal_export(at=aware)[0]
    assert item["made_at"] == REV
    assert item["sheet"]["時刻"] == REV


def test_export_at_none_is_now(punit, link):
    """R-EB-37 22.7: at が None なら今。made_at は 19.5 の形(YYYY-MM-DDTHH:MM)で、時刻 と同じ。"""
    u = punit
    link(u, "s1", A1)
    lo = datetime.now().replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    item = u.portal_export()[0]
    hi = datetime.now().strftime("%Y-%m-%dT%H:%M")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", item["made_at"])
    assert lo <= item["made_at"] <= hi
    assert item["sheet"]["時刻"] == item["made_at"]


def test_export_follows_relink(punit, link):
    """R-EB-36 22.7: 結び直した主体のコピーの owner は今の番号。外した主体は出ない。"""
    u = punit
    link(u, "s1", A1)
    link(u, "s1", A2)
    assert [x["owner"] for x in u.portal_export(at=dt(REV))] == [A2]
    u.unlink_portal("s1", reason="本人の申し出", **ROS)
    assert u.portal_export(at=dt(REV)) == []
