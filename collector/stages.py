"""Seven pipeline stages (Master_02, Master_03)."""
from __future__ import annotations

from typing import Any

from .events import EventLogger
from .hashing import transcript_hash
from .payload import snapshot_for_history, utcnow_iso
from .services import MockError, Services
from .store import JSONStore


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
    _set_stage(payload, stage, "failed", logger, reason=err.code)


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
    try:
        result = services.youtube_captions(payload["video_id"])
    except MockError as e:
        _fail(payload, "collect", e, logger)
        raise StageFail(e.code, e.detail)
    source = result.get("source", "none")
    text = result.get("text", "")
    if source == "none" or not text:
        err = StageFail("YT_NO_TRANSCRIPT", "no captions")
        _fail(payload, "collect", err, logger)
        raise err
    payload["caption_source"] = source
    payload["transcript"] = text
    payload["transcript_hash"] = transcript_hash(text)
    _set_stage(payload, "collect", "completed", logger)
    _set_record(payload, "collected", logger)
    return payload


def stage_extract(payload: dict, services: Services, logger: EventLogger) -> dict:
    _set_stage(payload, "extract", "started", logger)
    attempt = 0
    last_err: Exception | None = None
    while attempt < 2:  # at most one reprompt
        try:
            out = services.llm_extract(payload["transcript"], attempt)
            if not isinstance(out, dict) or "summary" not in out or "rules" not in out:
                raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", "missing keys")
            payload["summary"] = out.get("summary", "")
            payload["rules"] = list(out.get("rules", []))
            payload["tags"] = list(out.get("tags", []))[:5]
            payload["llm_context"]["input_tokens"] = len(payload["transcript"])
            payload["llm_context"]["output_tokens"] = len(payload["summary"]) + sum(
                len(r) for r in payload["rules"]
            )
            payload["llm_context"]["cost_usd"] = 0.0001 * payload["llm_context"]["input_tokens"]
            _set_stage(payload, "extract", "completed", logger, reason=f"attempt_{attempt}")
            _set_record(payload, "extracted", logger)
            return payload
        except MockError as e:
            last_err = e
            attempt += 1
    err = StageFail(getattr(last_err, "code", "SEMANTIC_JSON_SCHEMA_FAIL"), str(last_err))
    _fail(payload, "extract", err, logger)
    raise err


def stage_normalize(payload: dict, services: Services, logger: EventLogger) -> dict:
    _set_stage(payload, "normalize", "started", logger)
    rules = payload.get("rules") or []
    if not rules:
        err = StageFail("SEMANTIC_EMPTY_RULES", "no rules")
        _fail(payload, "normalize", err, logger)
        raise err
    summary = payload.get("summary") or ""
    forbidden = ["이 영상은", "전반적으로"]
    if any(w in summary for w in forbidden):
        err = StageFail("SEMANTIC_FORBIDDEN_WORD", "forbidden")
        _fail(payload, "normalize", err, logger)
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


def stage_package(payload: dict, services: Services, logger: EventLogger, *, max_retries: int = 5) -> dict:
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
    # exhausted
    err = StageFail(last_err.code if last_err else "GIT_CONFLICT", last_err.detail if last_err else "")
    _fail(payload, "package", err, logger)
    _set_record(payload, "invalid", logger, reason=err.code)
    raise err
