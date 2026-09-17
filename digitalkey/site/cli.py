"""`digitalkey site init <dir>` と `digitalkey site serve --config site.toml`。"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from . import config as C


def cmd_init(args) -> int:
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "site.toml"
    if f.exists() and not args.force:
        print(f"{f} はもうあります(--force で上書き)", file=sys.stderr)
        return 1
    f.write_text(C.sample(args.name, str((d / "vault").resolve())), encoding="utf-8")
    f.chmod(0o600)
    print(f"書きました: {f}\nトークンはこのファイルの中です。管理者とアプリに配ってください。")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app
    from .service import Site

    cfg = C.load(args.config)
    site = Site(cfg)
    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            site.tick()
            stop.wait(1.0)

    threading.Thread(target=ticker, daemon=True).start()
    print(f"digitaldoor site '{cfg.name}' http://{cfg.host}:{cfg.port}  扉: {', '.join(site.doors)}  記録: {cfg.vault}")
    uvicorn.run(create_app(site), host=cfg.host, port=cfg.port, log_level="info")
    stop.set()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="digitalkey site", description="事業所サーバー")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="設定の雛形(site.toml)を書く")
    p.add_argument("dir"); p.add_argument("--name", default="本社"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("serve", help="サーバーを起動する")
    p.add_argument("--config", default="site.toml"); p.set_defaults(fn=cmd_serve)
    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)
