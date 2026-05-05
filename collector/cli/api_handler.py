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
    POST /api/workflow      → body: {"domain", "count", "modes":[...]} → start full chain
    GET  /api/workflow/status → latest workflow run summary
    GET  /api/workflow/files  → list of artifacts in latest workflow out_dir

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

# Separate state for `collector workflow full` runs, kept distinct from
# /api/run so the existing single-keyword UI doesn't think the workflow
# is "the" run.
_WORKFLOW_STATE: dict[str, Any] = {
    "run_id": None,
    "status": "idle",
    "domain": None,
    "modes": [],
    "started_at": None,
    "ended_at": None,
    "out_dir": None,
    "error": None,
}
_WORKFLOW_LOCK = threading.Lock()

# Watch-tick serialization: only one watch tick may run at a time. When
# the user checks N watches and clicks "선택 업데이트", the dashboard
# fires N POSTs in quick succession — each spawns a worker thread that
# blocks on this lock before doing any YouTube / LLM work, so the API
# rate limits (Gemini 1500 RPD, YouTube residential throttle) only see
# one stream at a time. Watches "queue" automatically.
_WATCH_TICK_LOCK = threading.Lock()


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
                if path == "/api/workflow/status":
                    return self._handle_workflow_status()
                if path == "/api/workflow/files":
                    return self._handle_workflow_files()
                if path == "/api/resolve_channel":
                    return self._handle_resolve_channel_get()
                if path == "/api/events":
                    return self._handle_events_get()
                if path == "/api/watches":
                    return self._handle_watches_get()
                if path == "/api/run/status":
                    return self._handle_run_status()
                if path == "/api/records":
                    return self._handle_records()
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

        def do_DELETE(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path.startswith("/api/watches/"):
                    return self._handle_watch_delete(path)
                self.send_error(404, "not found")
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[app] DELETE {path} error: {e}\n")
                traceback.print_exc()
                self._send_json(500, {"ok": False, "error": "internal error"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/config":
                    return self._handle_config_post()
                if path == "/api/run":
                    return self._handle_run_post()
                if path == "/api/workflow":
                    return self._handle_workflow_post()
                if path == "/api/watches":
                    return self._handle_watches_post()
                if path.startswith("/api/watches/") and path.endswith("/tick"):
                    return self._handle_watch_tick(path)
                if path.startswith("/api/watches/") and path.endswith("/pause"):
                    return self._handle_watch_pause(path)
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
                "has_groq": has_keys(env, ["GROQ_API_KEY"]),
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
            groq = (body.get("groq") or body.get("GROQ_API_KEY") or "").strip()
            anth = (body.get("anthropic") or body.get("ANTHROPIC_API_KEY") or "").strip()
            if yt:
                updates["YOUTUBE_API_KEY"] = yt
            if goog:
                updates["GOOGLE_API_KEY"] = goog
            if groq:
                updates["GROQ_API_KEY"] = groq
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
                "has_groq": has_keys(env, ["GROQ_API_KEY"]),
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
            # Empty query is allowed *iff* a channel is provided — in that
            # case we just collect the channel's latest videos. Validate
            # that combination after channel resolution below.
            try:
                count = int(body.get("count") or 10)
            except (TypeError, ValueError):
                count = 10
            count = max(1, min(count, 200))
            llm_choice = body.get("llm_choice") or body.get("llm") or None
            try:
                min_views = max(0, int(body.get("min_views") or 0))
            except (TypeError, ValueError):
                min_views = 0
            try:
                min_subscribers = max(0, int(body.get("min_subscribers") or 0))
            except (TypeError, ValueError):
                min_subscribers = 0
            # Channels: accept either single `channel_id` (legacy) or
            # multiple `channels` (list / line-or-comma-separated string).
            raw_inputs = _parse_channels_field(
                body.get("channels"),
                body.get("channel_id") or body.get("target_channel_id")
                    or body.get("channel"),
            )
            channel_ids: list[str] = []
            channel_errors: list[str] = []
            for raw in raw_inputs:
                if raw.startswith("UC") and len(raw) >= 22 and " " not in raw:
                    channel_ids.append(raw[:24])
                    continue
                try:
                    cid = _resolve_channel_input(raw)
                except Exception as e:  # noqa: BLE001
                    channel_errors.append(f"{raw}: {type(e).__name__}: {e}")
                    continue
                if cid:
                    channel_ids.append(cid)
                else:
                    channel_errors.append(f"{raw}: 인식 불가")
            if raw_inputs and not channel_ids:
                return self._send_json(400, {
                    "ok": False,
                    "error": "채널 인식 실패: " + "; ".join(channel_errors),
                })
            target_channel_id = channel_ids[0] if len(channel_ids) == 1 else None

            # Must have at least one of: keyword, channel(s).
            if not query and not channel_ids:
                return self._send_json(400, {
                    "ok": False,
                    "error": "검색어 또는 채널 둘 중 하나는 필요합니다.",
                })
            run_id = f"api_{uuid.uuid4().hex[:8]}"

            with _RUN_LOCK:
                _RUN_STATE.update({
                    "run_id": run_id,
                    "status": "running",
                    "query": query,
                    "requested_count": count * max(1, len(channel_ids) or 1),
                    "min_views": min_views,
                    "min_subscribers": min_subscribers,
                    "channel_id": target_channel_id,
                    "channel_ids": channel_ids,
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
                    "min_views": min_views,
                    "min_subscribers": min_subscribers,
                    "target_channel_id": target_channel_id,
                    "channel_ids": channel_ids,
                    "data_store": data_store,
                    "logs_root": logs_root,
                    "docs_dir": docs_dir,
                },
                daemon=True,
            )
            t.start()
            self._send_json(202, {
                "ok": True, "run_id": run_id, "query": query, "count": count,
                "min_views": min_views, "min_subscribers": min_subscribers,
                "channel_id": target_channel_id,
                "channel_ids": channel_ids,
                "channel_errors": channel_errors or None,
            })

        def _handle_run_status(self) -> None:
            with _RUN_LOCK:
                snap = dict(_RUN_STATE)
            self._send_json(200, snap)

        def _handle_records(self) -> None:
            """Return the most recently collected records from the local
            data_store/ tree so the dashboard can render them in local mode
            without reaching out to the GitHub Contents API.

            Query params:
              limit: int (default 30, hard-capped at 200)
            """
            from urllib.parse import parse_qs
            qs = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            try:
                limit = int(qs.get("limit", ["30"])[0])
            except (TypeError, ValueError):
                limit = 30
            limit = max(1, min(limit, 200))

            files: list[Path] = []
            if data_store.exists():
                files = list(data_store.rglob("*.json"))
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

            out: list[dict] = []
            for p in files[:limit]:
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                # Trim transcript out — could be 50KB and we don't render it.
                rec.pop("transcript", None)
                out.append(rec)

            self._send_json(200, {
                "total_files": len(files),
                "records": out,
            })

        # ---- /api/workflow ----
        def _handle_workflow_post(self) -> None:
            with _WORKFLOW_LOCK:
                if _WORKFLOW_STATE["status"] == "running":
                    return self._send_json(409, {
                        "ok": False,
                        "error": "workflow already in progress",
                        "run_id": _WORKFLOW_STATE["run_id"],
                    })
            body = self._read_json_body()
            domain = (body.get("domain") or "").strip()
            if not domain:
                return self._send_json(400, {"ok": False, "error": "domain required"})
            try:
                count = int(body.get("count") or 5)
            except (TypeError, ValueError):
                count = 5
            count = max(1, min(count, 50))
            try:
                videos_per_keyword = int(body.get("videos_per_keyword") or 10)
            except (TypeError, ValueError):
                videos_per_keyword = 10
            videos_per_keyword = max(1, min(videos_per_keyword, 50))
            try:
                min_views = max(0, int(body.get("min_views") or 0))
            except (TypeError, ValueError):
                min_views = 0
            try:
                min_subscribers = max(0, int(body.get("min_subscribers") or 0))
            except (TypeError, ValueError):
                min_subscribers = 0

            # modes: list[str] or comma-separated string. Validated server-side.
            raw_modes = body.get("modes") or []
            if isinstance(raw_modes, str):
                modes_str = raw_modes
            else:
                modes_str = ",".join(str(m) for m in raw_modes)
            try:
                from ..workflows._nb_specs import parse_modes
                modes = parse_modes(modes_str)
            except ValueError as e:
                return self._send_json(400, {"ok": False, "error": str(e)})

            run_id = f"wf_{uuid.uuid4().hex[:8]}"
            out_dir = (project_root / "exports" / "run" / run_id).resolve()

            with _WORKFLOW_LOCK:
                _WORKFLOW_STATE.update({
                    "run_id": run_id,
                    "status": "running",
                    "domain": domain,
                    "modes": modes,
                    "started_at": _now_iso(),
                    "ended_at": None,
                    "out_dir": str(out_dir),
                    "error": None,
                })

            t = threading.Thread(
                target=_workflow_worker,
                kwargs={
                    "domain": domain,
                    "count": count,
                    "videos_per_keyword": videos_per_keyword,
                    "min_views": min_views,
                    "min_subscribers": min_subscribers,
                    "modes_csv": ",".join(modes),
                    "out_dir": out_dir,
                    "data_store": data_store,
                    "logs_root": logs_root,
                    "docs_dir": docs_dir,
                },
                daemon=True,
            )
            t.start()
            self._send_json(202, {
                "ok": True, "run_id": run_id, "domain": domain,
                "count": count, "modes": modes,
                "out_dir": str(out_dir),
            })

        def _handle_resolve_channel_get(self) -> None:
            """GET /api/resolve_channel?q=... — preview the UC… that the
            given input would resolve to, without starting a run. Used
            by the dashboard to show '✓ resolved to UC…' next to the
            channel input as the user types/pastes."""
            from urllib.parse import parse_qs
            qs = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            raw = (qs.get("q", [""])[0] or "").strip()
            if not raw:
                return self._send_json(200, {"ok": True, "channel_id": None})
            try:
                cid = _resolve_channel_input(raw)
            except Exception as e:  # noqa: BLE001
                return self._send_json(502, {"ok": False, "error": str(e)})
            if not cid:
                return self._send_json(200, {
                    "ok": False,
                    "channel_id": None,
                    "error": "인식 불가 — UC.../@핸들/채널 URL 중 하나여야 합니다.",
                })
            return self._send_json(200, {"ok": True, "channel_id": cid})

        def _handle_events_get(self) -> None:
            """GET /api/events?run_id=… — return events.jsonl entries.

            The dashboard uses these to do a deterministic replay (no
            race-induced flicker). When run_id is omitted, returns
            events for the most-recently-started run only — bounded by
            limit=2000 so very long histories don't bloat the response."""
            from urllib.parse import parse_qs
            qs = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            requested_run = (qs.get("run_id", [""])[0] or "").strip() or None
            try:
                limit = int(qs.get("limit", ["2000"])[0])
            except (TypeError, ValueError):
                limit = 2000
            limit = max(1, min(limit, 50000))

            events_path = logs_root / "events.jsonl"
            if not events_path.exists():
                return self._send_json(200, {"events": [], "run_id": None})

            # First pass: parse each line (cheap), find latest run_id.
            all_events: list[dict] = []
            try:
                raw = events_path.read_text(encoding="utf-8")
            except OSError as e:
                return self._send_json(500, {"error": str(e)})
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                all_events.append(e)

            target_run = requested_run
            if target_run is None:
                latest_ts = ""
                for e in all_events:
                    if e.get("entity_type") == "run" and e.get("to_status") == "running":
                        ts = e.get("recorded_at", "")
                        if ts >= latest_ts:
                            latest_ts = ts
                            target_run = e.get("run_id") or e.get("entity_id")

            filtered = [e for e in all_events if e.get("run_id") == target_run]
            if len(filtered) > limit:
                # Keep the most recent `limit` events (events.jsonl is
                # append-order; recent are at the tail).
                filtered = filtered[-limit:]
            self._send_json(200, {
                "events": filtered,
                "run_id": target_run,
                "total": len(filtered),
            })

        def _handle_workflow_status(self) -> None:
            with _WORKFLOW_LOCK:
                snap = dict(_WORKFLOW_STATE)
            self._send_json(200, snap)

        def _handle_workflow_files(self) -> None:
            """List artifacts in the latest (or queried) workflow out_dir.
            Used by the dashboard to render links to spec_NB_*.md after
            the run completes."""
            from urllib.parse import parse_qs
            qs = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            with _WORKFLOW_LOCK:
                cur_dir = _WORKFLOW_STATE.get("out_dir")
            requested = qs.get("dir", [cur_dir])[0]
            if not requested:
                return self._send_json(200, {"files": []})
            target = Path(requested).resolve()
            # Confine to project_root/exports/.
            try:
                target.relative_to((project_root / "exports").resolve())
            except ValueError:
                return self._send_json(403, {"error": "out_dir outside exports/"})
            if not target.exists() or not target.is_dir():
                return self._send_json(200, {"files": []})
            files = []
            for p in sorted(target.iterdir()):
                if p.is_file():
                    files.append({
                        "name": p.name,
                        "size": p.stat().st_size,
                        # Always POSIX so Windows backslashes don't end up in
                        # browser URLs (would 404 — '%5C' != '/').
                        "rel": p.relative_to(project_root).as_posix(),
                    })
            self._send_json(200, {"out_dir": str(target), "files": files})

        # ---- /api/watches (registered cron-like watches) ----
        def _watches_path(self) -> Path:
            return (project_root / "state" / "watches.json").resolve()

        def _handle_watches_get(self) -> None:
            from .. import watch_registry as reg
            items = reg.load(self._watches_path())
            # Render as a list (stable order: alphabetical by domain) so
            # the dashboard grid doesn't reorder rows on each refresh.
            entries = [items[k] for k in sorted(items.keys())]
            self._send_json(200, {"watches": entries})

        def _handle_watches_post(self) -> None:
            from .. import watch_registry as reg
            body = self._read_json_body()
            domain = (body.get("domain") or "").strip()
            if not domain:
                return self._send_json(400, {"ok": False, "error": "domain required"})
            kw_raw = body.get("keywords") or []
            if isinstance(kw_raw, str):
                keywords = [s.strip() for s in kw_raw.split(",") if s.strip()]
            else:
                keywords = [str(s).strip() for s in kw_raw if str(s).strip()]
            modes_raw = body.get("modes") or []
            if isinstance(modes_raw, str):
                modes = [s.strip() for s in modes_raw.split(",") if s.strip()]
            else:
                modes = [str(s) for s in modes_raw]
            interval = (body.get("interval") or "manual").strip()
            if interval not in ("daily", "weekly", "monthly", "manual"):
                return self._send_json(400, {"ok": False,
                                             "error": "interval must be daily/weekly/monthly/manual"})
            try:
                vpk = int(body.get("videos_per_keyword") or 10)
            except (TypeError, ValueError):
                vpk = 10

            # Channels: same parsing as /api/run — list, line-or-comma-
            # separated string, or a single legacy "channel" field.
            channel_inputs = _parse_channels_field(
                body.get("channels"), body.get("channel"))
            channel_ids: list[str] = []
            channel_errors: list[str] = []
            for raw in channel_inputs:
                if raw.startswith("UC") and len(raw) >= 22 and " " not in raw:
                    channel_ids.append(raw[:24])
                    continue
                try:
                    cid = _resolve_channel_input(raw)
                except Exception as e:  # noqa: BLE001
                    channel_errors.append(f"{raw}: {type(e).__name__}: {e}")
                    continue
                if cid:
                    channel_ids.append(cid)
                else:
                    channel_errors.append(f"{raw}: 인식 불가")
            if channel_inputs and not channel_ids:
                return self._send_json(400, {"ok": False,
                                             "error": "채널 인식 실패: " + "; ".join(channel_errors)})

            # Need at least one of: keywords, channels.
            if not keywords and not channel_ids:
                return self._send_json(400, {
                    "ok": False,
                    "error": "키워드 또는 채널 둘 중 하나는 필요합니다.",
                })

            entry = reg.upsert(
                domain, path=self._watches_path(),
                keywords=keywords, modes=modes,
                channels=channel_ids, channel_inputs=channel_inputs,
                interval=interval, videos_per_keyword=max(1, min(vpk, 50)),
                paused=False,
            )
            resp = {"ok": True, "watch": entry}
            if channel_errors:
                resp["channel_errors"] = channel_errors
            self._send_json(200, resp)

        def _handle_watch_delete(self, path: str) -> None:
            from .. import watch_registry as reg
            domain = path[len("/api/watches/"):].rstrip("/")
            if not domain:
                return self._send_json(400, {"ok": False, "error": "domain in path required"})
            from urllib.parse import unquote
            domain = unquote(domain)
            ok = reg.remove(domain, self._watches_path())
            self._send_json(200, {"ok": ok})

        def _handle_watch_pause(self, path: str) -> None:
            from .. import watch_registry as reg
            from urllib.parse import unquote
            domain = unquote(path.split("/")[-2])
            body = self._read_json_body()
            paused = bool(body.get("paused", True))
            entry = reg.upsert(domain, path=self._watches_path(), paused=paused)
            self._send_json(200, {"ok": True, "watch": entry})

        def _handle_watch_tick(self, path: str) -> None:
            """POST /api/watches/{domain}/tick — fire one tick in a
            background thread so the request returns immediately."""
            from urllib.parse import unquote
            domain = unquote(path.split("/")[-2])
            from .. import watch_registry as reg
            items = reg.load(self._watches_path())
            entry = items.get(domain)
            if entry is None:
                return self._send_json(404, {"ok": False, "error": "not registered"})

            t = threading.Thread(
                target=_run_watch_tick_worker,
                kwargs={
                    "domain": domain,
                    "data_store": data_store,
                    "logs_root": logs_root,
                    "registry_path": self._watches_path(),
                    "out_dir": project_root / "exports" / "watch",
                    "docs_dir": docs_dir,
                },
                daemon=True,
            )
            t.start()
            self._send_json(202, {"ok": True, "domain": domain})

    return _Handler


def _parse_channels_field(channels_value: Any, legacy_single: Any = None) -> list[str]:
    """Normalize the multiple-channel input shape to a flat list of
    raw strings (URLs/@handles/UC…). Accepts:

      - list[str]  → as-is, trimmed
      - str        → split on newlines AND commas
      - None       → []  (then fall back to `legacy_single`)
    """
    out: list[str] = []
    if isinstance(channels_value, list):
        for item in channels_value:
            s = str(item or "").strip()
            if s:
                out.append(s)
    elif isinstance(channels_value, str):
        for line in channels_value.replace(",", "\n").splitlines():
            s = line.strip()
            if s:
                out.append(s)
    if not out and legacy_single:
        s = str(legacy_single).strip()
        if s:
            out.append(s)
    # De-dup preserve order.
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def _resolve_channel_input(raw: str) -> str | None:
    """Turn user input (URL / @handle / UC…) into a UC… channel id.

    Builds a YouTubeAdapter from the env's YOUTUBE_API_KEY. Returns None
    on unresolvable input. Raises only if the adapter can't be built —
    typically because no API key is configured."""
    raw = (raw or "").strip()
    if not raw:
        return None
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        # No key → can only handle UC… directly without an API call.
        if raw.startswith("UC") and len(raw) >= 24:
            return raw[:24]
        raise RuntimeError("YOUTUBE_API_KEY 가 설정되지 않아 핸들/URL 변환 불가")
    from ..adapters.youtube import YouTubeAdapter
    return YouTubeAdapter(api_key).resolve_channel(raw)


def _refresh_status_json(*, docs_dir: Path, data_store: Path, logs_root: Path) -> None:
    """One-shot: regenerate docs/status.json from events.jsonl + data_store
    so the dashboard's stage diagram reflects current pipeline progress."""
    try:
        from .status_cli import main as status_main
        status_main([
            "--out", str(docs_dir / "status.json"),
            "--data-store", str(data_store),
            "--events", str(logs_root / "events.jsonl"),
        ])
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[app] status.json refresh failed: {e}\n")


class _StatusTicker:
    """Background thread that refreshes status.json every `interval` sec.

    Used to wrap long-running workers so the dashboard's pipeline
    diagram (which polls /status.json) shows live progress instead of
    being frozen on the previous run's terminal counts."""

    def __init__(self, *, docs_dir: Path, data_store: Path,
                 logs_root: Path, interval: float = 2.5) -> None:
        self._kw = {"docs_dir": docs_dir, "data_store": data_store,
                    "logs_root": logs_root}
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            _refresh_status_json(**self._kw)
            self._stop.wait(self._interval)

    def __enter__(self) -> "_StatusTicker":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 1.0)
        # One final synchronous refresh so the post-run snapshot is durable
        # even if the ticker's last tick lost the race with the worker exit.
        _refresh_status_json(**self._kw)


def _run_worker(
    *,
    query: str,
    count: int,
    llm_choice: str | None,
    data_store: Path,
    logs_root: Path,
    docs_dir: Path,
    min_views: int = 0,
    min_subscribers: int = 0,
    target_channel_id: str | None = None,
    channel_ids: list[str] | None = None,
) -> None:
    """Run the pipeline in a background thread and persist status.

    With 0/1 channel: single run_query call (existing path).
    With 2+ channels: serial loop — one run_query per channel, all under
    the same lock. The cohort dashboard tracks the latest sub-run; the
    summary in _RUN_STATE rolls up promoted/processed across all subs."""
    from .run import run_query

    chs = list(channel_ids or [])
    if not chs:
        chs = [target_channel_id]  # may be [None] = unrestricted

    try:
        with _StatusTicker(docs_dir=docs_dir, data_store=data_store,
                           logs_root=logs_root):
            agg = {"query": query,
                   "requested_count": count * max(1, len(chs)),
                   "promoted": 0, "processed": 0,
                   "candidates": 0, "skipped_duplicates": 0,
                   "per_video": [], "channel_summaries": []}
            for ch in chs:
                sub = run_query(
                    query,
                    count=count,
                    data_store_root=data_store,
                    logs_root=logs_root,
                    llm_choice=llm_choice,
                    min_views=min_views,
                    min_subscribers=min_subscribers,
                    target_channel_id=ch,
                )
                for k in ("promoted", "processed", "candidates", "skipped_duplicates"):
                    agg[k] += int(sub.get(k, 0) or 0)
                agg["per_video"].extend(sub.get("per_video", []) or [])
                agg["channel_summaries"].append(
                    {"target_channel_id": ch,
                     "promoted": sub.get("promoted", 0),
                     "processed": sub.get("processed", 0)})
        with _RUN_LOCK:
            _RUN_STATE.update({
                "status": "completed",
                "ended_at": _now_iso(),
                "summary": agg,
            })
    except Exception as e:  # noqa: BLE001
        with _RUN_LOCK:
            _RUN_STATE.update({
                "status": "failed",
                "ended_at": _now_iso(),
                "error": f"{type(e).__name__}: {e}",
            })
        traceback.print_exc()
        _refresh_status_json(docs_dir=docs_dir, data_store=data_store,
                             logs_root=logs_root)


def _workflow_worker(
    *,
    domain: str,
    count: int,
    videos_per_keyword: int,
    min_views: int,
    min_subscribers: int,
    modes_csv: str,
    out_dir: Path,
    data_store: Path,
    logs_root: Path,
    docs_dir: Path | None = None,
) -> None:
    """Run `collector workflow full --modes ...` in a background thread."""
    from .workflow import main as workflow_main

    out_dir.mkdir(parents=True, exist_ok=True)
    argv = [
        "full",
        "--domain", domain,
        "--count", str(count),
        "--videos-per-keyword", str(videos_per_keyword),
        "--min-views", str(min_views),
        "--min-subscribers", str(min_subscribers),
        "--data-store", str(data_store),
        "--logs", str(logs_root),
        "--out-dir", str(out_dir),
    ]
    if modes_csv:
        argv += ["--modes", modes_csv]

    # Resolve docs_dir lazily — if the caller didn't pass one (older
    # callers / tests), fall back to project_root/docs which is where
    # the dashboard reads status.json from.
    if docs_dir is None:
        docs_dir = data_store.parent / "docs"

    try:
        with _StatusTicker(docs_dir=docs_dir, data_store=data_store,
                           logs_root=logs_root):
            rc = workflow_main(argv)
        if rc != 0:
            with _WORKFLOW_LOCK:
                _WORKFLOW_STATE.update({
                    "status": "failed",
                    "ended_at": _now_iso(),
                    "error": f"workflow exit code {rc}",
                })
            return
        with _WORKFLOW_LOCK:
            _WORKFLOW_STATE.update({
                "status": "completed",
                "ended_at": _now_iso(),
            })
    except Exception as e:  # noqa: BLE001
        with _WORKFLOW_LOCK:
            _WORKFLOW_STATE.update({
                "status": "failed",
                "ended_at": _now_iso(),
                "error": f"{type(e).__name__}: {e}",
            })
        traceback.print_exc()
        _refresh_status_json(docs_dir=docs_dir, data_store=data_store,
                             logs_root=logs_root)


def _run_watch_tick_worker(
    *,
    domain: str,
    data_store: Path,
    logs_root: Path,
    registry_path: Path,
    out_dir: Path,
    docs_dir: Path,
) -> None:
    """Run a single watch tick (research_batch + optional NB refresh).

    Serializes via _WATCH_TICK_LOCK so multiple POSTs from the dashboard's
    "선택 업데이트" don't fan out into parallel YouTube / Gemini calls
    that would blow API quotas. Wrapped in StatusTicker so the cohort
    dashboard updates live."""
    from .watch import run_tick
    from .. import watch_registry as reg

    # Mark as queued before we acquire the lock so the dashboard polling
    # can show "대기중" instead of pretending nothing happened.
    try:
        reg.upsert(domain, path=registry_path, last_status="queued")
    except Exception:  # noqa: BLE001
        pass

    with _WATCH_TICK_LOCK:
        try:
            with _StatusTicker(docs_dir=docs_dir, data_store=data_store,
                               logs_root=logs_root):
                run_tick(
                    domain,
                    data_store_root=data_store,
                    logs_root=logs_root,
                    registry_path=registry_path,
                    out_dir=out_dir,
                )
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[watch-tick worker] {domain}: {e}\n")
            traceback.print_exc()


def reset_run_state_for_tests() -> None:
    """Test hook — not used at runtime."""
    with _RUN_LOCK:
        _RUN_STATE.update({
            "run_id": None,
            "status": "idle",
            "query": None,
            "requested_count": None,
            "min_views": 0,
            "min_subscribers": 0,
            "channel_id": None,
            "started_at": None,
            "ended_at": None,
            "summary": None,
            "error": None,
        })
    with _WORKFLOW_LOCK:
        _WORKFLOW_STATE.update({
            "run_id": None,
            "status": "idle",
            "domain": None,
            "modes": [],
            "started_at": None,
            "ended_at": None,
            "out_dir": None,
            "error": None,
        })
