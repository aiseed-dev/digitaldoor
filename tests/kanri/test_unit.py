"""設計書 19.1〜19.3、19.11: 置き場(動かす単位)、データの型、拒否、役と操作の確かめの順番、理由の言葉。"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime
import os
import stat

from pathlib import Path

import pytest
from digitaldoor_controller.audit import Ed25519Signer
from digitaldoor_controller.slips import key_id

import digitalkey.kanri as kanri
from digitalkey.kanri import Refused, Unit, reasons
from kanri_common import ALL3, T0, T1, dt


# ---- 19.11 拒否の理由の言葉 ----

REASONS = {
    "ROLE_NOT_ALLOWED": "その役ではできません",
    "BY_UNKNOWN": "操作する人が名簿にありません",
    "BY_INACTIVE": "操作する人が有効ではありません",
    "NO_ROLE": "その役を持っていません",
    "FORM": "様式に合いません",
    "ALREADY_CREATED": "既に作られています",
    "NO_UNIT": "置き場がありません",
    "NO_KEY": "発行者の鍵がありません",
    "WRONG_KEY": "発行者の鍵が違います",
    "SUBJECT_UNKNOWN": "名簿にありません",
    "SUBJECT_INACTIVE": "主体が有効ではありません",
    "DUPLICATE_SUBJECT": "同じ識別の主体があります",
    "NOTHING_TO_CHANGE": "変える項目がありません",
    "NAME_HAS_COMMA": "名前に , は使えません",
    "UNKNOWN_ROUTE": "知らない経路です",
    "UNKNOWN_DOOR": "この発行者の扉ではありません",
    "UNKNOWN_GROUP": "登録されていない群です",
    "BAD_SETTING": "設定の値が読めません",
    "BAD_PERIOD": "終了は開始より後にしてください",
    "VENDOR_NO_END": "業者と連携先に期限の無い権限は出せません",
    "VENDOR_TOO_LONG": "業者と連携先に出せる期間を超えています",
    "NO_REQUEST": "申請がありません",
    "ALREADY_DECIDED": "この申請は判断済みです",
    "SELF_REQUEST": "自分の申請は承認できません",
    "SELF_SUBJECT": "自分の権限は承認できません",
    "NO_APPROVAL": "承認がありません",
    "REJECTED": "却下された申請です",
    "MISMATCH": "承認と合いません",
    "ALREADY_ISSUED": "発行済みです",
    "REVOKED": "失効した権限です",
    "EXPIRED": "期限が切れています",
    "PERMISSION_UNKNOWN": "権限がありません",
    "NOT_ISSUED": "発行されていない権限です",
    "NO_DOOR_FOR_ROUTE": "この経路を使う扉がありません",
    "ONCE_CREDENTIAL_ONLY": "一回限りは署名つき資格情報だけです",
    "UNTIL_TOO_LATE": "権限の終了より後にはできません",
    "NEED_END": "署名つき資格情報には終了が要ります",
    "NEED_KEY_NUMBER": "鍵の番号が要ります",
    "KEY_LENT": "貸し出し中の鍵です",
    "ENTITY_UNKNOWN": "実体がありません",
    "NOT_PHYSICAL": "物理キーではありません",
    "ALREADY_RETURNED": "回収済みです",
    "NOT_ALL_STOPPED": "止まっていない実体があります",
    "OUT_EXISTS": "出力のファイルが既にあります",
    "GATE_NO_PERMISSION": "権限がありません",
    "GATE_REVOKED": "失効しています",
    "GATE_OUT_OF_PERIOD": "期間の外",
}


@pytest.mark.parametrize("name,word", sorted(REASONS.items()))
def test_reason_words(name, word):
    """19.11 の定数と言葉。"""
    assert getattr(reasons, name) == word
    assert kanri.reasons is reasons


# ---- 19.1、19.2 外に出す名前とデータの型 ----

TYPES = {
    "Subject": ["識別", "呼び名", "種別", "業者", "状態", "仮名", "持つ役"],
    "Door": ["名前", "経路", "場所", "記録の公開鍵", "状態", "失効の最長の分数"],   # 21.2: 失効の最長の分数 を足す
    "Group": ["名前", "扉", "状態"],
    "Permission": ["番号", "主体", "対象の種類", "対象", "開始", "終了", "理由", "出どころ", "申請者", "状態",
                   "承認", "発行", "扉", "失効"],
    # 20.2: Entity と Row に 機器 を最後に足す
    "Entity": ["番号", "権限", "主体", "経路", "扉", "識別", "発行者", "開始", "終了", "一回限り", "失効", "回収", "機器"],
    "Issued": ["伝票", "実体", "資格情報"],
    "Row": ["実体", "経路", "識別", "扉", "状態", "効くまで", "作業", "機器"],
    "Sheet": ["主体", "時刻", "行", "済み"],
    "Revocation": ["権限_失効", "実体_失効", "進み具合", "失効_一覧"],   # 21.2: 失効_一覧 を足す
    "GateVerdict": ["ok", "理由", "実体"],
    "ImportResult": ["申請", "飛ばした"],
    "RouteIssue": ["識別", "発行者", "資格情報"],
    "Stop": ["結果", "効くまで", "作業"],
    "EffectiveBy": ["種類", "時刻", "分数"],   # 21.2: 分数 を足す
    "Reconcile": ["結果", "詳細"],
}


@pytest.mark.parametrize("name", sorted(TYPES))
def test_data_types_are_frozen_dataclasses_with_form_names(name):
    """19.2 のデータの型: dataclass(frozen=True)、属性の名前は表のとおり。`digitalkey.kanri` から出す。"""
    cls = getattr(kanri, name)
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen
    assert {f.name for f in dataclasses.fields(cls)} >= set(TYPES[name])


def test_route_name_constants():
    """19.8 の経路の名の定数を `digitalkey.kanri` から出す。"""
    assert kanri.CREDENTIAL == "署名つき資格情報"
    assert kanri.GATE == "門番を通す錠"
    assert kanri.PHYSICAL == "物理キー"


def test_refused_is_value_error_with_reason_and_detail():
    """19.2 の拒否: Refused(ValueError)、属性 理由 と 詳細、str は 理由(詳細) か 理由。"""
    assert issubclass(Refused, ValueError)


def test_refused_str_with_and_without_detail(make_unit, refused):
    """19.2: str(e) は詳細があれば `理由(詳細)`、無ければ `理由`。"""
    u = make_unit()
    e = refused(u, reasons.UNKNOWN_DOOR, u.register_group, "事務所", ["倉庫"], by="adm", role="管理者")
    assert e.詳細 == "倉庫"
    assert str(e) == "この発行者の扉ではありません(倉庫)"
    e = refused(u, reasons.ROLE_NOT_ALLOWED, u.register_door, "玄関", list(ALL3), by="adm", role="発行者")
    assert str(e) == (f"{e.理由}({e.詳細})" if e.詳細 else e.理由)


# ---- 19.2 動かす単位 ----

def test_create_example(tmp_path):
    """19.2 の例: 置き場を作ると、adm の持つ役は 管理者 と 本人、台帳の伝票は 2 枚。"""
    signer = Ed25519Signer.generate()
    u = Unit.create(tmp_path / "A", admin="adm", admin_name="管理者A", signer=signer)
    assert u.subject("adm").持つ役 == {"管理者", "本人"}
    assert u.ledger.count() == 2
    assert u.issuer == key_id(signer.public_key())
    assert u.public_key == signer.public_key()
    assert u.signer is signer
    assert u.root == tmp_path / "A"
    assert set(u.routes) == {"署名つき資格情報", "門番を通す錠", "物理キー", "管理者の口"}   # 20.2: 組み込みは四つ


def test_create_writes_two_slips_with_init_role(make_unit):
    """19.2: create は 主体_登録 と 役_割当 を、伝票の発行者 admin、役 初期化 で書く。"""
    u = make_unit(admin="adm", admin_name="管理者A")
    reg, = u.ledger.select("主体_登録")
    assert reg.issuer == "adm" and reg.to == "adm" and reg.basis == []
    assert reg["識別"] == "adm" and reg["呼び名"] == "管理者A" and reg["種別"] == "社員"
    assert reg["役"] == "初期化"
    asg, = u.ledger.select("役_割当")
    assert asg.issuer == "adm" and asg.to == "adm"
    assert (asg["主体"], asg["割り当てる役"], asg["判断"], asg["理由"], asg["役"]) == \
        ("adm", "管理者", "付与", "置き場を作った", "初期化")
    s = u.subject("adm")
    assert (s.識別, s.呼び名, s.種別, s.業者, s.状態) == ("adm", "管理者A", "社員", False, "有効")


def test_create_admin_kind(make_unit):
    """19.2: admin_kind を渡せば主体_登録の種別になる。"""
    u = make_unit(admin_kind="住人")
    assert u.subject("adm").種別 == "住人"


def test_kanri_json_with_signer_has_no_key_file(make_unit):
    """19.2: signer を渡せば鍵のファイルは作らず、kanri.json の 鍵のファイル は空。秘密鍵の値は入れない。"""
    signer = Ed25519Signer.generate()
    u = make_unit(signer=signer)
    info = json.loads((u.root / "kanri.json").read_text(encoding="utf-8"))
    assert info == {"形式": "digitalkey-kanri/1", "発行者": key_id(signer.public_key()),
                    "公開鍵": signer.public_key().hex(), "鍵のファイル": ""}
    assert not (u.root / "鍵" / "発行者.key").exists()
    assert (u.root / "台帳").is_dir()


def test_create_without_key_makes_key_file(tmp_path):
    """19.2: signer も issuer_key も無ければ 鍵/発行者.key(32 バイト、権限 600。鍵/ は 700)を作る。"""
    u = Unit.create(tmp_path / "A", admin="adm", admin_name="管理者A")
    keyfile = u.root / "鍵" / "発行者.key"
    data = keyfile.read_bytes()
    assert len(data) == 32
    assert Ed25519Signer(data).public_key() == u.public_key
    assert stat.S_IMODE(os.stat(keyfile).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(u.root / "鍵").st_mode) == 0o700
    text = (u.root / "kanri.json").read_text(encoding="utf-8")
    info = json.loads(text)
    assert data.hex() not in text
    assert info["公開鍵"] == u.public_key.hex()
    # 開き直すと、鍵のファイルから同じ鍵を読む
    v = Unit.open(tmp_path / "A")
    assert v.public_key == u.public_key and v.issuer == u.issuer


def test_create_with_issuer_key_file(tmp_path):
    """19.2: issuer_key のファイルを読み、道を絶対パスで 鍵のファイル に書く。鍵/発行者.key は作らない。"""
    signer = Ed25519Signer.generate()
    keyfile = tmp_path / "keys" / "issuer.key"
    keyfile.parent.mkdir()
    keyfile.write_bytes(signer.private_bytes())
    u = Unit.create(tmp_path / "A", admin="adm", admin_name="管理者A", issuer_key=keyfile)
    assert u.public_key == signer.public_key()
    info = json.loads((u.root / "kanri.json").read_text(encoding="utf-8"))
    assert os.path.isabs(info["鍵のファイル"])
    assert Path(info["鍵のファイル"]).resolve() == keyfile.resolve()
    assert not (u.root / "鍵" / "発行者.key").exists()
    assert Unit.open(tmp_path / "A").issuer == key_id(signer.public_key())


@pytest.mark.parametrize("content", [None, b"\x01" * 31, b"\x01" * 33])
def test_create_with_unreadable_issuer_key(tmp_path, content):
    """19.2: issuer_key が読めない時と 32 バイトでない時は NO_KEY。"""
    keyfile = tmp_path / "issuer.key"
    if content is not None:
        keyfile.write_bytes(content)
    with pytest.raises(Refused) as ei:
        Unit.create(tmp_path / "A", admin="adm", admin_name="管理者A", issuer_key=keyfile)
    assert ei.value.理由 == reasons.NO_KEY


def test_create_twice_is_already_created(make_unit, tmp_path):
    """19.2: kanri.json があれば ALREADY_CREATED で、何も書かない。"""
    u = make_unit("A")
    with pytest.raises(Refused) as ei:
        Unit.create(tmp_path / "A", admin="adm2", admin_name="管理者B", signer=Ed25519Signer.generate())
    assert ei.value.理由 == reasons.ALREADY_CREATED
    assert Unit.open(tmp_path / "A", signer=u.signer).ledger.count() == 2


def test_create_existing_empty_dir_is_fine(tmp_path):
    """19.2: root はあってよい。"""
    (tmp_path / "A").mkdir()
    u = Unit.create(tmp_path / "A", admin="adm", admin_name="管理者A", signer=Ed25519Signer.generate())
    assert u.ledger.count() == 2


def test_open_refusals(make_unit, tmp_path):
    """19.2: open は kanri.json が無ければ NO_UNIT、違う signer は WRONG_KEY、鍵のファイルが空なら NO_KEY。"""
    with pytest.raises(Refused) as ei:
        Unit.open(tmp_path / "none")
    assert ei.value.理由 == reasons.NO_UNIT
    make_unit("A")   # signer で作ったので 鍵のファイル は空
    with pytest.raises(Refused) as ei:
        Unit.open(tmp_path / "A", signer=Ed25519Signer.generate())
    assert ei.value.理由 == reasons.WRONG_KEY
    with pytest.raises(Refused) as ei:
        Unit.open(tmp_path / "A")
    assert ei.value.理由 == reasons.NO_KEY


def test_open_with_unreadable_key_file(tmp_path):
    """19.2: 鍵のファイルが読めなければ NO_KEY。"""
    u = Unit.create(tmp_path / "A", admin="adm", admin_name="管理者A")
    keyfile = u.root / "鍵" / "発行者.key"
    os.chmod(keyfile, 0o600)
    keyfile.unlink()
    with pytest.raises(Refused) as ei:
        Unit.open(tmp_path / "A")
    assert ei.value.理由 == reasons.NO_KEY


def test_reopen_reads_the_same_state(unit, grant, tmp_path):
    """19.1: 状態は台帳の伝票から導く。同じ置き場をもう一度開くと同じ状態が読める。"""
    pid, _, _ = grant(unit)
    at = datetime(2026, 12, 5, 10, 0)
    v = Unit.open(tmp_path / "A", signer=unit.signer)
    assert v.subjects() == unit.subjects()
    assert v.doors() == unit.doors()
    assert v.permissions(at=at) == unit.permissions(at=at)
    assert v.permission(pid, at=at).状態 == "発行済み"
    assert v.trust_entry() == unit.trust_entry()


def test_two_units_have_separate_issuers(make_unit):
    """R-AC-09 19.2: 一つの置き場が一つの発行者の鍵。二つの発行者は置き場を二つ作る。"""
    a, b = make_unit("A"), make_unit("B")
    assert a.issuer != b.issuer
    assert a.root != b.root


# ---- 19.3 役と操作 ----

def _write_calls(u):
    """19.3 の表の書くメソッドを一つずつ呼ぶ(by と role は後で足す)。"""
    return {
        "register_subject": lambda **kw: u.register_subject("x1", "架空", "社員", False, **kw),
        "change_subject": lambda **kw: u.change_subject("s1", reason="直し", name="社員B", **kw),
        "assign_role": lambda **kw: u.assign_role("s1", "運用", reason="当番", **kw),
        "register_door": lambda **kw: u.register_door("倉庫", ["物理キー"], **kw),
        "register_group": lambda **kw: u.register_group("事務所", ["玄関"], **kw),
        "set_setting": lambda **kw: u.set_setting("自己承認", "許す", reason="一人で回す", **kw),
        "request": lambda **kw: u.request("s1", "扉", "玄関", T0, T1, "業務", **kw),
        "import_requests": lambda **kw: u.import_requests([], "人事の届け", **kw),
        "decide": lambda **kw: u.decide("x", "承認", **kw),
        "issue": lambda **kw: u.issue("x", "s1", "扉", "玄関", T0, T1, **kw),
        "issue_entity": lambda **kw: u.issue_entity("x", "門番を通す錠", **kw),
        "revoke_permission": lambda **kw: u.revoke_permission("x", "退職", **kw),
        "revoke_subject": lambda **kw: u.revoke_subject("s1", "退職", **kw),
        "return_physical": lambda **kw: u.return_physical("x", **kw),
        "confirm_stopped": lambda **kw: u.confirm_stopped("s1", **kw),
    }


WRITE_METHODS = ["register_subject", "change_subject", "assign_role", "register_door", "register_group",
                 "set_setting", "request", "import_requests", "decide", "issue", "issue_entity",
                 "revoke_permission", "revoke_subject", "return_physical", "confirm_stopped"]


@pytest.mark.parametrize("method", WRITE_METHODS)
@pytest.mark.parametrize("role", ["発行者", "初期化"])
def test_machine_and_init_roles_are_never_allowed(unit, refused, method, role):
    """19.3: role に 発行者 か 初期化 を渡すと、どの操作も ROLE_NOT_ALLOWED。"""
    refused(unit, reasons.ROLE_NOT_ALLOWED, _write_calls(unit)[method], by="adm", role=role)


# できる役の表(19.3)に無い役。adm が持っている役でも、表に無ければ ROLE_NOT_ALLOWED(1 は 4 より先)
NOT_ALLOWED = {
    "register_subject": "管理者",
    "change_subject": "管理者",
    "assign_role": "名簿の係",
    "register_door": "名簿の係",
    "register_group": "名簿の係",
    "set_setting": "名簿の係",
    "request": "管理者",
    "import_requests": "本人",
    "decide": "管理者",
    "issue": "名簿の係",
    "issue_entity": "名簿の係",       # 経路が門番を通す錠の時は管理者だけ
    "revoke_permission": "承認する人",
    "revoke_subject": "監査",
    "return_physical": "本人",
    "confirm_stopped": "名簿の係",
}


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_role_not_in_table_is_refused_before_anything_else(unit, refused, method):
    """19.3 の 1: role がその操作のできる役でなければ、by が持つ役でも、名簿に無い by でも ROLE_NOT_ALLOWED。"""
    calls = _write_calls(unit)
    refused(unit, reasons.ROLE_NOT_ALLOWED, calls[method], by="adm", role=NOT_ALLOWED[method])
    refused(unit, reasons.ROLE_NOT_ALLOWED, calls[method], by="nobody", role=NOT_ALLOWED[method])


ALLOWED = {
    "register_subject": "名簿の係",
    "change_subject": "名簿の係",
    "assign_role": "管理者",
    "register_door": "管理者",
    "register_group": "管理者",
    "set_setting": "管理者",
    "request": "名簿の係",
    "import_requests": "名簿の係",
    "decide": "承認する人",
    "issue": "管理者",
    "issue_entity": "管理者",
    "revoke_permission": "管理者",
    "revoke_subject": "名簿の係",
    "return_physical": "名簿の係",
    "confirm_stopped": "管理者",
}


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_by_unknown(unit, refused, method):
    """19.3 の 2: by が名簿に無ければ BY_UNKNOWN。"""
    refused(unit, reasons.BY_UNKNOWN, _write_calls(unit)[method], by="nobody", role=ALLOWED[method])


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_by_inactive_before_no_role(unit, refused, method):
    """19.3 の 3: by が有効でなければ、役を持たなくても BY_INACTIVE(3 は 4 より先)。"""
    unit.register_subject("k9", "架空", "社員", False, by="adm", role="名簿の係")
    unit.change_subject("k9", reason="休職", state="停止", by="adm", role="名簿の係")
    refused(unit, reasons.BY_INACTIVE, _write_calls(unit)[method], by="k9", role=ALLOWED[method])


@pytest.mark.parametrize("method", WRITE_METHODS)
def test_no_role(unit, refused, method):
    """19.3 の 4: by が role を持たなければ NO_ROLE(s1 は本人だけを持つ)。"""
    refused(unit, reasons.NO_ROLE, _write_calls(unit)[method], by="s1", role=ALLOWED[method])


def test_no_role_example(make_unit, refused):
    """19.3 の例: 名簿の係を持たない adm が register_subject(..., role="名簿の係") を呼ぶと NO_ROLE。"""
    u = make_unit()
    refused(u, reasons.NO_ROLE, u.register_subject, "s1", "社員A", "社員", False, by="adm", role="名簿の係")


def test_no_role_before_form(unit, refused):
    """19.3: 4(役を持つか)は 5(様式)より先。"""
    refused(unit, reasons.NO_ROLE, unit.register_subject, "x1", "あ" * 101, "社員", False,
            by="s1", role="名簿の係")


def test_form_before_operation_checks(unit, refused):
    """19.3: 5(様式)は 6(操作ごとの確かめ)より先。名簿に無い主体でも、日時が読めなければ FORM。"""
    refused(unit, reasons.FORM, unit.request, "nobody", "扉", "玄関", "あした", T1, "業務",
            by="adm", role="名簿の係")
    refused(unit, reasons.FORM, unit.request, "nobody", "部屋", "玄関", T0, T1, "業務",
            by="adm", role="名簿の係")


def test_held_roles_follow_role_slips(unit):
    """19.3: 持つ役は 役_割当 の 付与 と、有効な主体の 本人。"""
    assert unit.subject("adm").持つ役 == {"管理者", "名簿の係", "本人"}
    assert unit.subject("apr").持つ役 == {"承認する人", "本人"}
    assert unit.subject("s1").持つ役 == {"本人"}


def test_inactive_subject_does_not_hold_honnin(unit):
    """19.3: 本人 は有効な主体だけが持つ。"""
    unit.change_subject("apr", reason="退職", state="退出", by="adm", role="名簿の係")
    assert "本人" not in unit.subject("apr").持つ役


def test_honnin_may_request_for_self_and_guests(unit):
    """19.3、19.7: 本人の役で、自分の分も受け入れる来客の分も書ける。申請者は by。"""
    unit.register_subject("g1", "来客A", "来客", False, by="adm", role="名簿の係")
    own = unit.request("s1", "扉", "玄関", T0, T1, "業務", by="s1", role="本人")
    guest = unit.request("g1", "扉", "玄関", T0, "2026-12-01T18:00", "打ち合わせ", by="s1", role="本人")
    assert own.issuer == "s1" and own["役"] == "本人"
    assert unit.permission(guest.id, at=dt(T0)).申請者 == "s1"


def test_role_field_is_written_on_every_slip(unit, grant):
    """19.3: どの伝票も項目 役 に role を書く。発行者の書く伝票は 操作者 に by。"""
    pid, aid, issued = grant(unit)
    assert unit.ledger.get(pid)["役"] == "名簿の係"
    assert unit.ledger.get(aid)["役"] == "承認する人"
    assert issued.issuer == "発行者" and issued["操作者"] == "adm" and issued["役"] == "管理者"
    door, = unit.ledger.select("扉")
    assert door["役"] == "管理者" and door.issuer == "adm"
