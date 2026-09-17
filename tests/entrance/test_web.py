from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from digitalkey.entrance.keyring import DummyLock
from digitalkey.entrance.web import Config, create_app

FACE = b"same-face"


@pytest.fixture
def client(tmp_path):
    cfg = Config(vault=tmp_path / "vault", house_lat=35.0, house_lon=135.0, owner_token="owner",
                 locks={"玄関": DummyLock("玄関")})
    app = create_app(cfg)
    c = TestClient(app)
    c.app = app
    return c


def reserve(app, subject="Anna"):
    return app.state.ledger.append("予約", {"主体": subject, "層": "WWOOF", "到着": "2020-01-01T00:00",
                                          "出発": "2099-01-01T00:00", "錠": "玄関", "受付符号": "tok-" + subject},
                                   issuer="AI社員")


def test_checkin_flow(client):
    r = reserve(client.app)
    assert client.get("/checkin/nope").status_code == 404
    assert "チェックイン" in client.get("/checkin/tok-Anna").text
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    fr = client.post("/doorbell/玄関/frame", files={"image": ("d.jpg", FACE, "image/jpeg")}, data={"at": now})
    assert fr.status_code == 200 and len(fr.json()["ハッシュ"]) == 64
    res = client.post("/checkin/tok-Anna", files={"selfie": ("s.jpg", FACE, "image/jpeg"),
                                                   "id_image": ("i.jpg", FACE, "image/jpeg")},
                      data={"lat": "35.0002", "lon": "135.0"})
    j = res.json()
    assert j["判定"] == "承認" and j["解錠"] == "成功" and j["鍵番号"]
    assert client.app.state.keyring.locks["玄関"].actions == ["unlock"]
    key = client.app.state.ledger.get(j["鍵番号"])
    assert key["予約番号"] == r.id and key.basis == [j["本人確認番号"]]
    assert client.get("/healthz").json()["伝票"] == 4  # 予約・本人確認・鍵_発行・鍵_操作
    assert client.app.state.ledger.verify_chain()[0]


def test_hold_then_owner_decides(client):
    reserve(client.app, "Ben")
    res = client.post("/checkin/tok-Ben", files={"selfie": ("s.jpg", FACE, "image/jpeg"),
                                                  "id_image": ("i.jpg", FACE, "image/jpeg")},
                      data={"lat": "35.0", "lon": "135.0"})
    j = res.json()
    assert j["判定"] == "保留" and "ドアホンの映像がない" in j["理由"] and "鍵番号" not in j
    bad = client.post(f"/decide/{j['本人確認番号']}", data={"decision": "承認"}, headers={"X-Owner-Token": "x"})
    assert bad.status_code == 403
    ok = client.post(f"/decide/{j['本人確認番号']}", data={"decision": "承認", "reason": "電話で確認", "by": "オーナー"},
                     headers={"X-Owner-Token": "owner"})
    k = ok.json()
    assert k["判定"] == "承認" and k["解錠"] == "成功"
    assert client.app.state.ledger.get(k["本人確認番号"])["判定者"] == "オーナー"
