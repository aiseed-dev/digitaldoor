"""設計書 19.13: 要求ごとの確かめ方。ネットワークから切り離して動かす確かめは test_cli.py。

疑似の扉は Controller(SimContacts(), Audit(Ed25519Signer.generate()), name=..., trust=...)。
"""
from __future__ import annotations

import gc

from digitaldoor_controller import credentials
from digitaldoor_controller.audit import Ed25519Signer

from digitalkey.kanri import reasons
from kanri_common import CREDENTIAL, GATE, PHYSICAL, PHYSICAL_WORK, T0, T1, dt, plus, unix

AT = dt("2026-11-20T00:00")
IN = "2026-12-05T10:00"
REV = "2026-12-10T12:00"


# ---- 発行者ごとの扉 ----

def test_r_ac_09_other_issuer_cannot_open(make_unit, grant, door):
    """R-AC-09 発行者 A の資格情報で発行者 B の扉が開かない(19.13 の 1〜3)。"""
    a = make_unit("A", base=True)
    b = make_unit("B", base=True)
    assert a.issuer != b.issuer and a.public_key != b.public_key
    pid, _, _ = grant(a)
    cred = a.issue_entity(pid, CREDENTIAL, by="adm", role="管理者", at=AT).資格情報
    ctl_b = door(b, "玄関", at=IN)
    assert ctl_b.present(cred) == "拒否"
    assert ctl_b.audit.entries[-1].result == "知らない発行者"
    ctl_a = door(a, "玄関", at=IN)
    assert ctl_a.present(cred) == "解錠"


def test_r_ac_09_unregistered_door(make_unit, refused):
    """R-AC-09 A に登録していない扉は申請にも群にも入らない(19.13 の 4)。"""
    a = make_unit("A", base=True)
    make_unit("B", base=True).register_door("倉庫", [PHYSICAL], by="adm", role="管理者")   # B にだけある扉
    refused(a, reasons.UNKNOWN_DOOR, a.request, "s1", "扉", "倉庫", T0, T1, "業務", by="adm", role="名簿の係")
    refused(a, reasons.UNKNOWN_DOOR, a.register_group, "事務所", ["玄関", "倉庫"], by="adm", role="管理者",
            detail="倉庫")


def test_r_ac_09_trust_entry_is_own_issuer_and_doors(make_unit):
    """R-AC-09 A.trust_entry() の鍵は A の発行者の識別だけで、doors は A の使用の扉だけ(19.13 の 5)。"""
    a = make_unit("A", base=True)
    b = make_unit("B", base=True)
    b.register_door("倉庫", [PHYSICAL], by="adm", role="管理者")
    a.register_door("裏口", [GATE], by="adm", role="管理者")
    a.register_door("通用口", [GATE], by="adm", role="管理者")
    a.register_door("通用口", [GATE], state="撤去", by="adm", role="管理者")
    t = a.trust_entry()
    assert list(t) == [a.issuer]
    assert t[a.issuer] == {"public_key": a.public_key.hex(), "doors": ["玄関", "裏口"]}


# ---- 業者と連携先 ----

def test_r_ac_10_vendor_needs_end(unit, refused):
    """R-AC-10 業者の主体の、終了が空の申請は VENDOR_NO_END(19.13 の 1)。"""
    unit.register_subject("v1", "保守会社A", "業者", True, by="adm", role="名簿の係")
    refused(unit, reasons.VENDOR_NO_END, unit.request, "v1", "扉", "玄関", T0, "", "保守",
            by="adm", role="名簿の係")


def test_r_ac_10_vendor_max_days(unit, refused):
    """R-AC-10 業者の最長の日数 N を 1 分超える申請は VENDOR_TOO_LONG、ちょうど N 日は通る(19.13 の 2)。"""
    kw = dict(by="adm", role="名簿の係")
    unit.register_subject("v1", "保守会社A", "業者", True, **kw)
    unit.set_setting("業者の最長の日数", "14", reason="保守の契約", by="adm", role="管理者")
    refused(unit, reasons.VENDOR_TOO_LONG, unit.request, "v1", "扉", "玄関", T0, plus(T0, days=14, minutes=1),
            "保守", **kw)
    ok = unit.request("v1", "扉", "玄関", T0, plus(T0, days=14), "保守", **kw)
    assert ok.form == "権限_申請"


def test_r_ac_10_changed_to_vendor_before_issue(unit, refused, request_, approve):
    """R-AC-10 社員として申請し承認した後に業者に変えると、終了が空の権限の issue は VENDOR_NO_END(19.13 の 3)。"""
    r = request_(unit, end="")
    a = approve(unit, r.id)
    unit.change_subject("s1", reason="業務委託に変わった", vendor=True, by="adm", role="名簿の係")
    refused(unit, reasons.VENDOR_NO_END, unit.issue, a.id, "s1", "扉", "玄関", T0, "", by="adm", role="管理者",
            at=AT)


def test_r_ac_10_vendor_own_key_is_refused_at_door(unit, door):
    """R-AC-10 業者が自分で作った鍵で署名した資格情報を、trust=u.trust_entry() の扉は拒み、記録に残す(19.13 の 4)。"""
    vendor_key = Ed25519Signer.generate()
    forged = credentials.issue(vendor_key, cred_id="v1-remote", subject="v1", doors=["玄関"],
                               not_before=unix(T0), not_after=unix(T1) - 1)
    ctl = door(unit, "玄関", at=IN)
    assert ctl.present(forged) == "拒否"
    last = ctl.audit.entries[-1]
    assert (last.kind, last.event, last.result) == ("認証", "拒否", "知らない発行者")


def test_r_ac_10_once_credential_works_without_unit(make_unit, grant, door):
    """R-AC-10 一回限りの資格情報は、置き場の Unit を閉じた後でも扉で一度だけ解錠し、二度目は 使用済み(19.13 の 5)。"""
    u = make_unit("A", base=True)
    pid, _, _ = grant(u)
    cred = u.issue_entity(pid, CREDENTIAL, once=True, by="adm", role="管理者", at=AT).資格情報
    ctl = door(u, "玄関", at=IN)
    del u
    gc.collect()
    assert ctl.present(cred) == "解錠"
    assert ctl.present(cred) == "拒否"
    assert ctl.audit.entries[-1].result == "使用済み"


# ---- 一人の全経路 ----

def test_r_ac_15_all_routes_listed_and_revoked_at_once(unit, grant, request_, approve, door, forms, refused):
    """R-AC-15 一人の全経路を一覧にし、一度の操作で全部を失効できる。機械の鍵は回収の記録を残す(19.13 の 1〜7)。"""
    u = unit
    kw = dict(by="adm", role="管理者", at=AT)
    # 1. 同じ権限 P1 から三つの経路を出す。entities("s1") が三つの経路を返す。
    #    失効の前に別の申請を二つ: P2 は承認まで(発行しない)、P3 は申請のまま
    p1, _, _ = grant(u)
    got1 = u.issue_entity(p1, CREDENTIAL, **kw)
    e2 = u.issue_entity(p1, GATE, **kw).実体
    e3 = u.issue_entity(p1, PHYSICAL, key_number="K-7", **kw).実体
    cred, e1 = got1.資格情報, got1.実体
    ents = u.entities("s1")
    assert [e.経路 for e in ents] == [CREDENTIAL, GATE, PHYSICAL]
    assert [e.番号 for e in ents] == [e1.番号, e2.番号, e3.番号]
    p2 = request_(u)
    a2 = approve(u, p2.id)
    p3 = request_(u)
    assert (u.permission(p2.id, at=AT).状態, u.permission(p3.id, at=AT).状態) == ("承認", "申請中")

    # 2. 失効の前: 資格情報も門番も 解錠。進み具合は三つとも 効いている
    ctl = door(u, "玄関", at=IN)
    assert ctl.present(cred) == "解錠"
    assert u.relay(ctl, "s1") == "解錠"
    s = u.progress("s1", at=dt(IN))
    assert {r.実体: r.状態 for r in s.行} == {e1.番号: "効いている", e2.番号: "効いている", e3.番号: "効いている"}

    # 3. revoke_subject を一度呼ぶ。権限_失効は開いた権限の数(P1、P2、P3 の 3 枚)、実体_失効は 3 枚、
    #    失効_一覧 は 1 枚(21.2)
    before = forms(u)
    rev = u.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    after = forms(u)
    assert after - before == {"権限_失効": 3, "実体_失効": 3, "失効_一覧": 1}
    assert {s["権限"] for s in rev.権限_失効} == {p1, p2.id, p3.id}
    results = {s["実体"]: s["結果"] for s in rev.実体_失効}
    assert results == {e1.番号: "待ち", e2.番号: "止まった", e3.番号: "人の作業が要る"}

    # 4. 失効の直後: 門番は拒否(失効しています)。may_open は GATE_REVOKED。進み具合は 19.10 の例のとおり
    #    (E1 は 待ち で、効くまでは権限の終了。扉 玄関 に最長の分数が無いため。21.2)
    ctl.set_time(unix(REV), synced=True)
    assert u.relay(ctl, "s1") == "拒否"
    assert ctl.audit.entries[-1].result == "失効しています"
    assert u.may_open("s1", "玄関", at=dt(REV)).理由 == reasons.GATE_REVOKED
    s = u.progress("s1", at=dt(REV))
    rows = {r.実体: r for r in s.行}
    assert (rows[e1.番号].状態, rows[e1.番号].効くまで) == ("待ち", T1)
    assert rows[e2.番号].状態 == "止まった"
    assert (rows[e3.番号].状態, rows[e3.番号].作業) == ("人の作業が要る", PHYSICAL_WORK.format("K-7"))
    assert s.済み is False
    refused(u, reasons.NOT_ALL_STOPPED, u.confirm_stopped, "s1", by="adm", role="管理者", at=dt(REV))

    # 5. 待ちの物は期限を過ぎると開かない(一覧を扉に渡さない時の確かめ: 終了の 1 秒前までは開く。21.2)
    ctl.set_time(unix(T1) - 1, synced=True)
    assert ctl.present(cred) == "解錠"
    ctl.set_time(unix(T1), synced=True)
    assert ctl.present(cred) == "拒否"
    assert ctl.audit.entries[-1].result == "期限の外"

    # 6. 物理キーを回収すると 止まった。at を権限の終了にした進み具合は済みで、confirm_stopped が書ける
    u.return_physical(e3.番号, "回収", by="adm", role="名簿の係")
    assert {r.実体: r.状態 for r in u.progress("s1", at=dt(REV)).行}[e3.番号] == "止まった"
    s = u.progress("s1", at=dt(T1))
    assert s.済み is True
    c = u.confirm_stopped("s1", by="adm", role="管理者", at=dt(T1))
    assert c.form == "失効_確認"
    assert set(c["実体"].split(", ")) == {e1.番号, e2.番号, e3.番号}

    # 7. 二度目の revoke_subject は伝票(失効_一覧 も)を増やさず、列は空、Revocation.失効_一覧 は None(21.2)。
    #    P1 の issue_entity、P2 の issue、P3 の decide は REVOKED
    n = u.ledger.count()
    again = u.revoke_subject("s1", "退職", by="adm", role="管理者", at=dt(REV))
    assert u.ledger.count() == n
    assert (again.権限_失効, again.実体_失効, again.失効_一覧) == ([], [], None)
    refused(u, reasons.REVOKED, u.issue_entity, p1, GATE, **kw)
    refused(u, reasons.REVOKED, u.issue, a2.id, "s1", "扉", "玄関", T0, T1, **kw)
    refused(u, reasons.REVOKED, u.decide, p3.id, "承認", by="apr", role="承認する人")


# ---- 名簿が正 ----

def test_r_eb_37_people_outside_directory(unit, grant):
    """R-EB-37 ディレクトリに居ない人(清掃 c1、来客 g1)を名簿に載せ、門番を通す錠の実体を出すと may_open が通る(19.13 の 1)。"""
    unit.register_subject("c1", "清掃A", "清掃", False, by="adm", role="名簿の係")
    unit.register_subject("g1", "来客A", "来客", False, by="adm", role="名簿の係")
    for who in ("c1", "g1"):
        pid, _, _ = grant(unit, who)
        unit.issue_entity(pid, GATE, by="adm", role="管理者", at=AT)
        v = unit.may_open(who, "玄関", at=dt(IN))
        assert v.ok is True, v


def test_r_eb_37_import_skips_unknown_people(unit):
    """R-EB-37 名簿に無い識別の行は 飛ばした に 名簿にありません で入り、名簿も台帳も増えない(19.13 の 2)。"""
    n_subjects, n_slips = len(unit.subjects()), unit.ledger.count()
    res = unit.import_requests([{"識別": "x9", "対象の種類": "扉", "対象": "玄関", "開始": T0, "終了": T1,
                                 "理由": "入社"}], "人事の届け", by="adm", role="名簿の係")
    assert res.申請 == []
    assert res.飛ばした == [(1, "名簿にありません")]
    assert len(unit.subjects()) == n_subjects
    assert unit.ledger.count() == n_slips


def test_r_eb_37_import_makes_requests_only(unit, forms):
    """R-EB-37 名簿にある s1 の行は権限_申請(出どころ 外の一覧、一覧の名つき)だけを増やし、状態は申請中、門番は通さない(19.13 の 3)。"""
    before = forms(unit)
    res = unit.import_requests([{"識別": "s1", "対象の種類": "扉", "対象": "玄関", "開始": T0, "終了": T1,
                                 "理由": "異動"}], "予約の書き出し", by="adm", role="名簿の係")
    after = forms(unit)
    assert after - before == {"権限_申請": 1}
    slip, = res.申請
    assert (slip["出どころ"], slip["一覧の名"]) == ("外の一覧", "予約の書き出し")
    assert unit.permission(slip.id, at=dt(IN)).状態 == "申請中"
    assert unit.may_open("s1", "玄関", at=dt(IN)).理由 == reasons.GATE_NO_PERMISSION
    for form in ("権限_承認", "権限_発行", "実体_発行", "主体_登録", "役_割当"):
        assert after[form] == before[form]


def test_r_eb_37_import_only_by_roster_role(unit, refused):
    """R-EB-37 取り込みは名簿の係の役だけ。role="本人" は ROLE_NOT_ALLOWED(19.13 の 4)。"""
    refused(unit, reasons.ROLE_NOT_ALLOWED, unit.import_requests,
            [{"識別": "s1", "対象の種類": "扉", "対象": "玄関", "開始": T0, "終了": T1, "理由": "異動"}],
            "人事の届け", by="s1", role="本人")

