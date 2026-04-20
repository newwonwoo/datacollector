"""Rollback procedure (Master_03 — 롤백).

Forward-only invariant: payload_version never decreases.
A rollback creates a NEW revision that restores prior analysis fields
and records the rollback event in history[].
"""
from __future__ import annotations

from typing import Any

from .events import EventLogger
from .payload import snapshot_for_history, utcnow_iso
from .store import JSONStore


class RollbackError(Exception):
    pass


def rollback(
    source_key: str,
    *,
    store: JSONStore,
    logger: EventLogger,
    reason: str,
    to_history_index: int = 0,
    actor: str = "user:ops",
) -> dict[str, Any]:
    """Restore the payload to an earlier snapshot from history[].

    - `to_history_index` 0 = most recent prior snapshot (default).
    - Current state is added to history first, then prior values restored.
    - `payload_version` is incremented (not decremented).
    """
    current = store.get(source_key)
    if current is None:
        raise RollbackError(f"{source_key}: not in store")
    history = list(current.get("history") or [])
    if not history:
        raise RollbackError(f"{source_key}: no history to roll back to")
    if not (0 <= to_history_index < len(history)):
        raise RollbackError(f"{source_key}: history index {to_history_index} out of range")

    ev = logger.log(
        entity_type="manual_action",
        entity_id=source_key,
        from_status=current.get("record_status"),
        to_status=current.get("record_status"),
        run_id=current.get("run_id", ""),
        reason=f"rollback:{reason}",
        actor=actor,
    )
    # Snapshot current state into history (so we could go forward again)
    history.append(snapshot_for_history(current, f"rollback_from:{reason}", ev["event_id"]))
    target = history[to_history_index]

    restored = dict(current)
    # Apply prior snapshot fields (only the ones we store)
    if target.get("prev_summary") is not None:
        restored["summary"] = target["prev_summary"]
    if target.get("prev_rules_snapshot") is not None:
        restored["rules"] = list(target["prev_rules_snapshot"])
    if target.get("prev_transcript_hash"):
        restored["transcript_hash"] = target["prev_transcript_hash"]
    if target.get("prev_confidence"):
        restored["confidence"] = target["prev_confidence"]

    restored["payload_version"] = int(current.get("payload_version", 1)) + 1
    restored["history"] = history
    restored["failure_reason_code"] = None
    restored["failure_reason_detail"] = None

    store.upsert(restored)
    return restored
