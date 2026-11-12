"""設計書 21.13: 段 2a の要求ごとの確かめ方(R-AC-22、R-AC-14、R-AC-15、R-AC-19)。

疑似の扉は Controller(SimContacts(), Audit(Ed25519Signer.generate()), Config(...), name=..., trust=u.trust_entry())。
`ctl.present(credential, revocation=<一覧>)` は一覧を先に受けてから資格情報を確かめる。一覧だけを渡すのは
`ctl.accept_revocation(<一覧>)`。一覧の拒否の理由は増えた記録(種別 設定、事象 失効の一覧、結果 拒否)の detail、
資格情報の拒否の理由は `ctl.audit.entries[-1].result`(扉コントローラ仕様書 5.2c)。

段 2a の実装より先に書いた。まだ無い名前は関数の中で引く。時刻は固定の値で、扉の時計も `ctl.set_time` で決める。
"""
from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass

import pytest
from digitaldoor_controller.controller import Config

from digitalkey.kanri import Unit, cli as kanri_cli, reasons
from kanri_common import (CREDENTIAL, GATE, ISSUE_AT, PHYSICAL, R2A, T0, T1, at_minute_up, dt, plus, unix)

AT = dt(ISSUE_AT)
PUB = "2026-12-10T09:00"      # 失効より前の一覧(t1)
REV = "2026-12-10T12:00"      # 失効(t0)
CHK = "2026-12-15T10:00"      # 期限の前の、扉で確かめる時刻
ADM = dict(by="adm", role="管理者")
OPS = dict(by="ops", role="運用")


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def json(self):
        return json.loads(self.out)

    @property
    def lines(self):
        return self.out.splitlines()


@pytest.fixture
def kanri(capsys):
    def run(*argv) -> Result:
        try:
            code = kanri_cli.main([str(a) for a in argv])
        except SystemExit as e:
            code = e.code
        out, err = capsys.readouterr()
        return Result(0 if code is None else code, out, err)
    return run


def _rev_entry(ctl):
    """扉の記録の最後の一枚(種別 設定、事象 失効の一覧)。"""
    e = ctl.audit.entries[-1]
    assert (e.kind, e.event) == ("設定", "失効の一覧")
    return e


def _state(u, subject, entity_id, at):
    r = {r.実体: r for r in u.progress(subject, at=dt(at)).行}[entity_id]
    return (r.状態, r.効くまで)


# ---- R-AC-22 ----

def test_r_ac_22_offline_door_uses_carried_list(runit, make_unit, cred, door, refused):
    """R-AC-22 ネットワークの届かない扉が、端末の運んだ署名つきの失効の一覧を使う。古い一覧で戻せない(21.13 の 1〜8)。"""
    u = runit
    # 1. s1 と s2 に 玄関 の署名つき資格情報。扉は Config()(最長の時間を使わない)
    _, g1 = cred(u, "s1")
    _, g2 = cred(u, "s2")
    cred_s1, cred_s2 = g1.資格情報, g2.資格情報
    # 2. 失効の前に一度、世代 1(識別は空)
    pub = u.publish_revocations(**ADM, at=dt(PUB))
    assert (pub.世代, json.loads(pub.一覧["body"])["revoked"]) == (1, [])
    # 3. revoke_subject("s1") の失効_一覧は世代 2 で、識別は s1 の資格情報だけ
    rev = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert rev.失効_一覧.form == "失効_一覧" and rev.失効_一覧["世代"] == "2"
    assert u.revocations()[-1].識別 == [g1.実体.識別]
    # 4. 一覧を渡さない扉では、s1 の資格情報はまだ 解錠(段 1 の限界が残る)
    offline = door(u, "玄関", at=CHK, config=Config())
    assert offline.present(cred_s1) == "解錠"
    # 5. s2 の端末が一覧を運ぶ。その後、s1 は 拒否(失効済み、期限の前)
    ctl = door(u, "玄関", at=CHK, config=Config())
    assert ctl.present(cred_s2, revocation=u.revocation()) == "解錠"
    assert ctl.present(cred_s1) == "拒否"
    assert ctl.audit.entries[-1].result == "失効済み"
    # 6. 一つ前の世代の一覧で戻せない
    assert ctl.accept_revocation(u.revocation(1)) == "拒否"
    assert (_rev_entry(ctl).result, _rev_entry(ctl).detail) == ("拒否", "古い世代")
    assert ctl.present(cred_s1) == "拒否"
    assert ctl.audit.entries[-1].result == "失効済み"
    # 7. 別の置き場 B の発行者が作った一覧は 知らない発行者
    b = make_unit("B", base=True)
    refused(b, R2A.NO_REVOCATION_LIST, b.revocation, detail="")
    other = b.publish_revocations(**ADM, at=dt(REV))
    assert ctl.accept_revocation(other.一覧) == "拒否"
    assert (_rev_entry(ctl).result, _rev_entry(ctl).detail) == ("拒否", "知らない発行者")
    # 8. 組み立て直しで同じ署名。無い世代は UNKNOWN_GENERATION
    assert u.revocation() == u.revocation(2)
    assert u.revocation(1) == pub.一覧
    assert Unit.open(u.root, signer=u.signer).revocation(2) == u.revocation()
    refused(u, R2A.UNKNOWN_GENERATION, u.revocation, 3, detail="3")


# ---- R-AC-14 ----

M = 60                         # この試験の扉の最長の分数(テストが選ぶ)


def test_r_ac_14_door_minutes_shown_in_door_list(rfile_unit, kanri, refused):
    """R-AC-14 扉ごとに最長の時間を決めて表示する(21.13 の 1): door add --revocation-max-min M の扉は door list の末尾に M、
    --json に 失効の最長の分数 M。省いた扉は空の欄と null。0 は FORM。二枚目で省くと None に戻る。"""
    u = rfile_unit
    d = u.root
    res = kanri("door", "add", "--dir", d, "--by", "adm", "--role", "管理者", "--name", "通用口",
                "--routes", CREDENTIAL, "--revocation-max-min", M)
    assert res.code == 0, res.err
    assert res.lines[0].startswith("扉 ")
    res = kanri("door", "list", "--dir", d)
    assert res.code == 0
    rows = {line.split("\t")[0]: line.split("\t") for line in res.lines}
    assert rows["通用口"][-1] == str(M) and rows["玄関"][-1] == ""
    items = {i["名前"]: i for i in kanri("door", "list", "--dir", d, "--json").json["items"]}
    assert (items["通用口"]["失効の最長の分数"], items["玄関"]["失効の最長の分数"]) == (M, None)
    refused(u, reasons.FORM, u.register_door, "裏口", [CREDENTIAL], revocation_max_min=0, **ADM)
    u.register_door("通用口", [CREDENTIAL], **ADM)
    assert {x.名前: x for x in Unit.open(d).doors()}["通用口"].失効の最長の分数 is None


def test_r_ac_14_stale_door_refuses_long_credential(rfile_unit, kanri, grant, door, tmp_path):
    """R-AC-14 通知が古い扉は長い期限の資格情報を拒む。台帳の見立ての時刻に、扉は本当に拒む(21.13 の 2〜8)。

    限界(21.13 の 8): この見立ては、扉の設定(revocation_max_age_s)が台帳の 失効の最長の分数 × 60 と同じである前提の物。
    扉の設定と台帳の値の照らし合わせは段 3 で足す。扉の時計が合っている事も前提。"""
    u = rfile_unit
    d = u.root
    u.register_door("通用口", [CREDENTIAL], revocation_max_min=M, **ADM)
    # 2. 扉は Config(revocation_max_age_s=M*60)。失効より前の t1 の一覧を一度受ける
    u.publish_revocations(**ADM, at=dt(PUB))
    ctl = door(u, "通用口", at=PUB, config=Config(revocation_max_age_s=M * 60))
    assert ctl.accept_revocation(u.revocation()) == "受理"
    # 3. 長い資格情報(s1、T0〜T1)と、ちょうど M 分の資格情報(s2)。entity issue --json の 効くまで
    s2_start, s2_end = "2026-12-10T12:30", "2026-12-10T13:30"
    p_long, _, _ = grant(u, "s1", target="通用口")
    p_m, _, _ = grant(u, "s2", target="通用口", start=s2_start, end=s2_end)
    p_plain, _, _ = grant(u, "s2", target="玄関")

    def issue(pid, name):
        out = tmp_path / f"{name}.json"
        res = kanri("entity", "issue", "--dir", d, "--by", "adm", "--role", "管理者", "--permission", pid,
                    "--route", CREDENTIAL, "--at", ISSUE_AT, "--out", out, "--json")
        assert res.code == 0, res.err
        return res.json, json.loads(out.read_text(encoding="utf-8"))

    j_long, cred_long = issue(p_long, "long")
    j_m, cred_m = issue(p_m, "m")
    j_plain, _ = issue(p_plain, "plain")
    assert j_long["効くまで"] == {"種類": "最長の時間まで", "時刻": "", "分数": M}
    assert j_m["効くまで"] == {"種類": "期限まで", "時刻": s2_end, "分数": None}
    # 4. t0 に長い資格情報の主体、その 1 分後にもう一人を止める。扉には新しい一覧を渡さない
    u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    u.revoke_subject("s2", "退職", **ADM, at=dt(plus(REV, minutes=1)))
    # 5. 長い資格情報の効くまでは t0 + M 分。その 1 分前は 待ち、その時刻は 止まった。扉はその時刻に 失効の通知が古い で拒む
    when = at_minute_up(unix(REV) + M * 60)
    assert when == "2026-12-10T13:00"
    assert _state(u, "s1", j_long["番号"], plus(when, minutes=-1)) == ("待ち", when)
    assert _state(u, "s1", j_long["番号"], when) == ("止まった", "")
    ctl.set_time(unix(when), synced=True)
    assert ctl.present(cred_long) == "拒否"
    assert ctl.audit.entries[-1].result == "失効の通知が古い"
    # 6. 長さが M 分の資格情報の効くまでは終了。扉は終了の 1 秒前に 解錠、終了で 期限の外
    assert _state(u, "s2", j_m["番号"], plus(REV, minutes=1)) == ("待ち", s2_end)
    ctl.set_time(unix(s2_end) - 1, synced=True)
    assert ctl.present(cred_m) == "解錠"
    ctl.set_time(unix(s2_end), synced=True)
    assert ctl.present(cred_m) == "拒否"
    assert ctl.audit.entries[-1].result == "期限の外"
    # 7. 最長の分数が空の扉(玄関)の資格情報は、効くまでが終了で、effective_by は 期限まで
    assert j_plain["効くまで"] == {"種類": "期限まで", "時刻": T1, "分数": None}
    assert _state(u, "s2", j_plain["番号"], plus(REV, minutes=1)) == ("待ち", T1)


def test_r_ac_14_issued_at_increases_and_door_agrees(runit, cred, door):
    """R-AC-14 発行時刻は世代の順に必ず増える(21.13 の 9)。同じ at の二度目は + 1 秒、古い at でも前 + 1 で拒まない。
    同じ at で二人を続けて止めると、二人目の進み具合は二枚目の発行時刻(+1 秒)から読み、扉が一枚目の一覧だけを持つ時、
    二人目の 効くまで の時刻に、扉は二人目の長い資格情報を拒む。

    限界: 扉の設定が台帳の最長の分数と同じ前提(照らし合わせは段 3)。"""
    u = runit
    mins = 1440
    u.register_door("通用口", [CREDENTIAL], revocation_max_min=mins, **ADM)
    _, a = cred(u, "s1", "通用口")
    _, b = cred(u, "s2", "通用口")
    p1 = u.publish_revocations(**ADM, at=dt(PUB))
    p2 = u.publish_revocations(**ADM, at=dt(PUB))
    p3 = u.publish_revocations(**ADM, at=dt(plus(PUB, days=-1)))
    assert [json.loads(p.一覧["body"])["issued_at"] for p in (p1, p2, p3)] == [unix(PUB), unix(PUB) + 1, unix(PUB) + 2]
    r1 = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    r2 = u.revoke_subject("s2", "退職", **ADM, at=dt(REV))
    g_first, g_second = int(r1.失効_一覧["世代"]), int(r2.失効_一覧["世代"])
    assert (g_first, g_second) == (4, 5)
    assert [r.発行時刻 for r in u.revocations()][-2:] == [unix(REV), unix(REV) + 1]
    when = at_minute_up(unix(REV) + 1 + mins * 60)
    assert when == "2026-12-11T12:01"
    assert _state(u, "s2", b.実体.番号, plus(when, minutes=-1)) == ("待ち", when)
    assert _state(u, "s2", b.実体.番号, when) == ("止まった", "")
    # 扉は一枚目の失効の一覧(世代 4)だけを持つ
    ctl = door(u, "通用口", at=REV, config=Config(revocation_max_age_s=mins * 60))
    assert ctl.accept_revocation(u.revocation(g_first)) == "受理"
    ctl.set_time(unix(when), synced=True)
    assert ctl.present(b.資格情報) == "拒否"
    assert ctl.audit.entries[-1].result == "失効の通知が古い"
    assert ctl.present(a.資格情報) == "拒否"
    assert ctl.audit.entries[-1].result == "失効済み"


# ---- R-AC-15 ----

def test_r_ac_15_one_operation_includes_revocation_list(runit, grant, door, forms, refused):
    """R-AC-15 一人の全経路を一度の操作で失効できる。署名つき資格情報も同じ一度の操作で一覧に入る(21.13 の 1〜6)。"""
    u = runit
    kw = dict(by="adm", role="管理者", at=AT)
    p1, _, _ = grant(u)
    got1 = u.issue_entity(p1, CREDENTIAL, **kw)
    u.issue_entity(p1, GATE, **kw)
    e3 = u.issue_entity(p1, PHYSICAL, key_number="K-7", **kw).実体
    cred_s1, e1 = got1.資格情報, got1.実体
    # 1. 一度の revoke_subject で 失効_一覧 が 1 枚。識別は E1 の識別
    before = forms(u)
    rev = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert forms(u) - before == {"権限_失効": 1, "実体_失効": 3, "失効_一覧": 1}
    assert rev.失効_一覧["識別"] == e1.識別
    gen = int(rev.失効_一覧["世代"])
    # 2. 一覧を扉に渡すと、期限の前に E1 は 拒否(失効済み)
    ctl = door(u, "玄関", at=CHK)
    assert ctl.accept_revocation(u.revocation()) == "受理"
    assert ctl.present(cred_s1) == "拒否"
    assert ctl.audit.entries[-1].result == "失効済み"
    # 3. 届いた記録の前は 違い(扉に届いていない: 玄関)、後は 一致
    r = u.reconcile(e1.番号, at=dt(REV))
    assert (r.結果, r.詳細) == ("違い", "扉に届いていない: 玄関")
    u.record_delivery("玄関", gen, note="受付の端末で運んだ", **OPS, at=CHK)
    r = u.reconcile(e1.番号, at=dt(REV))
    assert (r.結果, r.詳細) == ("一致", "")
    # 4. 回収の後、期限の前でも 済み。confirm_stopped が失効_確認を書く
    u.return_physical(e3.番号, "回収", by="adm", role="名簿の係")
    assert u.progress("s1", at=dt(CHK)).済み is True
    assert u.confirm_stopped("s1", **ADM, at=dt(CHK)).form == "失効_確認"
    # 5. 二度目は 失効_一覧 も増やさない。門番を通す錠だけの主体も一覧を作らない
    n = u.ledger.count()
    again = u.revoke_subject("s1", "退職", **ADM, at=dt(REV))
    assert (again.失効_一覧, u.ledger.count()) == (None, n)
    p2, _, _ = grant(u, "s2")
    u.issue_entity(p2, GATE, **kw)
    before = forms(u)
    assert u.revoke_subject("s2", "退職", **ADM, at=dt(REV)).失効_一覧 is None
    assert forms(u)["失効_一覧"] == before["失効_一覧"]
    # 6. 撤去した扉、台帳に無い扉は UNKNOWN_DOOR。無い世代は UNKNOWN_GENERATION。名簿の係は ROLE_NOT_ALLOWED
    u.register_door("裏口", [GATE], **ADM)
    u.register_door("裏口", [GATE], state="撤去", **ADM)
    refused(u, reasons.UNKNOWN_DOOR, u.record_delivery, "裏口", gen, **OPS, at=CHK, detail="裏口")
    refused(u, reasons.UNKNOWN_DOOR, u.record_delivery, "倉庫", gen, **OPS, at=CHK, detail="倉庫")
    refused(u, R2A.UNKNOWN_GENERATION, u.record_delivery, "玄関", 9, **OPS, at=CHK, detail="9")
    refused(u, reasons.ROLE_NOT_ALLOWED, u.record_delivery, "玄関", gen, by="adm", role="名簿の係", at=CHK)


# ---- R-AC-19 ----

_WATCH: dict = {"paths": None}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _audit_hook(event, args):
    paths = _WATCH["paths"]
    if paths is None:
        return
    targets = []
    if event == "open":
        path, mode, flags = args
        writing = (isinstance(flags, int) and flags & _WRITE_FLAGS) or \
            (isinstance(mode, str) and any(c in mode for c in "wax+"))
        if writing:
            targets.append(path)
    elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate", "os.utime", "sqlite3.connect"):
        targets.append(args[0])
    elif event in ("os.rename", "os.replace", "os.symlink", "os.link"):
        targets.extend(args[:2])
    for t in targets:
        if isinstance(t, int) or t is None:
            continue
        p = os.path.abspath(os.fsdecode(t))
        if "__pycache__" in p or p.endswith(".pyc") or p.startswith("/dev/") or p == ":memory:":
            continue
        paths.append(p)


if not getattr(sys, "_kanri_test_hook_2a", False):
    sys.addaudithook(_audit_hook)
    sys._kanri_test_hook_2a = True


def test_r_ac_19_offline_revocation_list(kanri, tmp_path, monkeypatch):
    """R-AC-19 ネットワークから切り離して動かせる(21.13): 19.13 の筋書きのネットワークを塞いだまま、revoke --out、revocation publish --out、
    revocation show、revocation list、revocation delivered を呼ぶ。全部 0。--out に {"body", "sig"}。
    書くのは --dir の下と --out だけ。既にある --out は OUT_EXISTS で、台帳の伝票の数は変わらない。"""
    real_socket = socket.socket

    class NoNetwork(real_socket):
        def __init__(self, *a, **kw):
            raise OSError("ネットワークは使わない")

    def no_connection(*a, **kw):
        raise OSError("ネットワークは使わない")

    monkeypatch.setattr(socket, "socket", NoNetwork)
    monkeypatch.setattr(socket, "create_connection", no_connection)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    d = tmp_path / "kanri"
    cred_out = tmp_path / "s1-cred.json"
    rev_out = tmp_path / "rev.json"
    pub_out = tmp_path / "rev2.json"
    W = ("--by", "adm", "--role")

    _WATCH["paths"] = []
    try:
        codes = []

        def step(*argv, as_json=True):
            res = kanri(*argv, "--dir", d, *(("--json",) if as_json else ()))
            codes.append((argv[:2], res.code, res.err))
            return res

        step("init", "--admin", "adm", "--admin-name", "管理者A")
        step("role", "grant", *W, "管理者", "--subject", "adm", "--name", "名簿の係", "--reason", "兼務")
        step("subject", "add", *W, "名簿の係", "--id", "apr", "--name", "承認者A", "--kind", "社員")
        step("subject", "add", *W, "名簿の係", "--id", "s1", "--name", "社員A", "--kind", "社員")
        step("role", "grant", *W, "管理者", "--subject", "apr", "--name", "承認する人", "--reason", "承認の係")
        step("door", "add", *W, "管理者", "--name", "玄関", "--routes", "署名つき資格情報,門番を通す錠")
        pid = step("request", *W, "名簿の係", "--subject", "s1", "--door", "玄関", "--from", T0, "--until", T1,
                   "--reason", "業務").json["番号"]
        aid = step("decide", "--by", "apr", "--role", "承認する人", "--request", pid, "--approve").json["番号"]
        step("issue", *W, "管理者", "--approval", aid, "--at", ISSUE_AT, "--subject", "s1", "--door", "玄関",
             "--from", T0, "--until", T1)
        step("entity", "issue", *W, "管理者", "--permission", pid, "--at", ISSUE_AT, "--route", CREDENTIAL,
             "--out", cred_out)
        step("entity", "issue", *W, "管理者", "--permission", pid, "--at", ISSUE_AT, "--route", GATE)
        revoked = step("revoke", *W, "管理者", "--subject", "s1", "--reason", "退職", "--at", REV, "--out", rev_out,
                       as_json=False)
        progress = step("progress", "--subject", "s1", "--at", REV).json
        published = step("revocation", "publish", *W, "管理者", "--at", plus(REV, minutes=10), "--out", pub_out).json
        shown = step("revocation", "show", as_json=False)
        listed = step("revocation", "list").json
        delivered = step("revocation", "delivered", *W, "管理者", "--door", "玄関", "--generation", 1).json
        n = Unit.open(d).ledger.count()
        again = kanri("revocation", "publish", "--dir", d, *W, "管理者", "--out", pub_out)
        verify = step("verify").json
        written = list(_WATCH["paths"])
    finally:
        _WATCH["paths"] = None

    assert [c for c in codes if c[1] != 0] == [], codes
    # revoke の出力に 失効_一覧 <番号> の行が増える(実体_失効の行の後、進み具合の前)
    forms_out = [line.split(" ")[0] for line in revoked.lines[:4]]
    assert forms_out == ["権限_失効", "実体_失効", "実体_失効", "失効_一覧"]
    assert revoked.lines[4] == f"s1 {REV} 未了"
    rows = {r["経路"]: r for r in progress["実体"]}
    assert (rows[GATE]["状態"], rows[CREDENTIAL]["状態"]) == ("止まった", "待ち")
    for f in (rev_out, pub_out):
        assert set(json.loads(f.read_text(encoding="utf-8"))) == {"body", "sig"}
    assert json.loads(rev_out.read_text(encoding="utf-8")) == Unit.open(d).revocation(1)
    assert published["世代"] == 2
    assert json.loads(shown.out) == Unit.open(d).revocation()
    assert [i["世代"] for i in listed["items"]] == [1, 2]
    assert delivered["様式"] == "失効_配布"
    assert (again.code, again.err.startswith("拒否: 出力のファイルが既にあります")) == (1, True)
    assert Unit.open(d).ledger.count() == n
    assert verify["ok"] is True

    root = os.path.realpath(d)
    allowed = {os.path.realpath(p) for p in (cred_out, rev_out, pub_out)}
    outside = [p for p in written
               if not (os.path.realpath(p) == root or os.path.realpath(p).startswith(root + os.sep)
                       or os.path.realpath(p) in allowed)]
    assert outside == []
    assert written, "書いた道が一つも見えない(監視が効いていない)"
    assert sorted(x.name for x in tmp_path.iterdir()) == sorted(["cwd", "kanri", "s1-cred.json", "rev.json", "rev2.json"])
    assert list(cwd.iterdir()) == []
