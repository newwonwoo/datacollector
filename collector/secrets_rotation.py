"""Secret rotation tracking (Appendix D §1).

Maintains a lightweight ledger of secret rotations: key name, timestamp,
hashed fingerprint of the old/new values (sha256 first 8 chars), and the
actor. Writes to `logs/events.jsonl` with entity_type=secret_rotation so
it lives alongside the normal event stream and is picked up by trace/
audit tooling.

This module NEVER persists the actual secret values. It only sees the
operator-provided hashes (or computes them from fresh input).
"""
from __future__ import annotations

import hashlib
from typing import Any

from .events import EventLogger
from .payload import utcnow_iso


class SecretRotationError(Exception):
    pass


def fingerprint(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def log_rotation(
    key_name: str,
    *,
    logger: EventLogger,
    old_value: str | None = None,
    new_value: str | None = None,
    actor: str = "user:ops",
    reason: str = "90d_rotation",
) -> dict[str, Any]:
    """Record a secret rotation event. Neither value is persisted raw.

    Raises SecretRotationError if new fingerprint equals old fingerprint
    (rotation was a no-op).
    """
    old_fp = fingerprint(old_value) if old_value is not None else ""
    new_fp = fingerprint(new_value) if new_value is not None else ""
    if old_fp and new_fp and old_fp == new_fp:
        raise SecretRotationError(f"{key_name}: new value fingerprint identical to old")
    event = logger.log(
        entity_type="secret_rotation",
        entity_id=f"secret:{key_name}",
        from_status=old_fp or None,
        to_status=new_fp or None,
        run_id=f"rotation_{utcnow_iso()[:10]}",
        reason=reason,
        metrics={
            "old_fingerprint": old_fp or None,
            "new_fingerprint": new_fp or None,
        },
        actor=actor,
    )
    return event


def days_since_last_rotation(
    key_name: str,
    events_path,
) -> int | None:
    """Scan events.jsonl for the most recent rotation of `key_name`.

    Returns None if never rotated. Used by `collector alerts` to surface
    90-day-due warnings (ROTATION_DUE alert code, future work).
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    path = Path(events_path)
    if not path.exists():
        return None
    last_iso: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("entity_type") == "secret_rotation" and e.get("entity_id") == f"secret:{key_name}":
            last_iso = e.get("recorded_at") or last_iso
    if last_iso is None:
        return None
    try:
        dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    return (datetime.now(timezone.utc) - dt).days
