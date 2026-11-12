"""Sesame の鍵 — CANDY HOUSE の Sesame(SesameOS3。Sesame 5 / 6 / 6 Pro)を、Bluetooth で直接動かす画面(Flet)。

PC でもスマートフォンでも同じ画面です。複数の Sesame の鍵を持ち、選んで開け閉めし、鍵を家族などに対面で渡せます。

  digitalkey sesame app            PC の画面(BLE のアダプタが要る)
  digitalkey sesame app --web      画面をブラウザで開く(Bluetooth はこの PC のまま)
  digitalkey sesame app --fake     疑似の Sesame で試す(実機も Bluetooth も使わない。鍵は sesame-keys-fake.json)
  flet build apk / flet build ipa  スマートフォンのアプリに組む(drivers/digitalkey-sesame で)

【これは画面の歯止めで、鍵の守りではありません】
Sesame の錠が知っている鍵は 32 桁の一つだけで、錠の側では人を分けられません。以下の役の分け方は、このアプリの画面の
中の約束です。32 桁を持つ人が別の道具(このリポジトリの命令行や公開の SDK)を使えば、歯止めは通ります。防げるのは、
受け取った人がこのアプリで鍵をさらにコピーして広げることです。一人を本当に止めるには、Sesame をリセットして登録し直し、
残る人に渡し直します。

役 — 鍵ごとに「管理者」か「利用者」を持ちます。一台の端末で、ある Sesame は管理者、別の Sesame は利用者、ということがあります。
  管理者   その端末で登録した鍵、または管理者として渡された鍵。今までのバージョンで登録した鍵(役の欄が無い物)も管理者で引き継ぎます
  利用者   利用者として渡された鍵。管理者コードが無い鍵は利用者で入り、あとから管理者には上がりません

利用者の画面(最初の画面。全員)
  鍵のある Sesame     鍵のある Sesame を並べる(役つき)。押すと近くから探してつなぎ、ログインして、施錠と解錠、電池、角度を出す
  解錠、施錠、トグル    「自分の呼び名」が Sesame の履歴に残る(名乗りなので、偽ることもできる)
  鍵を削除            この端末の鍵だけを消す。ほかの端末にコピーがあれば、そちらは使える
  鍵を受け取る        渡す人の画面の QR を、このアプリのカメラで読む。読み取りが使えないときは、32 桁を打ち込む道がある(利用者として入る)

管理者の画面(管理者の鍵がある端末だけ。最初の Sesame を登録するために、鍵が一つも無い端末でも開く。利用者の鍵しか無い端末では開かない)
  探す、登録          近くの Sesame を並べる。未登録の Sesame とだけ登録でき、鍵を作ってこの端末に保存する(管理者になる)
  渡す                管理者の鍵の Sesame を、利用者として、または管理者として渡す。相手の呼び名を入れ、QR を出す。
                     PC にコピーするときは「32 桁を見せる」(管理者としてコピーするときは管理者コードも出る。`digitalkey sesame import` に入れる)
  渡した相手の控え      誰に、いつ、どの役で、どの道で渡したか。この端末から渡した分だけ。追記だけで、消さない。リセット前の行は印が付く
  リセットと渡し直し    止めたい人がいるときの順序の案内
  端末の認証           管理者の画面を開くときに、端末の認証(生体認証か PIN)を求めるか。管理者が選ぶ(スマートフォンのアプリ)

渡し方は対面で、アプリからアプリへ直接です。ネットワーク(LINE、メール、クラウド)を通しません。渡す側の画面に QR を出し(60 秒で自動で閉じて
消える)、受け取る側が自分のアプリのカメラで読みます。端末のふつうのカメラアプリや、画像を読み取るサービスは通しません。周りの人に
撮られない所で行います。受け取る側は、その Sesame にログインできたときだけ保存します(R-CM-13)。QR の中身は、バージョン、UUID、32 桁、役、
管理者コード(管理者のときだけ)で、人の名前は入れません(handoff.py)。

鍵について: 鍵は登録のときに錠と端末の間で作る 16 バイト(32 桁の 16 進)で、Sesame が知っている鍵はこの一つだけです。コピーした物は
全部同じ鍵で、持つ物は誰でもこの Sesame を開けられます。鍵はログ欄、logging、例外の文、標準出力に出しません(R-CM-12)。人が見られるのは、
管理者が確かめた上で行う「渡す」の表示だけで、閉じると消えます。

鍵の置き場: PC では ~/.config/digitalkey/sesame-keys.json(権限 600。`digitalkey sesame` の命令と共用)。スマートフォンでは端末の
安全な保管庫(Android Keystore、iOS のキーチェーン。Flet の SecureStorage)です。今までのバージョンは SharedPreferences に置いていたので、
新しいバージョンを初めて起動したときに保管庫へ移します(コピーする → 読み戻して一致を確かめる → 元を消す。途中で失敗したら元は消さず、今までの置き場のまま
動く)。移す前に、念のため鍵を PC にコピーしておくことを勧めます。保管庫はエラーのときに中身を消す設定(reset_on_error)を無効にしています。
鍵は端末のバックアップで別の端末へ移りません(Android はアプリのデータをバックアップから外し、iOS はこの端末だけの扱いにします)。

組み方、入れ方、困ったときは skills/sesame-app/SKILL.md にあります。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Optional

import flet as ft

from digitalkey_sesame import protocol as p
from digitalkey_sesame.handoff import (SCAN_SECONDS, SHOW_SECONDS, Handoff, HandoffError, HandoffLedger, ReceiveError,
                                       admin_code, encode_handoff, format_admin_code, parse_handoff, qr_png,
                                       receive_key, typed_handoff)
from digitalkey_sesame.keystore import (ROLE_ADMIN, ROLE_LABELS, ROLE_USER, DeviceKey, JsonFileKV, KeyStore,
                                        KeyStoreError, Migration, Settings, app_data_path, fake_path,
                                        format_secret_hex, migrate_kv)
from digitalkey_sesame.securestore import NoGate

APP_TITLE = "Sesame の鍵"
FAKE_MODE = "--fake" in sys.argv
MOBILE_PLATFORMS = {ft.PagePlatform.ANDROID, ft.PagePlatform.ANDROID_TV, ft.PagePlatform.IOS}
NEARBY_SECONDS = 5      # 32 桁を打ち込む道で、近くの Sesame を探す秒数

GUARD_NOTE = "これは画面の歯止めで、鍵の守りではありません。"
GUARD_DETAIL = (
    "Sesame の錠が知っている鍵は 32 桁の一つだけで、錠の側では人を分けられません。役の分け方は、このアプリの画面の中の約束です。"
    "32 桁を持つ人が別の道具を使えば、歯止めは通ります。防げるのは、受け取った人がこのアプリで鍵をさらにコピーして広げることです。"
    "一人を本当に止めるには、Sesame をリセットして登録し直し、残る人に渡し直します。")
RESET_GUIDE = (
    "止めたい人がいるときの順序です。Sesame の錠は鍵を一つしか知らないので、一人だけを止めることはできません。\n"
    "1. Sesame をリセットします。手順は CANDY HOUSE の案内に従います。リセットすると、今の鍵は(渡した相手の分も)全部使えなくなります。\n"
    "2. このアプリで、その Sesame の鍵を削除し、「探す」から登録し直します。\n"
    "3. 残す人に、新しい鍵を対面で渡し直します。「渡した相手の控え」を見て、誰に渡したかを確かめます。リセット前の行には印が付きます。")


def admin_keys(keys: dict[str, DeviceKey]) -> dict[str, DeviceKey]:
    """管理者の鍵だけ。"""
    return {u: k for u, k in keys.items() if k.is_admin}


def can_open_admin(keys: dict[str, DeviceKey]) -> bool:
    """管理者の画面を開けるか: 管理者の鍵が一つでもある、または鍵が一つも無い(最初の Sesame を登録するため)。
    利用者の鍵しか無い端末では開かない。"""
    return bool(admin_keys(keys)) or not keys


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
        from digitalkey_sesame.fake import FakeConnection, FakeScanner, fake_adapter_available
        return Backend("fake", FakeScanner, FakeConnection, fake_adapter_available)
    if page.platform in MOBILE_PLATFORMS:
        from digitalkey_sesame.transport_fletble import FletBleHub
        hub = FletBleHub(page)
        return Backend("flet-ble", hub.make_scanner, hub.make_connection, hub.adapter_available)
    try:
        from digitalkey_sesame.transport_bleak import (BleakSesameConnection, SesameScanner,
                                                adapter_available)
    except Exception as e:  # no bleak backend on this platform
        async def unavailable():
            return False, f"bleak を読み込めません: {e}"
        return Backend("none", None, None, unavailable)
    return Backend("bleak", SesameScanner, BleakSesameConnection, adapter_available)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

MAX_LOG_LINES = 300


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class SesameApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.keystore: KeyStore
        self.settings: Optional[Settings] = None
        self.ledger: Optional[HandoffLedger] = None
        self.gate = NoGate()
        self.qr_scanner = None                      # qrcamera.QrScanner。None なら 32 桁を打ち込む道だけ
        self.migration: Optional[Migration] = None
        self.backend: Optional[Backend] = None
        self.scanner = None
        self.found: dict[str, tuple[p.Advertisement, object]] = {}
        self.tiles: dict[str, ft.ListTile] = {}
        self.keys: dict[str, DeviceKey] = {}
        self.conn = None
        self.current_adv: Optional[p.Advertisement] = None
        self.busy = False
        self.admin_open = False
        self._list_dirty = False
        self._secret_task = None
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        pg = self.page
        pg.title = APP_TITLE
        pg.padding = 12
        self.btn_admin = ft.IconButton(icon=ft.Icons.ADMIN_PANEL_SETTINGS, tooltip="管理者の画面",
                                       on_click=self.on_open_admin, visible=False)
        pg.appbar = ft.AppBar(leading=ft.Icon(ft.Icons.LOCK), title=ft.Text(APP_TITLE), actions=[self.btn_admin])

        self.adapter_text = ft.Text("Bluetooth: 確認中…", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

        # ---- 利用者の画面(最初の画面)
        self.key_list = ft.Column(spacing=2)      # plain Column: a nested scrollable ListView swallowed taps on Android
        self.key_placeholder = ft.Text(
            "鍵のある Sesame はまだありません。鍵を渡されるときは「鍵を受け取る」を、最初の Sesame を登録するときは"
            "右上の「管理者の画面」を使います。", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.btn_receive = ft.Button(content="鍵を受け取る", icon=ft.Icons.QR_CODE_SCANNER, on_click=self.on_receive)
        self.tag_field = ft.TextField(
            label="自分の呼び名", helper="Sesame の履歴に残ります。名乗りなので、偽ることもできます", value="", dense=True,
            on_change=self.on_tag_change, on_blur=self.on_tag_save, on_submit=self.on_tag_save)
        self.home_view = ft.Column(spacing=8, controls=[
            ft.Row([self.btn_receive, self.adapter_text], wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text("鍵のある Sesame", weight=ft.FontWeight.BOLD),
            self.key_placeholder,
            self.key_list,
            self.tag_field,
        ])

        # ---- 管理者の画面
        self.scan_button = ft.Button(content="スキャン開始", icon=ft.Icons.BLUETOOTH_SEARCHING, on_click=self.on_toggle_scan)
        self.device_list = ft.Column(spacing=2)
        self.list_placeholder = ft.Text("スキャンを開始すると、近くの Sesame が表示されます。",
                                        size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.target_dd = ft.Dropdown(label="渡す Sesame (管理者の鍵がある物)", options=[], on_select=self.on_target_change)
        self.role_seg = ft.SegmentedButton(
            selected=[ROLE_USER], allow_empty_selection=False, allow_multiple_selection=False,
            segments=[ft.Segment(value=ROLE_USER, label=ft.Text("利用者として")),
                      ft.Segment(value=ROLE_ADMIN, label=ft.Text("管理者として"))])
        self.to_field = ft.TextField(label="相手の呼び名 (控えに残ります。人の名前は QR に入りません)")
        self.btn_qr = ft.Button(content="QR を出す", icon=ft.Icons.QR_CODE, on_click=self.on_hand_qr)
        self.btn_digits = ft.OutlinedButton(content="32 桁を見せる (PC にコピーするとき、QR を読めない相手)",
                                            icon=ft.Icons.KEY, on_click=self.on_hand_digits)
        self.ledger_list = ft.Column(spacing=2)
        self.auth_switch = ft.Switch(label="管理者の画面を開くとき、端末の認証 (生体認証か PIN) を求める",
                                     value=False, on_change=self.on_auth_toggle)
        self.auth_note = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.admin_view = ft.Column(spacing=8, visible=False, controls=[
            ft.Row([ft.OutlinedButton(content="利用者の画面へ戻る", icon=ft.Icons.ARROW_BACK, on_click=self.on_close_admin)]),
            ft.Container(padding=10, border_radius=6, bgcolor=ft.Colors.SECONDARY_CONTAINER, content=ft.Column(spacing=4, controls=[
                ft.Text(GUARD_NOTE, weight=ft.FontWeight.BOLD),
                ft.Text(GUARD_DETAIL, size=12)])),
            ft.Text("探す・登録", weight=ft.FontWeight.BOLD),
            ft.Text("近くの Sesame を探し、押してつなぎます。未登録の Sesame は「この Sesame を登録」で鍵を作ります。",
                    size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            self.scan_button, self.list_placeholder, self.device_list,
            ft.Divider(),
            ft.Text("渡す", weight=ft.FontWeight.BOLD),
            ft.Text("対面で、アプリからアプリへ直接渡します。ネットワーク(LINE、メール、クラウド)は通しません。"
                    "周りの人に撮られない所で、相手のアプリのカメラに QR を読ませます。",
                    size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            self.target_dd, self.role_seg, self.to_field,
            ft.Row([self.btn_qr, self.btn_digits], wrap=True),
            ft.Divider(),
            ft.Text("渡した相手の控え", weight=ft.FontWeight.BOLD),
            ft.Text("この端末から渡した分だけです。追記だけで、消しません。", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            self.ledger_list,
            ft.Divider(),
            ft.Text("リセットと渡し直しの案内", weight=ft.FontWeight.BOLD),
            ft.Text(RESET_GUIDE, size=12),
            ft.Divider(),
            ft.Text("設定", weight=ft.FontWeight.BOLD),
            self.auth_switch, self.auth_note,
        ])

        # ---- 選んだ Sesame の操作(両方の画面の上に出る)
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

        self.panel = ft.Card(
            visible=False,
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
                    self.panel,          # 選んだ Sesame の操作。つないでいる間だけ、画面の上に出る
                    self.home_view,
                    self.admin_view,
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

    def _tag(self) -> str:
        return (self.tag_field.value or "").strip() or "app"

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.progress.visible = busy
        self.panel.visible = busy or bool(self.conn)
        for b in (self.btn_unlock, self.btn_lock, self.btn_toggle, self.btn_status,
                  self.btn_version, self.btn_register):
            b.disabled = busy or not (self.conn and self.conn.is_connected)
        self.page.update()

    def _current_key(self) -> Optional[DeviceKey]:
        adv = self.current_adv
        return self.keys.get(str(adv.device_uuid)) if adv and adv.device_uuid else None

    def _set_connected_ui(self, connected: bool) -> None:
        logged_in = bool(connected and self.conn and self.conn.device.logged_in)
        for b in (self.btn_unlock, self.btn_lock, self.btn_toggle, self.btn_status, self.btn_version):
            b.disabled = not logged_in or self.busy
        self.btn_disconnect.disabled = not connected
        self.panel.visible = bool(connected or self.busy)
        adv = self.current_adv
        # 登録できるのは、管理者の画面で、未登録の Sesame とつないでいるときだけ
        self.btn_register.visible = bool(self.admin_open and connected and not logged_in and adv and not adv.is_registered)
        self.btn_register.disabled = self.busy
        self.btn_forget_key.visible = self._current_key() is not None
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

    def _badge(self, adv: p.Advertisement) -> tuple[str, str]:
        uuid_s = str(adv.device_uuid) if adv.device_uuid else "?"
        key = self.keys.get(uuid_s)
        if key is not None:
            return f"鍵あり ({ROLE_LABELS[key.role]})", ft.Colors.GREEN
        if adv.is_registered:
            return "登録済 (鍵なし)", ft.Colors.RED
        return "未登録 → 登録可", ft.Colors.ORANGE

    def _render_list(self) -> None:
        """管理者の画面の「探す」の一覧。Update tiles in place (re-creating controls on every RSSI update would
        invalidate a click that is in flight)."""
        for key, (adv, _dev) in self.found.items():
            uuid_s = str(adv.device_uuid) if adv.device_uuid else "?"
            badge, color = self._badge(adv)
            saved = self.keys.get(uuid_s)
            name = saved.name if saved and saved.name else adv.model_name
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

    def _render_keys(self) -> None:
        """利用者の画面の「鍵のある Sesame」。保存した鍵を並べる(近くで見えていれば RSSI も)。"""
        tiles = []
        for uuid_s, key in sorted(self.keys.items(), key=lambda kv: kv[1].name or kv[0]):
            near = self.found.get(uuid_s)
            where = f"近くにあります (RSSI {near[0].rssi} dBm)" if near else "押すと近くから探してつなぎます"
            tiles.append(ft.ListTile(
                dense=True,
                leading=ft.Icon(ft.Icons.LOCK, color=ft.Colors.GREEN if key.is_admin else ft.Colors.BLUE),
                title=ft.Text(f"{key.name or key.model_id}  ({ROLE_LABELS[key.role]})"),
                subtitle=ft.Text(f"{uuid_s}  {where}", size=11),
                trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT),
                on_click=lambda e, u=uuid_s: self.page.run_task(self.connect_key, u),
            ))
        self.key_list.controls = tiles
        self.key_placeholder.visible = not self.keys
        self.page.update()

    def _refresh_roles(self) -> None:
        """鍵の増減のあとに、役に依る見え方を直す: 管理者の画面の入口、渡す Sesame の選び、一覧。"""
        self.btn_admin.visible = can_open_admin(self.keys)
        if self.admin_open and not can_open_admin(self.keys):
            self._show_admin(False)
        mine = admin_keys(self.keys)
        self.target_dd.options = [ft.DropdownOption(key=u, text=f"{k.name or k.model_id}  ({u[:8]}…)") for u, k in mine.items()]
        if self.target_dd.value not in mine:
            self.target_dd.value = next(iter(mine), None)
        can_hand = bool(mine)
        for c in (self.target_dd, self.role_seg, self.to_field, self.btn_qr, self.btn_digits):
            c.disabled = not can_hand
        self._render_keys()
        self._render_list()

    # ---------------------------------------------------------- lifecycle
    async def start(self) -> None:
        pf = self.page.platform
        desktop = pf in (ft.PagePlatform.LINUX, ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS) or self.page.web
        try:
            await self._open_stores(desktop)
        except Exception as ex:
            # 置き場を読めないときは、何も書かずに止める(読めない中身を空と見なして上書きしない)
            msg = str(ex) if isinstance(ex, KeyStoreError) else f"鍵の置き場を開けません ({type(ex).__name__})"
            self.adapter_text.value = f"鍵の置き場: {msg}"
            self.adapter_text.color = ft.Colors.ERROR
            self.logline(f"keystore open failed: {msg}")
            self.page.update()
            return
        self.tag_field.value = self.settings["history_tag"]
        self.auth_switch.value = bool(self.settings["require_device_auth"])
        self._render_auth_note()
        self._refresh_roles()
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

    async def _open_stores(self, desktop: bool) -> None:
        """鍵・設定・渡した相手の控えの置き場を開く。スマートフォンでは、保管庫を開き、今までの保存領域(SharedPreferences)
        から初めて移す(keystore.migrate_kv)。移せなかったときは、今までの保存領域のまま動く(鍵は失わない)。"""
        if FAKE_MODE:
            from digitalkey_sesame.fake import FakeCameraScanner, FakeGate, fake_camera_path
            self.keystore = KeyStore(path=fake_path())
            data_kv = JsonFileKV(app_data_path(fake=True))
            self.gate = FakeGate()
            self.qr_scanner = FakeCameraScanner(channel=fake_camera_path())
        elif desktop:
            self.keystore = KeyStore()   # JSON file shared with `digitalkey sesame`
            data_kv = JsonFileKV(app_data_path())
        else:
            data_kv = await self._open_mobile_stores()
        self.settings = await Settings(data_kv).load()
        self.ledger = HandoffLedger(data_kv)
        self.keys = await self.keystore.all()

    async def _open_mobile_stores(self):
        legacy = ft.SharedPreferences()
        try:
            from digitalkey_sesame.securestore import make_vault
            vault = make_vault()
            mig = await migrate_kv(legacy, vault)
        except Exception as ex:      # flet-secure-storage を読み込めない等。元は触っていない
            vault = None
            mig = Migration("failed", "old", f"保管庫を使えません ({type(ex).__name__})。今までの保存領域のまま使います")
        self.migration = mig
        if mig.state != "none":
            self.logline(f"鍵の保管庫: {mig.detail}")      # detail に鍵は入らない
        if mig.state in ("failed", "lost"):
            self.snack(mig.detail, error=True)
        elif mig.state == "migrated":
            self.snack("鍵を端末の保管庫に移しました")
        data_kv = vault if (mig.use == "new" and vault is not None) else legacy
        self.keystore = KeyStore(kv=data_kv)
        try:
            from digitalkey_sesame.securestore import FletDeviceAuthGate
            self.gate = FletDeviceAuthGate()
        except Exception:
            self.gate = NoGate()
        from digitalkey_sesame.qrread import ZbarQrReader
        reader = ZbarQrReader()
        if reader.available():
            try:
                from digitalkey_sesame.qrcamera import CameraQrScanner
                import flet_camera  # noqa: F401
                self.qr_scanner = CameraQrScanner(reader)
            except Exception as ex:
                self.logline(f"カメラの読み取りを使えません ({type(ex).__name__})")
        else:
            self.logline("QR の読み取りを使えません (libzbar を読み込めない)。32 桁を打ち込む道を使います")
        return data_kv

    async def _list_refresher(self) -> None:
        while True:
            await asyncio.sleep(0.7)
            if self._list_dirty:
                self._list_dirty = False
                self._render_list()
                self._render_keys()

    # ------------------------------------------------------------ 画面の切り替え
    def _show_admin(self, show: bool) -> None:
        self.admin_open = show
        self.admin_view.visible = show
        self.home_view.visible = not show
        self.page.update()
        self._set_connected_ui(bool(self.conn and self.conn.is_connected))

    async def on_open_admin(self, e=None) -> None:
        """管理者の画面を開く。管理者の鍵が無く、鍵を持っている端末(利用者の鍵しか無い)では開かない。
        管理者が選んでいれば、端末の認証(生体認証か PIN)を通ってから開く。"""
        if not can_open_admin(self.keys):
            self.snack("この端末の鍵は利用者の鍵です。管理者の画面は開けません", error=True)
            return
        if self.settings and self.settings["require_device_auth"] and self.gate.available():
            if not await self.gate.verify():
                self.snack("端末の認証を通れませんでした。管理者の画面を開きません", error=True)
                return
        self._show_admin(True)
        await self._render_ledger()

    def on_close_admin(self, e=None) -> None:
        self._show_admin(False)

    # ----------------------------------------------------- 設定: 端末の認証
    def _render_auth_note(self) -> None:
        self.auth_switch.disabled = not self.gate.available()
        self.auth_note.value = ("管理者が選びます。入れると、管理者の画面を開くたびに端末の認証を求めます。これも画面の関所で、鍵の守りではありません。"
                                if self.gate.available() else "この端末では使えません (スマートフォンのアプリで使えます)。")

    async def on_auth_toggle(self, e=None) -> None:
        want = bool(self.auth_switch.value)
        if want:
            try:
                await self.gate.prepare()
                if not await self.gate.verify():
                    raise RuntimeError("認証を通れませんでした")
            except Exception as ex:
                self.auth_switch.value = False
                self.snack(f"端末の認証を使えません。端末に画面ロックと生体認証 (または PIN) が設定されているか確かめてください ({type(ex).__name__})", error=True)
                self.page.update()
                return
        try:
            await self.settings.set("require_device_auth", want)
        except Exception as ex:
            self.auth_switch.value = not want
            self.snack(f"設定を保存できませんでした ({type(ex).__name__})", error=True)
            self.page.update()
            return
        self.logline("管理者の画面を開くときの端末の認証: " + ("求める" if want else "求めない"))
        self.page.update()

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
    async def connect_key(self, uuid_s: str) -> None:
        """利用者の画面の鍵の一覧から。近くに見えていなければ、その Sesame を探してからつなぐ。"""
        if self.busy or uuid_s not in self.keys:
            return
        if uuid_s not in self.found:
            await self.stop_scan()
            self.set_busy(True)
            self.logline(f"{uuid_s} を探しています…")
            from digitalkey_sesame.handoff import find_sesame
            adv, dev = await find_sesame(self.backend, uuid_s, SCAN_SECONDS)
            self.set_busy(False)
            if dev is None:
                self.snack("その Sesame が近くに見つかりません。近づけて、もう一度押してください", error=True)
                return
            self.found[uuid_s] = (adv, dev)
            self._render_keys()
        await self.connect_to(uuid_s)

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
                                     history_tag=self._tag())
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
            self.logline("device is unregistered → 管理者の画面で「この Sesame を登録」を押してください")
            self.snack("未登録の Sesame です。管理者の画面の「登録」でペアリングできます。")
        else:
            self.logline("device is registered but no key saved here. "
                         "管理者から対面で鍵を受け取ってください（またはリセットして再登録）")
            self.snack("登録済みですが鍵がありません。管理者から鍵を受け取ってください", error=True)
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
        """未登録の Sesame と登録し、鍵を作ってこの端末に管理者の鍵として保存する(管理者の画面から)。"""
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
                        name=adv.model_name, address=adv.address, registered_at=time.time(), role=ROLE_ADMIN)
        try:
            await self.keystore.put(key)
        except Exception as ex:
            # 登録は済んでいるのに保存できなかった。鍵はこの画面を閉じると失われるので、はっきり伝える
            self.logline(f"register: key NOT saved ({type(ex).__name__})")
            self.snack("登録はできましたが、鍵を保存できませんでした。この Sesame はリセットして登録し直してください", error=True)
            self.set_busy(False)
            return
        self.keys[key.device_uuid] = key
        self.logline(f"registered & key saved: {key.device_uuid}")
        self.snack("登録完了。鍵を保存しました。")
        self.set_busy(False)
        self._render_status()
        self._refresh_roles()

    def on_tag_change(self, e) -> None:
        if self.conn:
            self.conn.device.history_tag = self._tag()

    async def on_tag_save(self, e=None) -> None:
        if self.settings is None:
            return
        try:
            await self.settings.set("history_tag", (self.tag_field.value or "").strip())
        except Exception as ex:
            self.logline(f"呼び名を保存できませんでした ({type(ex).__name__})")

    async def on_forget_key(self, e=None) -> None:
        adv = self.current_adv
        if not adv:
            return
        uuid_s = str(adv.device_uuid)
        key = self.keys.get(uuid_s)

        async def do_forget(_):
            self._close_dialog(dlg)
            try:
                await self.keystore.remove(uuid_s)
            except Exception as ex:
                self.snack(f"鍵を削除できませんでした ({type(ex).__name__})", error=True)
                return
            self.keys.pop(uuid_s, None)
            self.logline(f"key removed: {uuid_s}")
            self._render_status()
            self._refresh_roles()

        lines = ["この端末に保存した鍵を消します。この Sesame も、ほかの端末にコピーした鍵も消えません。",
                 "ほかの端末(PC や別のスマートフォン)にコピーがあれば、そちらでは使えます。",
                 "ほかにコピーが無いときは、管理者から鍵を受け取り直すか、Sesame をリセットして登録し直すまで、この Sesame を動かせません。"]
        if key is not None and key.is_admin:
            lines.append("この端末が渡した相手の鍵は、消えません(止めるには Sesame のリセットが要ります)。")
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("この端末の鍵を削除しますか？"),
            content=ft.Column([ft.Text(t) for t in lines], tight=True),
            actions=[ft.TextButton(content="削除", on_click=do_forget),
                     ft.TextButton(content="キャンセル", on_click=lambda _: self._close_dialog(dlg))],
        )
        self.page.show_dialog(dlg)

    # ------------------------------------------------- 渡す(管理者の画面)
    def _target_key(self) -> Optional[DeviceKey]:
        """「渡す」で選んだ Sesame の鍵。管理者の鍵だけが対象(利用者の鍵は渡せない)。"""
        key = self.keys.get(self.target_dd.value or "")
        return key if key is not None and key.is_admin else None

    def _role(self) -> str:
        sel = self.role_seg.selected or [ROLE_USER]
        return ROLE_ADMIN if sel[0] == ROLE_ADMIN else ROLE_USER

    async def on_target_change(self, e=None) -> None:
        await self._render_ledger()

    async def _render_ledger(self) -> None:
        """選んだ Sesame の渡した相手の控え。リセットして登録し直した後は、登録より前の行に印を付ける(控えは消さない)。"""
        key = self._target_key()
        rows = []
        if key is not None and self.ledger is not None:
            try:
                entries = await self.ledger.entries(key.device_uuid)
            except Exception as ex:
                entries = []
                rows.append(ft.Text(f"控えを読めません ({type(ex).__name__})", size=12, color=ft.Colors.ERROR))
            for r in sorted(entries, key=lambda r: -r.get("at", 0)):
                old = r.get("at", 0) < key.registered_at
                via = {"qr": "QR", "digits": "32 桁"}.get(r.get("via"), str(r.get("via", "")))
                tail = "  (リセット前: この鍵は今は使えません)" if old else ""
                rows.append(ft.Text(f"{_stamp(r.get('at', 0))}  {r.get('to') or '(呼び名なし)'}  "
                                    f"{ROLE_LABELS.get(r.get('role'), '?')}  {via}{tail}", size=12))
        if not rows:
            rows = [ft.Text("まだありません。", size=12, color=ft.Colors.ON_SURFACE_VARIANT)]
        self.ledger_list.controls = rows
        self.page.update()

    def _show_secret(self, title: str, body: list, seconds: Optional[int] = None, on_close=None) -> ft.AlertDialog:
        """鍵に関わる表示(QR、32 桁)を出すダイアログ。`seconds` 秒(既定は SHOW_SECONDS)で自動で閉じ、閉じたら中身を画面から消す。"""
        seconds = SHOW_SECONDS if seconds is None else seconds
        remain = ft.Text(f"あと {seconds} 秒で閉じます", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        holder = ft.Column([*body, remain], tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        state = {"closed": False}

        def done(_=None):
            if state["closed"]:
                return
            state["closed"] = True
            holder.controls = [ft.Text("閉じました。表示は消えています。")]    # QR と 32 桁の部品を、画面の部品からも消す
            if on_close:
                on_close()
            self._close_dialog(dlg)

        dlg = ft.AlertDialog(modal=True, title=ft.Text(title), content=holder,
                             actions=[ft.TextButton(content="閉じる", on_click=done)])
        dlg.data = {"state": state, "done": done}
        self.page.show_dialog(dlg)
        self._secret_task = self.page.run_task(self._count_down, remain, seconds, state, done)
        return dlg

    async def _count_down(self, remain: ft.Text, seconds: int, state: dict, done) -> None:
        for left in range(seconds, 0, -1):
            if state["closed"]:
                return
            remain.value = f"あと {left} 秒で閉じます"
            self.page.update()
            await asyncio.sleep(1)
        done()

    def _hand_over_checked(self) -> Optional[tuple[DeviceKey, str, str]]:
        """渡す前の確かめ: 管理者の鍵の Sesame を選んでいること、相手の呼び名があること。"""
        key = self._target_key()
        if key is None:
            self.snack("渡す Sesame を選んでください (管理者の鍵がある物だけ渡せます)", error=True)
            return None
        to = (self.to_field.value or "").strip()
        if not to:
            self.snack("相手の呼び名を入れてください (控えに残ります)", error=True)
            return None
        return key, self._role(), to

    def _confirm(self, title: str, lines: list[str], go) -> ft.AlertDialog:
        dlg = ft.AlertDialog(
            modal=True, title=ft.Text(title),
            content=ft.Column([ft.Text(t, size=13) for t in lines], tight=True),
            actions=[ft.TextButton(content="出す", on_click=lambda _: (self._close_dialog(dlg), go())),
                     ft.TextButton(content="キャンセル", on_click=lambda _: self._close_dialog(dlg))])
        self.page.show_dialog(dlg)
        return dlg

    async def on_hand_qr(self, e=None) -> None:
        """QR を出して、対面で相手のアプリのカメラに読ませる。確かめを挟み、了承されたときだけ出す。"""
        ok = self._hand_over_checked()
        if ok is None:
            return
        key, role, to = ok
        role_label = ROLE_LABELS[role]
        lines = [f"「{to}」に、{key.name or key.model_id} の鍵を{role_label}として渡します。",
                 "この QR を読んだ人は、この Sesame を開けられます。周りの人に撮られない所で、相手のアプリのカメラに読ませてください。",
                 "端末のふつうのカメラアプリや、画像を読み取るサービスは使わせないでください。",
                 f"{SHOW_SECONDS} 秒で自動で閉じます。",
                 GUARD_NOTE]
        if role == ROLE_ADMIN:
            lines.insert(1, "管理者として渡すと、相手もこのアプリで鍵を渡せるようになります。")
        self._confirm("鍵を渡しますか？", lines, lambda: self.page.run_task(self._show_qr, key, role, to))

    async def _show_qr(self, key: DeviceKey, role: str, to: str) -> ft.AlertDialog:
        payload = encode_handoff(key, role)
        png = qr_png(payload)
        try:
            await self.ledger.record(key.device_uuid, to, role, "qr")
        except Exception as ex:      # 控えに残せないときは出さない(渡したのに控えが無い状態を作らない)
            self.snack(f"渡した相手の控えを保存できないので、出しません ({type(ex).__name__})", error=True)
            return None
        if FAKE_MODE:
            self._fake_channel(payload)
        img = ft.Container(bgcolor=ft.Colors.WHITE, padding=8, content=ft.Image(src=png, width=300, height=300, fit=ft.BoxFit.CONTAIN))
        dlg = self._show_secret(f"{ROLE_LABELS[role]}として渡す QR", [
            img,
            ft.Text(f"「{to}」のアプリのカメラで読ませてください。", size=12),
            ft.Text(GUARD_NOTE, size=12)],
            on_close=(lambda: self._fake_channel(None)) if FAKE_MODE else None)
        self.logline(f"handoff shown: uuid={key.device_uuid} role={role} via=qr")
        await self._render_ledger()
        return dlg

    async def on_hand_digits(self, e=None) -> None:
        """32 桁を見せる(PC の `digitalkey sesame import` にコピーするとき。QR を読めない相手)。確かめを挟み、了承されたときだけ出す。"""
        ok = self._hand_over_checked()
        if ok is None:
            return
        key, role, to = ok
        lines = [f"「{to}」に、{key.name or key.model_id} の鍵を{ROLE_LABELS[role]}として渡します。32 桁を画面に出します。",
                 "この 32 桁を持つ人は、この Sesame を開けられます。人に見せず、チャットやメールに貼らないでください。",
                 f"{SHOW_SECONDS} 秒で自動で閉じます。",
                 GUARD_NOTE]
        self._confirm("32 桁を出しますか？", lines, lambda: self.page.run_task(self._show_digits, key, role, to))

    async def _show_digits(self, key: DeviceKey, role: str, to: str) -> ft.AlertDialog:
        try:
            await self.ledger.record(key.device_uuid, to, role, "digits")
        except Exception as ex:
            self.snack(f"渡した相手の控えを保存できないので、出しません ({type(ex).__name__})", error=True)
            return None
        body = [ft.Text("鍵 (32 桁)", size=12),
                ft.Text(format_secret_hex(key.secret_hex), size=20, font_family="monospace", selectable=True)]
        if role == ROLE_ADMIN:
            body += [ft.Text("管理者コード", size=12),
                     ft.Text(format_admin_code(admin_code(key.secret, key.device_uuid)), size=20, font_family="monospace", selectable=True),
                     ft.Text("PC の `digitalkey sesame import` で、鍵に続けてこのコードを入れると管理者として入ります。"
                             "コードを入れなければ利用者です。", size=12)]
        else:
            body += [ft.Text("PC の `digitalkey sesame import` で、管理者コードは空のまま Enter します (利用者として入ります)。", size=12)]
        body.append(ft.Text("人に見せず、チャットやメールに貼らないでください。閉じると画面から消えます。" + GUARD_NOTE,
                            size=12, color=ft.Colors.ON_SURFACE_VARIANT))
        dlg = self._show_secret(f"{ROLE_LABELS[role]}として渡す 32 桁", body)
        self.logline(f"handoff shown: uuid={key.device_uuid} role={role} via=digits")
        await self._render_ledger()
        return dlg

    def _fake_channel(self, payload: Optional[str]) -> None:
        """疑似の画面(--fake)だけ: 渡す側が出した QR の文字を、疑似のカメラの置き場に置く(受け取る側が別の画面で読む)。"""
        from digitalkey_sesame.fake import fake_camera_path
        path = fake_camera_path()
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            os.chmod(path, 0o600)

    # ---------------------------------------------- 受け取る(利用者の画面)
    async def accept_text(self, text: str, name: str = "") -> tuple[bool, str]:
        """QR から読んだ文字を受け取る。(読み終えたか, 人に見せる一文)。Sesame にログインできたときだけ保存する(R-CM-13)。
        画面から切り離して試験できる。文に鍵は入らない。"""
        try:
            handoff = parse_handoff(text)
        except HandoffError as e:
            return False, str(e)
        return await self.accept(handoff, name)

    async def accept(self, handoff: Handoff, name: str = "") -> tuple[bool, str]:
        await self.stop_scan()
        if self.conn is not None:
            await self.on_disconnect()
        self.set_busy(True)
        try:
            key = await receive_key(self.backend, self.keystore, handoff, name=name, history_tag=self._tag(), on_log=self.logline)
        except ReceiveError as e:
            self.logline(f"receive failed: {e}")
            return False, str(e)
        except Exception as e:
            self.logline(f"receive failed: {type(e).__name__}")
            return False, f"保存できませんでした ({type(e).__name__})。何も保存していません"
        finally:
            self.set_busy(False)
        self.keys[key.device_uuid] = key
        self.logline(f"key received: uuid={key.device_uuid} role={key.role}")
        self._refresh_roles()
        msg = f"受け取りました ({ROLE_LABELS[key.role]})。" + (handoff.notice + "。" if handoff.notice else "")
        return True, msg

    async def on_receive(self, e=None) -> None:
        """鍵を受け取る: 渡す人の画面の QR を、このアプリのカメラで読む。読み取りが使えないときは 32 桁を打ち込む道を案内する。"""
        status = ft.Text("", size=12)
        name_field = ft.TextField(label="この Sesame の名前 (省略すると機種名)", dense=True)
        scanner = self.qr_scanner
        controls: list = [
            ft.Text("渡す人の画面の QR を、このアプリのカメラで読みます。端末のふつうのカメラアプリでは読まないでください。", size=12),
            name_field]
        preview = None
        if scanner is not None:
            preview = ft.Container(height=260, content=scanner.build(), border_radius=6)
            controls.append(preview)
        else:
            status.value = "この端末では QR を読めません。「32 桁を打ち込む」を使ってください (利用者として入ります)。"
        controls.append(status)

        async def on_text(text: str) -> bool:
            done, msg = await self.accept_text(text, name_field.value or "")
            status.value = msg
            if done:
                await scanner.stop()
                self._close_dialog(dlg)
                self.snack(msg)
            self.page.update()
            return done

        async def close(_=None):
            if scanner is not None:
                await scanner.stop()
            self._close_dialog(dlg)

        async def typed(_=None):
            await close()
            await self.on_receive_typed()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("鍵を受け取る"), content=ft.Column(controls, tight=True, width=340),
            actions=[ft.TextButton(content="32 桁を打ち込む", on_click=typed),
                     ft.TextButton(content="閉じる", on_click=close)])
        self.page.show_dialog(dlg)
        if scanner is not None:
            if not await scanner.start(on_text):
                status.value = ("カメラを使えません (許可されていないか、この端末には無いようです)。"
                                "「32 桁を打ち込む」を使ってください (利用者として入ります)。")
                self.page.update()

    async def _scan_nearby(self, seconds: float = NEARBY_SECONDS) -> dict[str, p.Advertisement]:
        """近くの登録済みの Sesame を探す(32 桁を打ち込む道の、Sesame の選び用)。"""
        await self.stop_scan()
        seen: dict[str, p.Advertisement] = {}

        def on_adv(adv, dev):
            if adv.device_uuid is not None and adv.is_registered:
                seen[str(adv.device_uuid)] = adv

        sc = self.backend.make_scanner(on_adv)
        await sc.start()
        try:
            await asyncio.sleep(seconds)
        finally:
            await sc.stop()
        return seen

    async def on_receive_typed(self, e=None) -> None:
        """読み取りが使えないときの道: 近くの Sesame を選び、渡された 32 桁を打ち込む。利用者として入る。
        ログインできたときだけ保存する。32 桁の入力欄は伏せ字で、使い終わったら空にする。"""
        status = ft.Text("", size=12)
        dd = ft.Dropdown(label="受け取る Sesame (近くの登録済みの物)", options=[])
        secret_field = ft.TextField(label="鍵の 32 桁 (空白とハイフンは区切りとして読み飛ばします)", password=True,
                                    can_reveal_password=True, dense=True)
        name_field = ft.TextField(label="この Sesame の名前 (省略すると機種名)", dense=True)

        async def search(_=None):
            status.value = "近くの Sesame を探しています…"
            self.page.update()
            found = await self._scan_nearby()
            dd.options = [ft.DropdownOption(key=u, text=f"{a.model_name}  {u[:8]}…  RSSI {a.rssi}") for u, a in found.items()]
            dd.value = next(iter(found), None)
            status.value = "" if found else "近くに登録済みの Sesame が見つかりません。近づけて、もう一度探してください。"
            self.page.update()

        async def save(_=None):
            if not dd.value:
                status.value = "先に「近くの Sesame を探す」で Sesame を選んでください。"
                self.page.update()
                return
            try:
                handoff = typed_handoff(dd.value, secret_field.value or "")
            except ValueError as ex:
                status.value = str(ex)          # 文に入力は入っていない
                self.page.update()
                return
            status.value = "確かめています…"
            self.page.update()
            done, msg = await self.accept(handoff, name_field.value or "")
            secret_field.value = ""             # 使い終わったら、入力欄からも消す
            status.value = msg
            if done:
                self._close_dialog(dlg)
                self.snack(msg)
            self.page.update()

        def cancel(_=None):
            secret_field.value = ""
            self._close_dialog(dlg)

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("32 桁を打ち込んで受け取る"),
            content=ft.Column([
                ft.Text("QR を読めないときの道です。渡す人の画面の 32 桁を見て打ち込みます。利用者として入ります。", size=12),
                ft.OutlinedButton(content="近くの Sesame を探す", icon=ft.Icons.BLUETOOTH_SEARCHING, on_click=search),
                dd, secret_field, name_field, status], tight=True, width=340),
            actions=[ft.TextButton(content="確かめて保存", on_click=save), ft.TextButton(content="閉じる", on_click=cancel)])
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
