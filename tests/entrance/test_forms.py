import sqlite3

from digitalkey.entrance import forms as Y

SAMPLE = """= 試験様式
:様式: 試験
:書ける: 経営者

説明の行。

氏名:: [varchar(10), not null]
人数:: [integer, check (人数 between 1 and 5)]
区分:: [varchar(10), in ('甲', '乙'), not null] 甲か乙
日付:: [date]
  続きの説明
"""


def test_parse():
    y = Y.parse(SAMPLE)
    assert y.name == "試験" and y.title == "試験様式"
    assert y.attrs["書ける"] == "経営者"
    assert y.names == ["氏名", "人数", "区分", "日付"]
    assert y.item("氏名").max_len == 10 and y.item("氏名").not_null
    assert y.item("区分").choices == ["甲", "乙"]
    assert y.item("区分").description == "甲か乙"
    assert y.item("日付").description == "続きの説明"
    assert y.item("人数").check == "人数 between 1 and 5"
    assert y.notes == ["説明の行。"]


def test_bundled_forms_load():
    forms = Y.load_all()
    for name in ["滞在_WWOOF", "宿泊_民泊", "賃貸_定期借家", "利用_会議室", "予約", "鍵_発行", "鍵_失効",
                 "鍵_操作", "本人確認", "駆けつけ"]:
        assert name in forms
    assert forms["宿泊_民泊"].attrs["保存年限"] == "3"


def test_validate_repairs_and_problems():
    y = Y.parse(SAMPLE)
    clean, problems = Y.validate(y, {"氏名": "　山田　", "人数": "３", "区分": "甲", "日付": "令和8年9月16日"})
    assert problems == []
    assert clean == {"氏名": "山田", "人数": "3", "区分": "甲", "日付": "2026-09-16"}
    _, problems = Y.validate(y, {"氏名": "", "人数": "9", "区分": "丙", "日付": "きのう", "余分": "x"})
    assert any("氏名: 必須" in p for p in problems)
    assert any("人数: 条件" in p for p in problems)
    assert any("区分: 甲/乙" in p for p in problems)
    assert any("日付: 日付が読めません" in p for p in problems)
    assert any("余分: 様式にない" in p for p in problems)


def test_date_time_repairs():
    assert Y.repair_date("2026/9/6") == "2026-09-06"
    assert Y.repair_date("20260906") == "2026-09-06"
    assert Y.repair_date("平成元年1月8日") == "1989-01-08"
    assert Y.repair_date("2026-02-30") is None
    assert Y.repair_time("9時5分") == "09:05"
    assert Y.repair_datetime("2026/9/16 15:00") == "2026-09-16T15:00"
    assert Y.repair_datetime("2026-09-16") == "2026-09-16T00:00"


def test_kinyu_roundtrip_with_fullwidth_colon():
    y = Y.parse(SAMPLE)
    text = Y.kinyu_text(y)
    assert "氏名: " in text
    filled = "氏名： 山田\n人数:2\n区分 : 乙\n日付: 2026.9.1\nよけい: x\n"
    values, unknown = Y.parse_kinyu(y, filled)
    assert values == {"氏名": "山田", "人数": "2", "区分": "乙", "日付": "2026.9.1"}
    assert unknown == ["よけい: x"]
    clean, problems = Y.validate(y, values)
    assert problems == [] and clean["日付"] == "2026-09-01"


def test_ddl_runs_in_sqlite():
    db = sqlite3.connect(":memory:")
    for y in Y.load_all().values():
        db.execute(Y.ddl(y))
    db.execute(Y.ddl(Y.parse(SAMPLE)))
    db.execute('INSERT INTO "試験"("番号","氏名","人数","区分") VALUES (?,?,?,?)', ("1", "山田", 3, "甲"))
    try:
        db.execute('INSERT INTO "試験"("番号","氏名","人数","区分") VALUES (?,?,?,?)', ("2", "山田", 9, "甲"))
        assert False, "check が効いていない"
    except sqlite3.IntegrityError:
        pass
