"""Watch registry — persisted in state/watches.json.

Each entry binds a domain to a recurring research task: keywords,
selected NotebookLM modes, interval, last-run summary, and the
NotebookLM notebook id we keep reusing across ticks.

Concurrent access is protected by a single in-process lock + atomic
file writes (tempfile + os.replace). The file is small (few KB) so
naive load/save on every change is fine.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("state") / "watches.json"
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".watches.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load(path: Path = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    """Returns {domain: entry}. Missing file → empty dict."""
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    items = body.get("watches") if isinstance(body, dict) else None
    return items if isinstance(items, dict) else {}


def save(watches: dict[str, dict[str, Any]], path: Path = DEFAULT_PATH) -> None:
    with _LOCK:
        _atomic_write_json(path, {"watches": watches})


def upsert(domain: str, *, path: Path = DEFAULT_PATH, **fields: Any) -> dict[str, Any]:
    """Add a new watch or update fields on an existing one."""
    with _LOCK:
        items = load(path)
        existing = items.get(domain) or {
            "domain": domain,
            "created_at": _now_iso(),
            "paused": False,
            "history": [],
        }
        existing.update({k: v for k, v in fields.items() if v is not None})
        # Idempotent enrich
        existing["domain"] = domain
        items[domain] = existing
        _atomic_write_json(path, {"watches": items})
        return existing


def remove(domain: str, path: Path = DEFAULT_PATH) -> bool:
    with _LOCK:
        items = load(path)
        if domain not in items:
            return False
        del items[domain]
        _atomic_write_json(path, {"watches": items})
        return True


def record_run(
    domain: str,
    *,
    path: Path = DEFAULT_PATH,
    new_videos: int = 0,
    promoted: int = 0,
    invalid: int = 0,
    status: str = "completed",
    error: str | None = None,
    notebook_id: str | None = None,
) -> dict[str, Any]:
    """Append a tick result + bump last_* fields. Trims history to 20."""
    with _LOCK:
        items = load(path)
        entry = items.get(domain)
        if entry is None:
            return {}
        run_at = _now_iso()
        diff = {"new_videos": int(new_videos),
                "promoted": int(promoted),
                "invalid": int(invalid)}
        history = list(entry.get("history") or [])
        history.append({"run_at": run_at, **diff,
                        "status": status, "error": error or ""})
        history = history[-20:]
        entry.update({
            "last_run": run_at,
            "last_status": status,
            "last_diff": diff,
            "last_error": error,
            "history": history,
        })
        if notebook_id:
            entry["notebook_id"] = notebook_id
        items[domain] = entry
        _atomic_write_json(path, {"watches": items})
        return entry


__all__ = [
    "DEFAULT_PATH", "load", "save", "upsert", "remove", "record_run",
]
