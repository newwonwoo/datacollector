"""Lockfile with owner_id, lease TTL, and heartbeat (Master_01 §7).

Atomic via rename. Multiple workers are safe to call `acquire()`; exactly
one succeeds per source_key while the lease is valid.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEASE_SECONDS = 10 * 60        # 10 min lease
HEARTBEAT_SECONDS = 2 * 60     # beat every 2 min
STALE_SECONDS = 4 * 60         # other workers reclaim after 4 min


@dataclass
class Lock:
    path: Path
    owner_id: str
    acquired_at: float
    lease_expires_at: float
    heartbeat_at: float


def _now() -> float:
    return time.time()


def _owner_id() -> str:
    return f"worker-{socket.gethostname()}-{os.getpid()}"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".lock.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def acquire(source_key: str, *, root: Path = Path("locks"), owner: str | None = None) -> Lock | None:
    """Try to acquire an exclusive lock. Returns Lock on success, None if held."""
    path = root / f"{source_key.replace(':', '__')}.json"
    me = owner or _owner_id()
    now = _now()

    existing = _read(path)
    if existing is not None:
        # Steal iff heartbeat is stale
        hb = float(existing.get("heartbeat_at", 0))
        if now - hb < STALE_SECONDS:
            return None

    payload = {
        "source_key": source_key,
        "owner_id": me,
        "acquired_at": now,
        "lease_expires_at": now + LEASE_SECONDS,
        "heartbeat_at": now,
    }
    _atomic_write(path, payload)

    # Confirm we actually own it (in case of a race)
    again = _read(path)
    if again is None or again.get("owner_id") != me:
        return None

    return Lock(
        path=path, owner_id=me,
        acquired_at=now, lease_expires_at=now + LEASE_SECONDS, heartbeat_at=now,
    )


def heartbeat(lock: Lock) -> None:
    """Refresh heartbeat + extend lease."""
    now = _now()
    payload = {
        "source_key": lock.path.stem.replace("__", ":"),
        "owner_id": lock.owner_id,
        "acquired_at": lock.acquired_at,
        "lease_expires_at": now + LEASE_SECONDS,
        "heartbeat_at": now,
    }
    _atomic_write(lock.path, payload)
    lock.heartbeat_at = now
    lock.lease_expires_at = now + LEASE_SECONDS


def release(lock: Lock) -> None:
    """Release the lock (idempotent)."""
    try:
        current = _read(lock.path)
        if current is not None and current.get("owner_id") == lock.owner_id:
            lock.path.unlink(missing_ok=True)
    except OSError:
        pass
