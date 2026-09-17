"""事業所サーバー: 設定 → 鍵の発行 → 解錠 → 失効 → 拒否、役割の境界、記録の検査。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from digitalkey.site import config as C
from digitalkey.site.api import create_app
from digitalkey.site.service import Site

TOML = '''
[site]
name = "試験所"
vault = "{vault}"
[api]
port = 8800
[[door]]
id = "正面玄関"
lock = "dummy"
[[door]]
id = "サーバー室"
lock = "dummy"
two_person = 2
[[token]]
name = "管理者"
role = "管理者"
token = "admin-token"
[[token]]
name = "受付"
role = "受付"
token = "desk-token"
[[token]]
name = "山田"
role = "利用者"
token = "user-token"
subject = "山田"
'''


@pytest.fixture
def client(tmp_path: Path):
    f = tmp_path / "site.toml"
    f.write_text(TOML.format(vault=tmp_path / "vault"))
    site = Site(C.load(f))
    return TestClient(create_app(site)), site


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_sample_config_loads(tmp_path):
    f = tmp_path / "site.toml"; f.write_text(C.sample("本社", str(tmp_path / "vault")))
    cfg = C.load(f)
    assert cfg.name == "本社" and cfg.doors[0].id == "正面玄関" and {t.role for t in cfg.tokens} == {"管理者", "受付"}


def test_issue_unlock_revoke(client):
    c, site = client
    assert c.get("/health").json()["ok"]
    assert c.get("/keys").status_code == 401
    r = c.post("/keys", json={"subject": "山田", "door": "正面玄関", "start": "2020-01-01 00:00", "end": "2099-01-01 00:00"}, headers=h("admin-token"))
    assert r.status_code == 200, r.text
    key = r.json()["id"]
    mine = c.get("/keys", headers=h("user-token")).json()
    assert [k["id"] for k in mine] == [key]
    r = c.post("/doors/正面玄関/unlock", json={"key": key}, headers=h("user-token"))
    assert r.json()["result"] == "成功" and r.json()["door"]["locked"] is False
    # 別の扉には使えない
    r = c.post("/doors/サーバー室/unlock", json={"key": key}, headers=h("user-token"))
    assert r.json()["result"] == "拒否" and "この扉の鍵ではありません" in r.json()["reason"]
    # 失効 → 拒否
    assert c.delete(f"/keys/{key}", params={"reason": "退職"}, headers=h("admin-token")).status_code == 200
    r = c.post("/doors/正面玄関/unlock", json={"key": key}, headers=h("user-token"))
    assert r.json()["result"] == "拒否" and "失効" in r.json()["reason"]
    ops = c.get("/operations", headers=h("admin-token")).json()
    assert [o["result"] for o in ops] == ["成功", "拒否", "拒否"]
    v = c.get("/verify", headers=h("admin-token")).json()
    assert v["ok"] and v["doors"]["正面玄関"]["count"] >= 3


def test_roles(client):
    c, _ = client
    r = c.post("/keys", json={"subject": "x", "door": "正面玄関", "start": "2020-01-01 00:00", "end": "2099-01-01 00:00"}, headers=h("user-token"))
    assert r.status_code == 403
    r = c.post("/keys", json={"subject": "佐藤", "door": "正面玄関", "start": "2020-01-01 00:00", "end": "2099-01-01 00:00"}, headers=h("desk-token"))
    assert r.status_code == 200
    key = r.json()["id"]
    # 利用者は他人の鍵で開けられない
    r = c.post("/doors/正面玄関/unlock", json={"key": key}, headers=h("user-token"))
    assert r.json()["result"] == "拒否" and "他人" in r.json()["reason"]
    assert c.delete(f"/keys/{key}", headers=h("desk-token")).status_code == 403
    assert c.get("/events/正面玄関", headers=h("desk-token")).status_code == 403
    assert c.get("/events/正面玄関", headers=h("admin-token")).status_code == 200


def test_two_person_and_lock(client):
    c, _ = client
    k1 = c.post("/keys", json={"subject": "A", "door": "サーバー室", "start": "2020-01-01 00:00", "end": "2099-01-01 00:00"}, headers=h("admin-token")).json()["id"]
    k2 = c.post("/keys", json={"subject": "B", "door": "サーバー室", "start": "2020-01-01 00:00", "end": "2099-01-01 00:00"}, headers=h("admin-token")).json()["id"]
    r = c.post("/doors/サーバー室/unlock", json={"key": k1}, headers=h("admin-token"))
    assert r.json()["result"] == "待ち"
    r = c.post("/doors/サーバー室/unlock", json={"key": k2}, headers=h("desk-token"))
    assert r.json()["result"] == "成功"
    r = c.post("/doors/サーバー室/lock", headers=h("desk-token"))
    assert r.json()["result"] == "成功" and r.json()["door"]["locked"] is True
    assert c.post("/doors/無い扉/lock", headers=h("desk-token")).status_code == 404
