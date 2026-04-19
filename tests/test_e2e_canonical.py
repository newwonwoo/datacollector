"""9 canonical E2E scenarios (Appendix_B). Named, fully asserted."""
from __future__ import annotations

import pytest

from collector.events import EventLogger
from collector.hashing import transcript_hash
from collector.payload import new_payload
from collector.pipeline import detect_removed, manual_reinject, run_pipeline
from collector.services import MockError, build_mock_services
from collector.store import JSONStore


def _run(store: JSONStore, logger: EventLogger, payload: dict, services, **kw):
    return run_pipeline(payload, services, store, logger, **kw)


# SC-01 New collect success
def test_sc01_new_collect_success(store, logger, make_payload_fn):
    payload = make_payload_fn("SC01VIDEOID")
    services = build_mock_services(
        captions_map={"SC01VIDEOID": {"source": "manual", "text": "단타 매매는 장중 고점 돌파 시 진입한다."}},
        llm_script=[{"summary": "장중 고점 돌파 전략 요약.", "rules": ["장중 고점 돌파 시 분할 진입"], "tags": ["단타"]}],
        similarity=0.75,
    )
    _run(store, logger, payload, services)
    assert payload["record_status"] == "promoted"
    assert payload["stage_status"]["package"] == "completed"
    assert payload["confidence"] == "confirmed"
    assert store.get(payload["source_key"]) is not None


# SC-02 Rule C duplicate skip
def test_sc02_rule_c_skip(store, logger, make_payload_fn):
    text = "동일한 자막 텍스트"
    # pre-populate as already stored
    first = make_payload_fn("SC02VIDEOID")
    first["transcript_hash"] = transcript_hash(text)
    first["record_status"] = "promoted"
    store.upsert(first)

    payload = make_payload_fn("SC02VIDEOID")
    services = build_mock_services(captions_map={"SC02VIDEOID": {"source": "manual", "text": text}})
    _run(store, logger, payload, services)
    assert payload["stage_status"]["extract"] == "skipped"
    assert any(e.get("reason") == "rule_c_duplicate" for e in logger.events)


# SC-03 Rule B reprocess (hash changed)
def test_sc03_rule_b_reprocess(store, logger, make_payload_fn):
    first = make_payload_fn("SC03VIDEOID")
    first["transcript_hash"] = transcript_hash("이전 자막")
    first["record_status"] = "promoted"
    first["summary"] = "이전 요약"
    first["rules"] = ["이전규칙"]
    first["payload_version"] = 1
    store.upsert(first)

    payload = make_payload_fn("SC03VIDEOID")
    services = build_mock_services(
        captions_map={"SC03VIDEOID": {"source": "manual", "text": "새로운 자막 본문 차이"}},
        llm_script=[{"summary": "새 요약 본문", "rules": ["새규칙"], "tags": ["t"]}],
        similarity=0.8,
    )
    _run(store, logger, payload, services)
    assert payload["payload_version"] == 2
    assert payload["history"] and payload["history"][0]["prev_summary"] == "이전 요약"
    assert payload["record_status"] == "promoted"


# SC-04 JSON schema fail then success
def test_sc04_reprompt_then_success(store, logger, make_payload_fn):
    payload = make_payload_fn("SC04VIDEOID")
    services = build_mock_services(
        captions_map={"SC04VIDEOID": {"source": "manual", "text": "자막 본문"}},
        llm_script=[
            MockError("SEMANTIC_JSON_SCHEMA_FAIL", "bad json"),
            {"summary": "복구된 요약", "rules": ["복구규칙"], "tags": ["t"]},
        ],
        similarity=0.8,
    )
    _run(store, logger, payload, services)
    assert payload["record_status"] == "promoted"
    assert payload["stage_status"]["extract"] == "completed"


# SC-05 HTTP 429 during collect
def test_sc05_http_429_retry_wait(store, logger, make_payload_fn):
    payload = make_payload_fn("SC05VIDEOID")
    services = build_mock_services(captions_map={"SC05VIDEOID": MockError("HTTP_429", "rate limit")})
    _run(store, logger, payload, services)
    assert payload["stage_status"]["collect"] == "failed"
    assert payload["failure_reason_code"] == "HTTP_429"
    assert payload["record_status"] != "promoted"


# SC-06 Sync fails 5x → invalid + DLQ
def test_sc06_sync_invalid_dlq(store, logger, make_payload_fn):
    payload = make_payload_fn("SC06VIDEOID")
    services = build_mock_services(
        captions_map={"SC06VIDEOID": {"source": "manual", "text": "자막"}},
        llm_script=[{"summary": "요약", "rules": ["r"], "tags": ["t"]}],
        similarity=0.8,
        git_script=[MockError("GIT_CONFLICT", "conflict")] * 6,
    )
    _run(store, logger, payload, services)
    assert payload["stage_status"]["package"] == "failed"
    assert payload["record_status"] == "invalid"
    assert len(store.dlq) == 1
    assert store.dlq[0]["code"] == "GIT_CONFLICT"


# SC-07 Manual reinject from invalid → collected
def test_sc07_manual_reinject(store, logger, make_payload_fn):
    payload = make_payload_fn("SC07VIDEOID")
    payload["record_status"] = "invalid"
    manual_reinject(payload, store, logger, reason="fix_applied", actor="user:alice")
    assert payload["record_status"] == "collected"
    assert any(e["entity_type"] == "manual_action" for e in logger.events)


# SC-08 Archive-aware dedup
def test_sc08_archived_dedup(store, logger, make_payload_fn):
    text = "아카이브 자막"
    archived = make_payload_fn("SC08VIDEOID")
    archived["transcript_hash"] = transcript_hash(text)
    archived["record_status"] = "promoted"
    store.upsert(archived)
    store.archive(archived["source_key"])

    payload = make_payload_fn("SC08VIDEOID")
    services = build_mock_services(captions_map={"SC08VIDEOID": {"source": "manual", "text": text}})
    _run(store, logger, payload, services)
    assert any(e.get("reason") == "rule_c_duplicate" for e in logger.events)


# SC-09 Video removed (410) → archive_state REMOVED
def test_sc09_video_removed(store, logger, make_payload_fn):
    payload = make_payload_fn("SC09VIDEOID")
    payload["record_status"] = "promoted"
    store.upsert(payload)
    services = build_mock_services(alive_map={"SC09VIDEOID": False})
    detect_removed(payload, services, store, logger)
    assert payload["archive_state"] == "REMOVED"
    assert payload["failure_reason_code"] == "YT_VIDEO_REMOVED"
