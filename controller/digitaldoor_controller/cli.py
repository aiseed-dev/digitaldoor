from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .audit import Audit, load_signer
from .contacts import SimContacts
from .controller import Config, Controller
from .server import serve
from .slips import check_box, export


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="digitaldoor-controller", description="扉側のコントローラ(中核)",
        epilog="記録の署名は Ed25519 で、鍵は置き場の audit.ed25519 です(前からの置き場で audit.key がある物は HMAC のまま)。"
               "伝票は一件ずつ <扉>-<連番>.json として箱へ出し、受け手は pubkey で写した公開鍵だけで check-box で検べます。")
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
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="模擬の接点で core を開く")
    s.add_argument("--box", help="伝票を出す箱(フォルダ)。動いている間、増えた記録を続けて出す")
    sub.add_parser("verify", help="監査の連鎖を検査")
    sub.add_parser("pubkey", help="署名の公開鍵を 16 進で表示する。据付のときに受け手の台帳へ写す")
    e = sub.add_parser("export", help="まだ出していない記録を伝票にして箱へ出す(続きは置き場の export.cursor)")
    e.add_argument("--box", required=True, help="伝票を出す箱(フォルダ)。無ければ何も出さない")
    c = sub.add_parser("check-box", help="受け手の側で、箱の伝票を公開鍵で検べる(置き場は使わない)")
    c.add_argument("--box", required=True, help="伝票の箱")
    c.add_argument("--door", required=True, help="扉の名前")
    c.add_argument("--pubkey", required=True, help="台帳に写した公開鍵(16 進)")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    if args.cmd == "check-box":
        ok, n, why = check_box(args.box, args.door, bytes.fromhex(args.pubkey))
        print(f"{'正常' if ok else '異常'} {n}件 {why}")
        return 0 if ok else 1
    if args.cmd == "verify":
        if not (d / "audit.jsonl").exists():
            print(f"記録がありません: {d}")
            return 1
        audit = Audit(load_signer(d), d / "audit.jsonl")
        ok, n, why = audit.verify()
        print(f"{'正常' if ok else '異常'} {n}件 {why}")
        return 0 if ok else 1
    audit = Audit(load_signer(d), d / "audit.jsonl")
    if args.cmd == "pubkey":
        if not hasattr(audit.signer, "public_key"):
            print("この置き場の署名は HMAC で、公開鍵がありません", file=sys.stderr)
            return 1
        print(audit.signer.public_key().hex())
        return 0
    if args.cmd == "export":
        n = export(audit, args.box, args.name, d / "export.cursor")
        print(f"{n}件を出しました: {args.box}")
        return 0
    trust = json.loads(Path(args.trust).read_text(encoding="utf-8")) if args.trust else None
    admin_key = bytes.fromhex(args.admin_key) if args.admin_key else None
    ctl = Controller(SimContacts(), audit, Config(two_person=args.two_person, autolock_s=args.autolock,
                                                   power_policy=args.power_policy),
                     name=args.name, admin_key=admin_key, trust=trust)
    ctl.set_time(time.time(), True)
    print(f"digitaldoor-controller http://{args.host}:{args.port}  記録: {d}")
    box = (lambda: export(audit, args.box, args.name, d / "export.cursor")) if args.box else None
    serve(ctl, args.host, args.port, exporter=box)
    return 0
