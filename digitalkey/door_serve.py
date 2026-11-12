"""錠のドライバで動く扉 — `digitalkey door-serve`。

扉のコントローラ(digitaldoor_controller)の HTTP のインターフェース(/state /lock /unlock /event /audit /verify ほか)を、
模擬の接点ではなく、錠のドライバ(`digitalkey.locks` の登録簿)につないだ接点で開く。
パネル(`digitalkey panel`)や Matter の橋は、このインターフェースを叩いて本物の錠を動かす。

  digitalkey door-serve --lock sesame:<機器のUUID>      実機の Sesame(BLE 直結。鍵は `digitalkey sesame import` か `register` で置いておく)
  digitalkey door-serve --fake                          疑似の Sesame(実機にも Bluetooth にも触れない)
  digitalkey door-serve --lock dummy                    配線確認用の錠(何も動かさない)

引数は `digitaldoor-controller serve` と揃えてある(--dir --name --host --port --two-person --autolock --power-policy
--admin-key --trust --revocation-max-age --box)。違いは、錠の指定(--lock か --fake)と、錠に一回の命令を待つ秒数(--timeout)だけである。

しくみ: `Controller` に `digitalkey.contacts.LockDriverContacts` を差す。tools/jitsuen.py と同じ組み立てを、
実演の場面から切り離して常駐させたもの。錠の銘柄は接点の下に隠れ、扉の判断は変わらない(扉コントローラ仕様書 R-CT-03、R-CT-04)。
この命令は digitalkey(AGPL)の側にある。扉のコントローラ(Apache-2.0)は digitalkey を読み込まない。

- 錠の位置は、起動のとき施錠側として始まる。実機を読みには行かない。実機が解錠のままなら、先に施錠してから立てる。
- 錠に届かなかった命令は、扉の判断を止めず、錠の位置の記録を動かさない。`LockDriverContacts.failures` と標準エラーの行に残る。
- 実機の鍵(sesame-keys.json)はドライバが開く。この命令は鍵を読まず、出さない。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from digitaldoor_controller import server as door_server
from digitaldoor_controller.audit import Audit, load_signer
from digitaldoor_controller.controller import Config, Controller
from digitaldoor_controller.slips import export

from .contacts import LockDriverContacts


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="digitalkey door-serve", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lock = ap.add_mutually_exclusive_group(required=True)
    lock.add_argument("--lock", metavar="SPEC",
                      help="錠のドライバの指定 <種類>:<引数>(例: sesame:<機器のUUID>)。使える種類は pip で入れたドライバが決める。dummy は配線確認用")
    lock.add_argument("--fake", action="store_true", help="疑似の Sesame で動かす(digitalkey-sesame が要る。実機にも Bluetooth にも触れない)")
    ap.add_argument("--timeout", type=float, default=LockDriverContacts.timeout_s,
                    help="錠への一回の施錠・解錠を待つ秒数。実機の BLE は探して、つないで、送るので長め(既定 %(default)s)")
    # ---- digitaldoor-controller serve と揃える引数 ----
    ap.add_argument("--dir", default=os.environ.get("DIGITALKEY_DOOR_DIR", "vault"), help="記録と鍵の置き場")
    ap.add_argument("--name", default="door", help="扉の名前。伝票のファイル名と door の欄に入る")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--two-person", type=int, default=1)
    ap.add_argument("--autolock", type=float, default=5.0)
    ap.add_argument("--power-policy", choices=["release", "hold"], default="release")
    ap.add_argument("--admin-key", metavar="HEX",
                    help="管理者の公開鍵(16 進)。据付のときに入れる。この鍵で署名した要求だけが信頼の一覧を変えられる")
    ap.add_argument("--trust", metavar="FILE",
                    help="信頼する発行者の一覧(JSON。{発行者の識別: {\"public_key\": 16 進, \"doors\": [扉の名前, ...]}})。"
                         "管理者の変更が記録にあるときは、記録の一覧を使う")
    ap.add_argument("--revocation-max-age", type=float, default=0.0, metavar="秒",
                    help="失効の通知の古さの上限。発行者の失効の一覧の issued_at からこの秒数を過ぎると、"
                         "期限の長さがこの秒数より長い資格情報を拒む。0 はこの規則を使わない(既定)。"
                         "鍵の管理の台帳には、同じ値を分で書く(digitalkey kanri door add --revocation-max-min)")
    ap.add_argument("--box", help="伝票を出す箱(フォルダ)。動いている間、増えた記録を続けて出す")
    return ap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """引数を読む。--revocation-max-age が負なら引数の誤り(終了コード 2。digitaldoor-controller と同じ)。"""
    ap = make_parser()
    args = ap.parse_args(argv)
    if not args.revocation_max_age >= 0:
        ap.error("--revocation-max-age は 0 以上の秒数です")
    return args


def make_driver(args):
    """--fake なら疑似の Sesame、--lock なら登録簿の錠のドライバ。指定が合わなければ ValueError。"""
    if args.fake:
        try:
            from digitalkey_sesame.fake import FakeSesameLock
        except ImportError:
            raise ValueError("--fake には digitalkey-sesame が要ります(pip install -e drivers/digitalkey-sesame)") from None
        return FakeSesameLock(args.name)
    from .locks import make_lock
    return make_lock(args.name, args.lock)


def build(args, driver=None) -> tuple[Controller, LockDriverContacts, Audit]:
    """扉のコントローラを組み立てる(HTTP はまだ開かない)。"""
    d = Path(args.dir)
    contacts = LockDriverContacts(driver if driver is not None else make_driver(args), timeout_s=args.timeout)
    audit = Audit(load_signer(d), d / "audit.jsonl")
    trust = json.loads(Path(args.trust).read_text(encoding="utf-8")) if args.trust else None
    admin_key = bytes.fromhex(args.admin_key) if args.admin_key else None
    ctl = Controller(contacts, audit, Config(two_person=args.two_person, autolock_s=args.autolock,
                                             power_policy=args.power_policy,
                                             revocation_max_age_s=args.revocation_max_age),
                     name=args.name, admin_key=admin_key, trust=trust)
    ctl.set_time(time.time(), True)
    return ctl, contacts, audit


def open_door(args, driver=None) -> tuple[ThreadingHTTPServer, Controller, LockDriverContacts]:
    """扉を組み立て、HTTP のインターフェースを開いて返す(serve_forever は呼び手が回す。--port 0 なら空いたポート)。時計と伝票の書き出しは裏で回す。"""
    ctl, contacts, audit = build(args, driver)
    lock = threading.Lock()
    srv = ThreadingHTTPServer((args.host, args.port), door_server.make_handler(ctl, lock))
    box = (lambda: export(audit, args.box, args.name, Path(args.dir) / "export.cursor")) if args.box else None
    door_server.start_workers(ctl, lock, box)
    return srv, ctl, contacts


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        srv, ctl, contacts = open_door(args)
    except (ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"digitalkey door-serve http://{args.host}:{srv.server_port}  錠: {'疑似の Sesame' if args.fake else args.lock}  記録: {args.dir}",
          flush=True)
    try:
        threading.Thread(target=_report_failures, args=(contacts,), daemon=True).start()
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        contacts.close()
    return 0


def _report_failures(contacts: LockDriverContacts, interval: float = 0.5) -> None:
    """錠に届かなかった命令を、標準エラーに一行ずつ出す(扉の判断は止めない)。"""
    seen = 0
    while True:
        time.sleep(interval)
        if contacts.failures > seen:
            seen = contacts.failures
            print(f"錠に届きませんでした({seen} 回目): {contacts.last_error}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
