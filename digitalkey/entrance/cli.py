"""entrance の命令行。経営者と AI社員 はここから台帳を扱う(画面は作らない)。"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from datetime import date, datetime
from pathlib import Path

from . import report, safe
from . import forms as Y
from .ledger import Ledger
from .keyring import DummyLock, Keyring, SesameLock, SesameWebLock


def _vault(args) -> Path:
    return Path(args.vault or os.environ.get("DIGITALKEY_VAULT") or "vault")


def _daicho(args) -> Ledger:
    return Ledger(_vault(args))


def cmd_forms(args):
    for name, y in Y.load_all().items():
        print(f"{name}\t{y.title}\t{len(y.fields)}項目")


def cmd_form(args):
    y = Y.load_all()[args.name]
    sys.stdout.write(Y.kinyu_text(y))


def cmd_ddl(args):
    print(Y.ddl(Y.load_all()[args.name]))


def cmd_append(args):
    d = _daicho(args)
    y = d.form_set[args.name]
    text = Path(args.file).read_text(encoding="utf-8") if args.file != "-" else sys.stdin.read()
    values, unknown = Y.parse_kinyu(y, text)
    for u in unknown:
        print(f"読めない行: {u}", file=sys.stderr)
    den = d.append(args.name, values, issuer=args.issuer, to=args.to or "", basis=args.basis or [])
    print(den.id)


def cmd_list(args):
    d = _daicho(args)
    for den in d.select(args.name):
        head = " ".join(f"{k}={v}" for k, v in list(den.values.items())[:4] if v)
        print(f"{den.id}\t{den.issued_at}\t{den.issuer}\t{head}")


def cmd_show(args):
    d = _daicho(args)
    print(d.get(args.id).path.read_text(encoding="utf-8"), end="")


def cmd_verify(args):
    ok, n, why = _daicho(args).verify_chain()
    print(f"{'正常' if ok else '異常'} {n}枚 {why}")
    return 0 if ok else 1


def cmd_reserve(args):
    d = _daicho(args)
    token = secrets.token_urlsafe(16)
    den = d.append("予約", {"主体": args.subject, "層": args.layer, "到着": args.start, "出発": args.end,
                           "錠": args.lock, "連絡先": args.contact or "", "受付符号": token}, issuer=args.issuer)
    print(den.id)
    print(f"/checkin/{token}")


def _locks(specs: list[str]) -> dict:
    out = {}
    for s in specs or []:
        lock_id, _, spec = s.partition("=")
        if spec in ("", "dummy"):
            out[lock_id] = DummyLock(lock_id)
        elif spec.startswith("sesame:"):
            out[lock_id] = SesameLock(lock_id, spec[len("sesame:"):])
        elif spec.startswith("sesameweb:"):
            uuid = spec[len("sesameweb:"):]
            keys = _sesame_keys()
            secret = keys.get(uuid) or os.environ.get("SESAME_SECRET_" + uuid.replace("-", "").upper(), "")
            api_key = keys.get("api_key") or os.environ.get("SESAME_API_KEY", "")
            if not (secret and api_key):
                raise SystemExit(f"{lock_id}: SESAME_API_KEY と機器の secret key が要ります($SESAME_KEYS のJSONか環境変数)")
            out[lock_id] = SesameWebLock(lock_id, uuid, api_key, secret)
        else:
            raise SystemExit(f"錠の指定が読めません: {s}")
    return out


def _sesame_keys() -> dict:
    """$SESAME_KEYS が指す JSON: {"api_key": "...", "<uuid>": "<secret hex>", ...}。経営者の箱にだけ置く。"""
    path = os.environ.get("SESAME_KEYS")
    if not path or not Path(path).exists():
        return {}
    import json
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_key(args):
    d = _daicho(args)
    k = Keyring(d, _locks(args.lock_driver))
    if args.key_cmd == "issue":
        den = k.issue(subject=args.subject, lock_id=args.lock, start=args.start, end=args.end,
                      route=args.route, issuer=args.issuer)
        print(den.id)
    elif args.key_cmd == "revoke":
        print(k.revoke(args.id, args.reason, issuer=args.issuer).id)
    elif args.key_cmd == "active":
        for den in k.active():
            print(f"{den.id}\t{den['主体']}\t{den['錠']}\t{den['開始']}〜{den['終了']}")
    elif args.key_cmd == "open":
        den = asyncio.run(k.open(args.id, by=args.issuer))
        print(f"{den['結果']} {den['理由']}")


def cmd_report(args):
    d = _daicho(args)
    if args.kind == "wwoof":
        y, m = args.period.split("-")
        sys.stdout.write(report.wwoof_monthly(d, int(y), int(m)))
    else:
        sys.stdout.write(report.minpaku_teiki(d, date.fromisoformat(args.period), date.fromisoformat(args.end)))


def cmd_backup(args):
    out = safe.backup(_vault(args), args.out, passphrase_file=args.passphrase_file, remote=args.remote)
    print(out)


def cmd_retention(args):
    for den in safe.retention_candidates(_daicho(args)):
        print(f"{den.id}\t{den.form}\t{den.issued_at}")


def cmd_serve(args):
    import uvicorn

    from .web import Config, create_app

    cfg = Config(vault=_vault(args), house_lat=args.house_lat, house_lon=args.house_lon,
                 owner_token=args.owner_token or secrets.token_urlsafe(24), locks=_locks(args.lock_driver))
    print(f"経営者の符号: {cfg.owner_token}", file=sys.stderr)
    uvicorn.run(create_app(cfg), host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="digitalkey entrance", description=__doc__)
    ap.add_argument("--vault", help="金庫(既定: $DIGITALKEY_VAULT か ./vault)")
    ap.add_argument("--issuer", default="経営者", help="伝票の発行者")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("forms", help="様式の一覧").set_defaults(fn=cmd_forms)
    p = sub.add_parser("form", help="記入用テキストを出す"); p.add_argument("name"); p.set_defaults(fn=cmd_form)
    p = sub.add_parser("ddl", help="様式から CREATE TABLE を出す"); p.add_argument("name"); p.set_defaults(fn=cmd_ddl)
    p = sub.add_parser("append", help="記入済みテキストを伝票にする")
    p.add_argument("name"); p.add_argument("file", help="記入済みテキスト(- で標準入力)")
    p.add_argument("--to"); p.add_argument("--basis", nargs="*"); p.set_defaults(fn=cmd_append)
    p = sub.add_parser("list", help="伝票の一覧"); p.add_argument("name", nargs="?"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show", help="伝票を表示"); p.add_argument("id"); p.set_defaults(fn=cmd_show)
    sub.add_parser("verify", help="連鎖を検査").set_defaults(fn=cmd_verify)
    p = sub.add_parser("reserve", help="予約を起こし、チェックインの符号を出す")
    p.add_argument("subject"); p.add_argument("layer", choices=["WWOOF", "民泊", "貸家", "会議室"])
    p.add_argument("start"); p.add_argument("end"); p.add_argument("lock"); p.add_argument("--contact")
    p.set_defaults(fn=cmd_reserve)

    pk = sub.add_parser("key", help="鍵の発行・失効・一覧・解錠")
    pk.add_argument("--lock-driver", action="append", help="錠ID=dummy | 錠ID=sesameweb:<uuid>(Hub 3経由、既定) | 錠ID=sesame:<uuid>(BLE直結)")
    ks = pk.add_subparsers(dest="key_cmd", required=True)
    p = ks.add_parser("issue"); p.add_argument("--subject", required=True); p.add_argument("--lock", required=True)
    p.add_argument("--start", required=True); p.add_argument("--end", required=True)
    p.add_argument("--route", default="遠隔解錠")
    p = ks.add_parser("revoke"); p.add_argument("id"); p.add_argument("--reason", required=True)
    ks.add_parser("active")
    p = ks.add_parser("open"); p.add_argument("id")
    pk.set_defaults(fn=cmd_key)

    p = sub.add_parser("report", help="wwoof YYYY-MM | minpaku START END")
    p.add_argument("kind", choices=["wwoof", "minpaku"]); p.add_argument("period"); p.add_argument("end", nargs="?")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("backup", help="金庫を固めて暗号化し、控えへ")
    p.add_argument("out"); p.add_argument("--passphrase-file"); p.add_argument("--remote"); p.set_defaults(fn=cmd_backup)
    sub.add_parser("retention", help="保存年限を過ぎた伝票の候補").set_defaults(fn=cmd_retention)
    p = sub.add_parser("serve", help="門(Web)を開く")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8790)
    p.add_argument("--house-lat", type=float, required=True); p.add_argument("--house-lon", type=float, required=True)
    p.add_argument("--owner-token"); p.add_argument("--lock-driver", action="append"); p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    rc = args.fn(args)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
