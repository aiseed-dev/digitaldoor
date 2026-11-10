"""`digitalkey site init <dir>`、`digitalkey site token`、`digitalkey site serve --config site.toml`。"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from . import config as C


def cmd_init(args) -> int:
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "site.toml"
    if f.exists() and not args.force:
        print(f"{f} はもうあります(--force で上書き)", file=sys.stderr)
        return 1
    text, tokens = C.sample_with_tokens(args.name, str((d / "vault").resolve()))
    f.write_text(text, encoding="utf-8")
    f.chmod(0o600)
    print(f"書きました: {f}")
    print("トークンは今だけ出します(設定には SHA-256 だけを書きました)。管理者とアプリに配ってください:")
    for name, tok in tokens.items():
        print(f"  {name}: {tok}")
    return 0


def cmd_token(args) -> int:
    """設定の末尾に [[token]] を一つ足し、トークンを一度だけ出す。"""
    f = Path(args.config)
    try:
        cfg = C.load(f)
        if any(t.name == args.name for t in cfg.tokens):
            raise ValueError(f"同じ名前のトークンがもうあります: {args.name}")
        tok = C.new_token()
        block = C.token_block(args.name, args.role, tok, args.subject or "")
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(block)
    print(f"足しました: {args.name}({args.role}、主体 {C.PRINCIPALS[args.role]})。サーバーを起動し直すと効きます")
    print(f"トークン(今だけ出します): {tok}")
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
    p = sub.add_parser("token", help="トークンを一つ足す(設定には SHA-256 だけを書き、トークンは一度だけ出す)",
                       description="役割と共通コア 7.1 の主体: 管理者=管理者、受付=運用者、利用者=利用者、保守=保守者。"
                                   "止めるときは設定からその [[token]] を消して起動し直す。")
    p.add_argument("--config", default="site.toml"); p.add_argument("--name", required=True)
    p.add_argument("--role", required=True, choices=C.ROLES)
    p.add_argument("--subject", help="利用者のとき、鍵の主体名")
    p.set_defaults(fn=cmd_token)
    p = sub.add_parser("serve", help="サーバーを起動する")
    p.add_argument("--config", default="site.toml"); p.set_defaults(fn=cmd_serve)
    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)
