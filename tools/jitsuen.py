#!/usr/bin/env python3
"""実演の道具 — デジタル錠の開け閉めは簡単で、鍵の管理は難しいことを、場面ごとに見せる。

    python tools/jitsuen.py --fake kantan                          # 疑似の Sesame で見せる
    python tools/jitsuen.py --lock sesame:<機器の UUID> check      # 実機の Sesame に届くか確かめる
    python tools/jitsuen.py --fake --yes all                       # Enter を待たずに全部

場面(手順は docs/使い方.md の「実演」):
  check    錠に届くことを確かめる(施錠の命令を一つ送る)
  kantan   開け閉めは部品の仕事。錠のドライバで直接、次に疑似の扉のインターフェース(POST /unlock)から開け閉めする
  sono-hi  その日の鍵。扉はネットワークが無くても、発行者の署名と期間を自分で確かめる
  kigen    止めたつもり。ネットワークの届かない扉では、台帳で失効しても、期限が来るまで開く
  keiro    経路が散る。同じ人の鍵を一つの経路で失効しても、別の経路で開く
  more     発行者の鍵が漏れたら。信頼の一覧を変えられるのは管理者の鍵だけ
  kiroku   記録で確かめる。記録の連鎖を検べ、一字の書き換えを見つける
  all      check を除く全部を順に(kantan から kiroku まで)

しくみ: 疑似の扉(digitaldoor_controller の Controller と HTTP のインターフェース)を同じプロセスの中で開き、接点に
digitalkey.contacts.LockDriverContacts を差す。錠は --fake の疑似の Sesame か、--lock の錠のドライバ
(digitalkey.locks の登録簿。`sesame:<UUID>` は BLE の実機)。一つの場面は一つの手順ずつ進み、Enter で次へ行く
(--yes なら待たない)。場面ごとに一時の置き場(扉の記録、鍵、台帳)を作り直し、終わったら消すので、どの場面からでも
何度でも始められる。時計は扉の時計を POST /time で進める。実時間は待たない。

結果は短い日本語で出す(「開きました」「拒みました: 期限の外」)。拒む理由の言葉は扉コントローラ仕様書 5.2a の言葉そのまま。
錠に届かないときは、例外を見せずに「Sesame に届きません。電源と Bluetooth を確かめてください」と出して止まる。
実機の Sesame の鍵(sesame-keys.json)は読まない。ここで作る鍵は一時の置き場の中だけで、終われば消える。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digitaldoor_controller import credentials, server as door_server  # noqa: E402
from digitaldoor_controller.audit import Audit, load_signer  # noqa: E402
from digitaldoor_controller.cli import read_key_file, write_key_file  # noqa: E402
from digitaldoor_controller.controller import Config, Controller  # noqa: E402
from digitaldoor_controller.slips import key_id  # noqa: E402
from digitalkey.contacts import LockDriverContacts  # noqa: E402
from digitalkey.entrance.keyring import Keyring  # noqa: E402
from digitalkey.entrance.ledger import Ledger  # noqa: E402

DOOR = "genkan"
JST = timezone(timedelta(hours=9))
DAY0 = datetime(2026, 10, 21, tzinfo=JST).timestamp()   # 実演の第 0 日(時計はここから進める)
UNREACHABLE = "Sesame に届きません。電源と Bluetooth を確かめてください"
SCENES = ("check", "kantan", "sono-hi", "kigen", "keiro", "more", "kiroku")
ALL = SCENES[1:]
ROUTE_LABELS = {"スマートフォン": "遠隔解錠", "暗証番号": "暗証番号"}   # 台帳の様式 鍵_発行 の経路の名前


class Unreachable(Exception):
    """錠に届かなかった。場面を止めて、利用者には UNREACHABLE だけを見せる。"""


class SimClock:
    """疑似の扉の時計。HTTP のインターフェースは POST のたびに時計を `time.time()` へ進めるので、インターフェースの側の `time` をこれに替える。"""

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    @staticmethod
    def sleep(seconds: float) -> None:
        import time
        time.sleep(seconds)


def at(day: int, hour: int, minute: int = 0) -> float:
    return DAY0 + day * 86400 + hour * 3600 + minute * 60


def hhmm(t: float) -> str:
    d = datetime.fromtimestamp(t, JST)
    return f"{d.hour}:{d.minute:02d}"


class UI:
    """手順を一つずつ見せる。`step` は手順を出して Enter を待ち、`result` が結果、`note` が補足。"""

    def __init__(self, yes: bool, ask=input) -> None:
        self.yes, self.ask, self.n = yes, ask, 0

    def title(self, name: str, shows: str) -> None:
        self.n = 0
        print(f"\n== {name} ==\n{shows}")

    def step(self, text: str) -> None:
        self.n += 1
        print(f"\n{self.n}. {text}")
        if not self.yes:
            try:
                self.ask("   [Enter で進む] ")
            except EOFError:
                pass

    @staticmethod
    def result(text: str) -> None:
        print(f"   → {text}")

    @staticmethod
    def note(text: str) -> None:
        print(f"   ・{text}")


class Stage:
    """一つの場面の置き場。一時のフォルダ、疑似の扉(同じプロセスの HTTP のインターフェース)、錠、鍵。with で使い、出るときに全部消す。"""

    def __init__(self, lock_factory, *, issuers: tuple[str, ...] = ("発行者",)) -> None:
        self.driver = lock_factory()   # 指定が合わなければここで止まる(置き場はまだ作らない)
        self.dir = Path(tempfile.mkdtemp(prefix="jitsuen-"))
        self.clock = SimClock(at(0, 8))
        self.contacts = LockDriverContacts(self.driver)
        (self.dir / "keys").mkdir()
        self.signers = {name: write_key_file(self.dir / "keys" / f"{i}.key") for i, name in enumerate(("管理者", *issuers))}
        self.admin = self.signers["管理者"]
        trust = {key_id(s.public_key()): {"public_key": s.public_key().hex(), "doors": [DOOR]}
                 for n, s in self.signers.items() if n != "管理者"}
        self.vault = self.dir / "door"
        self.audit = Audit(load_signer(self.vault), self.vault / "audit.jsonl")
        # 時間を進める実演なので、時計の大きな動きを「異常」にしない(一年以上)
        self.ctl = Controller(self.contacts, self.audit, Config(autolock_s=0, time_jump_s=400 * 86400.0), name=DOOR,
                              admin_key=self.admin.public_key(), trust=trust)
        self._saved_time = door_server.time
        door_server.time = self.clock
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), door_server.make_handler(self.ctl, threading.Lock()))
        threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self._http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.set_clock(self.clock.now)

    def __enter__(self) -> "Stage":
        return self

    def __exit__(self, *exc) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        door_server.time = self._saved_time
        self.contacts.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- 疑似の扉のインターフェース ----
    def http(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        with self._http.open(req, timeout=self.contacts.timeout_s + 30) as r:
            return json.loads(r.read())

    def set_clock(self, t: float) -> None:
        self.clock.now = t
        self.http("POST", "/time", {"now": t})

    def _sent(self, call):
        """call() の間に錠へ届かなかった命令があれば Unreachable。"""
        before = self.contacts.failures
        out = call()
        if self.contacts.failures > before:
            raise Unreachable()
        return out

    def _refusal(self, since: int) -> str:
        rows = self.http("GET", f"/audit?since={since}")
        reasons = [e["result"] for e in rows if e["kind"] == "認証" and e["event"] == "拒否"]
        return reasons[-1] if reasons else "拒否"

    def present(self, credential: dict) -> str:
        """資格情報を POST /credential で提示し、結果を短い日本語にする。"""
        since = self.http("GET", "/state")["audit_seq"]
        r = self._sent(lambda: self.http("POST", "/credential", {"credential": credential, "route": "遠隔"}))["result"]
        return "開きました" if r == "解錠" else f"拒みました: {self._refusal(since)}" if r == "拒否" else r

    def unlock(self) -> str:
        """疑似の扉のインターフェース POST /unlock(同じコンピューターの Matter の橋が使うインターフェース)。"""
        r = self._sent(lambda: self.http("POST", "/unlock", {"by": "jitsuen"}))["result"]
        return "開きました" if r == "解錠" else f"拒みました: {r}"

    def lock(self) -> str:
        r = self._sent(lambda: self.http("POST", "/lock", {"by": "jitsuen"}))["result"]
        return "閉まりました" if r == "施錠" else f"拒みました: {r}"

    def drive(self, action: str) -> str:
        """錠のドライバを直接動かす(扉を通さない)。"""
        try:
            self.contacts.run(getattr(self.driver, action)())
        except Exception:
            raise Unreachable() from None
        return "開きました" if action == "unlock" else "閉まりました"

    # ---- 鍵 ----
    def issue(self, who: str, *, cid: str, subject: str, start: float, end: float, once: bool = False) -> dict:
        return credentials.issue(self.signers[who], cred_id=cid, subject=subject, doors=[DOOR],
                                 not_before=int(start), not_after=int(end), once=once)

    def issuer_id(self, who: str) -> str:
        return key_id(self.signers[who].public_key())

    def trust_change(self, action: str, who: str, rid: str) -> str:
        """管理者の鍵で、発行者 who を信頼の一覧から外す(remove)か足す(add)。受理か拒否の理由を返す。"""
        admin = read_key_file(self.dir / "keys" / "0.key")
        req = credentials.trust_change(admin, door=DOOR, action=action, issuer_public_key=self.signers[who].public_key(),
                                       doors=[DOOR] if action == "add" else None, req_id=rid)
        since = self.http("GET", "/state")["audit_seq"]
        r = self.http("POST", "/trust", {"request": req})["result"]
        if r == "受理":
            return "受理"
        rows = self.http("GET", f"/audit?since={since}")
        return "拒否: " + ([e["detail"] for e in rows if e["kind"] == "設定"] or ["?"])[-1]

    def ledger(self) -> Keyring:
        """受け入れの台帳(ネットワークの向こうにある物)。鍵の操作は同じ錠のドライバで動く。"""
        return Keyring(Ledger(self.dir / "daicho"), {DOOR: self.driver})


# ---- 場面 ----
def scene_check(st: Stage, ui: UI) -> None:
    ui.title("check 錠に届くこと", "施錠の命令を一つ送り、錠に届くかを確かめます。")
    ui.step("錠に施錠の命令を送ります")
    st.drive("lock")
    ui.result("錠に届きました")


def scene_kantan(st: Stage, ui: UI) -> None:
    ui.title("kantan 開け閉め", "開け閉めは部品の仕事です。数行で書け、AI でも書けます。")
    ui.note('lock = make_lock("genkan", "sesame:<UUID>")    # 錠のドライバを登録簿から選ぶ')
    ui.note("await lock.unlock()   /   await lock.lock()")
    ui.step("錠のドライバで直接、開けます")
    ui.result(st.drive("unlock"))
    ui.step("同じく直接、閉めます")
    ui.result(st.drive("lock"))
    ui.step("疑似の扉のインターフェースから開けます(POST /unlock)")
    ui.result(st.unlock())
    ui.step("疑似の扉のインターフェースから閉めます(POST /lock)")
    ui.result(st.lock())


def scene_sono_hi(st: Stage, ui: UI) -> None:
    ui.title("sono-hi その日の鍵", "扉はネットワークが無くても、発行者の署名と期間を自分で確かめます。")
    ui.step("清掃の人に 9〜12 時の資格情報を出します")
    cred = st.issue("発行者", cid="seisou-1021", subject="seisou-001", start=at(0, 9), end=at(0, 12))
    ui.note(f"発行者の識別 {st.issuer_id('発行者')}。署名は発行者の鍵、扉は公開鍵だけを持つ")
    ui.result("資格情報を出しました(9:00〜12:00)")
    ui.step("時計を 10 時にして提示します")
    st.set_clock(at(0, 10))
    ui.result(st.present(cred))
    ui.note(f"扉を閉め直します({st.lock()})")
    ui.step("時計を 13 時にして、同じ資格情報を提示します")
    st.set_clock(at(0, 13))
    ui.result(st.present(cred))


def scene_kigen(st: Stage, ui: UI) -> None:
    ui.title("kigen 止めたつもり", "ネットワークの届かない扉では、失効は有効期限でしか効きません。")
    keyring = st.ledger()
    ui.step("辞める人に、30 日の資格情報を出してあります")
    month = st.issue("発行者", cid="yamada-30d", subject="yamada-taro", start=at(0, 8), end=at(30, 8))
    slip = keyring.issue(subject="yamada-taro", lock_id=DOOR, start="2026-10-21T08:00", end="2026-11-20T08:00")
    ui.result("資格情報を出しました(30 日)")
    ui.step("台帳で失効します")
    keyring.revoke(slip.id, "退職")
    ok, _ = keyring.is_valid(slip.id, datetime(2026, 10, 22, 10))
    ui.note("台帳は扉に届きません。扉は失効を知りません")
    ui.result("台帳では失効しています" if not ok else "失効できていません")
    ui.step("時計を 10 日進めて提示します")
    st.set_clock(at(10, 10))
    ui.result(st.present(month))
    ui.note(f"扉を閉め直します({st.lock()})")
    ui.step("1 日の資格情報を毎日出し直す形では、今日の分だけを出します")
    day = st.issue("発行者", cid="yamada-1d", subject="yamada-taro", start=at(10, 10), end=at(11, 10))
    ui.note("辞めたので、あすの分は出しません")
    ui.result("資格情報を出しました(1 日)")
    ui.step("翌日に提示します")
    st.set_clock(at(11, 11))
    ui.result(st.present(day))


def scene_keiro(st: Stage, ui: UI) -> None:
    ui.title("keiro 経路が散る", "一人の鍵が経路ごとに散ると、止め漏れが起きます。")
    keyring = st.ledger()
    keys = {}
    ui.step("同じ人に、スマートフォンと暗証番号の二つの経路の鍵を出します")
    for label, route in ROUTE_LABELS.items():
        keys[label] = keyring.issue(subject="sato-hanako", lock_id=DOOR, start="2026-10-21T00:00",
                                    end="2026-10-31T00:00", route=route)
    ui.note("台帳の経路の名前は様式のとおり(スマートフォンは「遠隔解錠」)")
    ui.result("鍵を二つ出しました(スマートフォン、暗証番号)")
    ui.step("スマートフォンの鍵だけ失効します")
    keyring.revoke(keys["スマートフォン"].id, "端末の紛失")
    ui.result("スマートフォンの鍵を失効しました")
    now = datetime(2026, 10, 22, 10)

    def opened(label: str) -> str:
        slip = st.contacts.run(keyring.open(keys[label].id, at=now))
        if slip["結果"] == "失敗":
            raise Unreachable()
        return "開きました" if slip["結果"] == "成功" else f"拒みました: {slip['理由']}"

    ui.step("スマートフォンの鍵で解錠します")
    ui.result(opened("スマートフォン"))
    ui.step("暗証番号の経路で解錠します")
    ui.result(opened("暗証番号"))
    ui.note("暗証番号の鍵は止めていません。この人のすべての経路を一度に止める作りは、まだありません")
    ui.note(f"錠を閉め直します({st.drive('lock')})")


def scene_more(st: Stage, ui: UI) -> None:
    ui.title("more 発行者の鍵が漏れたら", "発行者の鍵を持つ者は、誰にでも鍵を出せます。信頼を変えられるのは管理者の鍵だけです。")
    ui.step("発行者 A の鍵が漏れました。持ち出した人が、知らない人の資格情報を作ります")
    st.set_clock(at(0, 10))
    stranger = st.issue("発行者A", cid="x-1", subject="unknown-001", start=at(0, 9), end=at(0, 12))
    ui.result("資格情報を作りました(発行者 A の鍵、知らない人)")
    ui.step("その資格情報を提示します")
    ui.result(st.present(stranger))
    ui.note("署名と期間が正しいので、扉には本物と見分けがつきません")
    ui.note(f"扉を閉め直します({st.lock()})")
    ui.step("管理者の鍵で、発行者 A を信頼の一覧から外します")
    r = st.trust_change("remove", "発行者A", "r-001")
    ui.result("信頼の一覧から外しました" if r == "受理" else f"拒みました: {r}")
    ui.step("同じ資格情報をもう一度提示します")
    ui.result(st.present(stranger))
    ui.step("発行者 B の正しい資格情報を、一字だけ書き換えて提示します")
    good = st.issue("発行者B", cid="b-1", subject="seisou-001", start=at(0, 9), end=at(0, 12))
    tampered = {"body": good["body"].replace("seisou-001", "seisou-002", 1), "sig": good["sig"]}
    ui.result(st.present(tampered))


def scene_kiroku(st: Stage, ui: UI) -> None:
    ui.title("kiroku 記録で確かめる", "起きたことは、改ざんの分かる記録に残ります。")
    ui.step("資格情報を出し、開く提示と拒まれる提示を行って、記録を作ります")
    cred = st.issue("発行者", cid="seisou-1021", subject="seisou-001", start=at(0, 9), end=at(0, 12))
    st.set_clock(at(0, 10))
    st.present(cred)
    st.lock()
    st.set_clock(at(0, 13))
    st.present(cred)
    ui.result("記録ができました")
    ui.step("記録の連鎖を検べます")
    v = st.http("GET", "/verify")
    ui.note(f"記録 {v['count']} 件。連番、前の記録のハッシュ、Ed25519 の署名を順に見る")
    ui.result("連鎖は正しい" if v["ok"] else f"連鎖が壊れています: {v['count']} 番「{v['why']}」")
    ui.step("記録のコピーを一字だけ書き換えて検べます")
    copy = st.dir / "copy"
    shutil.copytree(st.vault, copy)
    text = (copy / "audit.jsonl").read_text(encoding="utf-8")
    (copy / "audit.jsonl").write_text(text.replace('"result": "期限の外"', '"result": "期限の内"', 1), encoding="utf-8")
    ok, n, why = Audit(load_signer(copy), copy / "audit.jsonl").verify()
    ui.note("拒んだ理由の「期限の外」を「期限の内」に書き換えたコピー")
    ui.result("連鎖は正しい" if ok else f"壊れています: {n} 番「{why}」")
    ok, n, why = st.audit.verify()
    ui.note(f"もとの記録は{'正しいままです' if ok else '壊れています'}")


RUN = {"check": (scene_check, ("発行者",)), "kantan": (scene_kantan, ("発行者",)),
       "sono-hi": (scene_sono_hi, ("発行者",)), "kigen": (scene_kigen, ("発行者",)),
       "keiro": (scene_keiro, ("発行者",)), "more": (scene_more, ("発行者A", "発行者B")),
       "kiroku": (scene_kiroku, ("発行者",))}


def lock_factory_from(args) -> object:
    """--fake なら疑似の Sesame、--lock なら digitalkey.locks の登録簿から。"""
    if args.fake:
        from digitalkey_sesame.fake import FakeSesameLock
        return lambda: FakeSesameLock(DOOR)
    from digitalkey.locks import make_lock
    return lambda: make_lock(DOOR, args.lock)


def main(argv=None, *, lock_factory=None, ask=input) -> int:
    ap = argparse.ArgumentParser(
        prog="jitsuen.py", description="実演の道具。デジタル錠の開け閉めは簡単で、鍵の管理は難しいことを、場面ごとに見せる",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", choices=[*SCENES, "all"], help="場面。all は kantan から kiroku まで順に")
    ap.add_argument("--fake", action="store_true", help="疑似の Sesame で動かす(実機も Bluetooth も使わない)")
    ap.add_argument("--lock", metavar="SPEC", help="錠の指定。sesame:<機器の UUID>(BLE の実機)、dummy など。digitalkey.locks の登録簿の書き方")
    ap.add_argument("--yes", action="store_true", help="Enter を待たずに進める")
    ap.add_argument("--debug", action="store_true", help="うまくいかないとき、例外の詳細を出す")
    args = ap.parse_args(argv)
    if args.fake == bool(args.lock):
        ap.error("--fake か --lock sesame:<機器の UUID> のどちらか一つを指定してください")
    ui = UI(args.yes, ask)
    try:
        factory = lock_factory or lock_factory_from(args)
        for name in (ALL if args.scene == "all" else (args.scene,)):
            scene, issuers = RUN[name]
            with Stage(factory, issuers=issuers) as stage:
                scene(stage, ui)
    except Unreachable:
        ui.result(UNREACHABLE)
        return 1
    except Exception as e:  # 利用者には例外を見せない。詳細は --debug
        if args.debug:
            traceback.print_exc()
        print(f"\nうまくいきませんでした: {e}", file=sys.stderr)
        return 1
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
