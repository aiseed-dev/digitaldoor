"""命令行 `digitalkey`。`digitalkey entrance ...` は玄関(台帳と鍵)、`digitalkey door ...` は扉のコントローラ、`digitalkey panel` は盤。"""
from __future__ import annotations

import os
import sys

USAGE = """使い方:
  digitalkey entrance <命令> ...   様式・台帳・鍵・本人確認・報告・控え(--help で一覧)
  digitalkey door <命令> ...       扉のコントローラ(serve | verify)
  digitalkey panel                 盤(Flet)。$DIGITALKEY_DOOR_URL の door に結び、$DIGITALKEY_PANEL_PORT(既定 8798)で開く
  digitalkey <ドライバ名> ...       pip で入れた錠ドライバのコマンド(例: digitalkey-sesame を入れると `digitalkey sesame scan`)
  digitalkey site init <dir> | serve --config site.toml   事業所サーバー(扉のコントローラ+鍵の台帳+HTTP API)
  digitalkey mobile [--web]        管理者と利用者のスマートフォンアプリ(Flet)
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "entrance":
        from .entrance.cli import main as m
        return m(argv[1:])
    if argv and argv[0] == "door":
        from .door.cli import main as m
        return m(argv[1:])
    if argv and argv[0] == "site":
        from .site.cli import main as m
        return m(argv[1:])
    if argv and argv[0] == "mobile":
        from .mobile.main import run
        return run(argv[1:])
    if argv:
        from importlib.metadata import entry_points
        for ep in entry_points(group="digitalkey.commands"):
            if ep.name == argv[0]:
                return int(ep.load()(argv[1:]) or 0)
    if argv and argv[0] == "panel":
        import flet as ft
        from .panel.main import main as panel_main
        ft.run(panel_main, view=ft.AppView.WEB_BROWSER, port=int(os.environ.get("DIGITALKEY_PANEL_PORT", "8798")))
        return 0
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
