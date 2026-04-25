"""Payload model (schema_version=10.0.0) as plain dicts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "10.0.0"

STAGES = [
    "discover", "collect", "extract", "normalize",
    "review", "promote", "package",
]

RECORD_STATES = {
    "collected", "extracted", "normalized",
    "reviewed_confirmed", "reviewed_inferred",
    "reviewed_unverified", "reviewed_rejected",
    "promoted", "invalid",
}

CONFIDENCES = {"unverified", "inferred", "confirmed", "rejected"}
ARCHIVE_STATES = {"ACTIVE", "ARCHIVED", "REMOVED"}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_payload(
    video_id: str,
    *,
    run_id: str,
    channel_id: str = "",
    title: str = "",
    published_at: str = "",
    source_query: str = "",
    language: str = "ko",
) -> dict[str, Any]:
    source_key = f"youtube:{video_id}"
    return {
        "schema_version": SCHEMA_VERSION,
        "source_key": source_key,
        "video_id": video_id,
        "channel_id": channel_id,
        "title": title,
        "published_at": published_at,
        "collected_at": utcnow_iso(),
        "source_query": source_query,
        "language": language,
        "caption_source": "none",
        "transcript": "",
        "transcript_hash": "",
        "provenance": {
            "source_id": source_key,
            "segment_id": f"{source_key}#full",
            "run_id": run_id,
        },
        "run_id": run_id,
        "stage_status": {s: "not_started" for s in STAGES},
        "record_status": "collected",
        "archive_state": "ACTIVE",
        "retry_count": 0,
        "priority_score": 100,
        "payload_version": 1,
        "failure_reason_code": None,
        "failure_reason_detail": None,
        "llm_context": {
            "model_name": "mock-llm",
            "model_version": "v0",
            "temperature": 0.2,
            "prompt_version": "v1",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        },
        "confidence": "unverified",
        "reviewer": "none",
        "summary": "",
        "rules": [],
        "tags": [],
        "notes_md": "",
        "history": [],
    }


def snapshot_for_history(payload: dict[str, Any], reason: str, event_id: str) -> dict[str, Any]:
    return {
        "at": utcnow_iso(),
        "event_id": event_id,
        "reason": reason,
        "prev_transcript_hash": payload.get("transcript_hash"),
        "prev_summary": payload.get("summary"),
        "prev_rules_snapshot": list(payload.get("rules", [])),
        "prev_confidence": payload.get("confidence"),
    }
