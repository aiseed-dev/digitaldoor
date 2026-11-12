"""設計書 20.7: 機器の確かめ(据え付け、点検、廃棄)と更新の記録。

段 1b の実装より先に書いた。確かめの日時は固定の値を `at=` で渡す。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from digitalkey.kanri import reasons
from kanri_common import CHECK_ITEMS, RB

ADM = dict(by="adm", role="管理者")
C0 = "2026-12-01T09:00"
C1 = "2026-12-15T10:30"

# 場面ごとに足りる結果(20.7 の表)
ROUTER_CHECK = {"外部からのアクセス経路": "良い", "見覚えのない設定とアカウント": "良い"}


@pytest.fixture
def owned(dunit):
    """ルーター1 と複合機1 の責任者を t1 にした置き場。"""
    dunit.change_device("ルーター1", reason="責任者を決めた", owner="t1", **ADM)
    dunit.change_device("複合機1", reason="責任者を決めた", owner="t1", **ADM)
    return dunit


# ---- 確かめ ----

def test_check_device_example(owned):
    """20.7 の例: t1(責任者)が本人の役で据え付けの確かめを書くと、伝票 機器_確かめ。checks の最後の 結果 は空でない項目だけ。"""
    u = owned
    slip = u.check_device("ルーター1", "据え付け", {"初めのパスワード": "良い", "外部からのアクセス経路": "悪い"},
                          note="遠隔の管理が開いていた", at="2026-12-01T09:00", by="t1", role="本人")
    assert slip.form == "機器_確かめ" and slip.issuer == "t1" and slip.to == "" and slip.basis == []
    assert (slip["機器"], slip["場面"], slip["日時"], slip["初めのパスワード"], slip["外部からのアクセス経路"], slip["バージョン"],
            slip["所見"], slip["役"]) == \
        ("ルーター1", "据え付け", "2026-12-01T09:00", "良い", "悪い", "", "遠隔の管理が開いていた", "本人")
    c = u.checks("ルーター1")[-1]
    assert c.結果 == {"初めのパスワード": "良い", "外部からのアクセス経路": "悪い"}
    assert (c.番号, c.機器, c.場面, c.日時, c.所見, c.書いた人) == \
        (slip.id, "ルーター1", "据え付け", "2026-12-01T09:00", "遠隔の管理が開いていた", "t1")


def test_check_result_drops_empty_values(owned):
    """20.4 の Check.結果: 確かめの項目のうち空でない物だけ。鍵は項目の名。"""
    owned.check_device("複合機1", "点検", {"バージョン": "良い", "区画": "", "権限": "当たらない"}, at=C0, **ADM)
    assert owned.checks("複合機1")[-1].結果 == {"バージョン": "良い", "権限": "当たらない"}


def test_check_at_datetime(owned):
    """20.7、20.14: at は 19.5 の形の文字列か datetime。datetime は秒を落とす。"""
    owned.check_device("複合機1", "点検", {"バージョン": "良い"}, at=datetime(2026, 12, 1, 9, 0, 30), **ADM)
    assert owned.checks("複合機1")[-1].日時 == C0


def test_checks_order_and_unknown(owned, refused):
    """20.7: checks(name) はその機器の確かめを台帳に書いた順。機器が無ければ UNKNOWN_DEVICE(詳細に機器の名)。"""
    u = owned
    a = u.check_device("複合機1", "点検", {"バージョン": "良い"}, at=C1, **ADM)
    u.check_device("ルーター1", "点検", ROUTER_CHECK, at=C0, **ADM)
    b = u.check_device("複合機1", "据え付け", {"初めのパスワード": "良い"}, at=C0, **ADM)
    assert [c.番号 for c in u.checks("複合機1")] == [a.id, b.id]
    assert u.checks("玄関の錠") == []
    refused(u, RB.UNKNOWN_DEVICE, u.checks, "ルーター9", detail="ルーター9")


def test_check_roles(owned, refused):
    """20.3: 管理者、運用、本人で書ける。本人は機器の今の責任者だけ(ほかは NOT_OWNER)。運用は責任者を問わない。"""
    u = owned
    assert u.check_device("複合機1", "点検", {"バージョン": "良い"}, at=C0, by="t1", role="本人").issuer == "t1"
    assert u.check_device("複合機1", "点検", {"バージョン": "良い"}, at=C0, by="ops", role="運用")["役"] == "運用"
    assert u.check_device("複合機1", "点検", {"バージョン": "良い"}, at=C0, **ADM)["役"] == "管理者"
    refused(u, RB.NOT_OWNER, u.check_device, "複合機1", "点検", {"バージョン": "良い"}, at=C0, by="t2", role="本人", detail="")
    # 責任者の居ない機器は、本人の役では誰も書けない
    refused(u, RB.NOT_OWNER, u.check_device, "玄関の錠", "点検", {"バージョン": "良い"}, at=C0, by="t1", role="本人")
    # 責任者を替えると、前の責任者は書けない
    u.change_device("複合機1", reason="引き継ぎ", owner="t2", **ADM)
    refused(u, RB.NOT_OWNER, u.check_device, "複合機1", "点検", {"バージョン": "良い"}, at=C0, by="t1", role="本人")
    assert u.check_device("複合機1", "点検", {"バージョン": "良い"}, at=C0, by="t2", role="本人").form == "機器_確かめ"


def test_check_refusals(owned, refused):
    """20.7 の確かめ: UNKNOWN_DEVICE、DEVICE_RETIRED、知らない鍵は FORM(詳細にその鍵)、全部空で所見も空は FORM、
    場面の値と結果の値が様式に無ければ FORM。"""
    u = owned
    ck = u.check_device
    refused(u, RB.UNKNOWN_DEVICE, ck, "ルーター9", "点検", {"バージョン": "良い"}, at=C0, **ADM, detail="ルーター9")
    refused(u, reasons.FORM, ck, "複合機1", "点検", {"色": "良い"}, at=C0, **ADM, detail="色")
    refused(u, reasons.FORM, ck, "複合機1", "点検", {}, at=C0, **ADM, detail="確かめの項目か所見が要ります")
    refused(u, reasons.FORM, ck, "複合機1", "点検", {"バージョン": "", "区画": ""}, at=C0, **ADM,
            detail="確かめの項目か所見が要ります")
    refused(u, reasons.FORM, ck, "複合機1", "点検", {"バージョン": "まあまあ"}, at=C0, **ADM)
    refused(u, reasons.FORM, ck, "複合機1", "修理", {"バージョン": "良い"}, at=C0, **ADM)
    refused(u, reasons.FORM, ck, "複合機1", "点検", {"バージョン": "良い"}, note="あ" * 401, at=C0, **ADM)


def test_check_note_only_is_enough_when_nothing_required(owned):
    """20.7 の 5: 結果が全部空でも、所見があれば通る(要る項目の無い場面)。"""
    slip = owned.check_device("複合機1", "点検", {}, note="電源を入れ直した", at=C0, **ADM)
    assert slip["所見"] == "電源を入れ直した"
    assert owned.checks("複合機1")[-1].結果 == {}


def test_check_refusal_order(owned, refused):
    """20.7 の確かめの順: 機器 → 責任者 → 廃棄 → 知らない鍵 → 全部空 → 要る項目。"""
    u = owned
    ck = u.check_device
    refused(u, RB.UNKNOWN_DEVICE, ck, "ルーター9", "点検", {"色": "良い"}, at=C0, by="t2", role="本人")
    u.change_device("複合機1", reason="入れ替え", state="廃棄", **ADM)
    refused(u, RB.NOT_OWNER, ck, "複合機1", "点検", {"色": "良い"}, at=C0, by="t2", role="本人")
    refused(u, RB.DEVICE_RETIRED, ck, "複合機1", "点検", {"色": "良い"}, at=C0, by="t1", role="本人")
    refused(u, reasons.FORM, ck, "ルーター1", "点検", {"色": "良い"}, at=C0, **ADM, detail="色")
    refused(u, reasons.FORM, ck, "ルーター1", "点検", {}, at=C0, **ADM, detail="確かめの項目か所見が要ります")
    refused(u, RB.NEED_ITEM, ck, "ルーター1", "点検", {"バージョン": "良い"}, at=C0, **ADM, detail="外部からのアクセス経路")


def test_retired_device_only_disposal_check(owned, refused):
    """20.7 の 3: 廃棄の機器は、場面が 廃棄 の確かめだけ書ける。"""
    u = owned
    u.change_device("複合機1", reason="入れ替え", state="廃棄", **ADM)
    for scene in ("据え付け", "点検"):
        refused(u, RB.DEVICE_RETIRED, u.check_device, "複合機1", scene, {"初めのパスワード": "良い"}, at=C0, **ADM,
                detail="")
    slip = u.check_device("複合機1", "廃棄", {"中に残る物": "良い", "ネットワークから外した": "良い"}, at=C0, **ADM)
    assert slip["場面"] == "廃棄"


@pytest.mark.parametrize("device,scene,given,missing", [
    ("ルーター1", "据え付け", {"外部からのアクセス経路": "良い"}, "初めのパスワード"),
    ("複合機1", "据え付け", {"バージョン": "良い"}, "初めのパスワード"),
    ("ルーター1", "点検", {"バージョン": "良い"}, "外部からのアクセス経路"),
    ("ルーター1", "点検", {"外部からのアクセス経路": "良い"}, "見覚えのない設定とアカウント"),
    ("ルーター1", "点検", {"見覚えのない設定とアカウント": "良い"}, "外部からのアクセス経路"),
    ("ルーター1", "廃棄", {"バージョン": "良い"}, "中に残る物"),
    ("ルーター1", "廃棄", {"中に残る物": "良い"}, "ネットワークから外した"),
    ("複合機1", "廃棄", {"ネットワークから外した": "良い"}, "中に残る物"),
])
def test_need_item_per_scene(owned, refused, device, scene, given, missing):
    """20.7 の 6 の表: 場面ごとに要る項目が空なら NEED_ITEM(詳細に、足りない最初の項目。様式の項目の順)。"""
    refused(owned, RB.NEED_ITEM, owned.check_device, device, scene, given, at=C0, **ADM, detail=missing)


@pytest.mark.parametrize("device,scene,given", [
    ("ルーター1", "据え付け", {"初めのパスワード": "当たらない"}),
    ("ルーター1", "点検", {"外部からのアクセス経路": "当たらない", "見覚えのない設定とアカウント": "悪い"}),
    ("複合機1", "点検", {"バージョン": "良い"}),
    ("複合機1", "点検", {"外部からのアクセス経路": "", "見覚えのない設定とアカウント": "", "権限": "良い"}),
    ("ルーター1", "廃棄", {"中に残る物": "当たらない", "ネットワークから外した": "悪い"}),
])
def test_required_items_any_value(owned, device, scene, given):
    """20.7: 「要る」は空でない事で、当たらない も 悪い も通る。点検の要る項目はルーターだけ。"""
    assert owned.check_device(device, scene, given, at=C0, **ADM).form == "機器_確かめ"


def test_check_items_are_nine_in_form_order(owned):
    """20.7: 確かめの項目は九つ。どれも書ける。"""
    results = {k: "良い" for k in CHECK_ITEMS}
    slip = owned.check_device("ルーター1", "点検", results, at=C0, **ADM)
    assert owned.checks("ルーター1")[-1].結果 == results
    assert all(slip[k] == "良い" for k in CHECK_ITEMS)


# ---- 更新 ----

def test_record_update_slip(owned):
    """20.7: 機器_更新を書く。前のバージョンは書く時の台帳の今のバージョン(空でよい)、後のバージョンは version、結果は既定で 当てた。"""
    u = owned
    slip = u.record_update("ルーター1", "1.1", at="2026-12-02T10:00", by="t1", role="本人")
    assert slip.form == "機器_更新" and slip.issuer == "t1" and slip.to == "" and slip.basis == []
    assert (slip["機器"], slip["日時"], slip["前のバージョン"], slip["後のバージョン"], slip["結果"], slip["理由"], slip["役"]) == \
        ("ルーター1", "2026-12-02T10:00", "", "1.1", "当てた", "", "本人")
    assert u.device("ルーター1").バージョン == "1.1"


def test_version_is_last_of_register_change_and_applied_updates(dunit):
    """20.4: バージョンは、機器_登録、空でないバージョンの機器_変更、結果が 当てた の機器_更新の 後のバージョン を、書いた順に読んだ最後の物。"""
    u = dunit
    u.register_device("PC1", "PC", "使用", version="1.0", **ADM)
    assert u.device("PC1").バージョン == "1.0"
    u.record_update("PC1", "1.1", at=C0, **ADM)
    assert u.device("PC1").バージョン == "1.1"
    u.change_device("PC1", reason="台帳を直した", version="2.0", **ADM)
    assert u.device("PC1").バージョン == "2.0"
    failed = u.record_update("PC1", "2.1", result="当てられなかった", reason="容量が足りない", at=C0, **ADM)
    assert failed["前のバージョン"] == "2.0"
    assert u.device("PC1").バージョン == "2.0"
    u.change_device("PC1", reason="置き直し", place="二階", **ADM)
    assert u.device("PC1").バージョン == "2.0"
    applied = u.record_update("PC1", "2.2", at=C1, **ADM)
    assert applied["前のバージョン"] == "2.0"
    assert u.device("PC1").バージョン == "2.2"


def test_record_update_refusals(owned, refused):
    """20.7 の更新の確かめ: UNKNOWN_DEVICE、NOT_OWNER、DEVICE_RETIRED、当てられなかった で理由が空なら NEED_REASON。
    result の外れは FORM。"""
    u = owned
    ru = u.record_update
    refused(u, RB.UNKNOWN_DEVICE, ru, "ルーター9", "1.1", at=C0, **ADM, detail="ルーター9")
    refused(u, RB.NOT_OWNER, ru, "ルーター1", "1.1", at=C0, by="t2", role="本人", detail="")
    refused(u, RB.NEED_REASON, ru, "ルーター1", "1.1", result="当てられなかった", at=C0, **ADM, detail="")
    refused(u, reasons.FORM, ru, "ルーター1", "1.1", result="あとで", at=C0, **ADM)
    refused(u, reasons.FORM, ru, "ルーター1", "", at=C0, **ADM)
    u.change_device("ルーター1", reason="入れ替え", state="廃棄", **ADM)
    refused(u, RB.DEVICE_RETIRED, ru, "ルーター1", "1.1", at=C0, **ADM, detail="")


def test_record_update_refusal_order(owned, refused):
    """20.7 の更新の確かめの順: 機器 → 責任者 → 廃棄 → 理由。"""
    u = owned
    ru = u.record_update
    refused(u, RB.UNKNOWN_DEVICE, ru, "ルーター9", "1.1", result="当てられなかった", at=C0, by="t2", role="本人")
    u.change_device("複合機1", reason="入れ替え", state="廃棄", **ADM)
    refused(u, RB.NOT_OWNER, ru, "複合機1", "1.1", result="当てられなかった", at=C0, by="t2", role="本人")
    refused(u, RB.DEVICE_RETIRED, ru, "複合機1", "1.1", result="当てられなかった", at=C0, by="t1", role="本人")


def test_record_update_roles(owned):
    """20.3: record_update は 管理者、運用、本人(責任者)。運用は責任者を問わない。"""
    assert owned.record_update("ルーター1", "1.1", at=C0, by="ops", role="運用")["役"] == "運用"
    assert owned.record_update("ルーター1", "1.2", at=C0, by="t1", role="本人")["役"] == "本人"
    assert owned.device("ルーター1").バージョン == "1.2"
