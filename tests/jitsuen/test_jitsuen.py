"""実演の道具 tools/jitsuen.py を、疑似の Sesame(--fake)と --yes で場面ごとに回し、結果の並びが
docs/使い方.md の「実演」の表のとおりになることを確かめる。

実機にも Bluetooth にも触れず、ネットワークにもつながない(扉のインターフェースは 127.0.0.1 の空いたポート)。
結果の行は「   → 」で始まる。拒む理由の言葉は扉コントローラ仕様書 5.2a の言葉そのまま。
"""
import importlib.util
import re
import socket
from pathlib import Path

import pytest

from digitaldoor_controller import server as door_server
from digitalkey_sesame import protocol as p
from digitalkey_sesame.fake import FakeSesameLock

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "jitsuen.py"
UNREACHABLE = "Sesame に届きません。電源と Bluetooth を確かめてください"


@pytest.fixture(scope="module")
def jit():
    spec = importlib.util.spec_from_file_location("jitsuen", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def results(out: str) -> list[str]:
    return [line[5:] for line in out.splitlines() if line.startswith("   → ")]


def play(jit, capsys, name, *, reachable=True):
    """場面を一つ回し、(結果の並び、置き場、疑似の Sesame の錠)を返す。置き場の記録は場面の後も読める。"""
    lock = FakeSesameLock("genkan", reachable=reachable)
    scene, issuers = jit.RUN[name]
    with jit.Stage(lambda: lock, issuers=issuers) as stage:
        scene(stage, jit.UI(True))
    return results(capsys.readouterr().out), stage, lock


def moved(lock, item) -> int:
    return [i for i, _ in lock.fake.received].count(item)


def test_check_reaches_the_lock(jit, capsys):
    """R-CT-03: check は錠のドライバに施錠の命令を送り、届いたことを短く出す。"""
    out, _, lock = play(jit, capsys, "check")
    assert out == ["錠に届きました"]
    assert moved(lock, p.ItemCode.LOCK) == 1 and lock.locked


def test_kantan_opens_and_closes_directly_and_through_the_door(jit, capsys):
    """R-CT-03: kantan は錠のドライバで直接、次に疑似の扉のインターフェース(POST /unlock、/lock)から、開け閉めする。
    扉の判断は接点を通して同じ錠を動かし、銘柄(疑似の Sesame)を知らない。"""
    out, stage, lock = play(jit, capsys, "kantan")
    assert out == ["開きました", "閉まりました", "開きました", "閉まりました"]
    assert moved(lock, p.ItemCode.UNLOCK) == 2 and moved(lock, p.ItemCode.LOCK) == 2 and lock.locked
    assert stage.contacts.outputs == ["unlock", "lock"]  # 扉を通した分だけ接点に届く(最初の二つは直接)
    ops = [(e.event, e.route) for e in stage.audit.entries if e.kind == "操作"]
    assert ops == [("解錠", "系統2"), ("施錠", "")]


def test_sono_hi_opens_inside_the_period_and_refuses_outside(jit, capsys):
    """R-AC-02: sono-hi は 9〜12 時の資格情報を、10 時には開け、13 時には「期限の外」で拒む。拒んだときは錠を動かさない。"""
    out, stage, lock = play(jit, capsys, "sono-hi")
    assert out == ["資格情報を出しました(9:00〜12:00)", "開きました", "拒みました: 期限の外"]
    assert moved(lock, p.ItemCode.UNLOCK) == 1 and lock.locked  # 開けたのは一回で、扉を閉め直してある
    auth = [(e.event, e.result) for e in stage.audit.entries if e.kind == "認証"]
    assert auth == [("成功", ""), ("拒否", "期限の外")]


def test_kigen_revocation_in_the_ledger_does_not_reach_the_door(jit, capsys):
    """R-AC-02 R-AC-03: kigen は、台帳で失効してもネットワークの届かない扉は開き、期限だけが効くことを見せる。
    1 日の資格情報は、翌日に「期限の外」で拒まれる(不通のときは期限で効く側)。
    失効の通知を扉へ届ける側は、この場面が「届かない」ことを見せるだけで、満たしたとは確かめない。"""
    out, stage, lock = play(jit, capsys, "kigen")
    assert out == ["資格情報を出しました(30 日)", "台帳では失効しています", "開きました",
                   "資格情報を出しました(1 日)", "拒みました: 期限の外"]
    assert moved(lock, p.ItemCode.UNLOCK) == 1
    auth = [(e.event, e.result, e.subject) for e in stage.audit.entries if e.kind == "認証"]
    assert auth == [("成功", "", "yamada-taro"), ("拒否", "期限の外", "yamada-taro")]


def test_keiro_other_route_still_opens(jit, capsys):
    """実演 keiro。要求を満たしていない状態を見せる場面なので、満たす要求の番号は付けない。
    同じ人の鍵のうちスマートフォンだけ失効し、暗証番号の鍵は開く。台帳には両方の経路と失効が残る。"""
    out, stage, lock = play(jit, capsys, "keiro")
    assert out == ["鍵を二つ出しました(スマートフォン、暗証番号)", "スマートフォンの鍵を失効しました",
                   "拒みました: 失効しています", "開きました"]
    assert moved(lock, p.ItemCode.UNLOCK) == 1 and lock.locked
    # 台帳(伝票)は場面の後に消えるので、同じ並びをもう一度組んで見る
    scene, issuers = jit.RUN["keiro"]
    with jit.Stage(lambda: FakeSesameLock("genkan"), issuers=issuers) as st:
        scene(st, jit.UI(True))
        ledger = st.ledger().ledger
        capsys.readouterr()
        assert sorted(s["経路"] for s in ledger.select("鍵_発行")) == ["暗証番号", "遠隔解錠"]
        assert [s["理由"] for s in ledger.select("鍵_失効")] == ["端末の紛失"]
        assert [(s["結果"], s["理由"]) for s in ledger.select("鍵_操作")] == [("拒否", "失効しています"), ("成功", "")]
        assert ledger.verify_chain()[0]


def test_more_trust_change_by_the_admin_key_only(jit, capsys):
    """R-AC-12: more は、漏れた発行者の鍵の資格情報が開き、管理者の鍵でその発行者を外すと「知らない発行者」で拒まれ、
    別の発行者の資格情報を一字書き換えると「署名が合わない」で拒まれる。信頼の変更は前と新しい一覧が設定の記録に残る。"""
    out, stage, lock = play(jit, capsys, "more")
    assert out == ["資格情報を作りました(発行者 A の鍵、知らない人)", "開きました", "信頼の一覧から外しました",
                   "拒みました: 知らない発行者", "拒みました: 署名が合わない"]
    assert moved(lock, p.ItemCode.UNLOCK) == 1
    changes = [e for e in stage.audit.entries if e.kind == "設定" and e.event == "信頼する発行者"]
    assert [e.result for e in changes] == ["受理"]
    a, b = stage.issuer_id("発行者A"), stage.issuer_id("発行者B")
    assert a in changes[0].detail.split(" 新 ")[0] and a not in changes[0].detail.split(" 新 ")[1]
    assert b in changes[0].detail.split(" 新 ")[1]
    assert list(stage.ctl.trust) == [b]


def test_kiroku_finds_the_one_character_change(jit, capsys):
    """R-CT-12: kiroku は記録の連鎖を検べて「連鎖は正しい」と出し、コピーの一字を書き換えると、書き換えた記録の番号と
    「内容が変わっています」を出す。もとの記録は正しいまま。"""
    out, stage, _ = play(jit, capsys, "kiroku")
    assert out[:2] == ["記録ができました", "連鎖は正しい"]
    refused = next(e.seq for e in stage.audit.entries if e.kind == "認証" and e.event == "拒否")
    assert out[2] == f"壊れています: {refused} 番「内容が変わっています」"
    assert stage.audit.verify()[0]


def test_unreachable_lock_shows_one_message_and_no_traceback(jit, capsys):
    """R-CT-04: 錠に届かないときは、例外を見せず、一つの短い文を出して止まる。扉のインターフェースから開けるとき、
    直接のとき、鍵の台帳から開けるとき、どれでも同じ。"""
    for scene, steps in (("kantan", 1), ("sono-hi", 2), ("keiro", 4)):
        rc = jit.main(["--fake", "--yes", scene], lock_factory=lambda: FakeSesameLock("genkan", reachable=False))
        cap = capsys.readouterr()
        assert rc == 1, scene
        assert results(cap.out)[-1] == UNREACHABLE
        assert "Traceback" not in cap.out + cap.err and "RuntimeError" not in cap.out + cap.err
        assert len(re.findall(r"^\d+\. ", cap.out, re.M)) == steps, scene


def test_all_runs_every_scene_in_order(jit, capsys):
    """R-AC-02 R-AC-12: all は kantan から kiroku まで順に回し、結果の並びは場面ごとの並びをつないだ物になる。"""
    lock = FakeSesameLock("genkan")
    rc = jit.main(["--fake", "--yes", "all"], lock_factory=lambda: lock)
    whole = results(capsys.readouterr().out)
    assert rc == 0
    parts = []
    for name in jit.ALL:
        parts += play(jit, capsys, name)[0]
    assert whole[:4] == ["開きました", "閉まりました", "開きました", "閉まりました"]
    assert len(whole) == len(parts)
    assert [r for r in whole if not r.startswith("壊れています")] == [r for r in parts if not r.startswith("壊れています")]


def test_each_scene_starts_from_a_clean_stage(jit, capsys):
    """場面は一時の置き場を作り直す。同じ場面を二度回しても同じ結果になり、置き場とポートは残らない。"""
    first, stage1, _ = play(jit, capsys, "sono-hi")
    second, stage2, _ = play(jit, capsys, "sono-hi")
    assert first == second
    assert stage1.dir != stage2.dir and not stage1.dir.exists() and not stage2.dir.exists()
    assert door_server.time.__name__ == "time"  # インターフェースの側の時計を、場面の後で実時間に戻している
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", stage1.srv.server_port), timeout=0.5)


def test_scene_and_lock_arguments(jit, capsys):
    """--fake か --lock のどちらか一つが要る。場面の名前は決まった物だけ。"""
    with pytest.raises(SystemExit):
        jit.main(["--yes", "kantan"])
    with pytest.raises(SystemExit):
        jit.main(["--fake", "--lock", "dummy", "--yes", "kantan"])
    with pytest.raises(SystemExit):
        jit.main(["--fake", "--yes", "nosuch"])
    capsys.readouterr()


def test_unknown_lock_kind_is_a_short_message(jit, capsys):
    """R-CT-04: 入っていない錠の種類を指定しても、例外を見せず、何を入れるかの短い文で止まる。"""
    rc = jit.main(["--lock", "nosuch:1", "--yes", "check"])
    cap = capsys.readouterr()
    assert rc == 1 and "nosuch" in cap.err and "Traceback" not in cap.out + cap.err


def test_enter_is_awaited_without_yes(jit, capsys):
    """手順ごとに Enter を待つ(--yes なら待たない)。"""
    asked = []
    rc = jit.main(["--fake", "kantan"], lock_factory=lambda: FakeSesameLock("genkan"), ask=lambda prompt: asked.append(prompt))
    capsys.readouterr()
    assert rc == 0 and len(asked) == 4
