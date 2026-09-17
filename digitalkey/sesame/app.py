"""CANDY HOUSE Sesame (SesameOS3) BLE controller – Flet app.

Desktop:          digitalkey sesame app            (needs a Bluetooth LE adapter)
Web preview:      digitalkey sesame app --web      (UI in browser; BLE still runs on this PC)
Demo w/o device:  digitalkey sesame app --fake     (in-process fake Sesame)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Optional

import flet as ft

from digitalkey.sesame import protocol as p
from digitalkey.sesame.keystore import DeviceKey, KeyStore

FAKE_MODE = "--fake" in sys.argv
MOBILE_PLATFORMS = {ft.PagePlatform.ANDROID, ft.PagePlatform.ANDROID_TV, ft.PagePlatform.IOS}


class Backend:
    """Bundles the three transport entry points so main.py does not care whether
    BLE is done by bleak (desktop), the flet-ble extension (Android/iOS) or a fake."""

    def __init__(self, name, make_scanner, make_connection, adapter_available):
        self.name = name
        self.make_scanner = make_scanner          # (on_adv) -> scanner with start()/stop()
        self.make_connection = make_connection    # (target, **kw) -> connection with .device
        self.adapter_available = adapter_available  # async () -> (ok, msg)


def make_backend(page: ft.Page) -> Backend:
    if FAKE_MODE:
        from digitalkey.sesame.fake import FakeConnection, FakeScanner, fake_adapter_available
        return Backend("fake", FakeScanner, FakeConnection, fake_adapter_available)
    if page.platform in MOBILE_PLATFORMS:
        from digitalkey.sesame.transport_fletble import FletBleHub
        hub = FletBleHub(page)
        return Backend("flet-ble", hub.make_scanner, hub.make_connection, hub.adapter_available)
    try:
        from digitalkey.sesame.transport_bleak import (BleakSesameConnection, SesameScanner,
                                                adapter_available)
    except Exception as e:  # no bleak backend on this platform
        async def unavailable():
            return False, f"bleak を読み込めません: {e}"
        return Backend("none", None, None, unavailable)
    return Backend("bleak", SesameScanner, BleakSesameConnection, adapter_available)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

MAX_LOG_LINES = 300


class SesameApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.keystore: KeyStore
        self.backend: Optional[Backend] = None
        self.scanner: Optional[SesameScanner] = None
        self.found: dict[str, tuple[p.Advertisement, object]] = {}
        self.tiles: dict[str, ft.ListTile] = {}
        self.keys: dict[str, DeviceKey] = {}
        self.conn: Optional[BleakSesameConnection] = None
        self.current_adv: Optional[p.Advertisement] = None
        self.busy = False
        self._list_dirty = False
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        pg = self.page
        pg.title = "Sesame BLE"
        pg.padding = 12
        pg.appbar = ft.AppBar(
            leading=ft.Icon(ft.Icons.LOCK),
            title=ft.Text("Sesame 6 Pro BLE"),
            actions=[
                ft.IconButton(icon=ft.Icons.BLUETOOTH_SEARCHING, tooltip="スキャン開始/停止",
                              on_click=self.on_toggle_scan),
            ],
        )

        self.adapter_text = ft.Text("Bluetooth: 確認中…", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.scan_button = ft.Button(content="スキャン開始", icon=ft.Icons.BLUETOOTH_SEARCHING,
                                     on_click=self.on_toggle_scan)
        # plain Column: a nested scrollable ListView swallowed taps on Android
        self.device_list = ft.Column(spacing=2)
        self.list_placeholder = ft.Text("スキャンを開始すると、近くの Sesame が表示されます。",
                                        size=12, color=ft.Colors.ON_SURFACE_VARIANT)

        # --- device panel
        self.dev_title = ft.Text("未接続", size=18, weight=ft.FontWeight.BOLD)
        self.dev_sub = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.state_icon = ft.Icon(ft.Icons.HELP_OUTLINE, size=40)
        self.state_text = ft.Text("—", size=28, weight=ft.FontWeight.BOLD)
        self.battery_text = ft.Text("電池: —", size=13)
        self.position_text = ft.Text("角度: —", size=13)
        self.version_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.progress = ft.ProgressRing(width=20, height=20, stroke_width=2, visible=False)

        self.btn_unlock = ft.Button(content="解錠", icon=ft.Icons.LOCK_OPEN, on_click=self.on_unlock)
        self.btn_lock = ft.Button(content="施錠", icon=ft.Icons.LOCK, on_click=self.on_lock)
        self.btn_toggle = ft.Button(content="トグル", icon=ft.Icons.SYNC, on_click=self.on_toggle)
        self.btn_status = ft.OutlinedButton(content="状態更新", icon=ft.Icons.REFRESH, on_click=self.on_refresh)
        self.btn_version = ft.OutlinedButton(content="バージョン", icon=ft.Icons.INFO_OUTLINE, on_click=self.on_version)
        self.btn_register = ft.Button(content="この Sesame を登録", icon=ft.Icons.APP_REGISTRATION,
                                      bgcolor=ft.Colors.ORANGE, color=ft.Colors.WHITE,
                                      on_click=self.on_register, visible=False)
        self.btn_disconnect = ft.OutlinedButton(content="切断", icon=ft.Icons.LINK_OFF, on_click=self.on_disconnect)
        self.btn_forget_key = ft.TextButton(content="鍵を削除", icon=ft.Icons.DELETE_OUTLINE, on_click=self.on_forget_key)
        self.tag_field = ft.TextField(label="履歴タグ (Sesame の履歴に残る名前)", value="flet", dense=True,
                                      width=280, on_change=self.on_tag_change)

        self.panel = ft.Card(
            content=ft.Container(
                padding=14,
                content=ft.Column(
                    spacing=8,
                    controls=[
                        ft.Row([self.dev_title, self.progress], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        self.dev_sub,
                        ft.Row([self.state_icon, self.state_text,
                                ft.Column([self.battery_text, self.position_text], spacing=2)],
                               spacing=16),
                        self.version_text,
                        ft.Row([self.btn_register, self.btn_forget_key], wrap=True),
                        ft.Row([self.btn_unlock, self.btn_lock, self.btn_toggle], wrap=True),
                        ft.Row([self.btn_status, self.btn_version, self.btn_disconnect], wrap=True),
                        self.tag_field,
                    ],
                ),
            ),
        )

        # newest line first; auto_scroll would also drag the outer page to the bottom on every update
        self.log_view = ft.ListView(spacing=0, height=230)

        pg.add(
            ft.Column(
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                spacing=8,
                controls=[
                    ft.Row([self.scan_button, self.adapter_text], wrap=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Text("見つかった Sesame", weight=ft.FontWeight.BOLD),
                    self.list_placeholder,
                    self.device_list,
                    self.panel,
                    ft.Text("ログ", weight=ft.FontWeight.BOLD),
                    ft.Container(content=self.log_view, height=240,
                                 border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                                 border_radius=6, padding=6),
                ],
            )
        )
        self._set_connected_ui(False)

    # ----------------------------------------------------------- helpers
    def logline(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_view.controls.insert(0, ft.Text(f"{ts} {msg}", size=11, font_family="monospace", selectable=True))
        del self.log_view.controls[MAX_LOG_LINES:]
        log.info(msg)
        self.page.update()

    def snack(self, msg: str, error: bool = False) -> None:
        self.page.show_dialog(ft.SnackBar(content=ft.Text(msg),
                                          bgcolor=ft.Colors.ERROR if error else None))
        self.page.update()

    def _close_dialog(self, dlg: ft.AlertDialog) -> None:
        dlg.open = False
        dlg.update()
        self.page.update()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.progress.visible = busy
        for b in (self.btn_unlock, self.btn_lock, self.btn_toggle, self.btn_status,
                  self.btn_version, self.btn_register):
            b.disabled = busy or not (self.conn and self.conn.is_connected)
        self.page.update()

    def _set_connected_ui(self, connected: bool) -> None:
        logged_in = bool(connected and self.conn and self.conn.device.logged_in)
        for b in (self.btn_unlock, self.btn_lock, self.btn_toggle, self.btn_status, self.btn_version):
            b.disabled = not logged_in or self.busy
        self.btn_disconnect.disabled = not connected
        adv = self.current_adv
        has_key = bool(adv and adv.device_uuid and str(adv.device_uuid) in self.keys)
        self.btn_register.visible = bool(connected and not logged_in)
        self.btn_register.disabled = self.busy
        self.btn_forget_key.visible = has_key
        if not connected:
            self.dev_title.value = "未接続"
            self.state_icon.icon = ft.Icons.HELP_OUTLINE
            self.state_text.value = "—"
        self.page.update()

    def _render_status(self) -> None:
        if not self.conn:
            return
        d = self.conn.device
        ms = d.mech_status
        if ms is None:
            self.state_text.value = "ログイン済" if d.logged_in else "未ログイン"
            self.state_icon.icon = ft.Icons.BLUETOOTH_CONNECTED
        else:
            if ms.state == "locked":
                self.state_text.value, self.state_icon.icon, self.state_icon.color = "施錠", ft.Icons.LOCK, ft.Colors.GREEN
            elif ms.state == "unlocked":
                self.state_text.value, self.state_icon.icon, self.state_icon.color = "解錠", ft.Icons.LOCK_OPEN, ft.Colors.ORANGE
            else:
                self.state_text.value, self.state_icon.icon, self.state_icon.color = "動作中", ft.Icons.SYNC, ft.Colors.BLUE
            low = " ⚠低電圧" if ms.is_low_battery else ""
            self.battery_text.value = f"電池: {ms.battery_voltage:.2f} V (約{ms.battery_percent}%){low}"
            self.position_text.value = f"角度: {ms.position}°" + (f" → {ms.target}°" if ms.target is not None else "")
        if d.version:
            self.version_text.value = f"firmware: {d.version}"
        self._set_connected_ui(self.conn.is_connected)

    def _render_list(self) -> None:
        """Update tiles in place (re-creating controls on every RSSI update would
        invalidate a click that is in flight)."""
        for key, (adv, _dev) in self.found.items():
            uuid_s = str(adv.device_uuid) if adv.device_uuid else "?"
            has_key = uuid_s in self.keys
            if has_key:
                badge, color = "鍵あり", ft.Colors.GREEN
            elif adv.is_registered:
                badge, color = "登録済 (鍵なし)", ft.Colors.RED
            else:
                badge, color = "未登録 → 登録可", ft.Colors.ORANGE
            name = self.keys[uuid_s].name if has_key and self.keys[uuid_s].name else adv.model_name
            title = f"{name}  ({adv.model_name})"
            subtitle = f"{uuid_s}  RSSI {adv.rssi} dBm  {adv.address}  [{badge}]"
            tile = self.tiles.get(key)
            if tile is None:
                tile = ft.ListTile(
                    dense=True,
                    leading=ft.Icon(ft.Icons.LOCK if adv.is_lock else ft.Icons.DEVICES, color=color),
                    title=ft.Text(title),
                    subtitle=ft.Text(subtitle, size=11),
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                    on_click=lambda e, k=key: self.page.run_task(self.connect_to, k),
                )
                self.tiles[key] = tile
                self.device_list.controls.append(tile)
            else:
                tile.leading.color = color
                tile.title.value = title
                tile.subtitle.value = subtitle
        self.list_placeholder.visible = not self.found
        self.page.update()

    # ---------------------------------------------------------- lifecycle
    async def start(self) -> None:
        pf = self.page.platform
        desktop = pf in (ft.PagePlatform.LINUX, ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS) or self.page.web
        if FAKE_MODE:
            from digitalkey.sesame.keystore import default_path
            self.keystore = KeyStore(path=default_path().with_name("sesame-keys-fake.json"))
        elif desktop:
            self.keystore = KeyStore()  # JSON file shared with `digitalkey sesame`
        else:
            self.keystore = KeyStore(kv=ft.SharedPreferences())
        self.keys = await self.keystore.all()
        try:
            self.backend = make_backend(self.page)
        except Exception as e:
            self.adapter_text.value = f"Bluetooth: 初期化失敗 ({e})"
            self.adapter_text.color = ft.Colors.ERROR
            self.scan_button.disabled = True
            self.logline(f"backend init failed: {e}")
            self.page.update()
            return
        self.logline(f"platform={pf} backend={self.backend.name} saved keys={len(self.keys)}")
        if self.backend.make_scanner is None:
            self.adapter_text.value = "Bluetooth: 利用不可"
            self.adapter_text.color = ft.Colors.ERROR
            self.scan_button.disabled = True
            self.page.update()
            return
        ok, msg = await self.backend.adapter_available()
        self.adapter_text.value = f"Bluetooth: {msg}"
        self.adapter_text.color = ft.Colors.GREEN if ok else ft.Colors.ERROR
        # on mobile the plugin can still turn Bluetooth on / ask for permissions at scan time
        self.scan_button.disabled = not ok and self.backend.name != "flet-ble"
        if not ok:
            self.logline(msg if self.backend.name == "flet-ble" else
                         "Bluetooth アダプタが見つかりません。USB BLE ドングルを挿して bluetooth.service を起動してください。")
        self.page.update()
        self.page.run_task(self._list_refresher)

    async def _list_refresher(self) -> None:
        while True:
            await asyncio.sleep(0.7)
            if self._list_dirty:
                self._list_dirty = False
                self._render_list()

    # -------------------------------------------------------------- scan
    def _on_adv(self, adv: p.Advertisement, dev) -> None:
        key = str(adv.device_uuid) if adv.device_uuid else adv.address
        first = key not in self.found
        self.found[key] = (adv, dev)
        if first:
            self.logline(f"found {adv.model_name} uuid={adv.device_uuid} rssi={adv.rssi} "
                         f"{'registered' if adv.is_registered else 'UNREGISTERED'}")
        self._list_dirty = True

    async def on_toggle_scan(self, e=None) -> None:
        if self.scanner is None:
            self.scanner = self.backend.make_scanner(self._on_adv)
            try:
                await self.scanner.start()
            except Exception as ex:
                self.scanner = None
                self.logline(f"scan failed: {ex}")
                self.snack(f"スキャン開始に失敗: {ex}", error=True)
                return
            self.scan_button.content = "スキャン停止"
            self.scan_button.icon = ft.Icons.STOP
            self.logline("scanning…")
        else:
            await self.stop_scan()
        self.page.update()

    async def stop_scan(self) -> None:
        if self.scanner is not None:
            await self.scanner.stop()
            self.scanner = None
            self.scan_button.content = "スキャン開始"
            self.scan_button.icon = ft.Icons.BLUETOOTH_SEARCHING
            self.logline("scan stopped")
            self.page.update()

    # ----------------------------------------------------------- connect
    async def connect_to(self, key: str) -> None:
        if self.busy:
            return
        adv, dev = self.found[key]
        await self.stop_scan()
        if self.conn is not None:
            await self.on_disconnect()
        self.current_adv = adv
        self.dev_title.value = f"{adv.model_name}"
        self.dev_sub.value = f"uuid {adv.device_uuid}  addr {adv.address}"
        self.set_busy(True)
        self.logline(f"connecting to {adv.address} …")
        conn = self.backend.make_connection(dev, on_status=lambda d: self._render_status(),
                                     on_log=self.logline, on_disconnect=self._on_disconnected,
                                     history_tag=self.tag_field.value or "flet")
        try:
            await conn.connect()
        except Exception as ex:
            self.logline(f"connect failed: {ex}")
            self.snack(f"接続失敗: {ex}", error=True)
            self.set_busy(False)
            self._set_connected_ui(False)
            return
        self.conn = conn
        self.logline("connected, token received")
        uuid_s = str(adv.device_uuid)
        saved = self.keys.get(uuid_s)
        if saved is not None:
            try:
                await conn.device.login(saved.secret)
                self.snack("ログインしました")
            except Exception as ex:
                self.logline(f"login failed: {ex}")
                self.snack(f"ログイン失敗: {ex}", error=True)
        elif not adv.is_registered:
            self.logline("device is unregistered → 「この Sesame を登録」を押してください")
            self.snack("未登録の Sesame です。「登録」ボタンでペアリングできます。")
        else:
            self.logline("device is registered but no key saved here. "
                         "他のアプリで登録済みなら、その鍵が必要です（またはリセットして再登録）")
            self.snack("登録済みですが鍵がありません", error=True)
        self.set_busy(False)
        self._render_status()

    def _on_disconnected(self) -> None:
        self.logline("disconnected")
        self.conn = None
        self.busy = False
        self.progress.visible = False
        self._set_connected_ui(False)

    async def on_disconnect(self, e=None) -> None:
        if self.conn is not None:
            c, self.conn = self.conn, None
            await c.disconnect()
        self._set_connected_ui(False)

    # ---------------------------------------------------------- commands
    async def _run(self, label: str, coro) -> None:
        if not self.conn:
            return
        self.set_busy(True)
        try:
            await coro
            self.logline(f"{label}: OK")
        except Exception as ex:
            self.logline(f"{label}: {ex}")
            self.snack(f"{label} 失敗: {ex}", error=True)
        finally:
            self.set_busy(False)
            self._render_status()

    async def on_unlock(self, e=None) -> None:
        await self._run("解錠", self.conn.device.unlock())

    async def on_lock(self, e=None) -> None:
        await self._run("施錠", self.conn.device.lock())

    async def on_toggle(self, e=None) -> None:
        await self._run("トグル", self.conn.device.toggle())

    async def on_refresh(self, e=None) -> None:
        await self._run("状態更新", self.conn.device.request_mech_status())

    async def on_version(self, e=None) -> None:
        await self._run("バージョン取得", self.conn.device.get_version())

    async def on_register(self, e=None) -> None:
        if not self.conn or not self.current_adv:
            return
        adv = self.current_adv
        self.set_busy(True)
        try:
            secret = await self.conn.device.register()
        except Exception as ex:
            self.logline(f"register failed: {ex}")
            self.snack(f"登録失敗: {ex}", error=True)
            self.set_busy(False)
            return
        key = DeviceKey(device_uuid=str(adv.device_uuid), model_id=adv.model_id, secret_hex=secret.hex(),
                        name=adv.model_name, address=adv.address, registered_at=time.time())
        await self.keystore.put(key)
        self.keys[key.device_uuid] = key
        self.logline(f"registered & key saved: {key.device_uuid}")
        self.snack("登録完了。鍵を保存しました。")
        self.set_busy(False)
        self._render_status()
        self._render_list()

    def on_tag_change(self, e) -> None:
        if self.conn:
            self.conn.device.history_tag = self.tag_field.value or "flet"

    async def on_forget_key(self, e=None) -> None:
        adv = self.current_adv
        if not adv:
            return
        uuid_s = str(adv.device_uuid)

        async def do_forget(_):
            self._close_dialog(dlg)
            await self.keystore.remove(uuid_s)
            self.keys.pop(uuid_s, None)
            self.logline(f"key removed: {uuid_s}")
            self._render_status()
            self._render_list()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("鍵を削除しますか？"),
            content=ft.Text("保存した鍵を消すと、この Sesame をリセットして再登録するまで操作できなくなります。"),
            actions=[ft.TextButton(content="削除", on_click=do_forget),
                     ft.TextButton(content="キャンセル", on_click=lambda _: self._close_dialog(dlg))],
        )
        self.page.show_dialog(dlg)


async def main(page: ft.Page) -> None:
    app = SesameApp(page)
    await app.start()


def run(argv: list[str] | None = None) -> int:
    """`digitalkey sesame app [--web] [--fake]`。"""
    global FAKE_MODE
    argv = list(sys.argv[1:] if argv is None else argv)
    FAKE_MODE = "--fake" in argv
    web = "--web" in argv
    view = ft.AppView.WEB_BROWSER if web else ft.AppView.FLET_APP
    port = int(os.environ.get("DIGITALKEY_SESAME_PORT", 8551 if FAKE_MODE else 8550)) if web else 0
    ft.run(main, view=view, port=port)
    return 0


if __name__ == "__main__":
    sys.exit(run())
