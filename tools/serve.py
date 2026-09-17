#!/usr/bin/env python3
"""Development server with auto-rebuild + live reload for aiseed-style sites.

Watches <site>/{articles,tools/templates} and <site>/html/{css,js},
rebuilds on changes, and serves <site>/html/ over HTTP.

保存 → ビルド → ブラウザ自動リロード:
  - articles/<series>.adoc の変更は、そのシリーズだけの差分ビルド(数秒)
  - テンプレート・CSS/JS・資産の変更はフルビルド
  - 配信する HTML に SSE クライアントを注入し、ビルド完了で自動リロード
  - ビルド失敗(シリーズファイルの書き損じ等)は、ブラウザ画面上部に
    行番号付きのエラーバナーを表示する(修正して保存すれば消える)

Usage:
    python3 tools/serve.py                            # use parent dir of script
    python3 tools/serve.py --site /path/to/site
    python3 tools/serve.py --site . --port 8080
    AISEED_SITE=/path/to/site python3 tools/serve.py
    python3 tools/serve.py --no-initial-build

差分ビルドでは sitemap.xml 等は更新されない(デプロイ前のフルビルドで
生成される)。
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build.series import SERIES_MAP  # noqa: E402

BUILD_SCRIPT = Path(__file__).resolve().parent / "build_article.py"
DEBOUNCE_SEC = 0.4


# ---------------------------------------------------------------------------
# ライブリロード(SSE)
# ---------------------------------------------------------------------------

class LiveReload:
    """ビルド完了/失敗をSSEで接続中のブラウザへ通知する共有状態。"""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.version = 0
        self.error: str | None = None

    def notify(self, error: str | None = None) -> None:
        with self.cond:
            self.version += 1
            self.error = error
            self.cond.notify_all()


livereload = LiveReload()

LIVERELOAD_SNIPPET = """
<script>
(() => {
  const es = new EventSource("/__livereload");
  const BANNER_ID = "__serve_error_banner";
  es.onmessage = (e) => {
    const d = JSON.parse(e.data);
    if (d.type === "reload") { location.reload(); return; }
    if (d.type === "error") {
      let b = document.getElementById(BANNER_ID);
      if (!b) {
        b = document.createElement("div");
        b.id = BANNER_ID;
        b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;" +
          "background:#b91c1c;color:#fff;font:13px/1.5 monospace;" +
          "padding:10px 16px;white-space:pre-wrap;max-height:45vh;overflow:auto;";
        document.body.appendChild(b);
      }
      b.textContent = "ビルド失敗:\\n" + d.message;
    }
  };
})();
</script>
"""


def resolve_site(cli_value: str | None) -> Path:
    candidate = cli_value or os.environ.get("AISEED_SITE")
    if candidate is None:
        candidate = str(Path(__file__).resolve().parent.parent)
    site = Path(candidate).resolve()
    if not (site / "articles").exists():
        raise SystemExit(
            f"[serve] {site} does not look like an aiseed-style site "
            "(no articles/). Pass --site <path> or set AISEED_SITE."
        )
    return site


def watch_dirs(site: Path) -> list[Path]:
    html_dir = site / "html"
    return [
        site / "articles",
        site / "tools" / "templates",
        html_dir / "css",
        html_dir / "js",
    ]


def _run_build_cmd(site: Path, target: str) -> tuple[bool, str]:
    """build_article.py を1回実行。(成功?, 失敗時の出力末尾) を返す。"""
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--site", str(site), target],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, ""
    tail = (result.stderr.strip() or result.stdout.strip())[-2000:]
    return False, tail


class RebuildHandler(FileSystemEventHandler):
    def __init__(self, site: Path) -> None:
        self._site = site
        self._lock = threading.Lock()
        # ビルドは常に1つずつ——展開ステージが .build/ を作り直すため、
        # 並走すると相手のビルド中のツリーを消してしまう
        self._build_lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._stopped = False
        self._changed: set[str] = set()

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        # 書き込みを伴うイベントだけに反応する。"opened"/"closed_no_write" は
        # ビルド自身がシリーズファイルやテンプレートを読むだけで発生するため、
        # 反応するとビルドがビルドを呼ぶ無限ループになる(実測)。
        if event.event_type not in ("modified", "created", "deleted", "moved"):
            return
        src = getattr(event, "src_path", "") or ""
        if src.endswith(("~", ".swp", ".swo")) or "/__pycache__/" in src:
            return
        with self._lock:
            if self._stopped:
                return
            self._changed.add(src)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SEC, self._run_build)
            self._timer.daemon = True
            self._timer.start()

    def _run_build(self) -> None:
        with self._build_lock:
            self._run_build_locked()

    def _run_build_locked(self) -> None:
        with self._lock:
            changed, self._changed = self._changed, set()
        if not changed:
            return

        # 変更がシリーズファイルだけなら、そのシリーズだけの差分ビルド
        articles_dir = self._site / "articles"
        series_targets: set[Path] = set()
        full = False
        for src in changed:
            p = Path(src)
            if p.parent == articles_dir and p.name in SERIES_MAP:
                series_targets.add(p)
            else:
                full = True

        if full or not series_targets:
            others = [Path(s).name for s in changed if Path(s).name not in SERIES_MAP]
            print(
                f"[serve] change detected ({', '.join(sorted(others)[:5])}) — full rebuild…",
                flush=True,
            )
            ok, err = _run_build_cmd(self._site, "--all")
        else:
            names = ", ".join(sorted(p.name for p in series_targets))
            print(f"[serve] change detected — rebuilding {names}…", flush=True)
            ok, err = True, ""
            for p in sorted(series_targets):
                ok, err = _run_build_cmd(self._site, str(p))
                if not ok:
                    break

        if ok:
            print("[serve] build ok — reloading browsers", flush=True)
            livereload.notify()
        else:
            print(f"[serve] build failed:\n{err}", flush=True)
            livereload.notify(error=err)

    def cancel(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def initial_build(site: Path) -> None:
    print(f"[serve] initial build… (site: {site})", flush=True)
    subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--site", str(site), "--all"],
        check=True,
    )


def start_watcher(site: Path) -> tuple[Observer, RebuildHandler]:
    handler = RebuildHandler(site)
    observer = Observer()
    for target in watch_dirs(site):
        if target.exists():
            observer.schedule(handler, str(target), recursive=True)
    observer.daemon = True
    observer.start()
    return observer, handler


def serve(html_dir: Path, port: int) -> None:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(html_dir), **kw)

        def log_message(self, format: str, *args) -> None:
            print(f"[http] {self.address_string()} — {format % args}", flush=True)

        def do_GET(self):
            if self.path.startswith("/__livereload"):
                return self._serve_sse()

            # HTML にはライブリロードのクライアントを注入して返す
            candidate = None
            clean = self.path.split("?", 1)[0].split("#", 1)[0]
            if clean.endswith("/"):
                candidate = os.path.join(self.translate_path(clean), "index.html")
            elif clean.endswith(".html"):
                candidate = self.translate_path(clean)
            if candidate and os.path.isfile(candidate):
                body = Path(candidate).read_bytes()
                snippet = LIVERELOAD_SNIPPET.encode("utf-8")
                if b"</body>" in body:
                    body = body.replace(b"</body>", snippet + b"</body>", 1)
                else:
                    body += snippet
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

        def _serve_sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with livereload.cond:
                last = livereload.version
                # 接続直後: 直前のビルドが失敗していたらすぐバナーを出す
                pending = (
                    json.dumps({"type": "error", "message": livereload.error})
                    if livereload.error
                    else None
                )
            try:
                if pending:
                    self.wfile.write(f"data: {pending}\n\n".encode("utf-8"))
                    self.wfile.flush()
                while True:
                    with livereload.cond:
                        livereload.cond.wait(timeout=30)
                        version, error = livereload.version, livereload.error
                    if version != last:
                        last = version
                        payload = (
                            {"type": "error", "message": error}
                            if error
                            else {"type": "reload"}
                        )
                        self.wfile.write(
                            f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        )
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("", port), Handler) as httpd:
        print(f"[serve] http://localhost:{port} (Ctrl+C to stop)", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve] shutting down", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", help="Path to the site root (default: parent dir of this script, or $AISEED_SITE).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-initial-build", action="store_true")
    args = parser.parse_args()

    site = resolve_site(args.site)
    if not args.no_initial_build:
        initial_build(site)

    observer, handler = start_watcher(site)
    try:
        serve(site / "html", args.port)
    finally:
        handler.cancel()
        observer.stop()
        observer.join(timeout=2)


if __name__ == "__main__":
    main()
