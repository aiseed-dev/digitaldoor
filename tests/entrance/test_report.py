from datetime import date

from digitalkey.entrance import report

MINPAKU = {"氏名": "A", "住所": "x", "職業": "y", "国籍": "米国", "旅券番号": "P1"}


def test_minpaku_teiki(ledger):
    ledger.append("宿泊_民泊", {**MINPAKU, "到着日": "2026-10-01", "出発日": "2026-10-03"}, issuer="経営者")
    ledger.append("宿泊_民泊", {**MINPAKU, "氏名": "B", "国籍": "日本", "到着日": "2026-10-02", "出発日": "2026-10-04"},
                  issuer="経営者")
    ledger.append("宿泊_民泊", {**MINPAKU, "氏名": "C", "到着日": "2026-12-01", "出発日": "2026-12-02"}, issuer="経営者")
    out = report.minpaku_teiki(ledger, date(2026, 10, 1), date(2026, 11, 30))
    assert "届出住宅に人を宿泊させた日数:: 3" in out   # 10/1, 10/2, 10/3
    assert "宿泊者数:: 2" in out and "延べ宿泊者数:: 4" in out
    assert "| 日本 | 1" in out and "| 米国 | 1" in out


def test_wwoof_monthly(ledger):
    base = {"国籍": "ドイツ", "傷害保険": "あり", "手伝い時間": "6", "手伝いの内容": "草取り"}
    ledger.append("滞在_WWOOF", {**base, "氏名": "Anna", "到着日": "2026-09-20", "出発日": "2026-10-05"}, issuer="経営者")
    ledger.append("滞在_WWOOF", {**base, "氏名": "Ben", "到着日": "2026-10-20"}, issuer="経営者")
    ledger.append("滞在_WWOOF", {**base, "氏名": "Cid", "到着日": "2026-08-01", "出発日": "2026-08-10"}, issuer="経営者")
    out = report.wwoof_monthly(ledger, 2026, 10)
    assert "受け入れ 2 名" in out and "| Anna |" in out and "| Ben |" in out and "滞在中" in out and "Cid" not in out
