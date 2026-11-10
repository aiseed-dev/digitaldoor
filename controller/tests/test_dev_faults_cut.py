"""開発の口(仕様書 5.5)の故障注入のうち、途中で切れる物の試験。仕様書 第 8 章の表の 31 と 32。

期待値は仕様書から決めている。道具(扉を立てる、HTTP、WebSocket の小さな相手)は test_dev_api から借りる。
"""
import json
import socket
import struct
import time

import pytest

from test_dev_api import WS, audit_rows, call, fault_records, new_ctl, record_text, request, serve

FAULT_KEYS = {"lock", "clock_offset_s", "failures", "actual_bolt_out"}     # GET /fault の欄(仕様書 5.5「今の注入」)


def until_cut(ws, timeout=3.0):
    """サーバーが切るまで読む。(届いた記録, 届いた制御の文の opcode の並び) を返す。切れなければ落とす。"""
    records, controls = [], []
    deadline = time.time() + timeout
    while True:
        left = deadline - time.time()
        assert left > 0, "サーバーが配信を切らない"
        ws.sock.settimeout(left)
        try:
            op, data = ws.frame()
        except ConnectionError:                 # 読み切った(b'')か、相手が断った
            return records, controls
        except (socket.timeout, TimeoutError):
            pytest.fail("サーバーが配信を切らない")
        if op == 0x1:
            records.append(json.loads(data.decode("utf-8")))
        else:
            controls.append(op)


def seqs(records):
    return [r["seq"] for r in records]


# ---- 31 錠への通信が途中で切れる ----

def test_fault_lock_cut_midway_unlock_then_normal_lock():
    """R-DV-03: 途中で切断を注入して解錠すると、錠の本当の位置は解錠側に動き、扉の知る位置は施錠側のまま、失敗の数が増える。正常に戻して施錠すると両方が施錠側に揃う。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (_, base):
        start = call(base, "GET", "/fault")
        assert start == {"lock": "正常", "clock_offset_s": 0, "failures": 0, "actual_bolt_out": True}

        got = call(base, "POST", "/fault", {"lock": "途中で切断"})
        assert FAULT_KEYS <= set(got) and got["lock"] == "途中で切断" and got["failures"] == 0
        assert got["actual_bolt_out"] is True
        assert call(base, "GET", "/fault")["lock"] == "途中で切断"
        inject = fault_records(audit_rows(base))
        assert len(inject) == 1 and "途中で切断" in record_text(inject[0])
        before = len(ctl.audit.entries)

        call(base, "POST", "/unlock", {"by": "Matter"})
        now = call(base, "GET", "/fault")
        assert set(now) == FAULT_KEYS
        assert now["actual_bolt_out"] is False                 # 命令は錠に届いて錠は動く
        assert ctl.contacts.actual_bolt_out is False
        state = call(base, "GET", "/state")
        assert state["bolt_out"] is True                       # 返事が届かないので扉の知る位置は動かない
        assert state["locked"] is False                        # 判断の側は命令を出した側として続ける
        assert now["failures"] == 1
        assert ctl.contacts.outputs == []                      # 届かなかった命令は outputs に足さない
        unlock_rows = [r for r in audit_rows(base)[before:] if r["event"] == "解錠"]
        assert unlock_rows and inject[0]["seq"] < unlock_rows[0]["seq"]

        back = call(base, "POST", "/fault", {"lock": "正常"})
        assert back["lock"] == "正常" and back["failures"] == 1
        assert back["actual_bolt_out"] is False                # 戻しただけでは揃わない
        assert len(fault_records(audit_rows(base))) == 2

        assert call(base, "POST", "/lock", {"by": "Matter"})["result"] == "施錠"
        now = call(base, "GET", "/fault")
        state = call(base, "GET", "/state")
        assert now["actual_bolt_out"] is True and state["bolt_out"] is True     # 返事まで届いた命令で揃う
        assert ctl.contacts.actual_bolt_out is True
        assert state["locked"] is True
        assert ctl.contacts.outputs == ["lock"]
        assert now["failures"] == 1


def test_fault_lock_cut_midway_on_lock_command():
    """R-DV-03: 途中で切断は施錠の命令にも効く。錠は施錠側に動き、扉の知る位置は解錠側のまま。正常に戻して解錠すると揃う。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (_, base):
        call(base, "POST", "/unlock", {"by": "Matter"})
        assert call(base, "GET", "/state")["bolt_out"] is False
        assert call(base, "GET", "/fault")["actual_bolt_out"] is False

        call(base, "POST", "/fault", {"lock": "途中で切断"})
        call(base, "POST", "/lock", {"by": "Matter"})
        now = call(base, "GET", "/fault")
        assert now["actual_bolt_out"] is True
        assert call(base, "GET", "/state")["bolt_out"] is False
        assert now["failures"] == 1
        assert ctl.contacts.outputs == ["unlock"]

        call(base, "POST", "/fault", {"lock": "正常"})
        call(base, "POST", "/unlock", {"by": "Matter"})
        now = call(base, "GET", "/fault")
        assert now["actual_bolt_out"] is False and call(base, "GET", "/state")["bolt_out"] is False
        assert ctl.contacts.outputs == ["unlock", "unlock"]
        assert now["failures"] == 1


def test_fault_lock_unresponsive_does_not_move_actual_bolt():
    """R-DV-03: 応答しないでは命令が錠に届かないので、錠の本当の位置も動かない(途中で切断との違い)。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (_, base):
        call(base, "POST", "/fault", {"lock": "応答しない"})
        call(base, "POST", "/unlock", {"by": "Matter"})
        now = call(base, "GET", "/fault")
        assert set(now) == FAULT_KEYS
        assert now["actual_bolt_out"] is True and call(base, "GET", "/state")["bolt_out"] is True
        assert now["failures"] == 1


# ---- 32 配信が途中で切れる ----

def test_fault_events_cut_then_resume_without_gap():
    """R-DV-03 R-DV-14: 配信を二つつなぎ、配信が途中で切れるを注入すると、二つとも close の文無しに切れ、cut は 2。受け取った最後の連番でつなぎ直すと抜けも重なりも無く届く。後からつなぐ配信は切られない。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (srv, base):
        call(base, "POST", "/event", {"tamper": "蓋"})
        call(base, "POST", "/unlock", {"by": "Matter"})
        n = len(audit_rows(base))
        a, b = WS(srv.server_port, since=0), WS(srv.server_port, since=0)
        conns = [a, b]
        try:
            got_a = a.records_until(n)
            got_b = b.records_until(n)
            assert seqs(got_a) == seqs(got_b) == list(range(1, n + 1))

            got = call(base, "POST", "/fault", {"events": "切断"})
            assert set(got) == FAULT_KEYS | {"cut"}
            assert got["cut"] == 2
            assert got["lock"] == "正常" and got["failures"] == 0
            inject = fault_records(audit_rows(base))
            assert len(inject) == 1 and "切断" in record_text(inject[0])

            for ws, mine in ((a, got_a), (b, got_b)):
                more, controls = until_cut(ws)
                assert 0x8 not in controls, "close の文を送らずに切る"
                mine += more                                   # 切る前に届いた物(注入の記録など)はあってよい

            assert set(call(base, "GET", "/fault")) == FAULT_KEYS   # 一度だけの指示で、今の注入には残らない

            # 切れている間の操作
            call(base, "POST", "/lock", {"by": "Matter"})
            call(base, "POST", "/event", {"door_open": True})
            call(base, "POST", "/event", {"door_open": False})

            # 後からつなぐ相手は普通に受けられる
            c = WS(srv.server_port, since=0)
            conns.append(c)
            got_c = c.records_until(len(audit_rows(base)))

            # 受け取った最後の連番でつなぎ直す
            ra = WS(srv.server_port, since=got_a[-1]["seq"])
            rb = WS(srv.server_port, since=got_b[-1]["seq"])
            conns += [ra, rb]
            n = len(audit_rows(base))
            got_a += ra.records_until(n)
            got_b += rb.records_until(n)

            # つなぎ直した後の操作も続けて届く
            call(base, "POST", "/unlock", {"by": "Matter"})
            call(base, "POST", "/event", {"tamper": "配線"})
            n = len(audit_rows(base))
            for ws, mine in ((ra, got_a), (rb, got_b), (c, got_c)):
                mine += ws.records_until(n)
                mine += ws.quiet(0.3)                          # 二度送られる物は無く、切れもしない

            rows = audit_rows(base)
            for mine in (got_a, got_b, got_c):
                assert seqs(mine) == list(range(1, len(rows) + 1))
                assert mine == rows
        finally:
            for ws in conns:
                ws.close()


def test_fault_events_cut_with_no_streams():
    """R-DV-03: つながっている配信が無いときの配信の切断は cut が 0。指示は記録に残り、ほかの注入は変わらない。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (_, base):
        got = call(base, "POST", "/fault", {"events": "切断"})
        assert set(got) == FAULT_KEYS | {"cut"}
        assert got["cut"] == 0
        assert {k: got[k] for k in FAULT_KEYS} == {"lock": "正常", "clock_offset_s": 0, "failures": 0,
                                                   "actual_bolt_out": True}
        inject = fault_records(audit_rows(base))
        assert len(inject) == 1 and "切断" in record_text(inject[0])
        assert call(base, "POST", "/fault", {"events": "切断"})["cut"] == 0


def test_fault_events_cut_and_lock_in_one_request():
    """R-DV-03: 一つの要求で、配信の切断と錠の途中で切断を一緒に指示できる。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (srv, base):
        ws = WS(srv.server_port, since=0)
        try:
            ws.records_until(len(audit_rows(base)))
            got = call(base, "POST", "/fault", {"lock": "途中で切断", "events": "切断"})
            assert got["cut"] == 1 and got["lock"] == "途中で切断"
            _, controls = until_cut(ws)
            assert 0x8 not in controls
            assert call(base, "GET", "/fault")["lock"] == "途中で切断"
        finally:
            ws.close()


@pytest.mark.parametrize("body", [
    {"events": "x"},
    {"lock": "x"},
    {"events": True},
    {"events": None},
    {"events": "切断", "foo": 1},
    {"events": "切断", "lock": "x"},
    {"lock": "途中で切断", "events": "x"},
])
def test_fault_cut_bad_request_changes_nothing(body):
    """R-DV-03: 配信の切断と錠の途中で切断について、許されない値か無い項目が一つでもあれば 400 で、何も変えず、記録にも残さない。配信も切らない。"""
    ctl = new_ctl()
    with serve(ctl, dev=True) as (srv, base):
        ws = WS(srv.server_port, since=0)
        try:
            got = ws.records_until(len(audit_rows(base)))
            before = call(base, "GET", "/fault")
            n_before = len(ctl.audit.entries)

            status, _, _ = request(base, "POST", "/fault", body)
            assert status == 400
            assert call(base, "GET", "/fault") == before
            assert ctl.contacts.fault_lock == "正常"
            assert len(ctl.audit.entries) == n_before              # 400 は記録に残さない
            assert fault_records(audit_rows(base)) == []

            call(base, "POST", "/unlock", {"by": "Matter"})         # 錠は普通に動く
            assert call(base, "GET", "/state")["bolt_out"] is False
            assert call(base, "GET", "/fault")["actual_bolt_out"] is False

            got += ws.records_until(len(audit_rows(base)))          # 配信は切れていない
            got += ws.quiet(0.2)
            assert seqs(got) == list(range(1, len(audit_rows(base)) + 1))
            ws.send(0x8, struct.pack("!H", 1000))
        finally:
            ws.close()
