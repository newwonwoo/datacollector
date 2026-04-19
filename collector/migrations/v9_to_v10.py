"""Migrate a V9-shape payload to v10.

V9 shape (summary):
  { video_id, channel_id, title, published_at, collected_at, source_query,
    language, transcript_hash, status, archive_state, retry_count,
    priority_score, payload_version, failure_reason_code, failure_reason_detail,
    llm_context, history }

v10 adds:
  - schema_version = "10.0.0"
  - source_key = "youtube:{video_id}"
  - run_id
  - provenance = { source_id, segment_id, run_id }
  - stage_status = { discover, collect, extract, normalize, review, promote, package }
  - record_status (derived from old `status`)
  - caption_source, transcript, confidence, reviewer, summary, rules, tags
  - llm_context.{input_tokens, output_tokens, cost_usd}
"""
from __future__ import annotations

import uuid
from typing import Any

from ..payload import SCHEMA_VERSION, STAGES

_STATUS_TO_RECORD = {
    "PENDING": "collected",
    "COLLECTED": "collected",
    "VALIDATED": "normalized",
    "PROCESSED": "promoted",
    "SYNC_FAILED": "promoted",  # JSON saved, Markdown pending
    "RETRY_WAIT": "collected",
    "FAILED": "invalid",
}

_STATUS_TO_STAGE_STATUS = {
    "PENDING": {},
    "COLLECTED": {"discover": "completed", "collect": "completed"},
    "VALIDATED": {
        "discover": "completed", "collect": "completed",
        "extract": "completed", "normalize": "completed",
    },
    "PROCESSED": {s: "completed" for s in STAGES},
    "SYNC_FAILED": {
        "discover": "completed", "collect": "completed",
        "extract": "completed", "normalize": "completed",
        "review": "completed", "promote": "completed",
        "package": "failed",
    },
    "RETRY_WAIT": {"discover": "completed", "collect": "failed"},
    "FAILED": {},
}


def migrate_v9_to_v10(v9: dict[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
    """Return a new dict in v10 shape. Non-destructive (input not mutated)."""
    run_id = run_id or f"run_migrated_{uuid.uuid4().hex[:8]}"
    video_id = v9["video_id"]
    source_key = f"youtube:{video_id}"

    stage_base = {s: "not_started" for s in STAGES}
    stage_base.update(_STATUS_TO_STAGE_STATUS.get(v9.get("status", "PENDING"), {}))

    llm = dict(v9.get("llm_context") or {})
    llm.setdefault("model_name", "gemini-1.5-flash")
    llm.setdefault("model_version", "001")
    llm.setdefault("temperature", 0.2)
    llm.setdefault("prompt_version", "v1.3_saju")
    llm.setdefault("input_tokens", 0)
    llm.setdefault("output_tokens", 0)
    llm.setdefault("cost_usd", 0.0)

    record_status = _STATUS_TO_RECORD.get(v9.get("status", "PENDING"), "collected")
    confidence = "confirmed" if record_status == "promoted" else "unverified"

    return {
        "schema_version": SCHEMA_VERSION,
        "source_key": source_key,
        "video_id": video_id,
        "channel_id": v9.get("channel_id", ""),
        "title": v9.get("title", ""),
        "published_at": v9.get("published_at", ""),
        "collected_at": v9.get("collected_at", ""),
        "source_query": v9.get("source_query", ""),
        "language": v9.get("language", "ko"),
        "caption_source": "none",
        "transcript": "",
        "transcript_hash": v9.get("transcript_hash", ""),
        "provenance": {
            "source_id": source_key,
            "segment_id": f"{source_key}#full",
            "run_id": run_id,
        },
        "run_id": run_id,
        "stage_status": stage_base,
        "record_status": record_status,
        "archive_state": v9.get("archive_state", "ACTIVE"),
        "retry_count": int(v9.get("retry_count", 0)),
        "priority_score": int(v9.get("priority_score", 100)),
        "payload_version": int(v9.get("payload_version", 1)),
        "failure_reason_code": v9.get("failure_reason_code"),
        "failure_reason_detail": v9.get("failure_reason_detail"),
        "llm_context": llm,
        "confidence": confidence,
        "reviewer": "auto" if record_status == "promoted" else "none",
        "summary": "",
        "rules": [],
        "tags": [],
        "history": list(v9.get("history") or []),
    }
