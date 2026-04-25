"""Seven pipeline stages (Master_02, Master_03)."""
from __future__ import annotations

import time
from typing import Any

from .chunking import MAX_CHARS_SINGLE, chunk, reduce_outputs, should_chunk
from .clickbait import is_clickbait
from .events import EventLogger
from .hashing import transcript_hash
from .payload import snapshot_for_history, utcnow_iso
from .services import MockError, Services
from .store import JSONStore

# Soft filter thresholds (Master_02 §2A)
SHORT_MIN_SEC = 240           # < 4 min → hard drop
STREAM_LONG_MAX_SEC = 7200    # > 2 h → hard drop
LONG_PENALTY_SEC = 5400       # > 90 min → priority penalty flag


class StageFail(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _set_stage(payload: dict, stage: str, state: str, logger: EventLogger, reason: str = "") -> None:
    prev = payload["stage_status"].get(stage)
    payload["stage_status"][stage] = state
    logger.log(
        entity_type="stage",
        entity_id=f"{payload['source_key']}:{stage}",
        from_status=prev,
        to_status=state,
        run_id=payload["run_id"],
        reason=reason,
    )


def _set_record(payload: dict, state: str, logger: EventLogger, reason: str = "", metrics: dict | None = None) -> None:
    prev = payload.get("record_status")
    payload["record_status"] = state
    logger.log(
        entity_type="record",
        entity_id=payload["source_key"],
        from_status=prev,
        to_status=state,
        run_id=payload["run_id"],
        reason=reason,
        metrics=metrics or {},
    )


def _fail(payload: dict, stage: str, err: MockError | StageFail, logger: EventLogger) -> None:
    payload["failure_reason_code"] = err.code
    payload["failure_reason_detail"] = err.detail
    # Include detail in the stage event so status_cli/dashboard can surface it.
    reason = err.code
    prev = payload["stage_status"].get(stage)
    payload["stage_status"][stage] = "failed"
    logger.log(
        entity_type="stage",
        entity_id=f"{payload['source_key']}:{stage}",
        from_status=prev,
        to_status="failed",
        run_id=payload["run_id"],
        reason=reason,
        metrics={"detail": err.detail or ""},
    )


# ---------- Stages ----------

def stage_discover(payload: dict, services: Services, logger: EventLogger, *, fast_track: bool = False) -> dict:
    _set_stage(payload, "discover", "started", logger)
    if fast_track:
        _set_stage(payload, "discover", "skipped", logger, reason="fast_track")
        return payload
    # Pretend the search happened (we got video_id externally in tests)
    _set_stage(payload, "discover", "completed", logger)
    return payload


def stage_collect(payload: dict, services: Services, logger: EventLogger) -> dict:
    _set_stage(payload, "collect", "started", logger)

    # P1-b: Soft filter by duration (hard drop at extremes, priority flag for long)
    duration = payload.get("duration_sec")
    if isinstance(duration, (int, float)) and duration > 0:
        if duration < SHORT_MIN_SEC:
            err = StageFail("YT_SHORTS_DROP", f"duration {duration}s < 4min")
            _fail(payload, "collect", err, logger)
            raise err
        if duration > STREAM_LONG_MAX_SEC:
            err = StageFail("YT_STREAM_DROP", f"duration {duration}s > 2h")
            _fail(payload, "collect", err, logger)
            raise err
        if duration >= LONG_PENALTY_SEC:
            payload["_flag_long"] = True

    try:
        result = services.youtube_captions(payload["video_id"])
    except MockError as e:
        _fail(payload, "collect", e, logger)
        raise StageFail(e.code, e.detail)
    source = result.get("source", "none")
    text = result.get("text", "")
    if source == "none" or not text:
        detail = result.get("error") or "no captions from any source"
        err = StageFail("YT_NO_TRANSCRIPT", detail)
        _fail(payload, "collect", err, logger)
        raise err
    payload["caption_source"] = source
    payload["transcript"] = text
    payload["transcript_hash"] = transcript_hash(text)

    # P4-2: flag clickbait candidates (title vs transcript noun overlap)
    if is_clickbait(payload.get("title", ""), text):
        payload["_flag_clickbait"] = True

    _set_stage(payload, "collect", "completed", logger)
    _set_record(payload, "collected", logger)
    return payload


def _call_llm_once(payload: dict, services: Services, text: str, attempt: int) -> dict:
    out = services.llm_extract(text, attempt)
    if not isinstance(out, dict) or "summary" not in out or "rules" not in out:
        raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", "missing keys")
    return out


def stage_extract(payload: dict, services: Services, logger: EventLogger) -> dict:
    _set_stage(payload, "extract", "started", logger)
    transcript = payload["transcript"]

    # P4-1: long-transcript map-reduce
    chunks = chunk(transcript) if should_chunk(transcript) else [transcript]
    reason_suffix = f"chunks_{len(chunks)}" if len(chunks) > 1 else "single"

    attempt = 0
    last_err: Exception | None = None
    while attempt < 2:  # at most one reprompt per attempt level
        try:
            if len(chunks) == 1:
                out = _call_llm_once(payload, services, chunks[0], attempt)
            else:
                chunk_outs = [_call_llm_once(payload, services, c, attempt) for c in chunks]
                out = reduce_outputs(chunk_outs)
            payload["summary"] = out.get("summary", "")
            payload["rules"] = list(out.get("rules", []))
            payload["tags"] = list(out.get("tags", []))[:5]
            payload["notes_md"] = out.get("notes_md", "")
            payload["llm_context"]["input_tokens"] = len(transcript)
            payload["llm_context"]["output_tokens"] = len(payload["summary"]) + sum(
                len(r) for r in payload["rules"]
            )
            payload["llm_context"]["cost_usd"] = 0.0001 * payload["llm_context"]["input_tokens"]
            _set_stage(
                payload, "extract", "completed", logger,
                reason=f"attempt_{attempt}:{reason_suffix}",
            )
            _set_record(payload, "extracted", logger)
            return payload
        except MockError as e:
            last_err = e
            attempt += 1
    err = StageFail(getattr(last_err, "code", "SEMANTIC_JSON_SCHEMA_FAIL"), str(last_err))
    _fail(payload, "extract", err, logger)
    # P1-a: quarantine on terminal semantic failure
    _set_record(payload, "invalid", logger, reason=err.code)
    raise err


def stage_normalize(payload: dict, services: Services, logger: EventLogger) -> dict:
    _set_stage(payload, "normalize", "started", logger)
    rules = payload.get("rules") or []
    if not rules:
        err = StageFail("SEMANTIC_EMPTY_RULES", "no rules")
        _fail(payload, "normalize", err, logger)
        _set_record(payload, "invalid", logger, reason=err.code)
        raise err
    summary = payload.get("summary") or ""
    # P2-c: summary length check (50~300 chars)
    if len(summary) < 50 or len(summary) > 300:
        err = StageFail("SEMANTIC_SUMMARY_LENGTH", f"len={len(summary)}")
        _fail(payload, "normalize", err, logger)
        _set_record(payload, "invalid", logger, reason=err.code)
        raise err
    forbidden = ["이 영상은", "전반적으로"]
    if any(w in summary for w in forbidden):
        err = StageFail("SEMANTIC_FORBIDDEN_WORD", "forbidden")
        _fail(payload, "normalize", err, logger)
        _set_record(payload, "invalid", logger, reason=err.code)
        raise err
    payload["tags"] = [t.lower().replace(" ", "_") for t in payload.get("tags", [])][:5]
    _set_stage(payload, "normalize", "completed", logger)
    _set_record(payload, "normalized", logger)
    return payload


def stage_review(payload: dict, services: Services, logger: EventLogger) -> dict:
    _set_stage(payload, "review", "started", logger)
    cos = services.semantic_similarity(payload.get("transcript", ""), payload.get("summary", ""))
    rules = payload.get("rules") or []
    if cos >= 0.60 and len(rules) >= 1 and payload.get("retry_count", 0) <= 1:
        payload["confidence"] = "confirmed"
        payload["reviewer"] = "auto"
        _set_record(payload, "reviewed_confirmed", logger, metrics={"cosine": cos, "rules": len(rules)})
    elif cos >= 0.50 and len(rules) >= 1:
        payload["confidence"] = "inferred"
        payload["reviewer"] = "auto"
        _set_record(payload, "reviewed_inferred", logger, metrics={"cosine": cos})
    else:
        payload["confidence"] = "unverified"
        payload["reviewer"] = "auto"
        _set_record(payload, "reviewed_unverified", logger, metrics={"cosine": cos})
    _set_stage(payload, "review", "completed", logger)
    return payload


def stage_promote(payload: dict, services: Services, logger: EventLogger, store: JSONStore) -> dict:
    _set_stage(payload, "promote", "started", logger)
    if payload.get("record_status") != "reviewed_confirmed":
        _set_stage(payload, "promote", "skipped", logger, reason="not_confirmed")
        raise StageFail("SYS_PROMOTE_BLOCKED", "not confirmed")
    _set_record(payload, "promoted", logger)
    _set_stage(payload, "promote", "completed", logger)
    store.upsert(payload)
    return payload


def stage_package(
    payload: dict,
    services: Services,
    logger: EventLogger,
    *,
    max_retries: int = 5,
    backoff_base: float = 2.0,
) -> dict:
    """Package stage with exponential backoff (P2-a).

    Backoff schedule: 2, 4, 8, 16, 32 seconds between attempts.
    `time.sleep` is looked up at call time so tests can monkeypatch it.
    """
    _set_stage(payload, "package", "started", logger)
    attempt = 0
    last_err: MockError | None = None
    while attempt <= max_retries:
        try:
            services.git_sync(payload)
            _set_stage(payload, "package", "completed", logger, reason=f"attempt_{attempt}")
            return payload
        except MockError as e:
            last_err = e
            attempt += 1
            if attempt <= max_retries:
                delay = backoff_base ** attempt
                time.sleep(delay)  # runtime lookup → patchable in tests
    # exhausted
    err = StageFail(last_err.code if last_err else "GIT_CONFLICT", last_err.detail if last_err else "")
    _fail(payload, "package", err, logger)
    _set_record(payload, "invalid", logger, reason=err.code)
    raise err
