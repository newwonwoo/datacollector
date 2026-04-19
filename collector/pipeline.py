"""End-to-end orchestrator."""
from __future__ import annotations

from typing import Any

from .events import EventLogger
from .payload import snapshot_for_history
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


def run_pipeline(
    payload: dict[str, Any],
    services: Services,
    store: JSONStore,
    logger: EventLogger,
    *,
    fast_track: bool = False,
) -> dict[str, Any]:
    """Run all seven stages on a single payload.

    Enforces dedup rules A/B/C against `store` before collect.
    Returns the updated payload regardless of stage failure.
    """
    run_id = payload["run_id"]
    logger.log(entity_type="run", entity_id=run_id, from_status=None, to_status="running", run_id=run_id)

    try:
        stage_discover(payload, services, logger, fast_track=fast_track)
        stage_collect(payload, services, logger)
    except StageFail:
        logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="partially_completed", run_id=run_id)
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
        # Collect stage marked completed, but downstream skipped.
        for s in ("extract", "normalize", "review", "promote", "package"):
            payload["stage_status"][s] = "skipped"
        logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="completed", run_id=run_id)
        return payload

    if rule == "B":
        # Capture previous revision into history before reanalysis
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
        stage_normalize(payload, services, logger)
        stage_review(payload, services, logger)
    except StageFail:
        logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="partially_completed", run_id=run_id)
        return payload

    try:
        stage_promote(payload, services, store, logger) if False else stage_promote(payload, services, logger, store)
    except StageFail:
        logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="partially_completed", run_id=run_id)
        return payload

    try:
        stage_package(payload, services, logger)
    except StageFail:
        store.send_to_dlq(payload, payload.get("failure_reason_code") or "GIT_CONFLICT")
        store.upsert(payload)  # persist SYNC_FAILED / invalid state
        logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="partially_completed", run_id=run_id)
        return payload

    store.upsert(payload)  # persist final state including stage_status.package=completed
    logger.log(entity_type="run", entity_id=run_id, from_status="running", to_status="completed", run_id=run_id)
    return payload


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
