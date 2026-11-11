"""「人の出入り」の画面の中身（seed のサーバーの管理の口）。サーバーは偽の返事で置き換える。"""

import datetime as dt
import json
import os
import stat

import pytest

from kagi import admin, audit, inventory

TOKEN = "AdminTok3n-not-a-secret-XYZ123"  # 偽の形。実際の token ではない
NEW = "NewIssued9-not-a-secret-QRS456"
KEY_BYTES = b"admin-test-key-not-a-secret-01"
TODAY = dt.date(2026, 11, 6)

OVERVIEW = {
    "version": "0.9",
    "records": 3,
    "connections": 5,
    "tokens": {"live": 4, "expired": 1, "revoked": 2, "expiring": 1},
    "invites": 2,
    "read": False,
    "audit": "off",
    "remaining": {"reads": 0, "talks": 3, "usd": 1.5},
    "paused": True,
    "keys": {"claude": True, "plantnet": False},
    "denied_today": 25,
}


class Fake:
    """偽のサーバー。呼ばれた物を覚える。"""

    def __init__(self, routes=None):
        self.calls = []
        self.routes = {
            ("GET", "/api/admin/overview"): (200, OVERVIEW),
            ("GET", "/api/admin/tokens"): (
                200,
                {
                    "tokens": [
                        {"name": "z", "scope": ["buyer"], "state": "revoked", "created": "2026-01-01"},
                        {
                            "name": "a",
                            "scope": ["member"],
                            "state": "live",
                            "created": "2026-10-01",
                            "expires": "2026-11-10",
                            "note": "畑の仲間",
                        },
                        {"name": "b", "scope": ["admin"], "state": "live", "expires": "2027-06-01"},
                    ]
                },
            ),
            ("GET", "/api/admin/audit?limit=40"): (
                200,
                {
                    "verified": True,
                    "lines": [
                        {
                            "ts": "2026-11-06T10:00:00+09:00",
                            "actor": "?",
                            "action": "read",
                            "detail": "断った",
                        },
                        {"ts": "2026-11-06T09:00:00+09:00", "actor": "suzuki", "action": "admin.pause"},
                    ],
                },
            ),
            ("GET", "/api/admin/records"): (
                200,
                {
                    "records": [
                        {
                            "id": "r1",
                            "name": "畑",
                            "place": "里",
                            "owner": "suzuki",
                            "published": "2026-11-01",
                            "connections": 2,
                            "url": "https://seed.example/p/r1",
                        }
                    ]
                },
            ),
            ("GET", "/api/admin/records/r1/connections"): (
                200,
                {"connections": [{"name": "c1", "date": "2026-11-02", "from": "x", "word": "よい"}]},
            ),
            ("POST", "/api/admin/tokens"): (200, {"token": NEW, "name": "c"}),
            ("POST", "/api/admin/tokens/revoke"): (200, {"revoked": 1}),
            ("POST", "/api/admin/tokens/revoke_all"): (200, {"revoked": 3}),
            ("POST", "/api/admin/pause"): (200, {"paused": True}),
            ("DELETE", "/api/admin/records/r1"): (200, {"withdrawn": True}),
            ("DELETE", "/api/admin/records/r1/connections/c1"): (200, {"removed": True}),
            ("GET", "/api/admin/pending"): (
                200,
                {
                    "pending": [
                        {
                            "id": "r2",
                            "name": "suzuki",
                            "received": "2026-11-08T09:30:12",
                            "markdown": "---\nid: r2\n---\n",
                            "photos": ["a.jpg"],
                        }
                    ],
                    "review": True,
                },
            ),
            ("POST", "/api/admin/pending/r2/approve"): (200, {"id": "r2", "state": "public"}),
            ("POST", "/api/admin/pending/r2/return"): (200, {"id": "r2", "state": "returned"}),
        }
        self.routes.update(routes or {})

    def __call__(self, method, url, headers, body):
        assert url.startswith("https://seed.example/")
        path = url.removeprefix("https://seed.example")
        self.calls.append((method, path, headers, json.loads(body) if body else None))
        status, out = self.routes[(method, path)]
        return status, json.dumps(out).encode()


def _dir(tmp_path, inv=""):
    d = tmp_path / "k"
    d.mkdir()
    (d / "inventory.toml").write_text(inv, "utf-8")
    keyf = tmp_path / "key"
    keyf.write_bytes(KEY_BYTES)
    os.chmod(keyf, 0o600)
    return d, {"KAGI_LOG_KEY_FILE": str(keyf), "USER": "kakari"}


def _client(d, fake=None):
    admin.save_conn(d, "https://seed.example/", TOKEN)
    return admin.Client(admin.load_conn(d), fake or Fake())


# ---- 繋ぐ先 ----


@pytest.mark.parametrize(
    "url",
    [
        "http://seed.example",
        "http://192.0.2.10:8010",
        "ftp://seed.example",
        "seed.example",
        "",
        "https://u:p@seed.example",
        "https://seed.example/?x=1",
    ],
)
def test_url_refused(url):
    with pytest.raises(inventory.Refused):
        admin.check_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://seed.example", "https://192.0.2.10:8443/", "http://127.0.0.1:8010", "http://localhost:8010"],
)
def test_url_allowed(url):
    assert admin.check_url(url) == url.rstrip("/")


def test_save_conn_600_and_value_hidden(tmp_path):
    d, _env = _dir(tmp_path)
    path = admin.save_conn(d, "https://seed.example", TOKEN)
    assert path == d / admin.CONN_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert TOKEN in path.read_text()
    c = admin.load_conn(d)
    assert c.warning is None and c.url == "https://seed.example" and c.host == "seed.example"
    assert TOKEN not in repr(c) and TOKEN not in str(c) and TOKEN not in repr(admin.Client(c))
    os.chmod(path, 0o644)
    assert "600" in admin.load_conn(d).warning
    admin.save_conn(d, "https://seed.example", TOKEN)  # 入れ替えると 600 に戻る
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert admin.forget_conn(d) and admin.load_conn(d) is None


def test_save_conn_refuses(tmp_path):
    d, _env = _dir(tmp_path)
    for url, tok in [
        ("http://seed.example", TOKEN),
        ("https://seed.example", ""),
        ("https://seed.example", "a b"),
    ]:
        with pytest.raises(inventory.Refused) as e:
            admin.save_conn(d, url, tok)
        assert TOKEN not in str(e.value)
    assert not (d / admin.CONN_FILE).exists()


def test_token_sent_only_in_header(tmp_path):
    d, _env = _dir(tmp_path)
    fake = Fake()
    _client(d, fake).overview()
    method, path, headers, body = fake.calls[0]
    assert (method, path, body) == ("GET", "/api/admin/overview", None)
    assert headers["x-seed-invite"] == TOKEN


def test_errors_do_not_carry_token(tmp_path):
    d, _env = _dir(tmp_path)
    fake = Fake({("GET", "/api/admin/overview"): (403, {"error": "invite_required", "detail": [TOKEN]})})
    with pytest.raises(admin.AdminError) as e:
        _client(d, fake).overview()
    assert "403" in str(e.value) and TOKEN not in str(e.value)

    def boom(*a):
        raise ConnectionRefusedError(f"refused {TOKEN}")

    with pytest.raises(admin.AdminError) as e:
        _client(d, boom).overview()
    assert TOKEN not in str(e.value) and e.value.__cause__ is None

    fake = Fake({("GET", "/api/admin/records"): (400, {"error": "x", "detail": ["形が違う"]})})
    with pytest.raises(admin.AdminError, match="形が違う"):
        _client(d, fake).records()


def test_redirect_not_followed():
    h = admin._NoRedirect()
    assert h.redirect_request(None, None, 302, "", {}, "http://seed.example/") is None


# ---- 画面の中身 ----


def test_load_all_and_rows(tmp_path):
    d, _env = _dir(tmp_path)
    data = admin.load_all(_client(d))
    rows = dict(map(tuple, admin.state_rows(data["overview"])))
    assert rows["サーバー"] == "動いています（版 0.9）"
    assert rows["受け付け"] == "止めています"
    assert rows["Claude を呼べるか"] == "呼べません"
    assert rows["外の鍵"] == "Claude 在る、Pl@ntNet 無い"
    assert rows["操作の記録"] == "書いていません"
    assert rows["今日の残り"] == "読み取り 0 回、話す 3 回、使える額 1.5 ドル"
    assert rows["公開"] == "記録 3 件、寄せられた一言 5 件"
    assert rows["入れる人"] == "token 4 人、招待コード 2 人"  # Web 版の人数（boxes）はもう無い

    at = admin.attention(data["overview"], data["audit"])
    texts = [t for t, _ in at]
    assert texts[0].startswith("受け付けを止めています")
    assert any("Claude の鍵" in t for t in texts)
    cmds = dict(at)
    read = next(t for t in texts if "Claude の鍵" in t)
    assert cmds[
        read
    ] == [  # 鍵は root だけが読めるファイルに（無い時だけ作る。systemd が seed.service にだけ渡す）
        "sudo install -d -o root -g root -m 700 /etc/credstore",
        "sudo test -e /etc/credstore/seed.anthropic || sudo sh -c 'umask 077; cat > /etc/credstore/seed.anthropic'",
        "sudo systemctl restart seed",
    ]
    assert any("25 回" in t for t in texts) and any("上限" in t for t in texts)
    assert any("期限の切れた token が 1 件" in t for t in texts)
    assert any("14 日以内に切れる token が 1 件" in t for t in texts)
    assert not any("書き換え" in t for t in texts)
    assert any("書き換え" in t for t, _ in admin.attention(data["overview"], {"verified": False}))

    tr = admin.token_rows(data["tokens"], TODAY)
    assert [r["name"] for r in tr] == ["a", "b", "z"]
    assert tr[0]["soon"] and "あと 4 日で切れる" in tr[0]["state"] and tr[0]["note"] == "畑の仲間"
    assert tr[0]["scope"].startswith("仲間") and tr[1]["scope"].startswith("管理者")
    assert not tr[1]["soon"] and not tr[2]["live"] and tr[2]["state"] == "取り消し済み"

    rr = admin.record_rows(data["records"])
    assert rr == [
        {
            "id": "r1",
            "head": "畑 · 里",
            "meta": "公開した人 suzuki　公開した日 2026-11-01　寄せられた一言 2 件",
            "url": "https://seed.example/p/r1",
        }
    ]
    cr = admin.connection_rows(_client(d).connections("r1"))
    assert cr == [{"name": "c1", "head": "2026-11-02　x", "word": "よい"}]

    av = admin.audit_view(data["audit"])
    assert av["verified"] is True and not av["tampered"]
    assert av["rows"][0] == ["2026-11-06 10:00", "知らない人", "写真を読み取った", "断った"]
    assert av["rows"][1][2] == "管理: 受け付けを止めた"
    assert admin.audit_view({"verified": False})["tampered"]
    assert "確かめられなかった" in admin.audit_view({})["head"]
    assert TOKEN not in repr(data)


def test_audit_talk_lines():
    """web1 経由で Claude と話した行（talk）。誰が、通した・断った。"""
    a = {
        "verified": True,
        "lines": [
            {"ts": "2026-11-06T21:00:00", "actor": "仲間", "action": "talk", "detail": "通した"},
            {"ts": "2026-11-06T21:05:00", "actor": "?", "action": "talk", "detail": "断った: limit talks"},
        ],
    }
    rows = admin.audit_view(a)["rows"]
    assert rows[0] == ["2026-11-06 21:00", "仲間", "Claude と話した", "通した"]
    assert rows[1] == ["2026-11-06 21:05", "知らない人", "Claude と話した", "断った: limit talks"]


def test_overview_unknown_and_missing_keys():
    o = {"version": "1", "something_new": {"x": 1}, "keys": {"claude": True, "other": True}}
    rows = dict(map(tuple, admin.state_rows(o)))
    assert rows["外の鍵"] == "Claude 在る、Pl@ntNet 分からない"
    assert rows["受け付け"] == "分からない" and rows["操作の記録"] == "分からない"
    assert "今日の残り" not in rows and "入れる人" not in rows
    assert admin.attention(o) == []
    assert admin.state_rows({})[0] == ["サーバー", "動いています（版 ?）"]
    o2 = {"tokens": "壊れた", "remaining": None, "keys": [], "denied_today": "多い"}
    admin.state_rows(o2)
    assert admin.attention(o2, None) == []
    assert admin.token_rows([{"name": "x"}, "壊れた"], TODAY)[0]["state"] == "期限切れ"


# ---- 欄の決まり ----


@pytest.mark.parametrize(
    "name,scope,days,field",
    [
        ("", "member", "90", "name"),
        ("   ", "member", "90", "name"),
        ("x" * 65, "member", "90", "name"),
        ("a\nb", "member", "90", "name"),
        ("a", "owner", "90", "scope"),
        ("a", "", "90", "scope"),
        ("a", "member", "0", "days"),
        ("a", "member", "366", "days"),
        ("a", "member", "九十", "days"),
        ("a", "member", "abc", "days"),
        ("a", "member", "-1", "days"),
        ("a", "member", "", "days"),
        ("a", "member", "１０", "days"),
    ],
)
def test_token_form_refused(tmp_path, name, scope, days, field):
    errors, value = admin.check_token_form(name, scope, days)
    assert value is None and field in errors and errors[field]
    d, env = _dir(tmp_path)
    fake = Fake()
    with pytest.raises(inventory.Refused):
        admin.issue_token(d, _client(d, fake), name, scope, days, env=env)
    assert not any(c[0] == "POST" for c in fake.calls)  # 欄が駄目なら呼ばない


def test_token_form_ok_and_defaults():
    assert admin.check_token_form(" 山田 ", "buyer", "1") == (
        {},
        {"name": "山田", "scope": "buyer", "days": 1},
    )
    assert admin.check_token_form("a", "admin", "365")[1]["days"] == 365
    assert admin.default_days("member") == 90 and admin.default_days("admin") == 30


# ---- 二度押し、操作と kagi の記録 ----


def test_second_press():
    st: dict = {}
    calls = []
    for _ in range(2):
        if admin.second_press(st, ("revoke", "a")):
            calls.append("a")
    assert calls == ["a"] and st["confirm"] is None
    assert not admin.second_press(st, ("revoke", "a"))
    assert not admin.second_press(st, ("revoke", "b"))  # 別の物を押したら、やり直し
    assert admin.second_press(st, ("revoke", "b"))


def test_operations_logged_without_values(tmp_path):
    d, env = _dir(tmp_path)
    fake = Fake()
    c = _client(d, fake)
    code, msg = admin.issue_token(d, c, "c", "member", "90", "覚え", env=env)
    assert code == NEW and msg is None
    assert fake.calls[-1][3] == {"name": "c", "scope": "member", "days": 90, "note": "覚え"}
    assert admin.revoke_token(d, c, "a", env=env) == (1, None)
    assert admin.revoke_all_tokens(d, c, env=env) == (3, None)
    assert admin.set_paused(d, c, True, env=env) == (True, None)
    assert fake.calls[-1][3] == {"paused": True}
    assert admin.withdraw_record(d, c, "r1", env=env) is None
    assert admin.remove_connection(d, c, "r1", "c1", env=env) is None

    log = (d / "audit.log").read_text("utf-8")
    recs = [json.loads(x) for x in log.splitlines()]
    assert [r["action"] for r in recs] == [
        "seed.token.add",
        "seed.token.revoke",
        "seed.token.revoke_all",
        "seed.pause",
        "seed.withdraw",
        "seed.connection_remove",
    ]
    assert recs[0]["detail"] == "seed.example: c member 90 日"
    assert recs[1]["detail"] == "seed.example: a" and recs[2]["detail"].startswith("seed.example: 3 件")
    assert all(r["actor"] == "kakari" for r in recs)
    assert NEW not in log and TOKEN not in log
    n, fails = audit.verify(d / "audit.log", audit.find_key(env))
    assert n == 6 and fails == []


def test_operation_without_log_key_says_so(tmp_path):
    d, _env = _dir(tmp_path)
    code, msg = admin.issue_token(d, _client(d), "c", "admin", "30", env={})
    assert code == NEW and "署名の鍵が無い" in msg and NEW not in msg
    assert not (d / "audit.log").exists()


# ---- 在庫表と鍵の突き合わせ ----

INV = """
[[secret]]
id = "web1-plantnet-key"
kind = "token"
where = "web1:/etc/seed/env の PLANTNET_API_KEY"
holder = "h"
role = "草の名"
expires = 2026-11-20
rotate_days = 365
lost = "l"
leaked = "k"
"""


def test_server_keys_match(tmp_path):
    d, _env = _dir(tmp_path, INV)
    v = admin.server_keys_view(d, OVERVIEW, today=TODAY)
    assert v["seen"]
    # サーバーに Claude の鍵が在るのに在庫表に無い → 直す
    assert any(f.startswith("Claude（Anthropic）: サーバーに鍵が在るのに") for f in v["fixes"])
    # 在庫表に Pl@ntNet が在るのにサーバーに無い → 見るだけ
    assert any(n.startswith("Pl@ntNet: 在庫表に在るが、サーバーには無い") for n in v["notes"])
    assert any("期限が近い" in n for n in v["notes"])
    row = next(r for r in v["rows"] if r[2] == "web1-plantnet-key")
    assert row[1] == "無い" and row[6] == "2026-11-20"

    v = admin.server_keys_view(d, None, today=TODAY)
    assert not v["seen"] and v["fixes"] == []  # 尋ねていない時は、在る・無いで直す・見るだけを出さない
    assert all("期限" in n for n in v["notes"])

    (d / "inventory.toml").write_text(INV.replace("2026-11-20", "2026-01-01"), "utf-8")
    v = admin.server_keys_view(d, {"keys": {"claude": False, "plantnet": True}}, today=TODAY)
    assert any("期限が切れた" in f for f in v["fixes"])
    assert not any("サーバーに鍵が在るのに" in f for f in v["fixes"])


def _fields(**kw):
    f = {
        "id": "web1-anthropic-key",
        "where": "web1:/etc/seed/env の ANTHROPIC_API_KEY",
        "holder": "h",
        "role": "seed のサーバーが Claude を呼ぶ",
        "expires": "2027-02-01",
        "lost": "Console で作り直す",
        "leaked": "Console で失効",
    }
    f.update(kw)
    return f


@pytest.mark.parametrize(
    "key,value",
    [
        ("where", "sk-ant-api03-abcdefghijklmnop"),
        ("role", "AKIAEXAMPLEEXAMPLE12"),
        ("holder", "Zx9kQ2mP7vL4nR8tW1yB3c"),
        ("lost", "0123456789abcdef0123456789abcdef"),
        ("leaked", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
        ("id", "sk-ant-api03-abcdefghij"),
    ],
)
def test_secret_form_refuses_values(tmp_path, key, value):
    d, _env = _dir(tmp_path, INV)
    errors = admin.check_secret_form(_fields(**{key: value}))
    assert key in errors and "値" in errors[key] and value not in errors[key]
    with pytest.raises(inventory.Refused) as e:
        admin.add_secret(d, _fields(**{key: value}))
    assert value not in str(e.value)
    assert (d / "inventory.toml").read_text("utf-8") == INV


def test_secret_form_rules_and_add(tmp_path):
    d, _env = _dir(tmp_path, INV)
    assert "id" in admin.check_secret_form(_fields(id=""))
    assert "where" in admin.check_secret_form(_fields(where=" "))
    assert "expires" in admin.check_secret_form(_fields(expires="来年"))
    assert "id" in admin.check_secret_form(
        _fields(id="web1-plantnet-key"), inventory.load(d / "inventory.toml")
    )
    assert admin.check_secret_form(_fields(id="web1-plantnet-key-2026-11")) == {}
    assert admin.add_secret(d, _fields(), made=TODAY) == "web1-anthropic-key"
    secrets = inventory.load(d / "inventory.toml")
    s = secrets[-1]
    assert s["id"] == "web1-anthropic-key" and s["kind"] == "token" and s["expires"] == dt.date(2027, 2, 1)
    assert "value" not in s
    v = admin.server_keys_view(d, OVERVIEW, today=TODAY)
    assert not any(f.startswith("Claude") for f in v["fixes"])
    with pytest.raises(inventory.Refused, match="もう在庫表に在る"):
        admin.add_secret(d, _fields())


def test_admin_token_row_guidance(tmp_path):
    d, _env = _dir(tmp_path, INV)
    c = _client(d).conn
    assert not admin.has_admin_token_row(d)
    pre = admin.admin_token_prefill(d, c)
    assert pre["id"] == admin.ADMIN_TOKEN_ID and admin.CONN_FILE in pre["where"]
    assert TOKEN not in repr(pre)
    admin.add_secret(d, {**pre, "holder": "kakari", "expires": "2026-12-06"})
    assert admin.has_admin_token_row(d)
    assert TOKEN not in (d / "inventory.toml").read_text("utf-8")


# ---- 載せる前の記録（サーバーが預かった公開）----


def test_pending_rows_and_old_server(tmp_path):
    d, _env = _dir(tmp_path)
    data = admin.load_all(_client(d))
    assert admin.pending_rows(data["pending"]) == [
        {
            "id": "r2",
            "meta": "送った人 suzuki　届いた時 2026-11-08 09:30　写真 1 枚",
            "markdown": "---\nid: r2\n---\n",
            "photos": ["a.jpg"],
        }
    ]
    # 預かりの口の無い古いサーバーなら None（画面に出さない）。他の断りはそのまま
    old = Fake({("GET", "/api/admin/pending"): (404, {"detail": "Not Found"})})
    assert admin.load_all(_client(d, old))["pending"] is None
    bad = Fake({("GET", "/api/admin/pending"): (500, {"error": "x"})})
    with pytest.raises(admin.AdminError):
        admin.load_all(_client(d, bad))


def test_approve_and_return_logged_without_reason(tmp_path):
    d, env = _dir(tmp_path)
    fake = Fake()
    c = _client(d, fake)
    assert admin.approve_record(d, c, "r2", env=env) is None
    assert fake.calls[-1][:2] == ("POST", "/api/admin/pending/r2/approve")
    assert admin.return_record(d, c, "r2", "  人の名前が\n入っています ", env=env) is None
    assert fake.calls[-1][3] == {"reason": "人の名前が 入っています"}
    with pytest.raises(inventory.Refused):
        admin.return_record(d, c, "r2", "あ" * 201, env=env)
    recs = [json.loads(x) for x in (d / "audit.log").read_text("utf-8").splitlines()]
    assert [(r["action"], r["detail"]) for r in recs] == [
        ("seed.approve", "seed.example: 記録 r2 を載せた"),
        ("seed.return", "seed.example: 記録 r2 を戻した"),
    ]
    assert "人の名前" not in (d / "audit.log").read_text("utf-8")
    assert (
        admin.ACT["admin.approve"] == "管理: 記録を載せた"
        and admin.ACT["admin.return"] == "管理: 記録を戻した"
    )


def test_pending_photo_reads_jpeg_only(tmp_path):
    d, _env = _dir(tmp_path)
    seen = []

    def transport(method, url, headers, body):
        seen.append((method, url, headers))
        if url.endswith("/a.jpg"):
            return 200, b"\xff\xd8\xff\xe0jpeg"
        return 404, b'{"error": "not_found"}'

    c = _client(d, transport)
    assert c.pending_photo("r2", "a.jpg") == b"\xff\xd8\xff\xe0jpeg"
    assert seen[-1][1] == "https://seed.example/api/admin/pending/r2/photos/a.jpg"
    assert seen[-1][2]["x-seed-invite"] == TOKEN
    with pytest.raises(admin.AdminError) as e:
        c.pending_photo("r2", "b.jpg")
    assert TOKEN not in str(e.value)
