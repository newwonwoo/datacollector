"""End-to-end orchestrator."""
from __future__ import annotations

from typing import Any

import json
from pathlib import Path

from .events import EventLogger
from .killswitch import KillSwitchTriggered, is_paused
from .locks import acquire, heartbeat, release
from .payload import snapshot_for_history
from .runs import save_run_snapshot
from .services import Services
from .stages import (
    StageFail,
    stage_collect,
    stage_discover,
    stage_extract,
    stage_normalize,
    stage_package,
    stage_promote,
    stage_review,
)
from .store import JSONStore
from .vault import regenerate_moc, write_note


def _route_to_review_queue(payload: dict[str, Any], review_queue_root: Path, logger: EventLogger) -> None:
    """Auto-route reviewed_inferred / reviewed_unverified to review_queue/.

    Master_02 §3.3 — human review workflow.
    """
    rs = payload.get("record_status", "")
    if rs not in ("reviewed_inferred", "reviewed_unverified"):
        return
    review_queue_root.mkdir(parents=True, exist_ok=True)
    name = payload["source_key"].replace(":", "__") + ".json"
    target = review_queue_root / name
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.log(
        entity_type="record",
        entity_id=payload["source_key"],
        from_status=rs, to_status=rs,
        run_id=payload.get("run_id", ""),
        reason=f"routed_to_review_queue:{rs}",
    )


def _check_kill_switch(payload: dict[str, Any], logger: EventLogger, where: str) -> None:
    if not is_paused():
        return
    for s in payload.get("stage_status", {}):
        if payload["stage_status"][s] in ("not_started", "started"):
            payload["stage_status"][s] = "skipped"
    payload["failure_reason_code"] = "SYS_KILL_SWITCH"
    logger.log(
        entity_type="record",
        entity_id=payload["source_key"],
        from_status=payload.get("record_status"),
        to_status=payload.get("record_status"),
        run_id=payload["run_id"],
        reason=f"kill_switch:{where}",
    )
    raise KillSwitchTriggered(where)


def _fail_run(payload, logger, run_id, to_status="partially_completed"):
    logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status=to_status, run_id=run_id)


def run_pipeline(
    payload: dict[str, Any],
    services: Services,
    store: JSONStore,
    logger: EventLogger,
    *,
    fast_track: bool = False,
    use_lock: bool = True,
    vault_root: Path | None = Path("vault"),
    review_queue_root: Path | None = Path("review_queue"),
) -> dict[str, Any]:
    """Run all seven stages on a single payload.

    Enforces:
    - COLLECTOR_PAUSED kill switch at every stage boundary.
    - Exclusive lock per source_key (heartbeat + lease).
    - Dedup rules A/B/C against `store` before collect.
    Returns the updated payload regardless of outcome.
    """
    run_id = payload["run_id"]
    logger.log(entity_type="run", entity_id=run_id, from_status=None, to_status="running", run_id=run_id)

    # P0-a: pre-flight kill switch check
    if is_paused():
        payload["failure_reason_code"] = "SYS_KILL_SWITCH"
        for s in payload.get("stage_status", {}):
            payload["stage_status"][s] = "skipped"
        logger.log(entity_type="record", entity_id=payload["source_key"],
                   from_status=payload.get("record_status"), to_status=payload.get("record_status"),
                   run_id=run_id, reason="kill_switch:preflight")
        _fail_run(payload, logger, run_id, to_status="failed")
        return payload

    # P0-b: acquire exclusive lock for this source_key
    lock = None
    if use_lock:
        lock = acquire(payload["source_key"])
        if lock is None:
            payload["failure_reason_code"] = "SYS_LOCK_HELD"
            logger.log(entity_type="record", entity_id=payload["source_key"],
                       from_status=payload.get("record_status"), to_status=payload.get("record_status"),
                       run_id=run_id, reason="lock_held_by_other")
            _fail_run(payload, logger, run_id, to_status="failed")
            return payload

    try:
        try:
            stage_discover(payload, services, logger, fast_track=fast_track)
            _check_kill_switch(payload, logger, "after_discover")
            stage_collect(payload, services, logger)
            _check_kill_switch(payload, logger, "after_collect")
        except (StageFail, KillSwitchTriggered):
            _fail_run(payload, logger, run_id)
            return payload

        # Dedup check after collecting transcript_hash
        rule = store.dedup_rule(payload["source_key"], payload["transcript_hash"])
        if rule == "C":
            logger.log(
                entity_type="record",
                entity_id=payload["source_key"],
                from_status="collected",
                to_status="collected",
                run_id=run_id,
                reason="rule_c_duplicate",
            )
            for s in ("extract", "normalize", "review", "promote", "package"):
                payload["stage_status"][s] = "skipped"
            logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="completed", run_id=run_id)
            return payload

        if rule == "B":
            existing = store.get(payload["source_key"]) or {}
            ev = logger.log(
                entity_type="record",
                entity_id=payload["source_key"],
                from_status="collected",
                to_status="collected",
                run_id=run_id,
                reason="rule_b_changed",
            )
            if existing:
                payload["history"] = list(existing.get("history", []))
                payload["history"].append(snapshot_for_history(existing, "transcript_changed", ev["event_id"]))
                payload["payload_version"] = int(existing.get("payload_version", 1)) + 1

        try:
            stage_extract(payload, services, logger)
            _check_kill_switch(payload, logger, "after_extract")
            stage_normalize(payload, services, logger)
            _check_kill_switch(payload, logger, "after_normalize")
            stage_review(payload, services, logger)
            _check_kill_switch(payload, logger, "after_review")
        except (StageFail, KillSwitchTriggered):
            _fail_run(payload, logger, run_id)
            return payload

        # P1-β: auto-route inferred/unverified to human review queue
        if review_queue_root is not None:
            _route_to_review_queue(payload, review_queue_root, logger)

        # heartbeat before long-tail stages
        if lock is not None:
            heartbeat(lock)

        try:
            stage_promote(payload, services, logger, store)
        except StageFail:
            _fail_run(payload, logger, run_id)
            return payload

        # Obsidian vault write (Master_03 §2 Renderer) — local, always.
        if vault_root is not None:
            try:
                write_note(payload, vault_root)
                regenerate_moc(vault_root)
            except Exception as e:
                logger.log(
                    entity_type="stage",
                    entity_id=f"{payload['source_key']}:vault",
                    from_status=None, to_status="failed",
                    run_id=run_id, reason=f"vault_write_error:{e}",
                )

        try:
            stage_package(payload, services, logger)
        except StageFail:
            store.send_to_dlq(payload, payload.get("failure_reason_code") or "GIT_CONFLICT")
            store.upsert(payload)
            _fail_run(payload, logger, run_id)
            return payload

        store.upsert(payload)
        logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="completed", run_id=run_id)
        return payload
    finally:
        if lock is not None:
            release(lock)


def manual_reinject(
    payload: dict[str, Any], store: JSONStore, logger: EventLogger, *, reason: str, actor: str = "user:ops"
) -> dict[str, Any]:
    """Admin path: invalid → collected (Master_01 §4)."""
    prev = payload.get("record_status")
    payload["record_status"] = "collected"
    payload["retry_count"] = int(payload.get("retry_count", 0)) + 1
    logger.log(
        entity_type="manual_action",
        entity_id=payload["source_key"],
        from_status=prev,
        to_status="collected",
        run_id=payload["run_id"],
        reason=f"manual_reinject:{reason}",
        actor=actor,
    )
    return payload


def detect_removed(
    payload: dict[str, Any], services: Services, store: JSONStore, logger: EventLogger
) -> dict[str, Any]:
    """Daily health-check: mark REMOVED when YouTube side returns 410/403."""
    if services.youtube_video_alive(payload["video_id"]):
        return payload
    store.mark_removed(payload["source_key"])
    payload["archive_state"] = "REMOVED"
    payload["failure_reason_code"] = "YT_VIDEO_REMOVED"
    logger.log(
        entity_type="record",
        entity_id=payload["source_key"],
        from_status=payload.get("record_status"),
        to_status=payload.get("record_status"),
        run_id=payload["run_id"],
        reason="yt_video_removed",
    )
    return payload


def mark_dmca_takedown(
    source_key: str,
    *,
    store: JSONStore,
    logger: EventLogger,
    reason: str,
    actor: str = "user:legal",
) -> dict[str, Any]:
    """Legal takedown path (Appendix D).

    Immediately moves the record to REMOVED + DMCA_TAKEDOWN, preserving JSON
    for audit but ensuring downstream Package step skips it forever.
    """
    current = store.get(source_key)
    if current is None:
        raise ValueError(f"{source_key}: not in store")
    store.mark_removed(source_key)
    current["archive_state"] = "REMOVED"
    current["failure_reason_code"] = "DMCA_TAKEDOWN"
    current["failure_reason_detail"] = reason
    store.upsert(current)
    logger.log(
        entity_type="manual_action",
        entity_id=source_key,
        from_status=current.get("record_status"),
        to_status=current.get("record_status"),
        run_id=current.get("run_id", ""),
        reason=f"dmca_takedown:{reason}",
        actor=actor,
    )
    return current
