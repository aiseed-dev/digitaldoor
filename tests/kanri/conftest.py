"""鍵の管理ソフト(段 1)の試験の下ごしらえ。

設計書 19.13 の共通の下ごしらえ: `adm`(管理者、名簿の係を兼ねる)、`apr`(承認する人)、`s1`(社員)、
扉 `玄関`(三つの経路)。置き場は tmp_path に作り、発行者の鍵は `Ed25519Signer.generate()` を `signer` で渡す。
疑似の扉は `Controller(SimContacts(), Audit(Ed25519Signer.generate()), name=..., trust=...)`。

段 1b(設計書 20.13)の下ごしらえ `setup_devices` と、置き場 `dunit`、`dfile_unit`、機器の権限と管理者の口を出す `access` も置く。
段 2a(設計書 21.13)の下ごしらえ `setup_revocations` と、置き場 `runit`、`rfile_unit`、署名つき資格情報を出す `cred` も置く。

`digitalkey.kanri` は、ここでは関数の中で import する(まだ無い時も conftest は読める)。
"""
from __future__ import annotations

from collections import Counter

import pytest

from kanri_common import ACCESS, ALL3, CREDENTIAL, ISSUE_AT, T0, T1, dt, unix


def _kanri():
    import digitalkey.kanri as kanri
    return kanri


def setup_base(u) -> None:
    """19.13 の共通の下ごしらえ(adm は作った時の管理者に、名簿の係を足す)。"""
    u.assign_role("adm", "名簿の係", reason="兼務", by="adm", role="管理者")
    u.register_subject("apr", "承認者A", "社員", False, by="adm", role="名簿の係")
    u.assign_role("apr", "承認する人", reason="承認の係", by="adm", role="管理者")
    u.register_subject("s1", "社員A", "社員", False, by="adm", role="名簿の係")
    u.register_door("玄関", list(ALL3), by="adm", role="管理者")


@pytest.fixture
def make_unit(tmp_path):
    """置き場を作る。`make_unit("A")` は tmp_path/A に、発行者の鍵を signer で渡して作る。"""
    from digitaldoor_controller.audit import Ed25519Signer

    def make(name: str = "A", *, admin: str = "adm", admin_name: str = "管理者A", base: bool = False, **kw):
        kw.setdefault("signer", Ed25519Signer.generate())
        u = _kanri().Unit.create(tmp_path / name, admin=admin, admin_name=admin_name, **kw)
        if base:
            setup_base(u)
        return u
    return make


@pytest.fixture
def unit(make_unit):
    """共通の下ごしらえを済ませた置き場。"""
    return make_unit("A", base=True)


@pytest.fixture
def file_unit(tmp_path):
    """命令(19.12)で開ける置き場。signer を渡さずに作り、鍵/発行者.key を置く。共通の下ごしらえ済み。"""
    u = _kanri().Unit.create(tmp_path / "K", admin="adm", admin_name="管理者A")
    setup_base(u)
    return u


def _request(u, subject="s1", kind="扉", target="玄関", start=T0, end=T1, *, by="adm", role="名簿の係"):
    return u.request(subject, kind, target, start, end, "業務", by=by, role=role)


def _approve(u, request_id, *, by="apr", role="承認する人"):
    return u.decide(request_id, "承認", "確かめた", by=by, role=role)


def _grant(u, subject="s1", kind="扉", target="玄関", start=T0, end=T1, *, at=None):
    """申請(adm、名簿の係)、承認(apr)、発行(adm、管理者)。(権限の番号, 承認の番号, 権限_発行の伝票) を返す。"""
    r = _request(u, subject, kind, target, start, end)
    a = _approve(u, r.id)
    i = u.issue(a.id, subject, kind, target, start, end, by="adm", role="管理者",
                at=at if at is not None else dt("2026-11-20T00:00"))
    return r.id, a.id, i


@pytest.fixture
def request_():
    return _request


@pytest.fixture
def approve():
    return _approve


@pytest.fixture
def grant():
    return _grant


def _refused(u, why: str, fn, /, *args, detail: str | None = None, **kw):
    """fn が Refused(why) を投げ、台帳の伝票の数が変わらないことを確かめる(19.2 の「拒否」)。
    fn の引数の reason= などと重ならないよう、前の三つは位置だけで受ける。"""
    Refused = _kanri().Refused
    before = u.ledger.count()
    with pytest.raises(Refused) as ei:
        fn(*args, **kw)
    assert ei.value.理由 == why, f"{ei.value.理由!r} != {why!r}({ei.value.詳細!r})"
    if detail is not None:
        assert ei.value.詳細 == detail
    assert u.ledger.count() == before, "拒んだ時に台帳の伝票が増えた"
    return ei.value


@pytest.fixture
def refused():
    return _refused


def _forms(u) -> Counter:
    """様式ごとの伝票の数。"""
    return Counter(s.form for s in u.ledger.select())


@pytest.fixture
def forms():
    return _forms


def _door(u, name: str = "玄関", at: str | None = None, trust: dict | None = None, config=None):
    """19.13 の疑似の扉。at を渡せば、扉の時計をその地方時の unix 秒にして同期済みにする。
    config は扉の Config(21.13。省けば Config() で、失効の知らせの古さの規則を使わない)。"""
    from digitaldoor_controller.audit import Audit, Ed25519Signer
    from digitaldoor_controller.contacts import SimContacts
    from digitaldoor_controller.controller import Config, Controller

    ctl = Controller(SimContacts(), Audit(Ed25519Signer.generate()), config if config is not None else Config(),
                     name=name, trust=u.trust_entry() if trust is None else trust)
    if at is not None:
        ctl.set_time(unix(at), synced=True)
    return ctl


@pytest.fixture
def door():
    return _door


def last_auth(ctl):
    """扉の記録のうち、最後の種別 認証 の記録。"""
    return [e for e in ctl.audit.entries if e.kind == "認証"][-1]


@pytest.fixture
def auth_entry():
    return last_auth


# ---- 段 1b(設計書 20 章)の下ごしらえ ----

def setup_devices(u) -> None:
    """20.13 の共通の下ごしらえ(19.13 の物に足す)。ops(運用)、t1 と t2(社員)、v1(業者 はい)。
    区画 扉、事務、来客。機器 ルーター1(ルーター、使用)、玄関の錠(錠、使用、扉の系統 はい、扉 玄関、区画 扉)、
    複合機1(プリンター、使用)。機器の責任者は、テストが change_device で決める。"""
    kw = dict(by="adm", role="名簿の係")
    u.register_subject("ops", "運用A", "社員", False, **kw)
    u.assign_role("ops", "運用", reason="当番", by="adm", role="管理者")
    u.register_subject("t1", "社員T1", "社員", False, **kw)
    u.register_subject("t2", "社員T2", "社員", False, **kw)
    u.register_subject("v1", "保守会社A", "業者", True, **kw)
    adm = dict(by="adm", role="管理者")
    u.register_segment("扉", note="錠、扉のコントローラ、小さなサーバー", **adm)
    u.register_segment("事務", note="事務の PC と複合機", **adm)
    u.register_segment("来客", note="来客の無線", **adm)
    u.register_device("ルーター1", "ルーター", "使用", **adm)
    u.register_device("玄関の錠", "錠", "使用", door_system=True, door="玄関", segment="扉", **adm)
    u.register_device("複合機1", "プリンター", "使用", **adm)


@pytest.fixture
def dunit(unit):
    """20.13 の共通の下ごしらえを済ませた置き場。"""
    setup_devices(unit)
    return unit


@pytest.fixture
def dfile_unit(file_unit):
    """命令で開ける置き場に、20.13 の共通の下ごしらえを足した物。"""
    setup_devices(file_unit)
    return file_unit


def _access(u, subject="t1", device="ルーター1", account="admin", *, start=T0, end=T1):
    """機器の権限を申請、承認、発行し、管理者の口の実体を出す。(権限の番号, Entity) を返す。"""
    pid, _, _ = _grant(u, subject, "機器", device, start, end)
    got = u.issue_entity(pid, ACCESS, account=account, by="adm", role="管理者", at=dt(ISSUE_AT))
    return pid, got.実体


@pytest.fixture
def access():
    return _access


# ---- 段 2a(設計書 21 章)の下ごしらえ ----

def setup_revocations(u) -> None:
    """21.13 の共通の下ごしらえ(19.13 の物に足す): ops(運用)、s2(社員)。扉 玄関 は三つの経路で、最長の分数は空。
    最長の分数を持つ扉は、テストが register_door(..., revocation_max_min=M) で足す。"""
    u.register_subject("ops", "運用A", "社員", False, by="adm", role="名簿の係")
    u.assign_role("ops", "運用", reason="当番", by="adm", role="管理者")
    u.register_subject("s2", "社員B", "社員", False, by="adm", role="名簿の係")


@pytest.fixture
def runit(unit):
    """21.13 の共通の下ごしらえを済ませた置き場。"""
    setup_revocations(unit)
    return unit


@pytest.fixture
def rfile_unit(file_unit):
    """命令で開ける置き場に、21.13 の共通の下ごしらえを足した物。"""
    setup_revocations(file_unit)
    return file_unit


def _cred(u, subject="s1", target="玄関", kind="扉", start=T0, end=T1, *, until=None):
    """権限を申請、承認、発行し、署名つき資格情報の実体を出す。(権限の番号, Issued) を返す。"""
    pid, _, _ = _grant(u, subject, kind, target, start, end)
    got = u.issue_entity(pid, CREDENTIAL, until=until, by="adm", role="管理者", at=dt(ISSUE_AT))
    return pid, got


@pytest.fixture
def cred():
    return _cred
