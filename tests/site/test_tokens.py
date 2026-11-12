"""事業所サーバーのトークン: 設定には SHA-256 だけを置き、役割を共通コア 7.1 の主体に対応づけ、役割ごとにインターフェースを分ける。"""
import hashlib
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from digitalkey.site import cli
from digitalkey.site import config as C
from digitalkey.site.api import create_app
from digitalkey.site.service import Site


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


TOML = '''
[site]
name = "試験所"
vault = "{vault}"
[[door]]
id = "正面玄関"
lock = "dummy"
[[token]]
name = "管理者"
role = "管理者"
token_sha256 = "{admin}"
[[token]]
name = "受付"
role = "受付"
token_sha256 = "{desk}"
[[token]]
name = "山田"
role = "利用者"
token_sha256 = "{user}"
subject = "山田"
[[token]]
name = "業者"
role = "保守"
token_sha256 = "{maint}"
[[token]]
name = "前からの設定"
role = "受付"
token = "plain-token"
'''
TOKENS = {"管理者": "admin-tok", "受付": "desk-tok", "利用者": "user-tok", "保守": "maint-tok"}


@pytest.fixture
def setup(tmp_path: Path):
    f = tmp_path / "site.toml"
    f.write_text(TOML.format(vault=tmp_path / "vault", admin=sha("admin-tok"), desk=sha("desk-tok"),
                             user=sha("user-tok"), maint=sha("maint-tok")), encoding="utf-8")
    site = Site(C.load(f))
    return TestClient(create_app(site)), site, f


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_roles_map_to_core_principals(setup):
    """R-DV-12: 役割は共通コア 7.1 の主体に対応づき、/me がそれを返す。"""
    c, _, _ = setup
    want = {"管理者": "管理者", "受付": "運用者", "利用者": "利用者", "保守": "保守者"}
    for role, tok in TOKENS.items():
        me = c.get("/me", headers=h(tok)).json()
        assert me["role"] == role and me["principal"] == want[role]
    assert c.get("/me", headers=h(TOKENS["利用者"])).json()["subject"] == "山田"


def test_every_route_by_role(setup):
    """R-DV-12: 役割ごとに全部のインターフェースを叩き、許されたインターフェースは通り、許されないインターフェースは 403、トークンが違えば 401。"""
    c, site, _ = setup
    key = c.post("/keys", json={"subject": "山田", "door": "正面玄関", "start": "2020-01-01 00:00",
                                "end": "2099-01-01 00:00"}, headers=h("admin-tok")).json()["id"]
    routes = [
        (("GET", "/me", None), {"管理者", "受付", "利用者", "保守"}),
        (("GET", "/state", None), {"管理者", "受付", "保守"}),
        (("GET", "/doors", None), {"管理者", "受付", "利用者", "保守"}),
        (("POST", "/doors/正面玄関/lock", None), {"管理者", "受付", "保守"}),
        (("GET", "/keys", None), {"管理者", "受付", "利用者"}),
        (("POST", "/keys", {"subject": "x", "door": "正面玄関", "start": "2020-01-01 00:00", "end": "2099-01-01 00:00"}),
            {"管理者", "受付"}),
        (("GET", "/events/正面玄関", None), {"管理者", "保守"}),
        (("GET", "/operations", None), {"管理者", "受付"}),
        (("GET", "/verify", None), {"管理者", "保守"}),
    ]
    for (method, path, body), allowed in routes:
        assert c.request(method, path, json=body).status_code == 401, path
        assert c.request(method, path, json=body, headers=h("wrong-token")).status_code == 401, path
        for role, tok in TOKENS.items():
            code = c.request(method, path, json=body, headers=h(tok)).status_code
            assert (code == 200) == (role in allowed), (path, role, code)
            if role not in allowed:
                assert code == 403, (path, role, code)
    # 失効は管理者だけ(最後に回す)
    for role in ("受付", "利用者", "保守"):
        assert c.delete(f"/keys/{key}", headers=h(TOKENS[role])).status_code == 403
    assert c.delete(f"/keys/{key}", headers=h("admin-tok")).status_code == 200


def test_maintainer_cannot_unlock_without_an_issued_key(setup):
    """R-DV-12 R-AC-06: 保守者は、管理者が発行した鍵が無ければ開けられない。"""
    c, _, _ = setup
    r = c.post("/doors/正面玄関/unlock", json={"key": "無い鍵"}, headers=h("maint-tok"))
    assert r.json()["result"] == "拒否"
    key = c.post("/keys", json={"subject": "業者", "door": "正面玄関", "start": "2020-01-01 00:00",
                                "end": "2099-01-01 00:00"}, headers=h("admin-tok")).json()["id"]
    assert c.post("/doors/正面玄関/unlock", json={"key": key}, headers=h("maint-tok")).json()["result"] == "成功"


def test_config_keeps_only_hashes_and_old_plain_tokens_still_work(setup):
    """R-DV-12: 読んだ設定はトークンの SHA-256 だけを持つ。前からの token もそのまま使える。"""
    c, site, _ = setup
    assert all(not hasattr(t, "token") for t in site.cfg.tokens)
    assert c.get("/me", headers=h("plain-token")).json()["name"] == "前からの設定"
    # SHA-256 そのものをトークンとして出しても通らない
    assert c.get("/me", headers=h(sha("admin-tok"))).status_code == 401


def test_refused_requests_do_not_echo_the_token(setup):
    """R-DV-12: 401 と 403 の応答にトークンが入らない。"""
    c, _, _ = setup
    assert "secret-xyz" not in c.get("/state", headers=h("secret-xyz")).text
    assert "user-tok" not in c.get("/state", headers=h("user-tok")).text


def test_bad_token_entries_are_refused(tmp_path):
    """R-DV-12: 形の違う SHA-256、同じ名前、トークンの無い行は読み込みで止まる。"""
    base = '[site]\nname="x"\nvault="v"\n[[door]]\nid="d"\n'
    for tokens in ['[[token]]\nname="a"\nrole="管理者"\ntoken_sha256="xyz"\n',
                   f'[[token]]\nname="a"\nrole="管理者"\ntoken_sha256="{sha("1")}"\n'
                   f'[[token]]\nname="a"\nrole="受付"\ntoken_sha256="{sha("2")}"\n',
                   '[[token]]\nname="a"\nrole="管理者"\n']:
        f = tmp_path / "s.toml"
        f.write_text(base + tokens, encoding="utf-8")
        with pytest.raises(ValueError):
            C.load(f)


def test_init_and_token_commands_write_hashes_and_print_the_token_once(tmp_path, capsys):
    """R-DV-12: init と token は、設定に SHA-256 だけを書き、トークンは画面に一度だけ出す。足したトークンで入れる。"""
    assert cli.main(["init", str(tmp_path)]) == 0
    f = tmp_path / "site.toml"
    out = capsys.readouterr().out
    raw = tomllib.loads(f.read_text(encoding="utf-8"))
    assert all("token" not in t and len(t["token_sha256"]) == 64 for t in raw["token"])
    printed = {line.split(": ")[0].strip(): line.split(": ")[1] for line in out.splitlines() if line.startswith("  ")}
    assert {sha(v) for v in printed.values()} == {t["token_sha256"] for t in raw["token"]}
    assert all(v not in f.read_text(encoding="utf-8") for v in printed.values())

    assert cli.main(["token", "--config", str(f), "--name", "清掃", "--role", "受付"]) == 0
    tok = capsys.readouterr().out.strip().rsplit(": ", 1)[1]
    assert tok not in f.read_text(encoding="utf-8")
    site = Site(C.load(f))
    me = TestClient(create_app(site)).get("/me", headers=h(tok)).json()
    assert me == {"name": "清掃", "role": "受付", "principal": "運用者", "subject": ""}
    # 同じ名前は足さない。設定は変わらない
    before = f.read_text(encoding="utf-8")
    assert cli.main(["token", "--config", str(f), "--name", "清掃", "--role", "管理者"]) == 1
    assert f.read_text(encoding="utf-8") == before
