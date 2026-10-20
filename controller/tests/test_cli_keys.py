"""鍵と資格情報を作る命令(keygen、issue、trust-change。扉コントローラ仕様書 第9章)の試験。

命令が出した JSON を、扉の側の `Controller.present` / `Controller.change_trust` に渡して確かめる。
作った物は 5.2a の形そのままなので、扉は発行者の識別と署名と期間を自分で確かめる。主体は仮名("resident-101")。
"""
import json
import os
import stat

from digitaldoor_controller import credentials
from digitaldoor_controller.audit import Audit, Ed25519Signer, HmacSigner
from digitaldoor_controller.cli import main
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import TRUST_EVENT, Config, Controller
from digitaldoor_controller.slips import key_id

DOOR = "door-1"
NOW = 1_800_000_000


def run(capsys, *argv):
    rc = main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def keygen(capsys, path):
    rc, out, _ = run(capsys, "keygen", "--out", str(path))
    assert rc == 0
    return json.loads(out)


def door(*, trust=None, admin_key=None, now=NOW):
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=0), name=DOOR,
                     admin_key=admin_key, trust=trust)
    ctl.set_time(now, True)
    return ctl


def test_keygen_writes_private_key_with_mode_600(capsys, tmp_path):
    """R-AC-12: keygen は鍵の対を作り、秘密鍵を権限 600 のファイルに書き、公開鍵と発行者の識別を出す(信頼の一覧に入れる値)。"""
    path = tmp_path / "issuer.key"
    info = keygen(capsys, path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    signer = Ed25519Signer(path.read_bytes())
    assert info["public_key"] == signer.public_key().hex()
    assert info["issuer"] == key_id(signer.public_key())
    assert len(info["issuer"]) == 16


def test_keygen_never_overwrites(capsys, tmp_path):
    """R-AC-12: 既にある鍵のファイルには書かない(秘密鍵を取り違えて失わない)。"""
    path = tmp_path / "issuer.key"
    keygen(capsys, path)
    before = path.read_bytes()
    rc, out, err = run(capsys, "keygen", "--out", str(path))
    assert rc == 1 and out == "" and "上書きしません" in err
    assert path.read_bytes() == before


def test_issued_credential_is_checked_by_period(capsys, tmp_path):
    """R-AC-02: issue の資格情報を、扉が期間で確かめる。期間の中は開き、外は「期限の外」で拒む。"""
    issuer = keygen(capsys, tmp_path / "issuer.key")
    rc, out, _ = run(capsys, "issue", "--key", str(tmp_path / "issuer.key"), "--id", "c-1", "--subject", "resident-101",
                     "--doors", f"{DOOR},door-2", "--from", str(NOW - 100), "--until", str(NOW + 1000))
    assert rc == 0
    cred = json.loads(out)
    assert set(cred) == {"body", "sig"}
    body = json.loads(cred["body"])
    assert body["format"] == "digitaldoor-credential/1" and body["issuer"] == issuer["issuer"]
    assert body["doors"] == [DOOR, "door-2"] and body["once"] is False
    trust = {issuer["issuer"]: {"public_key": issuer["public_key"], "doors": [DOOR]}}
    ctl = door(trust=trust)
    assert ctl.present(cred) == "解錠"
    late = door(trust=trust, now=NOW + 1001)
    assert late.present(cred) == "拒否"
    assert [e.result for e in late.audit.entries if e.kind == "認証"] == ["期限の外"]


def test_issue_accepts_iso_time_and_once(capsys, tmp_path):
    """R-AC-02: --from と --until は ISO 8601 でも書け、時差つきなら unix 秒に直る。--once は一回限りの印になる。"""
    keygen(capsys, tmp_path / "issuer.key")
    rc, out, _ = run(capsys, "issue", "--key", str(tmp_path / "issuer.key"), "--id", "c-2", "--subject", "resident-101",
                     "--doors", DOOR, "--from", "2026-10-21T09:00:00+09:00", "--until", "2026-10-21T12:00:00+09:00",
                     "--once")
    assert rc == 0
    body = json.loads(json.loads(out)["body"])
    assert body["not_before"] == 1792540800 and body["not_after"] == 1792540800 + 3 * 3600
    assert body["once"] is True


def test_issue_signed_by_unknown_key_is_refused(capsys, tmp_path):
    """R-AC-09: 信頼の一覧に無い鍵で出した資格情報は、扉が「知らない発行者」で拒む。"""
    trusted = keygen(capsys, tmp_path / "a.key")
    keygen(capsys, tmp_path / "b.key")
    rc, out, _ = run(capsys, "issue", "--key", str(tmp_path / "b.key"), "--id", "c-3", "--subject", "resident-101",
                     "--doors", DOOR, "--from", str(NOW - 1), "--until", str(NOW + 1))
    assert rc == 0
    ctl = door(trust={trusted["issuer"]: {"public_key": trusted["public_key"], "doors": [DOOR]}})
    assert ctl.present(json.loads(out)) == "拒否"
    assert [e.result for e in ctl.audit.entries if e.kind == "認証"] == ["知らない発行者"]


def test_issue_bad_input_is_a_short_message(capsys, tmp_path):
    """R-AC-02: 読めない時刻や空の扉の名前は、例外ではなく短い文で止まり、資格情報を出さない。"""
    keygen(capsys, tmp_path / "issuer.key")
    base = ["issue", "--key", str(tmp_path / "issuer.key"), "--id", "c", "--subject", "s"]
    rc, out, err = run(capsys, *base, "--doors", DOOR, "--from", "あした", "--until", "1")
    assert rc == 1 and out == "" and "時刻を読めません" in err and "Traceback" not in err
    rc, out, err = run(capsys, *base, "--doors", " , ", "--from", "1", "--until", "2")
    assert rc == 1 and out == "" and "扉の名前" in err


def test_issue_rejects_a_key_file_of_the_wrong_shape(capsys, tmp_path):
    """R-AC-11: 秘密鍵でないファイルでは署名しない。"""
    (tmp_path / "bad.key").write_bytes(b"short")
    rc, out, err = run(capsys, "issue", "--key", str(tmp_path / "bad.key"), "--id", "c", "--subject", "s",
                       "--doors", DOOR, "--from", "1", "--until", "2")
    assert rc == 1 and out == "" and "32 バイト" in err


def test_trust_change_is_accepted_only_from_the_admin_key(capsys, tmp_path):
    """R-AC-12: trust-change の要求は、据付の管理者の鍵のときだけ受理され、前と新しい一覧が記録に残る。別の鍵の要求は拒む。"""
    admin = keygen(capsys, tmp_path / "admin.key")
    issuer = keygen(capsys, tmp_path / "issuer.key")
    stranger = keygen(capsys, tmp_path / "stranger.key")
    ctl = door(admin_key=bytes.fromhex(admin["public_key"]))

    def request(keyfile, action="add", rid="r-1"):
        argv = ["trust-change", "--admin-key", str(tmp_path / keyfile), "--door", DOOR, "--action", action,
                "--issuer-key", issuer["public_key"], "--id", rid]
        if action == "add":
            argv += ["--doors", DOOR]
        rc, out, _ = run(capsys, *argv)
        assert rc == 0
        return json.loads(out)

    assert ctl.change_trust(request("stranger.key", rid="r-0")) == "拒否"
    assert ctl.trust == {}
    assert ctl.change_trust(request("admin.key")) == "受理"
    assert ctl.trust == {issuer["issuer"]: {"public_key": issuer["public_key"], "doors": [DOOR]}}
    assert ctl.change_trust(request("admin.key", "remove", "r-2")) == "受理"
    assert ctl.trust == {}
    settings = [e for e in ctl.audit.entries if e.kind == "設定" and e.event == TRUST_EVENT]
    assert [e.result for e in settings] == ["拒否", "受理", "受理"]
    assert settings[0].detail == credentials.BAD_SIGNATURE
    assert stranger["issuer"] != admin["issuer"]


def test_trust_change_random_id_and_door_name(capsys, tmp_path):
    """R-AC-12: --id を省くと乱数の識別が付き(同じ要求の二度目は拒まれる)、扉の名前が違う要求は拒む。"""
    admin = keygen(capsys, tmp_path / "admin.key")
    issuer = keygen(capsys, tmp_path / "issuer.key")
    ctl = door(admin_key=bytes.fromhex(admin["public_key"]))
    argv = ["trust-change", "--admin-key", str(tmp_path / "admin.key"), "--action", "add",
            "--issuer-key", issuer["public_key"], "--doors", DOOR]
    rc, out, _ = run(capsys, *argv, "--door", "other-door")
    assert ctl.change_trust(json.loads(out)) == "拒否"
    rc, out, _ = run(capsys, *argv, "--door", DOOR)
    first = json.loads(out)
    rc, out, _ = run(capsys, *argv, "--door", DOOR)
    assert json.loads(first["body"])["id"] != json.loads(json.loads(out)["body"])["id"]
    assert ctl.change_trust(first) == "受理"
    assert ctl.change_trust(first) == "拒否"


def test_trust_change_add_needs_doors(capsys, tmp_path):
    """R-AC-12: add に範囲(--doors)が無い要求は作らず、短い文で止まる。"""
    keygen(capsys, tmp_path / "admin.key")
    issuer = keygen(capsys, tmp_path / "issuer.key")
    rc, out, err = run(capsys, "trust-change", "--admin-key", str(tmp_path / "admin.key"), "--door", DOOR,
                       "--action", "add", "--issuer-key", issuer["public_key"])
    assert rc == 1 and out == "" and "doors" in err
