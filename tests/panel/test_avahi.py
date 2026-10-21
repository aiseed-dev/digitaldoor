"""盤の起動と avahi-publish-service(Matter の広告に使う命令)の試験。

avahi を入れていない機械で、例外の山ではなく直し方を出して止まること。広告の子プロセスが盤の停止で残らないこと。
UDP 5541 は開かない(CircuitMatter を作らない)。avahi も入れない。
"""
import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from digitalkey import cli
from digitalkey.panel import main as panel_main
from digitalkey.panel import matter_node
from digitalkey.panel.door_client import Door


@pytest.fixture
def no_avahi(monkeypatch):
    monkeypatch.setattr(matter_node.shutil, "which", lambda name, *a, **k: None)


def test_node_stops_before_opening_matter_when_avahi_is_missing(no_avahi, monkeypatch, tmp_path):
    """R-CT-18: avahi-publish-service が無いとき、MatterNode は CircuitMatter(UDP 5541 を開く)を作る前に、直し方つきの例外で止まる。"""
    def boom(*a, **k):
        raise AssertionError("CircuitMatter を作ってしまった")
    monkeypatch.setattr(matter_node.cm, "CircuitMatter", boom)
    with pytest.raises(matter_node.AvahiMissing) as e:
        matter_node.MatterNode(Door(), tmp_path)
    assert "avahi-utils が入っていません" in str(e.value) and "sudo apt install avahi-utils" in str(e.value)


def test_panel_command_prints_the_fix_to_stderr_and_does_not_start(no_avahi, monkeypatch, capsys):
    """R-CT-18: `digitalkey panel` は、avahi-publish-service が無ければ標準エラーに直し方を出して 1 で終わる。盤は起動しない。"""
    import flet as ft

    def boom(*a, **k):
        raise AssertionError("盤を起動した")
    monkeypatch.setattr(ft, "run", boom)
    assert cli.main(["panel"]) == 1
    err = capsys.readouterr().err
    assert "avahi-utils が入っていません" in err and "sudo apt install avahi-utils" in err
    assert "Traceback" not in err


def test_panel_screen_shows_the_fix_instead_of_crashing(no_avahi, monkeypatch):
    """R-CT-18: 盤の画面(Flet)を直接開いたときも、例外で落ちず、画面に直し方を出す。"""
    monkeypatch.setattr(panel_main, "_node", None)
    added = []
    page = SimpleNamespace(add=lambda *c: added.extend(c), update=lambda: None, run_task=lambda *a: None)
    asyncio.run(panel_main.main(page))
    shown = " ".join(getattr(c, "value", "") or "" for c in added)
    assert "sudo apt install avahi-utils" in shown


def test_stop_ends_the_advertising_child_processes():
    """R-CT-18: 盤を止めると、広告の子プロセス(avahi-publish-service)も終わる。残さない。"""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    node = object.__new__(matter_node.MatterNode)
    node._stop = __import__("threading").Event()
    node.matter = SimpleNamespace(mdns_server=SimpleNamespace(active_services={"_matterc_udpX": child}))
    try:
        node.stop()
        assert child.poll() is not None and node._stop.is_set()
    finally:
        child.kill()


def test_stop_shared_node_stops_once_and_may_be_called_again(monkeypatch):
    """R-CT-18: 共有の Matter の機器は stop_shared_node で止まり、何度呼んでもよい(終了のときの後始末に使う)。"""
    stopped = []
    monkeypatch.setattr(panel_main, "_node", SimpleNamespace(stop=lambda: stopped.append(1)))
    panel_main.stop_shared_node()
    panel_main.stop_shared_node()
    assert stopped == [1] and panel_main._node is None


def test_daemon_warning_is_only_a_warning(monkeypatch, tmp_path):
    """R-CT-18: avahi-daemon が動いていないように見えるときは注意の文を返す(止めない)。動いていれば何も返さない。"""
    monkeypatch.setattr(matter_node, "AVAHI_SOCKET", tmp_path / "socket")
    assert "avahi-daemon" in matter_node.avahi_daemon_warning()
    (tmp_path / "socket").touch()
    assert matter_node.avahi_daemon_warning() is None
