"""命令行 `digitalkey`。`digitalkey entrance ...` は玄関(台帳と鍵)、`digitalkey door ...` は扉のコントローラ、`digitalkey panel` は盤。"""
from __future__ import annotations

import os
import sys

USAGE = """使い方:
  digitalkey entrance <命令> ...   様式・台帳・鍵・本人確認・報告・控え(--help で一覧)
  digitalkey door <命令> ...       扉のコントローラ(serve | verify)
  digitalkey panel                 盤(Flet)。$DIGITALKEY_DOOR_URL の door に結び、$DIGITALKEY_PANEL_PORT(既定 8798)で開く
  digitalkey sesame <命令> ...     CANDY HOUSE Sesame を BLE で直接(scan | register | status | lock | unlock | toggle | version | keys)
  digitalkey sesame app [--web] [--fake]   その画面(Flet)。--fake は疑似の Sesame
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "entrance":
        from .entrance.cli import main as m
        return m(argv[1:])
    if argv and argv[0] == "door":
        from .door.cli import main as m
        return m(argv[1:])
    if argv and argv[0] == "sesame":
        if len(argv) > 1 and argv[1] == "app":
            from .sesame.app import run
            return run(argv[2:])
        from .sesame.cli import main as m
        return m(argv[1:])
    if argv and argv[0] == "panel":
        import flet as ft
        from .panel.main import main as panel_main
        ft.run(panel_main, view=ft.AppView.WEB_BROWSER, port=int(os.environ.get("DIGITALKEY_PANEL_PORT", "8798")))
        return 0
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
