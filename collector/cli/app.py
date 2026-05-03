"""One-click app launcher.

- Builds SQLite sidecar index from data_store/ (first run creates the dir).
- Renders dashboard.html.
- Starts a local HTTP server on 127.0.0.1 with JSON APIs for running the
  pipeline and saving API keys to `.env` directly from the browser UI.
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

from .api_handler import make_handler
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


def _load_env_file(env_path: Path) -> None:
    """Read .env into os.environ (if the file exists). Non-destructive."""
    from ..env_io import apply_to_environ, read_env
    apply_to_environ(read_env(env_path))


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
    ap.add_argument("--docs", default="docs")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--watch", type=float, default=0.0, help="rebuild every N seconds when data_store changes (0=off)")
    args = ap.parse_args(argv)

    data_store = Path(args.data_store)
    db = Path(args.db)
    html = Path(args.html)
    docs_dir = Path(args.docs)
    env_path = Path(args.env)
    logs_root = Path(args.logs)

    # Project root is inferred from env file location so the sandbox is
    # correct whether the user runs from the repo root or elsewhere.
    project_root = env_path.resolve().parent if env_path.is_absolute() else Path.cwd()

    # Bring saved API keys into os.environ so /api/run uses real adapters on
    # first click (no restart required).
    _load_env_file(env_path)

    n, out = prepare_dashboard(data_store, db, html)
    print(f"[app] indexed {n} records → {out}")

    port = _pick_port(args.port)
    if port != args.port:
        print(f"[app] port {args.port} busy, using {port}")

    handler_cls = make_handler(
        project_root=project_root,
        docs_dir=docs_dir,
        dashboard_html=html,
        env_path=env_path,
        data_store=data_store,
        logs_root=logs_root,
    )
    url = f"http://127.0.0.1:{port}/"

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler_cls)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    print(f"[app] serving {project_root} at {url}")

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


# Kept for backwards-compat with tests that patched this helper.
def _make_handler(root: Path):
    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, fmt, *args):  # quiet
            sys.stderr.write(f"[app] {self.address_string()} - {fmt % args}\n")

    return _Handler


if __name__ == "__main__":
    sys.exit(main())
