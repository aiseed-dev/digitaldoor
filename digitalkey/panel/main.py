"""盤 — Flet。ペアリングの QR、錠と扉の状態、施錠と解錠、開発用の出来事(火災・停電・扉)。判断は door。"""
from __future__ import annotations

import asyncio
import os
import threading

import flet as ft

from .door_client import Door
from .matter_node import MatterNode

_lock = threading.Lock()
_node: MatterNode | None = None


def shared_node(door: Door) -> MatterNode:
    """Matter の機器はプロセスに一つ(UDP 5541 を一度だけ開く)。画面の接続ごとには作らない。"""
    global _node
    with _lock:
        if _node is None:
            _node = MatterNode(door, os.environ.get("DIGITALKEY_DOOR_DIR", "vault"))
            _node.start()
        return _node


async def main(page: ft.Page):
    page.title = "digitalkey 扉の盤"
    door = Door(os.environ.get("DIGITALKEY_DOOR_URL", "http://127.0.0.1:8797"))
    node = shared_node(door)

    status = ft.Text("door に接続中…", size=18)
    mode = ft.Text("")
    audit = ft.Text("", size=12)
    pairing = ft.Text(f"手動コード: {node.manual_code}", size=16, selectable=True)
    qr = ft.Image(src=node.qr_png(), width=220, height=220)
    err = ft.Text("", color=ft.Colors.RED)

    async def do(fn, *a, **kw):
        try:
            await asyncio.to_thread(fn, *a, **kw)
        except Exception as e:
            err.value = str(e)
        await refresh()

    def act(fn, *a, **kw):
        """ボタンの手。lambda がコルーチンを返しても Flet は待たないので、async の関数そのものを渡す。"""
        async def handler(e):
            await do(fn, *a, **kw)
        return handler

    async def refresh(_=None):
        try:
            s = await asyncio.to_thread(node.mirror)
            status.value = ("施錠" if s["locked"] else "解錠") + ("・扉が開いている" if s["door_open"] else "・扉は閉")
            mode.value = f"状態: {s['mode']}  火災信号: {'入' if s['fire'] else '無'}  電源: {'あり' if s['mains'] else '停電'}  記録 {s['audit_seq']} 件"
            rows = await asyncio.to_thread(door.audit, max(0, s["audit_seq"] - 6))
            audit.value = "\n".join(f"{r['seq']:>4} {r['kind']} {r['event']} {r['result']} {r['subject']} {r['detail']}" for r in rows)
            err.value = node.last_error
            pairing.value = ("ペアリング済み" if node.commissioned else f"手動コード: {node.manual_code}")
        except Exception as e:
            status.value = "door に届きません"
            err.value = str(e)
        page.update()

    page.add(
        ft.Row([
            ft.Column([ft.Text("Matter のペアリング", size=16), qr, pairing], tight=True),
            ft.Column([
                status, mode, err,
                ft.Row([
                    ft.Button("施錠", on_click=act(door.lock, "盤")),
                    ft.Button("解錠", on_click=act(door.unlock, "盤")),
                ]),
                ft.Text("開発用の出来事", size=14),
                ft.Row([
                    ft.Button("火災 入", on_click=act(door.event, fire=True)),
                    ft.Button("火災 復旧", on_click=act(door.event, fire=False)),
                    ft.Button("停電", on_click=act(door.event, mains=False)),
                    ft.Button("復電", on_click=act(door.event, mains=True)),
                    ft.Button("扉 開", on_click=act(door.event, door_open=True)),
                    ft.Button("扉 閉", on_click=act(door.event, door_open=False)),
                ], wrap=True),
                ft.Text("監査の記録(直近)", size=14), audit,
            ], expand=True),
        ], vertical_alignment=ft.CrossAxisAlignment.START),
    )

    async def loop():
        while True:
            await refresh()
            await asyncio.sleep(1)

    page.run_task(loop)


if __name__ == "__main__":
    ft.run(main)
