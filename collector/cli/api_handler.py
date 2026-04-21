"""HTTP request handler for the local `collector app` server.

Routes:
    GET  /                  → docs/index.html
    GET  /dashboard.html    → index/dashboard.html (built from SQLite sidecar)
    GET  /status.json       → docs/status.json
    GET  /<path>            → static file from project root (jail-protected)
    GET  /api/config        → {"has_youtube":bool, "has_gemini":bool, "has_anthropic":bool}
    POST /api/config        → body: {"youtube":"...", "google":"..."} → write .env
    POST /api/run           → body: {"query", "count", "llm_choice"} → start pipeline
    GET  /api/run/status    → latest run summary (from in-memory state)

Security:
- The handler only binds `127.0.0.1` (enforced by the caller).
- `/api/config` GET never returns key values — only presence booleans.
- Static file serving is sandboxed under the project root (directory traversal
  attempts are rejected with 404).
"""
from __future__ import annotations

import http.server
import json
import mimetypes
import os
import sys
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from ..env_io import apply_to_environ, has_keys, merge_env, read_env


# Run state — shared across handler instances via class attributes.
_RUN_STATE: dict[str, Any] = {
    "run_id": None,
    "status": "idle",           # idle | running | completed | failed
    "query": None,
    "started_at": None,
    "ended_at": None,
    "summary": None,
    "error": None,
}
_RUN_LOCK = threading.Lock()


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _safe_path(root: Path, rel: str) -> Path | None:
    """Resolve `rel` under `root`, refusing any escape attempts."""
    rel = rel.lstrip("/")
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_handler(
    *,
    project_root: Path,
    docs_dir: Path,
    dashboard_html: Path,
    env_path: Path,
    data_store: Path,
    logs_root: Path,
) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a request handler class bound to the given filesystem layout."""
    project_root = project_root.resolve()
    docs_dir = docs_dir.resolve()
    env_path = env_path.resolve()
    dashboard_html = dashboard_html.resolve()

    class _Handler(http.server.BaseHTTPRequestHandler):
        server_version = "collector-app/1.0"

        # ---- logging ----
        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(f"[app] {self.address_string()} - {fmt % args}\n")

        # ---- response helpers ----
        def _send_json(self, code: int, obj: dict) -> None:
            body = _json_bytes(obj)
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                self.send_error(404, "not found")
                return
            data = path.read_bytes()
            ctype = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                obj = json.loads(raw.decode("utf-8"))
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}

        # ---- routes ----
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/config":
                    return self._handle_config_get()
                if path == "/api/run/status":
                    return self._handle_run_status()
                if path in ("/", "/index.html"):
                    return self._send_file(docs_dir / "index.html", "text/html; charset=utf-8")
                if path == "/dashboard.html":
                    return self._send_file(dashboard_html, "text/html; charset=utf-8")
                if path == "/status.json":
                    p = docs_dir / "status.json"
                    if p.exists():
                        return self._send_file(p, "application/json; charset=utf-8")
                    return self._send_json(200, {})
                # static fall-through: first try project root, then docs/
                # (so docs/manifest.json is reachable as /manifest.json).
                target = _safe_path(project_root, path)
                if target is None:
                    self.send_error(404, "not found")
                    return
                if not target.exists():
                    alt = _safe_path(docs_dir, path)
                    if alt is not None and alt.exists():
                        target = alt
                if target.is_dir():
                    idx = target / "index.html"
                    if idx.exists():
                        return self._send_file(idx, "text/html; charset=utf-8")
                    self.send_error(404, "directory listing disabled")
                    return
                return self._send_file(target)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[app] GET {path} error: {e}\n")
                traceback.print_exc()
                self.send_error(500, "internal error")

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/config":
                    return self._handle_config_post()
                if path == "/api/run":
                    return self._handle_run_post()
                self.send_error(404, "not found")
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[app] POST {path} error: {e}\n")
                traceback.print_exc()
                self._send_json(500, {"ok": False, "error": "internal error"})

        # ---- /api/config ----
        def _handle_config_get(self) -> None:
            env = read_env(env_path)
            payload = {
                "has_youtube": has_keys(env, ["YOUTUBE_API_KEY"]),
                "has_gemini": has_keys(env, ["GOOGLE_API_KEY"])
                              or has_keys(env, ["GEMINI_API_KEY"]),
                "has_anthropic": has_keys(env, ["ANTHROPIC_API_KEY"]),
                "env_path": str(env_path),
            }
            self._send_json(200, payload)

        def _handle_config_post(self) -> None:
            body = self._read_json_body()
            updates: dict[str, str] = {}
            # Accept multiple field names for flexibility.
            yt = (body.get("youtube") or body.get("YOUTUBE_API_KEY") or "").strip()
            goog = (body.get("google") or body.get("gemini") or body.get("GOOGLE_API_KEY") or "").strip()
            anth = (body.get("anthropic") or body.get("ANTHROPIC_API_KEY") or "").strip()
            if yt:
                updates["YOUTUBE_API_KEY"] = yt
            if goog:
                updates["GOOGLE_API_KEY"] = goog
            if anth:
                updates["ANTHROPIC_API_KEY"] = anth
            if not updates:
                return self._send_json(400, {"ok": False, "error": "no keys provided"})
            try:
                merge_env(env_path, updates)
                apply_to_environ(updates)
            except OSError as e:
                return self._send_json(500, {"ok": False, "error": f"write failed: {e}"})
            finally:
                # Wipe locals as best-effort; Python GC will handle the rest.
                for k in list(updates.keys()):
                    updates[k] = ""
                del body
            env = read_env(env_path)
            self._send_json(200, {
                "ok": True,
                "has_youtube": has_keys(env, ["YOUTUBE_API_KEY"]),
                "has_gemini": has_keys(env, ["GOOGLE_API_KEY"])
                              or has_keys(env, ["GEMINI_API_KEY"]),
                "has_anthropic": has_keys(env, ["ANTHROPIC_API_KEY"]),
            })

        # ---- /api/run ----
        def _handle_run_post(self) -> None:
            with _RUN_LOCK:
                if _RUN_STATE["status"] == "running":
                    return self._send_json(409, {
                        "ok": False,
                        "error": "run already in progress",
                        "run_id": _RUN_STATE["run_id"],
                    })
            body = self._read_json_body()
            query = (body.get("query") or "").strip()
            if not query:
                return self._send_json(400, {"ok": False, "error": "query required"})
            try:
                count = int(body.get("count") or 10)
            except (TypeError, ValueError):
                count = 10
            count = max(1, min(count, 200))
            llm_choice = body.get("llm_choice") or body.get("llm") or None
            run_id = f"api_{uuid.uuid4().hex[:8]}"

            with _RUN_LOCK:
                _RUN_STATE.update({
                    "run_id": run_id,
                    "status": "running",
                    "query": query,
                    "started_at": _now_iso(),
                    "ended_at": None,
                    "summary": None,
                    "error": None,
                })

            t = threading.Thread(
                target=_run_worker,
                kwargs={
                    "query": query,
                    "count": count,
                    "llm_choice": llm_choice,
                    "data_store": data_store,
                    "logs_root": logs_root,
                    "docs_dir": docs_dir,
                },
                daemon=True,
            )
            t.start()
            self._send_json(202, {"ok": True, "run_id": run_id, "query": query, "count": count})

        def _handle_run_status(self) -> None:
            with _RUN_LOCK:
                snap = dict(_RUN_STATE)
            self._send_json(200, snap)

    return _Handler


def _run_worker(
    *,
    query: str,
    count: int,
    llm_choice: str | None,
    data_store: Path,
    logs_root: Path,
    docs_dir: Path,
) -> None:
    """Run the pipeline in a background thread and persist status."""
    # Lazy import so the handler module doesn't drag pipeline deps in during
    # unit tests that only exercise /api/config.
    from .run import run_query

    try:
        summary = run_query(
            query,
            count=count,
            data_store_root=data_store,
            logs_root=logs_root,
            llm_choice=llm_choice,
        )
        with _RUN_LOCK:
            _RUN_STATE.update({
                "status": "completed",
                "ended_at": _now_iso(),
                "summary": summary,
            })
    except Exception as e:  # noqa: BLE001
        with _RUN_LOCK:
            _RUN_STATE.update({
                "status": "failed",
                "ended_at": _now_iso(),
                "error": f"{type(e).__name__}: {e}",
            })
        traceback.print_exc()
        return

    # Best-effort: refresh docs/status.json so the dashboard polls see
    # up-to-date per-stage counts. Matches what the GH Actions workflow does.
    try:
        from .status_cli import main as status_main
        status_main(["--out", str(docs_dir / "status.json")])
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[app] status.json refresh failed: {e}\n")


def reset_run_state_for_tests() -> None:
    """Test hook — not used at runtime."""
    with _RUN_LOCK:
        _RUN_STATE.update({
            "run_id": None,
            "status": "idle",
            "query": None,
            "started_at": None,
            "ended_at": None,
            "summary": None,
            "error": None,
        })
