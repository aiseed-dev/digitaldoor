from __future__ import annotations

import argparse
import os
import secrets
import time
from pathlib import Path

from .audit import Audit, HmacSigner
from .contacts import SimContacts
from .controller import Config, Controller
from .server import serve


def _signer(keyfile: Path) -> HmacSigner:
    if not keyfile.exists():
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_bytes(secrets.token_bytes(32))
        keyfile.chmod(0o600)
    return HmacSigner(keyfile.read_bytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="digitalkey door", description="扉側のコントローラ(中核)")
    ap.add_argument("--dir", default=os.environ.get("DIGITALKEY_DOOR_DIR", "vault"), help="記録と鍵の置き場")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8797)
    ap.add_argument("--two-person", type=int, default=1)
    ap.add_argument("--autolock", type=float, default=5.0)
    ap.add_argument("--power-policy", choices=["release", "hold"], default="release")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="模擬の接点で core を開く")
    sub.add_parser("verify", help="監査の連鎖を検査")
    args = ap.parse_args(argv)
    d = Path(args.dir)
    if args.cmd == "verify":
        if not (d / "audit.jsonl").exists():
            print(f"記録がありません: {d}")
            return 1
        audit = Audit(_signer(d / "audit.key"), d / "audit.jsonl")
        ok, n, why = audit.verify()
        print(f"{'正常' if ok else '異常'} {n}件 {why}")
        return 0 if ok else 1
    audit = Audit(_signer(d / "audit.key"), d / "audit.jsonl")
    ctl = Controller(SimContacts(), audit, Config(two_person=args.two_person, autolock_s=args.autolock,
                                                   power_policy=args.power_policy))
    ctl.set_time(time.time(), True)
    print(f"digitalkey door http://{args.host}:{args.port}  記録: {d}")
    serve(ctl, args.host, args.port)
    return 0
