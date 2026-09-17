import os

import pytest

WWOOF = {"氏名": "Anna", "国籍": "ドイツ", "在留資格": "ワーキングホリデー", "到着日": "2026-10-01",
         "傷害保険": "あり", "手伝い時間": "6"}


def test_append_is_immutable_and_chained(ledger):
    a = ledger.append("滞在_WWOOF", WWOOF, issuer="経営者")
    b = ledger.append("滞在_WWOOF", {**WWOOF, "氏名": "Ben"}, issuer="AI社員")
    assert a.prev_hash == "" and b.prev_hash == a.hash
    assert not os.access(a.path, os.W_OK)
    assert ledger.verify_chain() == (True, 2, "")
    got = ledger.get(b.id)
    assert got.values["氏名"] == "Ben" and got.issuer == "AI社員" and got.form == "滞在_WWOOF"
    text = a.path.read_text(encoding="utf-8")
    assert text.startswith("= 滞在_WWOOF ") and ":前ハッシュ: \n" in text and "氏名:: Anna" in text


def test_tampering_is_detected(ledger):
    a = ledger.append("滞在_WWOOF", WWOOF, issuer="経営者")
    ledger.append("滞在_WWOOF", {**WWOOF, "氏名": "Ben"}, issuer="経営者")
    os.chmod(a.path, 0o640)
    a.path.write_text(a.path.read_text(encoding="utf-8").replace("Anna", "Eve"), encoding="utf-8")
    ok, n, why = ledger.verify_chain()
    assert not ok and n == 0 and "内容が変わって" in why


def test_owner_only_writes(ledger):
    with pytest.raises(PermissionError):
        ledger.append("賃貸_定期借家", {}, issuer="AI社員")
    with pytest.raises(PermissionError):
        ledger.append("滞在_WWOOF", WWOOF, issuer="通りすがり")
    ledger.append("滞在_WWOOF", WWOOF, issuer="オーナー")  # 役割で書ける


def test_rejects_bad_values(ledger):
    with pytest.raises(ValueError) as e:
        ledger.append("滞在_WWOOF", {**WWOOF, "傷害保険": "たぶん"}, issuer="経営者")
    assert "傷害保険" in str(e.value)
    with pytest.raises(ValueError):
        ledger.append("無い様式", {}, issuer="経営者")


def test_select_where_and_basis(ledger):
    a = ledger.append("滞在_WWOOF", WWOOF, issuer="経営者")
    ledger.append("滞在_WWOOF", {**WWOOF, "氏名": "Ben"}, issuer="経営者", basis=[a.id], to="Ben")
    rows = ledger.select("滞在_WWOOF", where={"氏名": "Ben"})
    assert len(rows) == 1 and rows[0].basis == [a.id] and rows[0].to == "Ben"
    assert ledger.count() == 2
