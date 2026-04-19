"""One-click app launcher.

- Builds SQLite sidecar index from data_store/ (first run creates the dir).
- Renders dashboard.html.
- Starts a local HTTP server (127.0.0.1).
- Auto-opens the page in the default browser.
- Optional --watch mode rebuilds every N seconds when data_store/ changes.
"""
from __future__ import annotations

import argparse
import http.server
import socket
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from .dashboard import build_dashboard, build_index


def prepare_dashboard(data_store: Path, db: Path, html: Path) -> tuple[int, Path]:
    data_store.mkdir(parents=True, exist_ok=True)
    n = build_index(data_store, db)
    out = build_dashboard(db, html)
    return n, out


def _pick_port(start: int, attempts: int = 10) -> int:
    for i in range(attempts):
        port = start + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit(f"no free port in {start}..{start+attempts-1}")


def _make_handler(root: Path):
    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            sys.stderr.write(f"[app] {self.address_string()} - {fmt % args}\n")

    return _Handler


def _watch_loop(data_store: Path, db: Path, html: Path, interval: float, stop: threading.Event) -> None:
    last_state: tuple[int, float] = (-1, -1.0)
    while not stop.is_set():
        files = list(Path(data_store).rglob("*.json"))
        state = (len(files), max((f.stat().st_mtime for f in files), default=0.0))
        if state != last_state:
            try:
                n = build_index(Path(data_store), Path(db))
                build_dashboard(Path(db), Path(html))
                print(f"[app] rebuilt: {n} records")
            except Exception as e:  # noqa: BLE001
                print(f"[app] rebuild error: {e}", file=sys.stderr)
            last_state = state
        stop.wait(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector app", description="Collector desktop app (local dashboard).")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--db", default="index/collector.sqlite")
    ap.add_argument("--html", default="index/dashboard.html")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--watch", type=float, default=0.0, help="rebuild every N seconds when data_store changes (0=off)")
    args = ap.parse_args(argv)

    data_store = Path(args.data_store)
    db = Path(args.db)
    html = Path(args.html)

    n, out = prepare_dashboard(data_store, db, html)
    print(f"[app] indexed {n} records → {out}")

    port = _pick_port(args.port)
    if port != args.port:
        print(f"[app] port {args.port} busy, using {port}")

    root = out.parent.resolve()
    url = f"http://127.0.0.1:{port}/{out.name}"

    handler_cls = _make_handler(root)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler_cls)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    print(f"[app] serving {root} at {url}")

    stop = threading.Event()
    watcher: threading.Thread | None = None
    if args.watch > 0:
        watcher = threading.Thread(
            target=_watch_loop, args=(data_store, db, html, args.watch, stop), daemon=True
        )
        watcher.start()
        print(f"[app] watch: rebuild every {args.watch}s on data_store/ change")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    print("[app] Ctrl+C 로 종료")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[app] stopping")
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
