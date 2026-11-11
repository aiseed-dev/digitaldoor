from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

from . import credentials
from .audit import Audit, Ed25519Signer, load_signer
from .contacts import SimContacts
from .controller import Config, Controller
from .server import serve
from .slips import check_box, export, key_id


def write_key_file(path: Path | str) -> Ed25519Signer:
    """Ed25519 の鍵の対を作り、秘密鍵(32 バイト)を権限 600 のファイルに書く。あるファイルは上書きしない(FileExistsError)。"""
    signer = Ed25519Signer.generate()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(signer.private_bytes())
    return signer


def read_key_file(path: Path | str) -> Ed25519Signer:
    """write_key_file で書いた秘密鍵のファイルを開く。読めない形なら ValueError(OSError はそのまま)。"""
    raw = Path(path).read_bytes()
    if len(raw) != 32:
        raise ValueError(f"鍵のファイルの形が違います(32 バイトの秘密鍵が要ります): {path}")
    return Ed25519Signer(raw)


def parse_time(text: str) -> int:
    """unix 秒(整数)か、ISO 8601 の日時(2026-10-21T09:00:00+09:00 など)を unix 秒にする。
    時差の無い日時は、この機械の地方時として読む。"""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        raise ValueError(f"時刻を読めません: {text}(unix 秒か 2026-10-21T09:00:00+09:00 の形で書いてください)") from None


def _names(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def parse_generation(text: str) -> int:
    """失効の一覧の世代(1 以上の整数)を読む。読めなければ ValueError。"""
    try:
        n = int(text)
    except ValueError:
        raise ValueError(f"世代を読めません: {text}(1 以上の整数で書いてください)") from None
    if n < 1:
        raise ValueError(f"世代は 1 以上です: {text}")
    return n


def _key_command(args) -> int:
    """keygen、issue、trust-change、revocation。置き場(--dir)は使わず、記録も作らない。出力は JSON を一つ。"""
    try:
        if args.cmd == "keygen":
            signer = write_key_file(args.out)
            out = {"public_key": signer.public_key().hex(), "issuer": key_id(signer.public_key())}
        elif args.cmd == "issue":
            doors = _names(args.doors)
            if not doors:
                raise ValueError("--doors に扉の名前を一つ以上書いてください")
            out = credentials.issue(read_key_file(args.key), cred_id=args.id, subject=args.subject, doors=doors,
                                    not_before=parse_time(args.from_), not_after=parse_time(args.until),
                                    once=args.once)
        elif args.cmd == "revocation":
            generation = parse_generation(args.generation)
            issued_at = parse_time(args.at) if args.at is not None else int(time.time())
            out = credentials.revocation_list(read_key_file(args.key), generation=generation, issued_at=issued_at,
                                              revoked=_names(args.ids) if args.ids else [])
        else:
            try:
                issuer_public = bytes.fromhex(args.issuer_key)
            except ValueError:
                raise ValueError("--issuer-key は公開鍵の 16 進です") from None
            if len(issuer_public) != 32:
                raise ValueError("--issuer-key は 32 バイト(64 桁)の公開鍵です")
            doors = _names(args.doors) if args.doors else None
            out = credentials.trust_change(read_key_file(args.admin_key_file), door=args.door, action=args.action,
                                           issuer_public_key=issuer_public, doors=doors,
                                           req_id=args.id or secrets.token_hex(8))
    except FileExistsError:
        print(f"鍵のファイルが既にあります(上書きしません): {args.out}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="digitaldoor-controller", description="扉側のコントローラ(中核)",
        epilog="記録の署名は Ed25519 で、鍵は置き場の audit.ed25519 です(前からの置き場で audit.key がある物は HMAC のまま)。"
               "伝票は一件ずつ <扉>-<連番>.json として箱へ出し、受け手は pubkey で写した公開鍵だけで check-box で検べます。"
               "発行者と管理者の側では keygen で鍵を作り(秘密鍵のファイルは権限 600)、issue で資格情報を、trust-change で信頼の一覧の変更の要求を、"
               "JSON で出します。revocation は発行者の鍵で署名した失効の一覧を JSON で出します。"
               "この四つは置き場(--dir)を使わず、網も要りません。")
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
                    help="失効の知らせの古さの上限。発行者の失効の一覧の issued_at からこの秒数を過ぎると、"
                         "期限の長さがこの秒数より長い資格情報を拒む。0 はこの規則を使わない(既定)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="模擬の接点で core を開く(開発の口の故障注入も開く)")
    s.add_argument("--box", help="伝票を出す箱(フォルダ)。動いている間、増えた記録を続けて出す")
    sub.add_parser("verify", help="監査の連鎖を検査")
    sub.add_parser("pubkey", help="署名の公開鍵を 16 進で表示する。据付のときに受け手の台帳へ写す")
    e = sub.add_parser("export", help="まだ出していない記録を伝票にして箱へ出す(続きは置き場の export.cursor)")
    e.add_argument("--box", required=True, help="伝票を出す箱(フォルダ)。無ければ何も出さない")
    c = sub.add_parser("check-box", help="受け手の側で、箱の伝票を公開鍵で検べる(置き場は使わない)")
    c.add_argument("--box", required=True, help="伝票の箱")
    c.add_argument("--door", required=True, help="扉の名前")
    c.add_argument("--pubkey", required=True, help="台帳に写した公開鍵(16 進)")
    k = sub.add_parser("keygen", help="発行者か管理者の Ed25519 の鍵の対を作る。秘密鍵は --out のファイル(権限 600)に書き、公開鍵と発行者の識別を JSON で出す",
                       description="秘密鍵(32 バイト)を権限 600 で --out に書く。ある名前には書かない。"
                                   "公開鍵の 16 進(信頼の一覧と --admin-key に入れる)と、発行者の識別(公開鍵の SHA-256 の先頭 16 桁。資格情報の issuer)を JSON で出す。")
    k.add_argument("--out", required=True, metavar="FILE", help="秘密鍵を書くファイル。無い名前にする")
    i = sub.add_parser("issue", help="発行者の鍵で署名した資格情報(5.2a)を JSON で出す",
                       description="資格情報 {\"body\": ..., \"sig\": ...} を標準出力に出す。扉の POST /credential にそのまま送れる。")
    i.add_argument("--key", required=True, metavar="FILE", help="発行者の秘密鍵のファイル(keygen の --out)")
    i.add_argument("--id", required=True, help="資格情報の識別。一回限りの使用済みの印に使う")
    i.add_argument("--subject", required=True, help="仮名化した主体")
    i.add_argument("--doors", required=True, metavar="NAME[,NAME...]", help="開けてよい扉の名前(コンマで区切る)")
    i.add_argument("--from", dest="from_", required=True, metavar="TIME",
                   help="有効の始まり。unix 秒か ISO 8601(2026-10-21T09:00:00+09:00)。時差の無い日時はこの機械の地方時")
    i.add_argument("--until", required=True, metavar="TIME", help="有効の終わり。--from と同じ書き方")
    i.add_argument("--once", action="store_true", help="一回限りの資格情報にする")
    t = sub.add_parser("trust-change", help="管理者の鍵で署名した、信頼の一覧の変更の要求を JSON で出す",
                       description="要求 {\"body\": ..., \"sig\": ...} を標準出力に出す。扉の POST /trust に送る。"
                                   "扉は据付のときに入れた管理者の公開鍵で署名を検べる。")
    t.add_argument("--admin-key", dest="admin_key_file", required=True, metavar="FILE",
                   help="管理者の秘密鍵のファイル。(サブコマンドの前に置く --admin-key は公開鍵の 16 進で、別の物)")
    t.add_argument("--door", required=True, help="この要求が効く扉の名前")
    t.add_argument("--action", required=True, choices=["add", "remove"], help="発行者を足すか、外すか")
    t.add_argument("--issuer-key", required=True, metavar="HEX", help="対象の発行者の公開鍵(16 進)")
    t.add_argument("--doors", metavar="NAME[,NAME...]", help="その発行者に許す扉の名前(add のとき要る。コンマで区切る)")
    t.add_argument("--id", help="要求の識別。扉は同じ識別を二度受けない。省くと乱数で作る")
    r = sub.add_parser("revocation", help="発行者の鍵で署名した失効の一覧(5.2c)を JSON で出す",
                       description="失効の一覧 {\"body\": ..., \"sig\": ...} を標準出力に出す。扉の POST /revocation に "
                                   "{\"list\": 一覧} として送るか、端末が POST /credential の revocation に入れて運ぶ。"
                                   "一覧は失効させた資格情報の全部で、差分ではない。扉は前の世代より大きい世代だけを受理する。"
                                   "失効が無くても、世代を上げて --at を新しくした一覧を、扉の --revocation-max-age より短い間隔で出す。")
    r.add_argument("--key", required=True, metavar="FILE", help="発行者の秘密鍵のファイル(keygen の --out)")
    r.add_argument("--generation", required=True, metavar="N", help="世代の番号。1 以上の整数。出すたびに上げる")
    r.add_argument("--ids", metavar="ID[,ID...]", help="失効した資格情報の識別(コンマで区切る)。省くと空の一覧")
    r.add_argument("--at", metavar="TIME", help="作った時刻(issued_at)。issue の --from と同じ書き方。省くと今の時刻")
    args = ap.parse_args(argv)
    if not args.revocation_max_age >= 0:
        ap.error("--revocation-max-age は 0 以上の秒数です")
    if args.cmd in ("keygen", "issue", "trust-change", "revocation"):
        return _key_command(args)
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
                                                   power_policy=args.power_policy,
                                                   revocation_max_age_s=args.revocation_max_age),
                     name=args.name, admin_key=admin_key, trust=trust)
    ctl.set_time(time.time(), True)
    print(f"digitaldoor-controller http://{args.host}:{args.port}  記録: {d}")
    box = (lambda: export(audit, args.box, args.name, d / "export.cursor")) if args.box else None
    print(f"  公開 API の定義: http://{args.host}:{args.port}/openapi.json  出来事の配信: ws://{args.host}:{args.port}/events"
          "  故障注入: POST /fault(模擬の扉なので開く)")
    serve(ctl, args.host, args.port, exporter=box, dev=True)
    return 0
