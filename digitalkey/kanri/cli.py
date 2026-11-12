# SPDX-License-Identifier: AGPL-3.0-or-later
"""命令 `digitalkey kanri`(設計書 19.12、20.12、21.12、22.9)。鍵の管理の置き場を、ネットワークを使わず、置き場のファイルだけで扱います(R-AC-19)。

    digitalkey kanri init --dir /srv/kanri --admin adm --admin-name 管理者A
    digitalkey kanri request --dir /srv/kanri --by adm --role 名簿の係 --subject s1 --door 玄関 --from 2026-12-01T09:00 ...
    digitalkey kanri device add --dir /srv/kanri --by adm --role 管理者 --name ルーター1 --kind ルーター --state 使用
    digitalkey kanri device findings --dir /srv/kanri

名簿と扉と権限のほかに、ネットワークの区画(segment)、機器の台帳(device)、機器の管理者のインターフェースの削除(access)、
業者と連携先に渡している物の目録(vendor)を扱います。機器の権限は request と issue の --device で申請と発行をし、
entity issue --route 管理者のインターフェース --account <インターフェースの名> で出します。パスワードと鍵の値は、どの命令でも受けず、書きません。

失効の一覧(revocation)は、発行者の鍵で署名した一覧です。revoke で署名つき資格情報を止めると同じ命令で一枚でき、
revoke --out か revocation show --out でファイルに出します。ネットワークの届かない扉へは、人か利用者の端末が運びます。
扉が受けた事は、扉の GET /revocation の応答や画面を見て revocation delivered で書きます。扉ごとの失効が効くまでの
最長の時間は door add --revocation-max-min に、扉に入れた --revocation-max-age の秒を分で書きます。

    digitalkey kanri revoke --dir /srv/kanri --by adm --role 管理者 --subject s2 --reason 退職 --out /media/usb/rev.json
    digitalkey kanri revocation delivered --dir /srv/kanri --by ops --role 運用 --door 通用口 --generation 1

窓口(お問い合わせと照会の頁)で本人が自分の権限と鍵を見るために、portal で名簿の主体と窓口の番号を結びます。
本人が頁に出る番号を名簿の係に伝え、名簿の係は本人を対面かビデオ通話で確かめてから portal link を書きます。
portal export --out は、結んだ人の照会用のコピー(本人が見てよい物だけ。呼び名、仮名、理由は入らない)を権限 600 の
ファイルに出します。この命令はネットワークを使わず、コピーを窓口へ置きに行くのは digitalkey madoguchi push です。

    digitalkey kanri portal link --dir /srv/kanri --by ros --role 名簿の係 --subject s1 --account k3x9m2p7q1w8e5r --how 対面
    digitalkey kanri portal export --dir /srv/kanri --out /media/usb/shoukai.json

置き場は --dir か環境変数 DIGITALKEY_KANRI_DIR。書く命令には --by(操作する人)と --role(この操作で使う役)が要ります。
--json で出力を JSON 一つにします。鍵の値は、どの引数でも受けません(R-CM-13)。発行者の鍵は init --issuer-key の
ファイルの道か、置き場の 鍵/発行者.key で渡します。

終了コード: 0 できた(may-open は開けてよい)、1 拒否(may-open の拒否、verify の異常)、2 引数の誤り、
3 置き場を開けない・作れない(置き場が無い、発行者の鍵が無い・違う)。
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import reasons as R
from .reasons import Refused
from .devices import CHECK_ITEMS
from .routes import CREDENTIAL
from .unit import Unit, write_private

OPEN_ERRORS = (R.NO_UNIT, R.NO_KEY, R.WRONG_KEY)


# ---- 出力 ----
def _emit(args, human: list[str] | str, data: dict) -> None:
    if args.json:
        print(json.dumps({"ok": True, **data}, ensure_ascii=False))
    else:
        for line in [human] if isinstance(human, str) else human:
            print(line)


def _slip(args, slip) -> int:
    _emit(args, f"{slip.form} {slip.id}", {"様式": slip.form, "番号": slip.id})
    return 0


def _plain(v):
    """型の値を JSON の値に。set と list は列。"""
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, set):
        return sorted(v)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if isinstance(v, dict):
        return ", ".join(f"{k}={x}" for k, x in v.items())
    if isinstance(v, (set, list, tuple)):
        return ", ".join(sorted(v) if isinstance(v, set) else v)
    return str(v)


def _items(args, objs) -> int:
    rows = [dataclasses.asdict(o) for o in objs]
    _emit(args, ["\t".join(_cell(v) for v in r.values()) for r in rows], {"items": _plain(rows)})
    return 0


def _sheet_lines(sheet) -> list[str]:
    head = f"{sheet.主体} {sheet.時刻} {'済み' if sheet.済み else '未了'}"
    return [head] + ["\t".join([r.状態, r.経路, r.識別, ", ".join(r.扉), r.効くまで, r.作業, r.機器]) for r in sheet.行]


def _sheet_data(sheet) -> dict:
    return {"主体": sheet.主体, "時刻": sheet.時刻, "済み": sheet.済み,
            "実体": [_plain(dataclasses.asdict(r)) for r in sheet.行]}


# ---- 命令 ----
def _init(args, d: str) -> int:
    u = Unit.create(d, admin=args.admin, admin_name=args.admin_name, admin_kind=args.admin_kind,
                    issuer_key=args.issuer_key)
    _emit(args, [f"発行者 {u.issuer}", f"公開鍵 {u.public_key.hex()}"],
          {"発行者": u.issuer, "公開鍵": u.public_key.hex(), "台帳": str(Path(d) / "台帳")})
    return 0


def _w(args) -> dict:
    return {"by": args.by, "role": args.role}


def _kind(args) -> tuple[str, str]:
    if args.door is not None:
        return "扉", args.door
    if args.group is not None:
        return "群", args.group
    return "機器", args.device


def _subject_add(u, a):
    return _slip(a, u.register_subject(a.id, a.name, a.kind, a.vendor, **_w(a)))


def _subject_change(u, a):
    vendor = None if a.vendor is None else a.vendor == "はい"
    return _slip(a, u.change_subject(a.id, reason=a.reason, name=a.name, kind=a.kind, vendor=vendor, state=a.state,
                                     **_w(a)))


def _role(grant: bool):
    def run(u, a):
        return _slip(a, u.assign_role(a.subject, a.name, grant, reason=a.reason, **_w(a)))
    return run


def _door_add(u, a):
    return _slip(a, u.register_door(a.name, a.routes.split(","), place=a.place, audit_key=a.audit_key,
                                    state="撤去" if a.retire else "使用", revocation_max_min=a.revocation_max_min,
                                    **_w(a)))


def _group_add(u, a):
    return _slip(a, u.register_group(a.name, a.doors.split(","), state="廃止" if a.retire else "使用", **_w(a)))


def _setting_set(u, a):
    return _slip(a, u.set_setting(a.item, a.value, reason=a.reason, **_w(a)))


def _trust_entry(u, a):
    print(json.dumps(u.trust_entry(), ensure_ascii=False))
    return 0


def _request(u, a):
    kind, target = _kind(a)
    return _slip(a, u.request(a.subject, kind, target, a.start, a.until, a.reason, **_w(a)))


def _import(u, a):
    try:
        with open(a.file, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        a.parser.error(f"--file を読めません: {e}")
    res = u.import_requests(rows, a.source, **_w(a))
    human = [f"{s.form} {s.id}" for s in res.申請] + [f"飛ばした {n} {why}" for n, why in res.飛ばした]
    _emit(a, human, {"申請": [s.id for s in res.申請], "飛ばした": [{"行": n, "理由": why} for n, why in res.飛ばした]})
    return 0


def _decide(u, a):
    return _slip(a, u.decide(a.request, "承認" if a.approve else "却下", a.reason, **_w(a)))


def _issue(u, a):
    kind, target = _kind(a)
    return _slip(a, u.issue(a.approval, a.subject, kind, target, a.start, a.until, at=a.at, **_w(a)))


def _permission_list(u, a):
    return _items(a, u.permissions(a.subject, at=a.at))


def _entity_issue(u, a):
    out = Path(a.out) if a.out else None
    if out is not None and os.path.lexists(out):
        raise Refused(R.OUT_EXISTS)
    got = u.issue_entity(a.permission, a.route, until=a.until, once=a.once, key_number=a.key_number,
                         account=a.account, at=a.at, **_w(a))
    written = ""
    if got.資格情報 is not None and out is not None:
        write_private(out, json.dumps(got.資格情報, ensure_ascii=False).encode("utf-8"))
        written = str(out)
    e = got.実体
    eff = u._route(e.経路).effective_by(u, e)
    _emit(a, f"{got.伝票.form} {got.伝票.id}", {"様式": got.伝票.form, "番号": got.伝票.id, "経路": e.経路, "識別": e.識別,
                                               "扉": e.扉, "開始": e.開始, "終了": e.終了, "資格情報のファイル": written,
                                               "効くまで": {"種類": eff.種類, "時刻": eff.時刻, "分数": eff.分数}})
    return 0


def _physical_return(u, a):
    return _slip(a, u.return_physical(a.entity, a.result, a.note, **_w(a)))


def _may_open(u, a):
    v = u.may_open(a.subject, a.door, at=a.at)
    _emit(a, "開けてよい" if v.ok else f"拒否 {v.理由}", {"開けてよい": v.ok, "理由": v.理由, "実体": v.実体})
    return 0 if v.ok else 1


def _out_path(a) -> Path | None:
    """--out の道。既にあれば、何かを書く前に OUT_EXISTS。"""
    out = Path(a.out) if a.out else None
    if out is not None and os.path.lexists(out):
        raise Refused(R.OUT_EXISTS)
    return out


def _write_list(out: Path | None, lst: dict) -> str:
    """署名つきの一覧を --out に書く(秘密ではないので権限は既定のまま)。書いた道か空。"""
    if out is None:
        return ""
    with open(out, "x", encoding="utf-8") as f:
        f.write(json.dumps(lst, ensure_ascii=False) + "\n")
    return str(out)


def _revoke(u, a):
    out = _out_path(a)
    if a.subject is not None:
        rev = u.revoke_subject(a.subject, a.reason, at=a.at, **_w(a))
    else:
        rev = u.revoke_permission(a.permission, a.reason, at=a.at, **_w(a))
    slips = rev.権限_失効 + rev.実体_失効 + ([rev.失効_一覧] if rev.失効_一覧 is not None else [])
    gen = int(rev.失効_一覧["世代"]) if rev.失効_一覧 is not None else None
    written = _write_list(out, u.revocation(gen)) if gen is not None else ""
    _emit(a, [f"{s.form} {s.id}" for s in slips] + _sheet_lines(rev.進み具合),
          {"権限_失効": [s.id for s in rev.権限_失効], "実体_失効": [s.id for s in rev.実体_失効],
           "進み具合": _sheet_data(rev.進み具合), "失効_一覧": rev.失効_一覧.id if rev.失効_一覧 is not None else "",
           "世代": gen, "一覧のファイル": written})
    return 0


def _progress(u, a):
    sheet = u.progress(a.subject, at=a.at)
    _emit(a, _sheet_lines(sheet), _sheet_data(sheet))
    return 0


def _segment_add(u, a):
    return _slip(a, u.register_segment(a.name, note=a.note, state="廃止" if a.retire else "使用", **_w(a)))


DEVICE_OPTIONS = ("owner", "place", "segment", "door", "maker", "model", "serial", "net_id", "version",
                  "support_until", "update_mode", "secret_ref", "log_to", "holds")


def _device_add(u, a):
    kw = {k: getattr(a, k) or "" for k in DEVICE_OPTIONS}
    return _slip(a, u.register_device(a.name, a.kind, a.state, door_system=a.door_system, **kw, **_w(a)))


def _device_change(u, a):
    kw = {k: getattr(a, k) for k in DEVICE_OPTIONS}
    door_system = None if a.door_system is None else a.door_system == "はい"
    got = u.change_device(a.name, reason=a.reason, at=a.at, kind=a.kind, door_system=door_system, state=a.state,
                          **kw, **_w(a))
    slips = [got.伝票] + got.権限_失効 + got.実体_失効 + ([got.失効_一覧] if got.失効_一覧 is not None else [])
    _emit(a, [f"{s.form} {s.id}" for s in slips],
          {"様式": got.伝票.form, "番号": got.伝票.id, "権限_失効": [s.id for s in got.権限_失効],
           "実体_失効": [s.id for s in got.実体_失効],
           "失効_一覧": got.失効_一覧.id if got.失効_一覧 is not None else ""})
    return 0


def _device_check(u, a):
    results = dict(a.item or [])
    return _slip(a, u.check_device(a.name, a.scene, results, note=a.note, at=a.at, **_w(a)))


def _device_update(u, a):
    return _slip(a, u.record_update(a.name, a.version, result="当てられなかった" if a.failed else "当てた",
                                    reason=a.reason, at=a.at, **_w(a)))


def _access_remove(u, a):
    return _slip(a, u.remove_access(a.entity, a.result, a.note, **_w(a)))


def _vendor_list(u, a):
    held = u.vendor_inventory(at=a.at)
    human = ["\t".join([h.主体, h.権限, h.対象の種類, h.対象, h.開始, h.終了, h.状態,
                        ", ".join(f"{r.実体}({r.状態})" for r in h.残り)]) for h in held]
    _emit(a, human, {"items": [_plain(dataclasses.asdict(h)) for h in held]})
    return 0


def _revocation_publish(u, a):
    out = _out_path(a)
    p = u.publish_revocations(at=a.at, **_w(a))
    written = _write_list(out, p.一覧)
    body = json.loads(p.一覧["body"])
    _emit(a, [f"{p.伝票.form} {p.伝票.id}", f"世代 {p.世代}"],
          {"様式": p.伝票.form, "番号": p.伝票.id, "世代": p.世代, "発行時刻": body["issued_at"],
           "識別": body["revoked"], "一覧のファイル": written})
    return 0


def _revocation_show(u, a):
    out = _out_path(a)
    lst = u.revocation(a.generation)
    if out is None:
        print(json.dumps(lst, ensure_ascii=False))
        return 0
    gen = json.loads(lst["body"])["generation"]
    written = _write_list(out, lst)
    _emit(a, f"世代 {gen}", {"世代": gen, "一覧のファイル": written})
    return 0


def _revocation_list(u, a):
    rl = u.revocations()
    _emit(a, [f"{r.世代}\t{r.発行時刻}\t{len(r.識別)}\t{r.番号}" for r in rl],
          {"items": [_plain(dataclasses.asdict(r)) for r in rl]})
    return 0


def _revocation_delivered(u, a):
    return _slip(a, u.record_delivery(a.door, a.generation, at=a.at, note=a.note, **_w(a)))


def _portal_link(u, a):
    return _slip(a, u.link_portal(a.subject, a.account, a.how, **_w(a)))


def _portal_unlink(u, a):
    return _slip(a, u.unlink_portal(a.subject, reason=a.reason, **_w(a)))


def _portal_list(u, a):
    links = u.portal_links()
    _emit(a, [f"{x.主体}\t{x.窓口の番号}\t{x.確かめ方}\t{x.伝票}" for x in links],
          {"items": [dataclasses.asdict(x) for x in links]})
    return 0


def _portal_export(u, a):
    out = _out_path(a)
    items = u.portal_export(at=a.at)
    write_private(out, (json.dumps(items, ensure_ascii=False) + "\n").encode("utf-8"))
    _emit(a, [f"コピー {len(items)} 人", f"ファイル {out}"], {"人数": len(items), "ファイル": str(out)})
    return 0


def _verify(u, a):
    ok, n, why = u.ledger.verify_chain()
    if a.json:
        print(json.dumps({"ok": ok, "件数": n, "理由": why}, ensure_ascii=False))
    else:
        print(f"連鎖 正常 {n}" if ok else f"連鎖 異常 {why}")
    return 0 if ok else 1


# ---- 引数 ----
def _at(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"日時が読めません: {s}") from None


def _item(s: str) -> tuple[str, str]:
    """device check --item の 項目=結果 を、最初の = で分ける。"""
    if "=" not in s:
        raise argparse.ArgumentTypeError(f"項目=結果 の形で書いてください: {s}")
    k, v = s.split("=", 1)
    return k, v


def build() -> argparse.ArgumentParser:
    """命令の引数の形。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", help="置き場のディレクトリ(省けば環境変数 DIGITALKEY_KANRI_DIR)")
    common.add_argument("--by", help="操作する人(名簿の主体の識別)。書く命令で要る")
    common.add_argument("--role", help="この操作で使う役(管理者、名簿の係、承認する人、本人 など)。書く命令で要る")
    common.add_argument("--json", action="store_true", help="出力を JSON 一つにする")
    ap = argparse.ArgumentParser(prog="digitalkey kanri", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<命令>")

    def cmd(parent, name, func, help, *, write=True):
        p = parent.add_parser(name, parents=[common], help=help, description=help)
        p.set_defaults(func=func, write=write, parser=p)
        return p

    def group(name, help):
        g = sub.add_parser(name, help=help, description=help)
        return g.add_subparsers(dest="sub", required=True, metavar="<命令>")

    def at(p):
        p.add_argument("--at", type=_at, help="判定に使う時刻(datetime.fromisoformat で読める形、時差つき可。省けば今)")

    def target(p):
        t = p.add_mutually_exclusive_group(required=True)
        t.add_argument("--door", help="扉の名前")
        t.add_argument("--group", help="群の名前")
        t.add_argument("--device", help="機器の名前(その機器の管理者のインターフェースに入る権限。対象の種類 機器)")
        p.add_argument("--from", dest="start", required=True, help="開始(年-月-日T時:分、地方時)")
        p.add_argument("--until", default="", help="終了(省けば期限なし。業者と連携先には要る)")

    p = cmd(sub, "init", _init, "置き場を作る。発行者の識別と公開鍵を出す(--by、--role は要らない)", write=False)
    p.add_argument("--admin", required=True, help="最初の管理者の識別")
    p.add_argument("--admin-name", required=True, help="最初の管理者の呼び名")
    p.add_argument("--admin-kind", default="社員", help="最初の管理者の種別(既定 社員)")
    p.add_argument("--issuer-key", help="発行者の秘密鍵のファイルの道(32 バイト)。省けば 鍵/発行者.key を作る")

    s = group("subject", "名簿(主体の登録、変更、一覧)")
    p = cmd(s, "add", _subject_add, "主体を名簿に載せる(名簿の係)")
    p.add_argument("--id", required=True, help="識別(名簿の中で一つ。「,」は使わない)")
    p.add_argument("--name", required=True, help="呼び名")
    p.add_argument("--kind", required=True, help="種別(社員、清掃、業者、来客 など)")
    p.add_argument("--vendor", action="store_true", help="業者と連携先(期限の無い権限を出さない)")
    p = cmd(s, "change", _subject_change, "主体の項目を変える、止める(名簿の係)。渡した項目だけが変わる")
    p.add_argument("--id", required=True, help="変える主体の識別")
    p.add_argument("--name", help="呼び名")
    p.add_argument("--kind", help="種別")
    p.add_argument("--vendor", choices=["はい", "いいえ"], help="業者と連携先か")
    p.add_argument("--state", choices=["有効", "停止", "退出"], help="状態")
    p.add_argument("--reason", required=True, help="理由")
    cmd(s, "list", lambda u, a: _items(a, u.subjects()), "名簿の一覧", write=False)

    r = group("role", "役の割り当て(管理者)")
    for name, grant, text in (("grant", True, "役を付ける"), ("remove", False, "役を外す")):
        p = cmd(r, name, _role(grant), f"{text}(管理者)")
        p.add_argument("--subject", required=True, help="主体の識別")
        p.add_argument("--name", required=True, help="役(管理者、名簿の係、承認する人、運用、監査)")
        p.add_argument("--reason", required=True, help="理由")

    s = group("door", "この発行者の扉")
    p = cmd(s, "add", _door_add, "扉を書く(管理者)。同じ名前の二枚目が今の値になる")
    p.add_argument("--name", required=True, help="扉の名前(扉のコントローラに入れた名前)")
    p.add_argument("--routes", required=True, help="経路を「,」でつなぐ(署名つき資格情報、門番を通す錠、物理キー)")
    p.add_argument("--place", default="", help="場所")
    p.add_argument("--audit-key", default="", help="扉の記録の公開鍵の 16 進(秘密でない値)")
    p.add_argument("--retire", action="store_true", help="撤去にする")
    p.add_argument("--revocation-max-min", type=int, metavar="N",
                   help="失効が効くまでの最長の分数。扉に据付で入れた --revocation-max-age の秒を分で書く(秒 = 分 × 60)。"
                        "1 以上の整数。省けば空(扉はその規則を使っていない)。二枚目で省くと空に戻る")
    cmd(s, "list", lambda u, a: _items(a, u.doors()),
        "扉の一覧(撤去した扉も入る)。末尾の列は失効の最長の分数(空は決めていない)", write=False)

    s = group("group", "扉の群")
    p = cmd(s, "add", _group_add, "群を書く(管理者)")
    p.add_argument("--name", required=True, help="群の名前")
    p.add_argument("--doors", required=True, help="扉の名前を「,」でつなぐ(この発行者の使用の扉だけ)")
    p.add_argument("--retire", action="store_true", help="廃止にする")

    s = group("setting", "管理者が決める値")
    p = cmd(s, "set", _setting_set, "設定を書く(管理者)")
    p.add_argument("--item", required=True,
                   help="項目(業者の最長の日数、自己承認、点検の間の日数、更新の期限の通知の日数)")
    p.add_argument("--value", default="",
                   help="値(自己承認 は 許す か 許さない、ほかは 1 以上の整数か空。点検の間の日数 と "
                        "更新の期限の通知の日数 が空の間は、その通知を出さない)")
    p.add_argument("--reason", required=True, help="理由")

    cmd(sub, "trust-entry", _trust_entry, "扉の信頼の一覧に入れる形(いつも JSON)", write=False)

    p = cmd(sub, "request", _request, "権限を申請する(本人、名簿の係)")
    p.add_argument("--subject", required=True, help="主体の識別")
    target(p)
    p.add_argument("--reason", required=True, help="理由")

    p = cmd(sub, "import", _import, "外の一覧の CSV から申請だけを作る(名簿の係)。見出しは 識別,対象の種類,対象,開始,終了,理由")
    p.add_argument("--file", required=True, help="CSV のファイル(UTF-8)")
    p.add_argument("--source", required=True, help="一覧の名")

    p = cmd(sub, "decide", _decide, "申請を承認する、却下する(承認する人)")
    p.add_argument("--request", required=True, help="権限_申請の番号")
    d = p.add_mutually_exclusive_group(required=True)
    d.add_argument("--approve", action="store_true", help="承認")
    d.add_argument("--reject", action="store_true", help="却下")
    p.add_argument("--reason", default="", help="理由")

    p = cmd(sub, "issue", _issue, "承認の番号と、承認と同じ値で権限を発行する(管理者)")
    p.add_argument("--approval", required=True, help="権限_承認の番号")
    p.add_argument("--subject", required=True, help="主体の識別")
    target(p)
    at(p)

    s = group("permission", "権限")
    p = cmd(s, "list", _permission_list, "権限の一覧と状態", write=False)
    p.add_argument("--subject", help="主体で絞る")
    at(p)

    s = group("entity", "経路の実体")
    p = cmd(s, "issue", _entity_issue, "発行済みの権限から経路の実体を出す(管理者。物理キーは名簿の係も)")
    p.add_argument("--permission", required=True, help="権限の番号(権限_申請の番号)")
    p.add_argument("--route", required=True, help="経路(署名つき資格情報、門番を通す錠、物理キー、管理者のインターフェース)")
    p.add_argument("--until", help="実体の終了(権限の終了より後にはできない)")
    p.add_argument("--once", action="store_true", help="一回限り(署名つき資格情報だけ)")
    p.add_argument("--key-number", default="", help="物理キーの鍵の番号")
    p.add_argument("--account", default="",
                   help="管理者のインターフェースの名(アカウントの名や鍵の名。パスワードや鍵の値は書かない)。管理者のインターフェースでは要る")
    p.add_argument("--out", help="署名つき資格情報の JSON を書くファイル(権限 600。署名つき資格情報では要る)")
    at(p)
    p = cmd(s, "list", lambda u, a: _items(a, u.entities(a.subject)), "実体の一覧(一人の全経路)", write=False)
    p.add_argument("--subject", help="主体で絞る")

    s = group("physical", "物理キー")
    p = cmd(s, "return", _physical_return, "物理キーの回収か、錠の交換を書く(名簿の係、管理者)")
    p.add_argument("--entity", required=True, help="実体_発行の番号")
    p.add_argument("--result", default="回収", help="回収 か 錠を交換")
    p.add_argument("--note", default="", help="備考")

    p = cmd(sub, "may-open", _may_open, "門番を通す錠: その主体がその扉を開けてよいか", write=False)
    p.add_argument("--subject", required=True, help="主体の識別")
    p.add_argument("--door", required=True, help="扉の名前")
    at(p)

    p = cmd(sub, "revoke", _revoke, "一人の全経路か、一つの権限を止める(管理者、名簿の係)。進み具合を添える")
    w = p.add_mutually_exclusive_group(required=True)
    w.add_argument("--subject", help="一人の全経路を止める主体")
    w.add_argument("--permission", help="止める権限の番号")
    p.add_argument("--reason", required=True, help="理由")
    p.add_argument("--out", help="署名つき資格情報を止めて失効の一覧を作った時に、その一覧の JSON を書くファイル"
                                 "(既にあれば何もせずに拒否。一覧を作らなかった時は作らない)")
    at(p)

    p = cmd(sub, "progress", _progress, "一人の全経路の進み具合の一枚", write=False)
    p.add_argument("--subject", required=True, help="主体の識別")
    at(p)

    p = cmd(sub, "confirm", lambda u, a: _slip(a, u.confirm_stopped(a.subject, at=a.at, **_w(a))),
            "全部の実体が止まったことを確かめて書く(管理者)")
    p.add_argument("--subject", required=True, help="主体の識別")
    at(p)

    s = group("segment", "ネットワークの区画(R-NW-02)")
    p = cmd(s, "add", _segment_add, "区画を書く(管理者)。同じ名前の二枚目が今の値になる")
    p.add_argument("--name", required=True, help="区画の名前(扉の系統、事務、来客、設備 など。「,」は使わない)")
    p.add_argument("--note", default="", help="説明(どの機器をつなぐか、ほかの区画とどう分けているか)")
    p.add_argument("--retire", action="store_true", help="廃止にする(新しく機器を載せない)")
    cmd(s, "list", lambda u, a: _items(a, u.segments()), "区画の一覧(廃止した区画も入る)", write=False)

    s = group("device", "機器の台帳(登録、変更、紛失と廃棄、確かめ、更新、通知)")

    def device_items(p):
        p.add_argument("--owner", help="責任者(名簿の主体の識別。更新、点検、交換をする人)")
        p.add_argument("--place", help="置き場(部屋や建物)")
        p.add_argument("--segment", help="区画の名前(使用の区画)")
        p.add_argument("--door", help="この機器が付く扉の名前(扉、錠、リーダーの時)")
        p.add_argument("--maker", help="製造者")
        p.add_argument("--model", help="型")
        p.add_argument("--serial", help="製造番号")
        p.add_argument("--net-id", help="ネットワークの番号(MAC アドレスなど)")
        p.add_argument("--version", help="今のバージョン")
        p.add_argument("--support-until", help="製造者が更新を出す期限(年-月-日)")
        p.add_argument("--update-mode", choices=["自動", "人"], help="更新の当て方")
        p.add_argument("--secret-ref", help="秘密の在庫表(kagi/)の番号。パスワードや鍵の値は書かない")
        p.add_argument("--log-to", help="記録の送り先(コンピューターの外のどこへ送っているか)")
        p.add_argument("--holds", help="中に残る物(個人の情報、鍵、設定など、廃棄や返却の時に消す物)")

    p = cmd(s, "add", _device_add, "機器を台帳に載せる(管理者)。足りない項目は空のまま載せ、通知に挙げる")
    p.add_argument("--name", required=True, help="機器の名前(台帳の中で一つ。廃棄した機器の名前も使い直さない)")
    p.add_argument("--kind", required=True, help="種類(PC、サーバー、プリンター、ルーター、ネットワークの機器、錠 など。様式のとおり)")
    p.add_argument("--state", required=True, choices=["準備", "使用", "休止"], help="状態")
    p.add_argument("--door-system", action="store_true", help="扉の系統(錠、扉のコントローラ、小さなサーバー、リーダー)に入る")
    device_items(p)

    p = cmd(s, "change", _device_change,
            "機器の項目を変える(管理者)。渡した項目だけが変わる。紛失か廃棄にすると、その機器の権限と管理者のインターフェースを止める")
    p.add_argument("--name", required=True, help="変える機器の名前")
    p.add_argument("--reason", required=True, help="理由")
    p.add_argument("--kind", help="種類")
    p.add_argument("--door-system", choices=["はい", "いいえ"], help="扉の系統に入るか")
    p.add_argument("--state", choices=["準備", "使用", "休止", "紛失", "廃棄"],
                   help="状態(紛失からは戻せる。戻しても失効した権限は戻らない。廃棄の機器は変えられない)")
    device_items(p)
    p.add_argument("--at", type=_at, help="止める時刻(紛失と廃棄の時。datetime.fromisoformat で読める形。省けば今)")

    cmd(s, "list", lambda u, a: _items(a, u.devices()), "機器の一覧(廃棄した機器も入る)", write=False)

    p = cmd(s, "checks", lambda u, a: _items(a, u.checks(a.name)), "その機器の確かめの一覧", write=False)
    p.add_argument("--name", required=True, help="機器の名前")

    p = cmd(s, "check", _device_check,
            "据え付け、点検、廃棄の確かめを書く(管理者、運用、本人。本人は機器の責任者だけ)")
    p.add_argument("--name", required=True, help="機器の名前")
    p.add_argument("--scene", required=True, choices=["据え付け", "点検", "廃棄"], help="場面")
    p.add_argument("--item", type=_item, action="append",
                   help="項目=結果(結果は 良い、悪い、当たらない)。何度でも書ける。項目は " + "、".join(CHECK_ITEMS) +
                        "。据え付けは 初めのパスワード、ルーターの点検は 外部からのアクセス経路 と 見覚えのない設定とアカウント、"
                        "廃棄は 中に残る物 と ネットワークから外した が要る")
    p.add_argument("--note", default="", help="所見(見た事、直した事、次にする事)")
    p.add_argument("--at", type=_at, help="確かめた日時(省けば今)")

    p = cmd(s, "update", _device_update, "更新を当てた事、当てられなかった事を書く(管理者、運用、本人。本人は責任者だけ)")
    p.add_argument("--name", required=True, help="機器の名前")
    p.add_argument("--version", required=True, help="当てたバージョンか、当てようとしたバージョン")
    p.add_argument("--failed", action="store_true", help="当てられなかった(--reason が要る)")
    p.add_argument("--reason", default="", help="理由")
    p.add_argument("--at", type=_at, help="当てた日時(省けば今)")

    p = cmd(s, "findings", lambda u, a: _items(a, u.device_findings(at=a.at)),
            "台帳だけでできる照らし合わせの通知(機器、通知、詳細)。通知があっても終了コードは 0", write=False)
    at(p)

    s = group("access", "機器の管理者のインターフェース")
    p = cmd(s, "remove", _access_remove,
            "人が機器の中で管理者のインターフェースを消した事を書く(管理者、運用、本人。本人は機器の責任者だけ)。ソフトは機器を変えない")
    p.add_argument("--entity", required=True, help="実体_発行の番号(経路 管理者のインターフェース)")
    p.add_argument("--result", default="消した", help="消した、パスワードを替えた、機器を初期化、機器が無い")
    p.add_argument("--note", default="", help="備考")

    s = group("vendor", "業者と連携先に渡している物(R-RS-04)")
    p = cmd(s, "list", _vendor_list,
            "業者と連携先の、発行済みの権限と、止まっていない実体が残る権限の目録。残りは <実体>(<状態>)", write=False)
    at(p)

    s = group("revocation", "失効の一覧(扉へ運ぶ署名つきの一覧と、扉に届いた記録)")
    p = cmd(s, "publish", _revocation_publish,
            "失効の一覧を一枚作る(管理者、運用)。止めてあって期限の過ぎていない署名つき資格情報の全部を入れる。"
            "失効が無くても、扉の通知を新しく保つために作れる")
    p.add_argument("--out", help="署名つきの一覧の JSON を書くファイル(既にあれば何もせずに拒否)")
    at(p)
    p = cmd(s, "show", _revocation_show,
            "署名つきの一覧の JSON を出す(--json でも同じ形。扉の POST /revocation の list にそのまま入れられる)",
            write=False)
    p.add_argument("--generation", type=int, metavar="N", help="世代(省けば最後の一枚)")
    p.add_argument("--out", help="一覧の JSON を書くファイル(既にあれば拒否)。書いた時は 世代 <n> を出す")
    cmd(s, "list", _revocation_list,
        "作った一覧。一件一行 <世代> <発行時刻(unix 秒)> <件数> <番号>", write=False)
    p = cmd(s, "delivered", _revocation_delivered,
            "一覧のその世代が扉に届いた事を書く(管理者、運用)。扉の GET /revocation の応答や画面を見て書く")
    p.add_argument("--door", required=True, help="扉の名前(この置き場の使用の扉)")
    p.add_argument("--generation", required=True, type=int, metavar="N", help="扉が受けたと確かめた世代")
    p.add_argument("--note", default="", help="備考(運び方など)")
    p.add_argument("--at", type=_at, help="扉が受けたと確かめた日時(省けば今)")

    s = group("portal", "窓口の照会(名簿の主体と窓口の番号の結び、照会用のコピー)")
    p = cmd(s, "link", _portal_link,
            "主体に窓口の番号を結ぶ(名簿の係)。本人を対面かビデオ通話で確かめてから書く。結び直すと後の番号が今の結び")
    p.add_argument("--subject", required=True, help="名簿の主体の識別")
    p.add_argument("--account", required=True,
                   help="窓口の番号(窓口の頁に出る番号。英数字だけ。メールアドレスは書かない)")
    p.add_argument("--how", required=True, help="本人を確かめた方法(対面 か ビデオ通話。様式で確かめる)")
    p = cmd(s, "unlink", _portal_unlink, "主体の今の結びを外す(名簿の係)。停止、退出の主体も外せる")
    p.add_argument("--subject", required=True, help="名簿の主体の識別")
    p.add_argument("--reason", required=True, help="理由")
    cmd(s, "list", _portal_list, "今の結び。一件一行 <主体> <窓口の番号> <確かめ方> <伝票>", write=False)
    p = cmd(s, "export", _portal_export,
            "照会用のコピーをファイルに出す。結んだ主体のうち有効と停止の人の分。ネットワークは使わない。"
            "窓口へ置くのは digitalkey madoguchi push", write=False)
    p.add_argument("--out", required=True, help="コピーの JSON を書くファイル(権限 600 で新しく作る。既にあれば拒否)")
    at(p)

    cmd(sub, "verify", _verify, "台帳の連鎖を検べる", write=False)
    return ap


def _refused(args, e: Refused) -> None:
    if args.json:
        print(json.dumps({"ok": False, "理由": e.理由, "詳細": e.詳細}, ensure_ascii=False))
    else:
        sys.stderr.write(f"拒否: {e}\n")


def main(argv: list[str] | None = None) -> int:
    """`digitalkey kanri <命令> ...` を動かし、終了コードを返す。"""
    args = build().parse_args(argv)
    d = args.dir or os.environ.get("DIGITALKEY_KANRI_DIR")
    if not d:
        args.parser.error("--dir か環境変数 DIGITALKEY_KANRI_DIR が要ります")
    if args.write and not (args.by and args.role):
        args.parser.error("書く命令には --by と --role が要ります")
    if getattr(args, "route", None) == CREDENTIAL and not args.out:
        args.parser.error("署名つき資格情報には --out が要ります")
    try:
        if args.func is _init:
            return _init(args, d)
        unit = Unit.open(d)
    except Refused as e:
        if e.理由 in OPEN_ERRORS:
            sys.stderr.write(f"拒否: {e}\n")
            return 3
        _refused(args, e)
        return 1
    try:
        return args.func(unit, args)
    except Refused as e:
        _refused(args, e)
        return 1
