"""スマートフォンアプリの画面(Flet 1.0)。

画面:
  接続      サーバーの URL とトークンを入れて保存する。端末の保存領域に残る
  扉        扉の一覧。自分の鍵がある扉は「開ける」ボタンが出る。管理者・受付は「施錠」も
  鍵        利用者: 自分の鍵の一覧。管理者・受付: 全員の鍵と、発行(名前・扉・開始・終了)と失効
  記録      管理者・受付: 鍵の操作の記録(新しい順)

起動: digitalkey mobile [--web]。Android/iOS は `flet build` で組む(pyproject の [tool.flet])。
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

import flet as ft

from .client import ApiError, Client

SCREENS = ["接続", "扉", "鍵", "記録"]


async def main(page: ft.Page) -> None:
    page.title = "デジタルドア"
    page.padding = 16
    page.theme = ft.Theme(color_scheme_seed="#2E6B5E", use_material3=True)
    prefs = ft.SharedPreferences()
    state = {"client": None, "role": "", "keys": [], "doors": []}

    body = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=12)
    msg = ft.Text("", size=13, color=ft.Colors.ON_SURFACE_VARIANT)

    def note(text: str, error: bool = False) -> None:
        msg.value = text
        msg.color = ft.Colors.ERROR if error else ft.Colors.ON_SURFACE_VARIANT
        page.update()

    async def call(fn, *a):
        try:
            return await asyncio.to_thread(fn, *a)
        except ApiError as e:
            note(f"サーバーの応答: {e.message}", error=True)
        except Exception as e:
            note(f"つながりません: {e}", error=True)
        return None

    # ---- 接続 ----
    url = ft.TextField(label="サーバーの URL", value="http://127.0.0.1:8800")
    tok = ft.TextField(label="トークン", password=True, can_reveal_password=True)

    async def connect(e=None):
        c = Client(url.value.strip(), tok.value.strip())
        r = await call(c.health)
        if not r:
            return
        state["client"] = c
        await prefs.set("digitaldoor.url", url.value.strip())
        await prefs.set("digitaldoor.token", tok.value.strip())
        st = await asyncio.to_thread(lambda: _safe(c.state))
        state["role"] = "管理者" if st else "利用者"
        note(f"{r.get('site', '')} につながりました({state['role']})")
        await show_doors()

    def _safe(fn):
        try:
            return fn()
        except ApiError:
            return None

    def screen_connect():
        body.controls = [ft.Text("接続", size=22, weight=ft.FontWeight.W_600), url, tok,
                         ft.FilledButton("つなぐ", icon=ft.Icons.LINK, on_click=connect)]
        page.update()

    # ---- 扉 ----
    async def show_doors(e=None):
        c = state["client"]
        if c is None:
            return screen_connect()
        doors = await call(c.doors)
        keys = await call(c.keys)
        if doors is None or keys is None:
            return
        state["doors"], state["keys"] = doors, keys
        rows = []
        for d in doors:
            mine = [k for k in keys if k["door"] == d["id"]]
            buttons = []
            if mine:
                buttons.append(ft.FilledButton("開ける", icon=ft.Icons.LOCK_OPEN, on_click=unlock_handler(d["id"], mine[0]["id"])))
            if state["role"] == "管理者":
                buttons.append(ft.OutlinedButton("施錠", icon=ft.Icons.LOCK, on_click=lock_handler(d["id"])))
            rows.append(ft.Container(ft.Row([
                ft.Icon(ft.Icons.LOCK if d["locked"] else ft.Icons.LOCK_OPEN, color=ft.Colors.PRIMARY if d["locked"] else ft.Colors.TERTIARY),
                ft.Column([ft.Text(d["id"], size=16, weight=ft.FontWeight.W_600), ft.Text(("施錠中" if d["locked"] else "解錠中") + " · " + d["mode"], size=12)], spacing=0, expand=True),
                *buttons], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=12, border_radius=12, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW))
        body.controls = [ft.Text("扉", size=22, weight=ft.FontWeight.W_600), *rows]
        page.update()

    def unlock_handler(door, key):
        async def h(e):
            r = await call(state["client"].unlock, door, key)
            if r:
                note(f"{door}: {r['result']} {r.get('reason', '')}", error=r["result"] != "成功")
                await show_doors()
        return h

    def lock_handler(door):
        async def h(e):
            r = await call(state["client"].lock, door)
            if r:
                note(f"{door}: 施錠 {r['result']} {r.get('reason', '')}", error=r["result"] != "成功")
                await show_doors()
        return h

    # ---- 鍵 ----
    async def show_keys(e=None):
        c = state["client"]
        if c is None:
            return screen_connect()
        keys = await call(c.keys)
        if keys is None:
            return
        rows = [ft.Container(ft.Row([
            ft.Column([ft.Text(f"{k['subject']} — {k['door']}", size=15, weight=ft.FontWeight.W_600),
                       ft.Text(f"{k['start']} 〜 {k['end']}", size=12)], spacing=0, expand=True),
            *([ft.TextButton("失効", on_click=revoke_handler(k["id"]))] if state["role"] == "管理者" else [])]),
            padding=12, border_radius=12, bgcolor=ft.Colors.SURFACE_CONTAINER_LOW) for k in keys]
        controls = [ft.Text("鍵", size=22, weight=ft.FontWeight.W_600)]
        if state["role"] == "管理者":
            subject = ft.TextField(label="名前")
            door = ft.Dropdown(label="扉", options=[ft.DropdownOption(d["id"], d["id"]) for d in state["doors"]], value=(state["doors"][0]["id"] if state["doors"] else None))
            now = datetime.now()
            start = ft.TextField(label="開始", value=now.strftime("%Y-%m-%d %H:%M"))
            end = ft.TextField(label="終了", value=(now + timedelta(days=365)).strftime("%Y-%m-%d %H:%M"))

            async def issue(e):
                r = await call(c.issue, subject.value.strip(), door.value, start.value.strip(), end.value.strip())
                if r:
                    note(f"発行しました: {r['subject']} — {r['door']}")
                    await show_keys()

            controls.append(ft.Container(ft.Column([ft.Text("発行", size=16, weight=ft.FontWeight.W_600), subject, door, start, end,
                                                    ft.FilledButton("発行する", icon=ft.Icons.KEY, on_click=issue)], spacing=8),
                                         padding=12, border_radius=12, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)))
        body.controls = controls + rows
        page.update()

    def revoke_handler(key):
        async def h(e):
            r = await call(state["client"].revoke, key, "アプリから失効")
            if r:
                note("失効しました")
                await show_keys()
        return h

    # ---- 記録 ----
    async def show_ops(e=None):
        c = state["client"]
        if c is None:
            return screen_connect()
        ops = await call(c.operations, 50)
        if ops is None:
            return
        rows = [ft.Text(f"{o['at'][:16]}  {o['door']}  {o['op']}  {o['result']}  {o['subject']}  {o['reason']}", size=13, font_family="monospace")
                for o in reversed(ops)]
        body.controls = [ft.Text("記録", size=22, weight=ft.FontWeight.W_600), *rows]
        page.update()

    async def nav(e):
        i = e.control.selected_index
        await [screen_connect_async, show_doors, show_keys, show_ops][i]()

    async def screen_connect_async(e=None):
        screen_connect()

    page.navigation_bar = ft.NavigationBar(on_change=nav, destinations=[
        ft.NavigationBarDestination(icon=ft.Icons.LINK, label="接続"),
        ft.NavigationBarDestination(icon=ft.Icons.DOOR_FRONT_DOOR, label="扉"),
        ft.NavigationBarDestination(icon=ft.Icons.KEY, label="鍵"),
        ft.NavigationBarDestination(icon=ft.Icons.HISTORY, label="記録")])
    page.add(body, msg)

    saved_url = await prefs.get("digitaldoor.url")
    saved_tok = await prefs.get("digitaldoor.token")
    if saved_url and saved_tok:
        url.value, tok.value = saved_url, saved_tok
        await connect()
    else:
        screen_connect()


def run(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    web = "--web" in argv
    ft.run(main, view=ft.AppView.WEB_BROWSER if web else ft.AppView.FLET_APP,
           port=int(os.environ.get("DIGITALKEY_MOBILE_PORT", "8801")) if web else 0)
    return 0


if __name__ == "__main__":
    sys.exit(run())
