"""スマートフォンアプリのクライアントを、事業所サーバーの試験用 TestClient に直結して確かめる。"""
from pathlib import Path

from fastapi.testclient import TestClient

from digitalkey.mobile.client import ApiError, Client
from digitalkey.site import config as C
from digitalkey.site.api import create_app
from digitalkey.site.service import Site

TOML = '''
[site]
name = "試験所"
vault = "{vault}"
[[door]]
id = "正面玄関"
[[token]]
name = "管理者"
role = "管理者"
token = "admin"
[[token]]
name = "山田"
role = "利用者"
token = "user"
subject = "山田"
'''


def make(tmp_path: Path):
    f = tmp_path / "site.toml"; f.write_text(TOML.format(vault=tmp_path / "vault"))
    tc = TestClient(create_app(Site(C.load(f))))

    def opener(method, path, body, token):
        r = tc.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.json().get("detail", ""))
        return r.json()
    return opener


def test_user_flow(tmp_path):
    opener = make(tmp_path)
    admin = Client("http://test", "admin", opener=opener)
    user = Client("http://test", "user", opener=opener)
    assert admin.health()["site"] == "試験所"
    k = admin.issue("山田", "正面玄関", "2020-01-01 00:00", "2099-01-01 00:00")
    assert user.keys()[0]["id"] == k["id"]
    assert user.unlock("正面玄関", k["id"])["result"] == "成功"
    try:
        user.state(); assert False
    except ApiError as e:
        assert e.status == 403
    assert admin.lock("正面玄関")["result"] == "成功"
    assert admin.revoke(k["id"], "試験")["key"] == k["id"]
    assert user.unlock("正面玄関", k["id"])["result"] == "拒否"
    assert [o["op"] for o in admin.operations()] == ["解錠", "施錠", "解錠"]
