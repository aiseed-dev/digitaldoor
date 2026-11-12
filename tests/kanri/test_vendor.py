"""設計書 20.10: 業者と連携先に渡している物の目録(vendor_inventory)。要求ごとの筋書きは test_requirements_1b.py。"""
from __future__ import annotations

from kanri_common import ACCESS, GATE, ISSUE_AT, PHYSICAL, T0, T1, dt

AT = dt(ISSUE_AT)
IN = "2026-12-05T10:00"
REV = "2026-12-10T12:00"
ADM = dict(by="adm", role="管理者")


def test_held_attributes(dunit, access):
    """R-RS-04 20.10: Held(主体, 権限, 対象の種類, 対象, 開始, 終了, 状態, 残り)。残り は止まっていない進み具合の行。"""
    pid, e = access(dunit, "v1", account="maint")
    h, = dunit.vendor_inventory(at=dt(IN))
    assert (h.主体, h.権限, h.対象の種類, h.対象, h.開始, h.終了, h.状態) == ("v1", pid, "機器", "ルーター1", T0, T1, "発行済み")
    r, = h.残り
    assert (r.実体, r.経路, r.識別, r.状態, r.機器) == (e.番号, ACCESS, "maint", "効いている", "ルーター1")


def test_only_vendors_by_current_roster(dunit, access, grant):
    """R-RS-04 20.10: 今の名簿で 業者 が はい の主体の権限だけ。後で業者に変えた主体も載る。"""
    access(dunit, "t1")
    assert dunit.vendor_inventory(at=dt(IN)) == []
    dunit.change_subject("t1", reason="業務委託に変わった", vendor=True, by="adm", role="名簿の係")
    assert [h.主体 for h in dunit.vendor_inventory(at=dt(IN))] == ["t1"]


def test_issued_without_entities_is_listed(dunit, grant, request_, approve):
    """R-RS-04 20.10: 状態が 発行済み の権限は、実体が無くても載る(残り は空)。申請中と承認だけの権限は載らない。"""
    pid, _, _ = grant(dunit, "v1")
    r = request_(dunit, "v1", "機器", "ルーター1")
    approve(dunit, request_(dunit, "v1", "機器", "複合機1").id)
    h, = dunit.vendor_inventory(at=dt(IN))
    assert (h.権限, h.対象の種類, h.対象, h.残り) == (pid, "扉", "玄関", [])
    assert r.id not in {x.権限 for x in dunit.vendor_inventory(at=dt(IN))}


def test_order_by_request(dunit, grant, access):
    """R-RS-04 20.10: 権限_申請を台帳に書いた順。扉、群、機器の権限のどれも入る。"""
    dunit.register_group("事務所", ["玄関"], **ADM)
    p1, _ = access(dunit, "v1", "複合機1")
    p2, _, _ = grant(dunit, "v1", "群", "事務所")
    p3, _, _ = grant(dunit, "v1")
    assert [(h.権限, h.対象の種類) for h in dunit.vendor_inventory(at=dt(IN))] == \
        [(p1, "機器"), (p2, "群"), (p3, "扉")]


def test_revoked_permission_stays_while_something_is_left(dunit, access, grant):
    """R-RS-04 20.10: 失効した後でも、管理者のインターフェースや物理キーが残っている間は目録に残る。門番の錠は止まるので消える。"""
    p_dev, e = access(dunit, "v1", account="maint")
    p_door, _, _ = grant(dunit, "v1")
    k = dunit.issue_entity(p_door, PHYSICAL, key_number="K-9", **ADM, at=AT).実体
    p_gate, _, _ = grant(dunit, "v1")
    dunit.issue_entity(p_gate, GATE, **ADM, at=AT)
    dunit.revoke_subject("v1", "契約の終了", **ADM, at=dt(REV))
    got = {h.権限: h for h in dunit.vendor_inventory(at=dt(REV))}
    assert list(got) == [p_dev, p_door]
    assert got[p_dev].状態 == "失効"
    assert [(r.実体, r.状態) for r in got[p_dev].残り] == [(e.番号, "人の作業が要る")]
    assert [(r.実体, r.状態) for r in got[p_door].残り] == [(k.番号, "人の作業が要る")]
    dunit.return_physical(k.番号, by="adm", role="名簿の係")
    assert [h.権限 for h in dunit.vendor_inventory(at=dt(REV))] == [p_dev]


def test_expired_permission_with_unreturned_key(dunit, grant):
    """R-RS-04 20.10: 期限切れの権限も、止まっていない実体(回収の無い物理キー)があれば載る。"""
    pid, _, _ = grant(dunit, "v1", end="2026-12-05T00:00")
    k = dunit.issue_entity(pid, PHYSICAL, key_number="K-9", **ADM, at=AT).実体
    h, = dunit.vendor_inventory(at=dt(REV))
    assert (h.権限, h.状態) == (pid, "期限切れ")
    assert [r.実体 for r in h.残り] == [k.番号]


def test_left_rows_in_issue_order_and_without_stopped(dunit, grant, access):
    """R-RS-04 20.10: 残り は止まった でない行だけで、実体_発行を台帳に書いた順。"""
    pid, _, _ = grant(dunit, "v1")
    k2 = dunit.issue_entity(pid, PHYSICAL, key_number="K-2", **ADM, at=AT).実体
    dunit.issue_entity(pid, GATE, **ADM, at=AT)
    k1 = dunit.issue_entity(pid, PHYSICAL, key_number="K-1", **ADM, at=AT).実体
    h, = dunit.vendor_inventory(at=dt(IN))
    assert [r.実体 for r in h.残り][0] == k2.番号 and [r.実体 for r in h.残り][-1] == k1.番号
    dunit.revoke_permission(pid, "契約の終了", **ADM, at=dt(REV))
    h, = dunit.vendor_inventory(at=dt(REV))
    assert [r.実体 for r in h.残り] == [k2.番号, k1.番号]   # 門番の行は 止まった なので入らない


def test_inventory_does_not_write(dunit, access):
    """20.10: 目録は読むだけ(役を取らず、伝票を書かない)。"""
    access(dunit, "v1")
    n = dunit.ledger.count()
    dunit.vendor_inventory(at=dt(IN))
    assert dunit.ledger.count() == n
