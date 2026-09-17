#!/usr/bin/env python3
"""Command-line client (no GUI) for CANDY HOUSE Sesame 5/6/6 Pro over BLE via bleak.

  digitalkey sesame scan [--seconds 5]
  digitalkey sesame register <uuid-or-address> [--name 玄関]
  digitalkey sesame status|lock|unlock|toggle|version <uuid-or-address>
  digitalkey sesame keys
Keys are stored in ~/.config/digitalkey/sesame-keys.json (or $SESAME_KEYSTORE).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from digitalkey.sesame import protocol as p
from digitalkey.sesame.keystore import DeviceKey, KeyStore
from digitalkey.sesame.transport_bleak import BleakSesameConnection, SesameScanner, adapter_available


def fmt_adv(adv: p.Advertisement) -> str:
    reg = "registered" if adv.is_registered else "UNREGISTERED"
    return (f"{adv.model_name:<16} uuid={adv.device_uuid} addr={adv.address} "
            f"rssi={adv.rssi} name={adv.name!r} {reg}")


async def find(target: str, seconds: float):
    target = target.lower()
    for adv, dev in await SesameScanner.scan_once(seconds):
        if target in (str(adv.device_uuid), dev.address.lower()):
            return adv, dev
    return None, None


async def cmd_scan(args):
    ok, msg = await adapter_available()
    print(msg)
    if not ok:
        return 1
    found = await SesameScanner.scan_once(args.seconds)
    if not found:
        print("no CANDY HOUSE devices found")
    for adv, _ in found:
        print(fmt_adv(adv))
    return 0


async def connect(args, ks: KeyStore):
    adv, dev = await find(args.target, args.seconds)
    if dev is None:
        print("device not found (is it advertising? another phone connected?)")
        sys.exit(2)
    print(fmt_adv(adv))
    conn = BleakSesameConnection(dev, on_log=lambda m: print("  " + m), history_tag=args.tag)
    await conn.connect()
    return adv, conn


async def cmd_register(args):
    ks = KeyStore()
    adv, conn = await connect(args, ks)
    try:
        if adv.is_registered:
            print("advertisement says the device is already registered; trying anyway")
        secret = await conn.device.register()
        key = DeviceKey(device_uuid=str(adv.device_uuid), model_id=adv.model_id, secret_hex=secret.hex(),
                        name=args.name or adv.model_name, address=adv.address, registered_at=time.time())
        await ks.put(key)
        print(f"registered. secret={secret.hex()} saved to {ks._path}")
        print(f"state={conn.device.state}")
    finally:
        await conn.disconnect()
    return 0


async def cmd_action(args):
    ks = KeyStore()
    adv, conn = await connect(args, ks)
    try:
        key = await ks.get(str(adv.device_uuid))
        if key is None:
            print("no saved key for this device; run 'register' first")
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


async def cmd_keys(_args):
    for k in (await KeyStore().all()).values():
        print(f"{k.device_uuid}  {k.model_id:<14} {k.name!r}  secret={k.secret_hex}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="digitalkey sesame", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--seconds", type=float, default=5.0, help="scan duration")
    ap.add_argument("--tag", default="cli", help="history tag written into the lock's log")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    sub.add_parser("keys")
    r = sub.add_parser("register")
    r.add_argument("target")
    r.add_argument("--name", default="")
    for c in ("status", "lock", "unlock", "toggle", "version"):
        sub.add_parser(c).add_argument("target")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    fn = {"scan": cmd_scan, "keys": cmd_keys, "register": cmd_register}.get(args.command, cmd_action)
    return asyncio.run(fn(args))


if __name__ == "__main__":
    sys.exit(main())
