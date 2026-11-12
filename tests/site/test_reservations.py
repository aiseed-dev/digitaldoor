"""事業所サーバーの予約と資格情報の発行のインターフェース(使い方.md「予約と資格情報の発行のインターフェース」)。"""
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from digitaldoor_controller import credentials as cred
from digitalkey.site import config as C
from digitalkey.site.api import create_app
from digitalkey.site.service import Site

TOML = '''
[site]
name = "試験所"
vault = "{vault}"
[credentials]
max_hours = 2
[[door]]
id = "離れ"
lock = "dummy"
[[door]]
id = "母屋"
lock = "dummy"
[[token]]
name = "管理者"
role = "管理者"
token = "admin-tok"
[[token]]
name = "受付"
role = "受付"
token = "desk-tok"
[[token]]
name = "山田"
role = "利用者"
token = "user-tok"
subject = "山田"
[[token]]
name = "業者"
role = "保守"
token = "maint-tok"
'''


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


@pytest.fixture
def setup(tmp_path: Path):
    f = tmp_path / "site.toml"
    f.write_text(TOML.format(vault=tmp_path / "vault"), encoding="utf-8")
    site = Site(C.load(f))
    return TestClient(create_app(site)), site, tmp_path


def book(c, *, door="離れ", start=None, end=None, tok="desk-tok"):
    now = datetime.now()
    body = {"subject": "鈴木", "layer": "民泊", "arrival": fmt(start or now - timedelta(hours=1)),
            "departure": fmt(end or now + timedelta(days=2)), "door": door, "contact": "x@example.invalid"}
    return c.post("/reservations", json=body, headers=h(tok))


def test_reservation_issue_and_cancel_revokes_its_keys(setup):
    """R-DV-13: 予約を起こし、予約から鍵を出し、取り消すと出した鍵が全部失効する。記録は追記だけ。"""
    c, site, _ = setup
    r = book(c)
    assert r.status_code == 200, r.text
    y = r.json()
    assert y["door"] == "離れ" and y["cancelled"] == "" and len(y["checkin_code"]) >= 16
    k1 = c.post(f"/reservations/{y['id']}/keys", json={}, headers=h("desk-tok")).json()
    k2 = c.post(f"/reservations/{y['id']}/keys", json={"route": "暗証番号"}, headers=h("admin-tok")).json()
    assert k1["subject"] == "鈴木" and k1["door"] == "離れ" and k2["route"] == "暗証番号"
    assert k1["start"] == y["arrival"] and k1["end"] == y["departure"]
    one = c.get(f"/reservations/{y['id']}", headers=h("desk-tok")).json()
    assert [k["id"] for k in one["keys"]] == [k1["id"], k2["id"]] and not any(k["revoked"] for k in one["keys"])
    # 鍵で開く
    assert c.post("/doors/離れ/unlock", json={"key": k1["id"]}, headers=h("desk-tok")).json()["result"] == "成功"

    n_before = site.ledger.count()
    r = c.post(f"/reservations/{y['id']}/cancel", json={"reason": "客の都合"}, headers=h("desk-tok"))
    assert r.status_code == 200 and sorted(r.json()["revoked"]) == sorted([k1["id"], k2["id"]])
    assert site.ledger.count() == n_before + 3          # 取消 1 枚と失効 2 枚。前の伝票は消えない
    assert site.ledger.get(y["id"])["主体"] == "鈴木"    # 予約の伝票はそのまま
    one = c.get(f"/reservations/{y['id']}", headers=h("admin-tok")).json()
    assert one["cancelled"] == r.json()["id"] and all(k["revoked"] for k in one["keys"])
    r2 = c.post("/doors/離れ/unlock", json={"key": k1["id"]}, headers=h("desk-tok")).json()
    assert r2["result"] == "拒否" and "失効" in r2["reason"]
    # 取り消した予約には鍵を出さない。二度は取り消さない
    assert c.post(f"/reservations/{y['id']}/keys", json={}, headers=h("desk-tok")).status_code == 409
    assert c.post(f"/reservations/{y['id']}/cancel", json={}, headers=h("desk-tok")).status_code == 409
    assert site.ledger.verify_chain()[0]


def test_reservation_input_is_checked(setup):
    """R-DV-13: 無い扉は 404、様式に合わない層と、出発が到着より前の予約は 400 で、何も書かない。"""
    c, site, _ = setup
    n = site.ledger.count()
    assert book(c, door="無い扉").status_code == 404
    bad = {"subject": "鈴木", "layer": "ホテル", "arrival": "2026-01-01 10:00", "departure": "2026-01-02 10:00",
           "door": "離れ"}
    assert c.post("/reservations", json=bad, headers=h("desk-tok")).status_code == 400
    now = datetime.now()
    assert book(c, start=now, end=now - timedelta(hours=1)).status_code == 400
    assert site.ledger.count() == n
    assert c.get("/reservations/無い番号", headers=h("desk-tok")).status_code == 404


def test_reservation_routes_by_role(setup):
    """R-DV-12 R-DV-13: 予約と資格情報の発行は管理者と受付だけ。発行者の公開鍵は保守も読める。"""
    c, _, _ = setup
    y = book(c).json()
    key = c.post(f"/reservations/{y['id']}/keys", json={}, headers=h("desk-tok")).json()["id"]
    for tok in ("user-tok", "maint-tok"):
        assert book(c, tok=tok).status_code == 403
        assert c.get("/reservations", headers=h(tok)).status_code == 403
        assert c.get(f"/reservations/{y['id']}", headers=h(tok)).status_code == 403
        assert c.post(f"/reservations/{y['id']}/keys", json={}, headers=h(tok)).status_code == 403
        assert c.post(f"/reservations/{y['id']}/cancel", json={}, headers=h(tok)).status_code == 403
        assert c.post(f"/keys/{key}/credential", json={}, headers=h(tok)).status_code == 403
    assert c.get("/issuer", headers=h("user-tok")).status_code == 403
    assert c.get("/issuer", headers=h("maint-tok")).status_code == 200
    assert c.get("/reservations").status_code == 401


def test_credential_is_checked_by_the_door_itself(setup):
    """R-DV-13 R-AC-09: 鍵から作った資格情報で、その扉は開き、範囲の外の扉は拒む。書き換えた物は署名で拒む。"""
    c, site, _ = setup
    y = book(c).json()
    key = c.post(f"/reservations/{y['id']}/keys", json={}, headers=h("desk-tok")).json()["id"]
    got = c.post(f"/keys/{key}/credential", json={}, headers=h("desk-tok")).json()
    body = json.loads(got["credential"]["body"])
    assert body["doors"] == ["離れ"] and body["subject"] == key and "鈴木" not in got["credential"]["body"]
    info = c.get("/issuer", headers=h("admin-tok")).json()
    assert body["issuer"] == info["issuer"]
    # 発行者の公開鍵だけで、外の扉と同じ確かめができる
    trust = {info["issuer"]: {"public_key": info["public_key"], "doors": info["doors"]}}
    assert cred.verify_credential(got["credential"], door="離れ", trust=trust, now=time.time(), synced=True,
                                  used_ids=set()).ok
    r = c.post("/doors/離れ/credential", json={"credential": got["credential"]}, headers=h("user-tok")).json()
    assert r["result"] == "解錠" and r["door"]["locked"] is False
    r = c.post("/doors/母屋/credential", json={"credential": got["credential"]}, headers=h("user-tok")).json()
    assert r["result"] == "拒否" and r["reason"] == cred.OUT_OF_RANGE
    forged = dict(got["credential"], body=got["credential"]["body"].replace("離れ", "母屋"))
    r = c.post("/doors/母屋/credential", json={"credential": forged}, headers=h("user-tok")).json()
    assert r["result"] == "拒否" and r["reason"] == cred.BAD_SIGNATURE
    # 鍵の操作の伝票に、鍵番号つきで成功と拒否が残る
    ops = c.get("/operations", headers=h("admin-tok")).json()
    assert [(o["door"], o["result"]) for o in ops[-3:]] == [("離れ", "成功"), ("母屋", "拒否"), ("母屋", "拒否")]
    assert ops[-3]["key"] == key
    # 発行の記録は残るが、署名そのものは台帳に書かない
    rec = site.ledger.select("資格情報_発行")
    assert len(rec) == 1 and rec[0]["識別"] == got["id"] and rec[0]["鍵番号"] == key
    assert got["credential"]["sig"] not in rec[0].path.read_text(encoding="utf-8")


def test_credential_validity_is_capped_by_max_hours_and_shown(setup):
    """R-AC-14: 資格情報の終わりは、鍵の終わりと 作った時刻 + 最長の有効時間 の早い方。最長の時間を管理ソフトが返す。"""
    c, _, _ = setup
    assert c.get("/issuer", headers=h("admin-tok")).json()["max_hours"] == 2
    y = book(c).json()                                       # 鍵は 2 日先まで
    key = c.post(f"/reservations/{y['id']}/keys", json={}, headers=h("desk-tok")).json()["id"]
    t0 = time.time()
    got = c.post(f"/keys/{key}/credential", json={}, headers=h("desk-tok")).json()
    assert abs(got["not_after"] - (t0 + 2 * 3600)) <= 5
    now = datetime.now()                                      # 鍵が 30 分で終わるなら、そこまで
    y2 = book(c, start=now - timedelta(minutes=5), end=now + timedelta(minutes=30)).json()
    k2 = c.post(f"/reservations/{y2['id']}/keys", json={}, headers=h("desk-tok")).json()
    g2 = c.post(f"/keys/{k2['id']}/credential", json={}, headers=h("desk-tok")).json()
    assert g2["not_after"] == int(datetime.fromisoformat(k2["end"]).timestamp())


def test_no_credential_for_revoked_or_expired_keys_and_once_works_once(setup):
    """R-DV-13 R-AC-10: 失効した鍵と終わった鍵には出さない(409)。一回限りの資格情報は一度だけ開く。"""
    c, _, _ = setup
    now = datetime.now()
    old = book(c, start=now - timedelta(days=3), end=now - timedelta(days=1)).json()
    k_old = c.post(f"/reservations/{old['id']}/keys", json={}, headers=h("desk-tok")).json()["id"]
    assert c.post(f"/keys/{k_old}/credential", json={}, headers=h("desk-tok")).status_code == 409
    y = book(c).json()
    key = c.post(f"/reservations/{y['id']}/keys", json={}, headers=h("desk-tok")).json()["id"]
    once = c.post(f"/keys/{key}/credential", json={"once": True}, headers=h("desk-tok")).json()
    assert c.post("/doors/離れ/credential", json={"credential": once["credential"]},
                  headers=h("user-tok")).json()["result"] == "解錠"
    again = c.post("/doors/離れ/credential", json={"credential": once["credential"]}, headers=h("user-tok")).json()
    assert again["result"] == "拒否" and again["reason"] == cred.ALREADY_USED
    assert c.delete(f"/keys/{key}", headers=h("admin-tok")).status_code == 200
    assert c.post(f"/keys/{key}/credential", json={}, headers=h("desk-tok")).status_code == 409
    assert c.post("/keys/無い鍵/credential", json={}, headers=h("desk-tok")).status_code == 404


def test_issuer_key_is_kept_in_the_vault_and_reused(setup, tmp_path):
    """R-DV-13: 発行者の鍵は金庫に権限 600 で一つ置き、起動し直しても同じ鍵を使う。"""
    c, site, _ = setup
    p = site.cfg.vault / "発行者.ed25519"
    assert p.exists() and (os.stat(p).st_mode & 0o777) == 0o600
    again = Site(site.cfg)
    assert again.issuer_info()["public_key"] == site.issuer_info()["public_key"]


def test_max_hours_must_be_a_positive_number(tmp_path):
    """R-AC-14: 最長の有効時間に、0 以下や数でない値は入れられない。"""
    for v in ("0", "-1", '"24"', "true"):
        f = tmp_path / "s.toml"
        f.write_text(TOML.format(vault=tmp_path / "v").replace("max_hours = 2", f"max_hours = {v}"), encoding="utf-8")
        with pytest.raises(ValueError):
            C.load(f)
