"""失効の一覧を作る命令 `revocation` と、共通の引数 `--revocation-max-age`(扉コントローラ仕様書 5.2c、試験の対 45)の試験。

命令が出した JSON を、扉の側の `Controller.accept_revocation` に渡して確かめる。
`--revocation-max-age` は、`serve` が HTTP のインターフェースを開く所(cli の `serve`)を差し替えて、渡る `Config` を見る。
"""
import json
import time

import pytest

from digitaldoor_controller import cli, credentials
from digitaldoor_controller.audit import Audit, Ed25519Signer, Ed25519Verifier, HmacSigner
from digitaldoor_controller.cli import main
from digitaldoor_controller.contacts import SimContacts
from digitaldoor_controller.controller import Config, Controller

DOOR = "door-1"
NOW = 1_800_000_000


def run(capsys, *argv):
    """(終了コード, 標準出力, 標準エラー)。引数の誤りで argparse が止めたときも終了コードを返す。"""
    try:
        rc = main(list(argv))
    except SystemExit as e:
        rc = e.code
    out = capsys.readouterr()
    return rc, out.out, out.err


def keygen(capsys, path):
    rc, out, _ = run(capsys, "keygen", "--out", str(path))
    assert rc == 0
    return json.loads(out)


def door(issuer):
    ctl = Controller(SimContacts(), Audit(HmacSigner(b"k" * 32)), Config(autolock_s=0), name=DOOR,
                     trust={issuer["issuer"]: {"public_key": issuer["public_key"], "doors": [DOOR]}})
    ctl.set_time(NOW, True)
    return ctl


def test_pair45_revocation_command_output_is_accepted(capsys, tmp_path):
    """R-AC-22: 試験の対 45。revocation で作った一覧は仕様の形で、扉に送ると「受理」になり、一覧の資格情報は「失効済み」。"""
    issuer = keygen(capsys, tmp_path / "issuer.key")
    rc, out, err = run(capsys, "--dir", str(tmp_path / "vault"), "revocation", "--key", str(tmp_path / "issuer.key"),
                       "--generation", "7", "--ids", "c-0012,c-0031", "--at", "1794700800")
    assert rc == 0, err
    lst = json.loads(out)
    assert set(lst) == {"body", "sig"}
    body = json.loads(lst["body"])
    assert body == {"format": "digitaldoor-revocation/1", "issuer": issuer["issuer"], "generation": 7,
                    "issued_at": 1794700800, "revoked": ["c-0012", "c-0031"]}
    assert Ed25519Verifier(bytes.fromhex(issuer["public_key"])).verify(lst["body"].encode("utf-8"), lst["sig"])
    ctl = door(issuer)
    assert ctl.accept_revocation(lst) == "受理"
    signer = Ed25519Signer((tmp_path / "issuer.key").read_bytes())
    c = credentials.issue(signer, cred_id="c-0031", subject="resident-031", doors=[DOOR],
                          not_before=NOW - 10, not_after=NOW + 10)
    assert ctl.present(c) == "拒否"
    assert [e.result for e in ctl.audit.entries if e.kind == "認証"][-1] == "失効済み"
    assert not (tmp_path / "vault").exists()            # 置き場を使わない


def test_pair45_ids_omitted_is_empty_list(capsys, tmp_path):
    """R-AC-22: 試験の対 45。--ids を省くと空の一覧になり、扉は受理する。"""
    issuer = keygen(capsys, tmp_path / "issuer.key")
    rc, out, _ = run(capsys, "revocation", "--key", str(tmp_path / "issuer.key"), "--generation", "1", "--at", "1")
    assert rc == 0
    assert json.loads(json.loads(out)["body"])["revoked"] == []
    assert door(issuer).accept_revocation(json.loads(out)) == "受理"


def test_pair45_at_accepts_iso_time(capsys, tmp_path):
    """R-AC-22: 試験の対 45。--at は issue の --from と同じ書き方で、時差つきの ISO 8601 は unix 秒に直る。"""
    keygen(capsys, tmp_path / "issuer.key")
    rc, out, _ = run(capsys, "revocation", "--key", str(tmp_path / "issuer.key"), "--generation", "2",
                     "--at", "2026-10-21T09:00:00+09:00")
    assert rc == 0
    assert json.loads(json.loads(out)["body"])["issued_at"] == 1792540800


def test_pair45_at_omitted_is_now(capsys, tmp_path):
    """R-AC-14: 試験の対 45。--at を省くと issued_at は今の時刻(整数の unix 秒)。"""
    keygen(capsys, tmp_path / "issuer.key")
    before = int(time.time())
    rc, out, _ = run(capsys, "revocation", "--key", str(tmp_path / "issuer.key"), "--generation", "3")
    after = int(time.time()) + 1
    assert rc == 0
    issued_at = json.loads(json.loads(out)["body"])["issued_at"]
    assert isinstance(issued_at, int) and before <= issued_at <= after


def test_pair45_unreadable_key_file_is_a_short_message(capsys, tmp_path):
    """R-AC-22: 試験の対 45。読めない鍵のファイル(形が違う、無い)は短い文で止まり、終了コード 1 で、一覧を出さない。"""
    (tmp_path / "bad.key").write_bytes(b"short")
    for path in (tmp_path / "bad.key", tmp_path / "missing.key"):
        rc, out, err = run(capsys, "revocation", "--key", str(path), "--generation", "1", "--at", "1")
        assert rc == 1 and out == "" and err.strip() and "Traceback" not in err


@pytest.mark.parametrize("generation", ["0", "-1"])
def test_pair45_generation_below_one_is_a_short_message(capsys, tmp_path, generation):
    """R-AC-22: 試験の対 45。1 より小さい世代は短い文で止まり、終了コード 1 で、一覧を出さない。"""
    keygen(capsys, tmp_path / "issuer.key")
    rc, out, err = run(capsys, "revocation", "--key", str(tmp_path / "issuer.key"),
                       f"--generation={generation}", "--at", "1")
    assert rc == 1 and out == "" and err.strip() and "Traceback" not in err


@pytest.mark.parametrize("bad", [["--generation", "abc", "--at", "1"], ["--generation", "1", "--at", "あした"]])
def test_pair45_unreadable_input_is_a_short_message(capsys, tmp_path, bad):
    """R-AC-22: 試験の対 45。読めない世代や時刻は短い文で止まり、終了コード 1。(仕様の「読めない入力は終了コード 1」の素直な読み)"""
    keygen(capsys, tmp_path / "issuer.key")
    rc, out, err = run(capsys, "revocation", "--key", str(tmp_path / "issuer.key"), *bad)
    assert rc == 1 and out == "" and "Traceback" not in err


def _serve_and_capture(monkeypatch, tmp_path, *extra):
    seen = {}

    def fake_serve(ctl, *a, **kw):
        seen["ctl"] = ctl

    monkeypatch.setattr(cli, "serve", fake_serve)
    rc = main(["--dir", str(tmp_path / "vault"), *extra, "serve"])
    assert rc == 0
    return seen["ctl"]


def test_revocation_max_age_option_reaches_serve_config(monkeypatch, tmp_path, capsys):
    """R-AC-14: 試験の対 45。共通の引数 --revocation-max-age の秒数が、serve の Config.revocation_max_age_s に渡る。"""
    ctl = _serve_and_capture(monkeypatch, tmp_path, "--revocation-max-age", "3600")
    assert ctl.config.revocation_max_age_s == 3600


def test_revocation_max_age_option_defaults_to_zero(monkeypatch, tmp_path, capsys):
    """R-AC-14: 試験の対 45。--revocation-max-age を省くと 0(この規則を使わない)。"""
    ctl = _serve_and_capture(monkeypatch, tmp_path)
    assert ctl.config.revocation_max_age_s == 0
