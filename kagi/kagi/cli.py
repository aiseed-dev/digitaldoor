"""kagi の命令。設計書 §3。終了コード: 0 よし、1 差・落ち・見つかった、2 読まなかった・鍵が無い。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from kagi import __version__, audit, install, inventory, machine, ports, report, scan, smb, tokens

DEFAULT_DIR = "./kagi-data"

INVENTORY_TEMPLATE = """\
# 在庫表（K-14、K-04、K-05）。今ある秘密を全部、一件ずつ [[secret]] に書く。
# 値は書かない。value という欄があると kagi は読まずに止まる（K-13）。
# 必須: id, kind, where, holder, role。expires, rotate_days, lost, leaked が無いと「抜け」と出る。
#
# [[secret]]
# id = "web1-app-env"           # 一意の名
# kind = "token"                # password | token | key | cert | passphrase | other
# where = "web1:/etc/app/env"   # 置き場。機械:パス か 紙/金庫 の名。値ではない
# holder = "suzuki"             # 持つ人
# role = "業務のサーバーが外の API を呼ぶ"   # 一つの役（K-01）
# made = 2026-11-04             # 作った日
# expires = 2027-05-04          # 期限
# rotate_days = 180             # 回す周期
# lost = "token を失効し、新しい token を発行して env を書き直す"   # なくした時
# leaked = "失効 → 記録で範囲 → 作り直し → 知らせる"              # 漏れた時
"""

PORTS_TEMPLATE = """\
# 口の表（K-07）。機械ごとに、開けてよい口を一件ずつ [[port]] に書く。
# kagi check ports --host NAME --ss FILE で `ss -ltnp` の出力と突き合わせる。
# bind が 0.0.0.0 か :: か * なら外向き。
#
# [[port]]
# host = "db1"
# port = 8080
# proto = "tcp"
# service = "業務のサーバー"
# bind = "127.0.0.1"
# reason = "nginx の後ろ"
"""

MACHINES_TEMPLATE = """\
# 機械の表と鍵の表。人が書く。値は書かない（公開鍵の指紋は値ではない。秘密鍵とパスワードは書かない。K-13）。
# kagi machine collect / check / fix / install、kagi key list が読む。
#
# [[machine]]
# host = "db1"                  # 機械の名
# ssh = "db1"                   # ~/.ssh/config の名（無ければ host）。入るのは人の ssh と人の鍵
# role = "社内のデータベース"     # 一つの役
# os = "debian-13"
# users = ["aoki"]              # 入れてよい人（アカウント）
# admins = ["aoki"]             # sudo を持ってよい人。入れる時は先頭が最初の管理者
# keys = ["suzuki-pc"]          # 入れてよい鍵（下の [[key]] の id）
# hostkey = "SHA256:…"          # 機械の鍵（ed25519）の指紋。機械の画面で見て書く
# services = ["app", "caddy"]   # 動いているべきサービス（systemd）。止まっていたら直す手が出る
# user_services = ["sync"]      # 写しを取る人の systemd（systemctl --user）で動いているべき物
# data = ["/srv/photos"]        # 大きさを見るデータの置き場（写真の元など、文字でない重い物）
# timers = ["backup"]           # 動いているべき timer（控えなど）。止まると「直す」に出る
# local = true                  # この PC 自身（写しを ssh でなくここで取る）
# lan = "192.168.1.0/24"        # 入れる時: ssh を通す網。無ければどこからでも（回数を絞る）
# profile = "server"            # 入れる時: server | desktop
# encrypt = true                # 入れる時: 盤を暗号化する機械（選ぶのは人）
#
# [[door]]                      # 入口の表: 外から来る入口ごとに、誰が、何で守られて入るか
# host = "web1"
# site = "app.example.org"      # Caddy の設定の入口の名
# who = "招待した人"             # 誰が入れるか（人の言葉）
# auth = "app"                  # public（誰でも）| app（裏のアプリが確かめる）| gate（Caddy が確かめる）| lan（網で絞る）
#
# [[key]]
# id = "suzuki-pc"              # 在庫表（inventory.toml）にも同じ id で一件書く
# holder = "suzuki"             # 持つ人
# pub = "~/.ssh/id_ed25519.pub" # 公開鍵のファイル。kagi は .pub だけを読む
# fingerprint = "SHA256:…"      # 指紋（pub があれば要らない。kagi key fingerprint FILE.pub で出る）
"""


def _dir(args) -> Path:
    """作業の場所。--dir が無ければ ./kagi-data。それが無く、今の場所に表があれば今の場所。"""
    given = args.sub_dir or args.top_dir
    if given:
        return Path(given).expanduser()
    default = Path(DEFAULT_DIR)
    here = Path(".")
    if not default.exists() and any((here / n).exists() for n in (machine.FILENAME, "inventory.toml")):
        return here
    return default


def _err(msg: str) -> None:
    print(f"kagi: {msg}", file=sys.stderr)


def cmd_init(args) -> int:
    d = _dir(args)
    d.mkdir(parents=True, exist_ok=True)
    for name, body in (
        ("inventory.toml", INVENTORY_TEMPLATE),
        ("ports.toml", PORTS_TEMPLATE),
        (machine.FILENAME, MACHINES_TEMPLATE),
        ("audit.log", ""),
    ):
        p = d / name
        if p.exists():
            print(f"既にある（触らない）: {p}")
            continue
        p.write_text(body, "utf-8")
        print(f"作った: {p}")
    return 0


def cmd_check_inventory(args) -> int:
    try:
        secrets = inventory.load(_dir(args) / "inventory.toml")
    except inventory.Refused as e:
        _err(str(e))
        return 2
    res = inventory.check(secrets, args.days)
    print(inventory.table(res, args.days))
    if not secrets:
        # 在庫表が空でも「抜けも期限切れも無い」になるので、鍵の表に鍵があれば一行言う（機械の表が読めた時だけ）
        path = _dir(args) / machine.FILENAME
        table = _optional(machine.load, path) if path.exists() else None
        if table and table.keys:
            print(f"\n鍵の表に鍵が {len(table.keys)} 件あるが、在庫表は空（kagi key list で見る）")
    return 1 if res.failed else 0


def cmd_check_ports(args) -> int:
    try:
        table = ports.load(_dir(args) / "ports.toml")
        text = Path(args.ss).read_text("utf-8")
    except inventory.Refused as e:
        _err(str(e))
        return 2
    except (OSError, UnicodeDecodeError) as e:
        _err(f"ss の出力を読めない: {args.ss} ({type(e).__name__})")
        return 2
    diff = ports.compare(table, ports.parse_ss(text), args.host)
    print(ports.table(diff, args.host))
    return 1 if diff.any else 0


def cmd_check_smb(args) -> int:
    try:
        conf = smb.load(Path(args.conf))
    except inventory.Refused as e:
        _err(str(e))
        return 2
    findings = smb.check(conf)
    print(smb.table(findings, args.conf))
    return 1 if smb.fixes(findings) else 0


def cmd_check_tokens(args) -> int:
    path = Path(args.file or tokens.DEFAULT).expanduser()
    try:
        rs = tokens.rows(tokens.load(path), args.days)
    except inventory.Refused as e:
        _err(str(e))
        return 2
    print(tokens.table(rs, path, args.days))
    return 1 if tokens.fixes(rs) else 0


def cmd_scan(args) -> int:
    res = scan.scan(args.paths, args.exclude)
    for h in res.hits:
        print(f"{h.path}:{h.line}  {h.kind}")
    print(f"読んだファイル {res.files}、飛ばした {res.skipped}、見つかった {len(res.hits)}", file=sys.stderr)
    return 1 if res.hits else 0


def _key():
    try:
        key = audit.find_key()
    except audit.NoKey as e:
        _err(str(e))
        return None
    if key.warning:
        _err(f"警告: {key.warning}")
    return key


def cmd_log_add(args) -> int:
    key = _key()
    if key is None:
        return 2
    try:
        seq = audit.add(_dir(args) / "audit.log", key, args.actor, args.action, args.detail)
    except ValueError as e:
        _err(str(e))
        return 1
    print(seq)
    return 0


def cmd_log_verify(args) -> int:
    key = _key()
    if key is None:
        return 2
    try:
        n, fails = audit.verify(_dir(args) / "audit.log", key)
    except (OSError, UnicodeDecodeError) as e:
        _err(f"記録を読めない ({type(e).__name__})")
        return 1
    if not fails:
        print(f"ok {n} lines")
        return 0
    for f in fails:
        print(f"{f.where}: {f.reason}")
    return 1


def cmd_report(args) -> int:
    print(
        report.build(
            _dir(args),
            args.days,
            Path(args.ss) if args.ss else None,
            args.host,
            args.scan,
            args.exclude,
            smb_conf=Path(args.smb) if args.smb else None,
        )
    )
    return 0


def _table(args) -> machine.Table:
    return machine.load(_dir(args) / machine.FILENAME)


def _optional(load, path: Path):
    """口の表と在庫表は、無ければ無しで進む（機械の突き合わせの脇役）。"""
    try:
        return load(path)
    except inventory.Refused:
        return None


def _log(args, action: str, detail: str) -> None:
    """鍵を使った事は一行（K-06）。署名の鍵が無ければ、足していない事を言う。"""
    try:
        key = audit.find_key()
        seq = audit.add(_dir(args) / "audit.log", key, os.environ.get("USER", "?"), action, detail)
        _err(f"記録に足した: seq {seq}（{action} {detail}）")
    except audit.NoKey:
        _err(f"記録には足していない（署名の鍵が無い。skills/kagi/02-記録.md）: {action} {detail}")
    except (ValueError, OSError) as e:
        _err(f"記録に足せなかった ({type(e).__name__}): {action} {detail}")


def _checked(args, host: str):
    """（写し、見つけた物）。写しは --facts か、作業の場所の一番新しい物。"""
    d = _dir(args)
    table = _table(args)
    table.machine(host)
    path = Path(args.facts) if getattr(args, "facts", None) else machine.latest_facts(d, host)
    if path is None:
        raise inventory.Refused(f"{host} の写しが無い。kagi machine collect --host {host}（か --facts FILE）")
    facts = machine.load_facts(path)
    inv = _optional(inventory.load, d / "inventory.toml")
    findings = machine.check(
        table,
        host,
        facts,
        _optional(ports.load, d / "ports.toml"),
        {str(s.get("id")) for s in inv} if inv is not None else None,
    )
    return facts, findings


def cmd_machine_list(args) -> int:
    try:
        table = _table(args)
    except inventory.Refused as e:
        _err(str(e))
        return 2
    print(f"機械 {len(table.machines)} 台。")
    if table.machines:
        print("\n| host | 役 | OS | 写し |\n|---|---|---|---|")
    for m in table.machines:
        f = machine.latest_facts(_dir(args), m["host"])
        print(f"| {m['host']} | {m.get('role', '')} | {m.get('os', '')} | {f.name if f else '無い'} |")
    return 0


def cmd_machine_script(args) -> int:
    print(machine.SCRIPT, end="")
    return 0


def cmd_machine_collect(args) -> int:
    try:
        m = _table(args).machine(args.host)
        local = args.local or m.get("local") is True
        text = machine.collect(None if local else str(m.get("ssh", m["host"])), paths=m.get("data"))
        path = machine.save_facts(_dir(args), args.host, text)
    except inventory.Refused as e:
        _err(str(e))
        return 2
    print(path)
    if not local:
        _log(args, "machine.collect", args.host)
    return 0


def cmd_machine_check(args) -> int:
    try:
        hosts = [args.host] if args.host else _table(args).hosts()
        if getattr(args, "facts", None) and not args.host:
            raise inventory.Refused("--facts には --host が要る")
    except inventory.Refused as e:
        _err(str(e))
        return 2
    code = 0
    for i, host in enumerate(hosts):
        if i:
            print()
        try:
            facts, findings = _checked(args, host)
        except inventory.Refused as e:
            if args.host:
                _err(str(e))
                return 2
            print(f"機械 {host}: 見ていない（{e}）")
            code = max(code, 1)
            continue
        print(machine.table(host, facts, findings))
        if not getattr(args, "facts", None):
            try:
                row = next(r for r in report.machine_rows(_dir(args)) if r["host"] == host)
                lines = report.change_lines(row)
            except (inventory.Refused, StopIteration):
                lines = []
            if lines:
                print("\n" + "\n".join(lines))
        if machine.fixes(findings):
            code = max(code, 1)
    return code


def cmd_machine_fix(args) -> int:
    try:
        facts, findings = _checked(args, args.host)
    except inventory.Refused as e:
        _err(str(e))
        return 2
    print(machine.fix_script(args.host, facts, findings))
    return 1 if machine.fixes(findings) else 0


def cmd_machine_install(args) -> int:
    try:
        paths = install.write(_dir(args), _table(args), args.host, Path(args.out) if args.out else None)
    except inventory.Refused as e:
        _err(str(e))
        return 2
    for p in paths:
        print(f"書いた: {p}")
    print("パスワードと盤は書いていない（インストーラーで人が打つ・選ぶ）。次は 手順.md。")
    return 0


def cmd_key_list(args) -> int:
    try:
        table = _table(args)
    except inventory.Refused as e:
        _err(str(e))
        return 2
    inv = _optional(inventory.load, _dir(args) / "inventory.toml")
    print(machine.key_table(table, inv))
    _fps, problems = machine.key_fingerprints(table)
    missing = inv is not None and any(k["id"] not in {str(s.get("id")) for s in inv} for k in table.keys)
    return 1 if problems or missing else 0


def cmd_key_fingerprint(args) -> int:
    try:
        infos = machine.read_pub(Path(args.file))
    except inventory.Refused as e:
        _err(str(e))
        return 2
    for _line, info in infos:
        bits = f" {info.bits} bit" if info.bits else ""
        print(f"{info.fingerprint}  {info.type}{bits}")
    return 0


def cmd_weekly(args) -> int:
    """週の定例（連載 第 11 章）: 全部の機械の写しを取り直し、一枚の報告を weekly-<日>.md に書く。"""
    import datetime as dt

    d = _dir(args)
    try:
        table = _table(args)
    except inventory.Refused as e:
        _err(str(e))
        return 2
    failed = []
    if not args.no_collect:
        for m in table.machines:
            host, local = m["host"], m.get("local") is True
            try:
                text = machine.collect(None if local else str(m.get("ssh", host)), paths=m.get("data"))
                machine.save_facts(d, host, text)
                _err(f"写しを取った: {host}")
                if not local:
                    _log(args, "machine.collect", host)
            except inventory.Refused as e:
                failed.append(f"- {host}: 写しが取れなかった（{e}）")
                _err(f"{host}: {e}")
    text = report.build(d, args.days, tokens_file=Path(args.tokens) if args.tokens else None)
    if failed:
        text += "\n## 写しが取れなかった機械\n\n" + "\n".join(failed) + "\n"
    out = d / f"weekly-{dt.date.today().isoformat()}.md"
    out.write_text(text, "utf-8")
    print(text)
    _err(f"書いた: {out}")
    return 0


def cmd_app(args) -> int:
    try:
        import flet  # noqa: F401  画面だけの任意の依存（kagi の本体は標準ライブラリだけ）
    except ImportError:
        _err("画面には flet が要る: pip install flet（または pip install -e '.[app]'）")
        return 2
    from kagi import app

    app.run(_dir(args))
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", dest="sub_dir", default=None, help=f"作業の場所（既定 {DEFAULT_DIR}）")

    p = argparse.ArgumentParser(prog="kagi", description="鍵の管理の道具。鍵の値は読まない（K-13）。")
    p.add_argument("--version", action="version", version=f"kagi {__version__}")
    p.add_argument("--dir", dest="top_dir", default=None, help=f"作業の場所（既定 {DEFAULT_DIR}）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", parents=[common], help="三つのファイルの雛形を作る")
    s.set_defaults(func=cmd_init)

    c = sub.add_parser("check", help="在庫表、口の表、smb.conf を確かめる").add_subparsers(
        dest="what", required=True
    )
    s = c.add_parser("inventory", parents=[common], help="期限切れ、期限の近い物、欄の抜け")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(func=cmd_check_inventory)
    s = c.add_parser("ports", parents=[common], help="口の表と ss -ltnp の出力の差")
    s.add_argument("--ss", required=True, help="ss -ltnp の出力のファイル（kagi は ss を走らせない）")
    s.add_argument("--host", default=None, help="表のどの機械か")
    s.set_defaults(func=cmd_check_ports)
    s = c.add_parser("smb", help="smb.conf の口と認証の形（値は出さない）")
    s.add_argument("--conf", required=True, help="smb.conf のファイル（include は辿らない）")
    s.set_defaults(func=cmd_check_smb, sub_dir=None)

    s = c.add_parser("tokens", help="相手に出した token の台帳（誰に、範囲、期限。ハッシュは出さない）")
    s.add_argument("--file", default=None, metavar="PATH", help=f"台帳（既定 {tokens.DEFAULT}）")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(func=cmd_check_tokens, sub_dir=None)

    s = sub.add_parser("scan", help="秘密らしい文字列の在り処（path:line と種類だけ）")
    s.add_argument("paths", nargs="+")
    s.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    s.set_defaults(func=cmd_scan, sub_dir=None)

    lg = sub.add_parser("log", help="追記だけの記録").add_subparsers(dest="what", required=True)
    s = lg.add_parser("add", parents=[common], help="一行足す")
    s.add_argument("--actor", required=True)
    s.add_argument("--action", required=True)
    s.add_argument("--detail", required=True)
    s.set_defaults(func=cmd_log_add)
    s = lg.add_parser("verify", parents=[common], help="連番、連鎖、hash、署名を全行で見る")
    s.set_defaults(func=cmd_log_verify)

    s = sub.add_parser("report", parents=[common], help="一枚の報告（Markdown）")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--ss", default=None, help="ss -ltnp の出力のファイル")
    s.add_argument("--host", default=None)
    s.add_argument("--scan", nargs="+", default=None, metavar="PATH", help="走査する場所")
    s.add_argument("--smb", default=None, metavar="FILE", help="smb.conf（口の節に足す）")
    s.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    s.set_defaults(func=cmd_report)

    mc = sub.add_parser(
        "machine", help="Linux の機械を入れる、見る（機械は変えない。直す手は文で出す）"
    ).add_subparsers(dest="what", required=True)
    s = mc.add_parser("list", parents=[common], help="機械の表と、写しの有無")
    s.set_defaults(func=cmd_machine_list)
    s = mc.add_parser("script", help="機械で走らせる読むだけの文を出す（人が読める）")
    s.set_defaults(func=cmd_machine_script, sub_dir=None)
    s = mc.add_parser("collect", parents=[common], help="写しを取る（人の ssh で、鍵でだけ。読むだけ）")
    s.add_argument("--host", required=True)
    s.add_argument("--local", action="store_true", help="この機械で取る（ssh を使わない）")
    s.set_defaults(func=cmd_machine_collect)
    s = mc.add_parser("check", parents=[common], help="写しを機械の表・口の表・在庫表と突き合わせる")
    s.add_argument("--host", default=None, help="無ければ表の全部の機械")
    s.add_argument("--facts", default=None, metavar="FILE", help="写しのファイル（無ければ一番新しい物）")
    s.set_defaults(func=cmd_machine_check)
    s = mc.add_parser("fix", parents=[common], help="直す手を sh の文で出す（走らせるのは人）")
    s.add_argument("--host", required=True)
    s.add_argument("--facts", default=None, metavar="FILE")
    s.set_defaults(func=cmd_machine_fix)
    s = mc.add_parser(
        "install", parents=[common], help="入れる時のファイル（preseed.cfg、firstboot.sh、手順.md）"
    )
    s.add_argument("--host", required=True)
    s.add_argument("--out", default=None, metavar="DIR", help="書く場所（既定は 作業の場所/install/<host>）")
    s.set_defaults(func=cmd_machine_install)

    ky = sub.add_parser("key", help="ssh の鍵の表（公開鍵と指紋だけ。秘密鍵は読まない）").add_subparsers(
        dest="what", required=True
    )
    s = ky.add_parser("list", parents=[common], help="鍵、持つ人、指紋、入れてよい機械、在庫表の期限")
    s.set_defaults(func=cmd_key_list)
    s = ky.add_parser("fingerprint", help="公開鍵のファイル（.pub）の指紋")
    s.add_argument("file")
    s.set_defaults(func=cmd_key_fingerprint, sub_dir=None)

    s = sub.add_parser(
        "weekly", parents=[common], help="週の定例: 全部の機械の写しを取り直し、一枚の報告を書く"
    )
    s.add_argument("--no-collect", action="store_true", help="写しを取り直さない（今ある写しで）")
    s.add_argument("--days", type=int, default=30)
    s.add_argument(
        "--tokens", default=None, metavar="FILE", help=f"出した token の台帳（既定 {tokens.DEFAULT}）"
    )
    s.set_defaults(func=cmd_weekly)

    s = sub.add_parser("app", parents=[common], help="係の机の画面（Flet の PC 版。網には出さない）")
    s.set_defaults(func=cmd_app)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
