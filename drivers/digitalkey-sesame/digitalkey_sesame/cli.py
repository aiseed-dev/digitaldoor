#!/usr/bin/env python3
"""Command-line client (no GUI) for CANDY HOUSE Sesame 5/6/6 Pro over BLE via bleak.

  digitalkey sesame scan [--seconds 5]
  digitalkey sesame register <uuid-or-address> [--name 玄関]
  digitalkey sesame import <uuid-or-address> [--name 玄関]
  digitalkey sesame status|lock|unlock|toggle|version <uuid-or-address>
  digitalkey sesame keys
  digitalkey sesame --fake <command> ...      疑似の Sesame(実機も Bluetooth も使わない。鍵は sesame-keys-fake.json)
Keys are stored in ~/.config/digitalkey/sesame-keys.json (or $SESAME_KEYSTORE).

鍵(16 バイト、32 桁の 16 進)は、この Sesame を開けられる唯一の物です。ログにも標準出力にも出しません。
別の端末(スマートフォンのアプリ)の鍵を写すときは、アプリの管理者の画面の「渡す」で「32 桁を見せる」を選んで 32 桁を出し、
この機械で `import` します(鍵は引数で受けず、伏せた入力で受けます)。

鍵には役があります。この機械で `register` した鍵は管理者、`import` した鍵は、管理者の符号を添えたときだけ管理者で、
符号が無ければ利用者です(符号は、スマートフォンの管理者が「管理者として渡す」ときに画面に出す 8 桁です)。
役はアプリの画面の歯止めで、鍵の守りではありません。この命令行は、役にかかわらず鍵があれば Sesame を動かせます。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from getpass import getpass

from digitalkey_sesame import protocol as p
from digitalkey_sesame.device import SesameError
from digitalkey_sesame.handoff import check_admin_code, normalize_admin_code
from digitalkey_sesame.keystore import (ROLE_ADMIN, ROLE_LABELS, ROLE_USER, DeviceKey, KeyStore, fake_path,
                                        parse_secret_hex)
from digitalkey_sesame.transport_bleak import BleakSesameConnection, SesameScanner, adapter_available


def fmt_adv(adv: p.Advertisement) -> str:
    reg = "registered" if adv.is_registered else "UNREGISTERED"
    return (f"{adv.model_name:<16} uuid={adv.device_uuid} addr={adv.address} "
            f"rssi={adv.rssi} name={adv.name!r} {reg}")


class Backend:
    """BLE の実機(bleak)か、疑似の Sesame(`--fake`)か。命令の側は、どちらかを知らずに使う。"""

    def __init__(self, fake: bool) -> None:
        self.fake = fake
        if fake:
            from digitalkey_sesame.fake import FakeConnection, fake_adapter_available, fake_scan_once
            self.scan_once, self.Connection, self.adapter_available = fake_scan_once, FakeConnection, fake_adapter_available
        else:
            self.scan_once, self.Connection, self.adapter_available = (
                SesameScanner.scan_once, BleakSesameConnection, adapter_available)

    def keystore(self) -> KeyStore:
        return KeyStore(path=fake_path()) if self.fake else KeyStore()


async def find(target: str, seconds: float, backend: Backend | None = None):
    target = target.lower()
    for adv, dev in await (backend or Backend(False)).scan_once(seconds):
        if target in (str(adv.device_uuid), getattr(dev, "address", adv.address).lower()):
            return adv, dev
    return None, None


async def cmd_scan(args):
    ok, msg = await args.backend.adapter_available()
    print(msg)
    if not ok:
        return 1
    found = await args.backend.scan_once(args.seconds)
    if not found:
        print("no CANDY HOUSE devices found")
    for adv, _ in found:
        print(fmt_adv(adv))
    return 0


async def connect(args, ks: KeyStore):
    adv, dev = await find(args.target, args.seconds, args.backend)
    if dev is None:
        print("device not found (is it advertising? another phone connected?)")
        sys.exit(2)
    print(fmt_adv(adv))
    conn = args.backend.Connection(dev, on_log=lambda m: print("  " + m), history_tag=args.tag)
    await conn.connect()
    return adv, conn


async def cmd_register(args):
    ks = args.backend.keystore()
    adv, conn = await connect(args, ks)
    try:
        if adv.is_registered:
            print("advertisement says the device is already registered; trying anyway")
        secret = await conn.device.register()
        key = DeviceKey(device_uuid=str(adv.device_uuid), model_id=adv.model_id, secret_hex=secret.hex(),
                        name=args.name or adv.model_name, address=adv.address, registered_at=time.time())
        await ks.put(key)
        print(f"registered. key saved to {ks._path}")   # 鍵そのものは出さない(32 桁を見せるのは app の管理者の画面の「渡す」だけ)
        print(f"state={conn.device.state}")
    finally:
        await conn.disconnect()
    return 0


async def cmd_import(args):
    """別の端末(スマートフォンのアプリ)で登録した Sesame の鍵を、この機械の鍵の置き場に取り込む。

    流れ: 伏せた入力で 32 桁を受ける → 伏せた入力で管理者の符号を受ける(任意。空の Enter なら利用者) → 形を確かめる
    (32 桁の 16 進でなければ断る。符号は入力があれば 16 進 8 桁) → Sesame を探して BLE でつなぐ → 符号が鍵と合うか確かめる
    (合わなければ断る) → その鍵でログインできることを確かめる → できたときだけ、機種(広告の model_id)と役と一緒に保存する。
    符号が合ったときだけ管理者で、符号が無ければ利用者として入る。
    ログインできなければ何も保存しない。確かめは必ず行う(間違った鍵を置くと、使う場面で初めて動かないと分かるため。飛ばす選択肢は置かない)。
    保存した鍵は鍵の置き場の権限(600)で守られる。鍵を画面にもログにも出さない。
    """
    ks = args.backend.keystore()
    try:
        secret = parse_secret_hex(read_secret())
        code = read_admin_code().strip()
        if code:
            normalize_admin_code(code)
    except ValueError as e:     # HandoffError も ValueError。文に入力は入っていない
        print(e, file=sys.stderr)
        return 1
    adv, dev = await find(args.target, args.seconds, args.backend)
    if dev is None:
        print("device not found (is it advertising? another phone connected?)", file=sys.stderr)
        return 2
    print(fmt_adv(adv))
    if not adv.is_registered:
        print("この Sesame は未登録です。import は登録済みの Sesame の鍵を写す命令です(未登録なら register を使います)", file=sys.stderr)
        return 1
    if code and not check_admin_code(secret, str(adv.device_uuid), code):
        print("管理者の符号がこの鍵と合いません。保存しません(符号の無い利用者として入れるときは、符号を空にします)", file=sys.stderr)
        return 1
    role = ROLE_ADMIN if code else ROLE_USER
    conn = args.backend.Connection(dev, history_tag=args.tag)
    try:
        await conn.connect()
        await conn.device.login(secret)
    except SesameError as e:
        print(f"ログインできませんでした。鍵が違うか、別の端末がつないでいるかもしれません。保存しません({e})", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Sesame につなげませんでした。保存しません({type(e).__name__}: {e})", file=sys.stderr)
        return 2
    finally:
        await conn.disconnect()
    replaced = await ks.get(str(adv.device_uuid)) is not None
    await ks.put(DeviceKey(device_uuid=str(adv.device_uuid), model_id=adv.model_id, secret_hex=secret.hex(),
                           name=args.name or adv.model_name, address=adv.address, registered_at=time.time(), role=role))
    print(f"ログインできました。{'既にあった鍵を置き換えて、' if replaced else ''}{ks._path} に保存しました"
          f"(uuid={adv.device_uuid} model={adv.model_id} 役={ROLE_LABELS[role]})")
    return 0


def read_secret() -> str:
    """鍵の 32 桁を、伏せた入力で受ける(画面に出ず、シェルの履歴にも残らない)。試験ではこれを差し替える。"""
    return getpass("Sesame の鍵(32 桁の 16 進。入力は画面に出ません): ")


def read_admin_code() -> str:
    """管理者の符号を、伏せた入力で任意に受ける(空の Enter なら利用者として取り込む)。試験ではこれを差し替える。"""
    return getpass("管理者の符号(管理者として写すときだけ。利用者なら空のまま Enter。入力は画面に出ません): ")


async def cmd_action(args):
    ks = args.backend.keystore()
    adv, conn = await connect(args, ks)
    try:
        key = await ks.get(str(adv.device_uuid))
        if key is None:
            print("no saved key for this device; run 'register' (or 'import') first")
            return 3
        await conn.device.login(key.secret)
        d = conn.device
        if args.command == "lock":
            await d.lock()
        elif args.command == "unlock":
            await d.unlock()
        elif args.command == "toggle":
            await d.toggle()
        elif args.command == "version":
            print("version:", await d.get_version())
        await asyncio.sleep(1.5)  # let the mech-status publish arrive
        ms = d.mech_status
        if ms:
            print(f"state={ms.state} position={ms.position} battery={ms.battery_voltage:.2f}V "
                  f"({ms.battery_percent}%) low_battery={ms.is_low_battery}")
        else:
            print("no mech status received yet")
    finally:
        await conn.disconnect()
    return 0


async def cmd_keys(args):
    """保存した鍵の一覧(UUID、機種、役、名前)。鍵そのものは出さない。"""
    for k in (await args.backend.keystore().all()).values():
        print(f"{k.device_uuid}  {k.model_id:<14} {ROLE_LABELS[k.role]}  {k.name!r}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="digitalkey sesame", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--fake", action="store_true", help="疑似の Sesame を使う(実機も Bluetooth も使わない。鍵は sesame-keys-fake.json)")
    ap.add_argument("--seconds", type=float, default=5.0, help="scan duration")
    ap.add_argument("--tag", default="cli", help="history tag written into the lock's log")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    sub.add_parser("keys", help="保存した鍵の一覧(役付き。鍵そのものは出さない)")
    r = sub.add_parser("register")
    r.add_argument("target")
    r.add_argument("--name", default="")
    i = sub.add_parser("import", help="別の端末で登録した Sesame の鍵を取り込む(鍵と管理者の符号は伏せた入力で受ける)",
                       description=cmd_import.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog="鍵はコマンドの引数に書かせません(シェルの履歴に残るため)。実行すると 32 桁を尋ね、続けて管理者の符号を尋ねます。"
                              "区切りの空白とハイフンは読み飛ばすので、アプリの「32 桁を見せる」の表示をそのまま貼れます。"
                              "管理者の符号は、管理者として写すときだけ入れます(利用者なら空の Enter)。")
    i.add_argument("target", help="Sesame の UUID か BLE の住所")
    i.add_argument("--name", default="", help="鍵の置き場に残す名前(既定は機種名)")
    for c in ("status", "lock", "unlock", "toggle", "version"):
        sub.add_parser(c).add_argument("target")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    args.backend = Backend(args.fake)
    fn = {"scan": cmd_scan, "keys": cmd_keys, "register": cmd_register, "import": cmd_import}.get(args.command, cmd_action)
    return asyncio.run(fn(args))


if __name__ == "__main__":
    sys.exit(main())


def command(argv: list[str]) -> int:
    """`digitalkey sesame ...` の入口(entry point digitalkey.commands)。`app` は画面、それ以外は命令行。"""
    if argv and argv[0] == "app":
        from .app import run
        return run(argv[1:])
    return main(argv)
