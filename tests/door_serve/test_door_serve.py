"""`digitalkey door-serve`(錠のドライバで動く扉)の試験。

疑似の Sesame(FakeSesameLock)で扉の HTTP のインターフェースを開き、HTTP で解錠と施錠をして、記録が残ることを確かめる。
実機にも Bluetooth にも触れない。ポートは 127.0.0.1 の空いたポート(--port 0)。
"""
import json
import re
import threading
import urllib.request
from pathlib import Path

import pytest

from digitalkey import door_serve
from digitalkey.entrance.keyring import DummyLock
from digitalkey_sesame.fake import FakeSesameLock

_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with _HTTP.open(req, timeout=10) as r:
        return json.loads(r.read())


def parse(tmp_path, *extra):
    return door_serve.make_parser().parse_args(["--fake", "--dir", str(tmp_path / "door"), "--port", "0", "--autolock", "0", *extra])


class Opened:
    """open_door で開いた扉。with で使い、出るときにポートと裏の処理を閉じる。"""

    def __init__(self, args, driver):
        self.srv, self.ctl, self.contacts = door_serve.open_door(args, driver)
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        self.contacts.close()


def test_http_unlock_and_lock_move_the_lock_and_leave_records(tmp_path):
    """R-CT-03 R-CT-04 R-CT-20: HTTP のインターフェースの解錠と施錠が、錠のドライバ(疑似の Sesame)を動かし、扉の記録に残る。"""
    lock = FakeSesameLock("door")
    with Opened(parse(tmp_path), lock) as door:
        assert call(door.base, "GET", "/state")["locked"] is True and lock.locked
        assert call(door.base, "POST", "/unlock", {"by": "Matter"})["result"] == "解錠"
        assert lock.locked is False and call(door.base, "GET", "/state")["locked"] is False
        assert call(door.base, "POST", "/lock", {"by": "Matter"})["result"] == "施錠"
        assert lock.locked is True
        rows = call(door.base, "GET", "/audit?since=0")
        ops = [(r["kind"], r["event"], r["result"]) for r in rows if r["kind"] == "操作"]
        assert ops == [("操作", "解錠", "成功"), ("操作", "施錠", "成功")]
        v = call(door.base, "GET", "/verify")
        assert v["ok"] and v["count"] == len(rows)
        assert door.contacts.outputs == ["unlock", "lock"] and door.contacts.failures == 0


def test_records_persist_across_restart_and_only_grow(tmp_path):
    """R-CT-14: 扉を立て直しても記録は前の続きで、前の記録は変わらない(追記だけ)。"""
    args = parse(tmp_path)
    with Opened(args, FakeSesameLock("door")) as door:
        call(door.base, "POST", "/unlock", {"by": "パネル"})
        before = call(door.base, "GET", "/audit?since=0")
    with Opened(args, FakeSesameLock("door")) as door:
        call(door.base, "POST", "/lock", {"by": "パネル"})
        after = call(door.base, "GET", "/audit?since=0")
        assert after[:len(before)] == before and len(after) > len(before)
        assert call(door.base, "GET", "/verify")["ok"]


def test_unreachable_lock_is_recorded_not_hidden(tmp_path):
    """R-CT-04: 錠に届かないとき、位置の記録(bolt_out)を動かさず、届かなかった回数を残す。扉は応答し続ける。"""
    lock = FakeSesameLock("door", reachable=False)
    with Opened(parse(tmp_path), lock) as door:
        call(door.base, "POST", "/unlock", {"by": "Matter"})
        assert door.contacts.failures == 1 and "見つかりません" in door.contacts.last_error
        assert door.contacts.bolt_out() is True and lock.locked
        assert call(door.base, "GET", "/verify")["ok"]


def test_arguments_match_the_controller_serve():
    """R-CT-04: door serve と同じ引数(--dir --name --host --port --two-person --autolock --power-policy --admin-key --trust --box)を受ける。"""
    ap = door_serve.make_parser()
    a = ap.parse_args(["--lock", "dummy", "--dir", "x", "--name", "genkan", "--host", "0.0.0.0", "--port", "9", "--two-person", "2",
                       "--autolock", "7", "--power-policy", "hold", "--admin-key", "ab" * 32, "--trust", "t.json", "--box", "b"])
    assert (a.dir, a.name, a.host, a.port, a.two_person, a.autolock, a.power_policy, a.trust, a.box) == (
        "x", "genkan", "0.0.0.0", 9, 2, 7.0, "hold", "t.json", "b")
    with pytest.raises(SystemExit):
        ap.parse_args([])                      # 錠の指定が無い
    with pytest.raises(SystemExit):
        ap.parse_args(["--fake", "--lock", "dummy"])   # 二つは指定できない


def test_lock_specs_go_through_the_registry(tmp_path):
    """R-CT-04: --lock は digitalkey.locks の登録簿から選ぶ。--fake は疑似の Sesame。入っていない種類は何を入れるかを添えて断る。"""
    ap = door_serve.make_parser()
    assert isinstance(door_serve.make_driver(ap.parse_args(["--lock", "dummy"])), DummyLock)
    assert isinstance(door_serve.make_driver(ap.parse_args(["--fake"])), FakeSesameLock)
    assert door_serve.make_driver(ap.parse_args(["--lock", "sesame:11111111-2222-3333-4444-555555555502"])).device_uuid.endswith("502")
    assert door_serve.main(["--lock", "nosuch:1", "--dir", str(tmp_path / "d"), "--port", "0"]) == 1


def test_digitalkey_command_routes_to_door_serve(capsys):
    """R-CT-04: `digitalkey --help` の使い方に door-serve があり、`digitalkey door-serve --help` が説明を出す。"""
    from digitalkey import cli
    assert cli.main([]) == 2
    assert "door-serve" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        cli.main(["door-serve", "--help"])
    assert e.value.code == 0 and "--lock" in capsys.readouterr().out


def test_controller_package_does_not_import_digitalkey():
    """R-CT-04: 扉のコントローラ(Apache-2.0)は digitalkey(AGPL)を読み込まない。向きは digitalkey から controller の一方向。"""
    root = Path(__file__).resolve().parents[2] / "controller" / "digitaldoor_controller"
    bad = [str(p) for p in root.rglob("*.py") if re.search(r"^\s*(from|import)\s+digitalkey\b", p.read_text(encoding="utf-8"), re.M)]
    assert bad == []
