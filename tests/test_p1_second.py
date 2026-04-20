"""Tests for the second P1 round: circuit breaker, review queue routing, DLQ replayer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.circuit_breaker import (
    CircuitOpen, check, open_until, record_failure, record_success,
)
from collector.dlq_replayer import MAX_RETRIES_BEFORE_HUMAN, replay_all
from collector.events import EventLogger
from collector.payload import new_payload
from collector.pipeline import run_pipeline
from collector.services import MockError, build_mock_services
from collector.store import JSONStore


_LONG_SUMMARY = (
    "단타 매매 전략 요약입니다. 장중 고점 돌파 시 분할 진입하고 "
    "거래량·저항선을 반드시 확인합니다. 손절선은 직전 저점, 익절은 분할로 수행합니다."
)


# ============== P1-α Circuit Breaker ==============

def test_breaker_trips_after_threshold(tmp_path):
    import time
    now = time.time()
    for i in range(2):
        tripped = record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now + i)
        assert tripped is False
    tripped = record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now + 2)
    assert tripped is True
    assert open_until("youtube_api", root=tmp_path) > now


def test_breaker_check_raises_when_open(tmp_path):
    import time
    now = time.time()
    for i in range(3):
        record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now + i)
    with pytest.raises(CircuitOpen):
        check("youtube_api", root=tmp_path)


def test_breaker_success_resets_window(tmp_path):
    import time
    now = time.time()
    record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now)
    record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now + 1)
    record_success("youtube_api", root=tmp_path)
    tripped = record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now + 2)
    assert tripped is False  # window reset


def test_breaker_old_failures_expire(tmp_path):
    import time
    now = time.time()
    # Two failures at the edge of the 5-min window
    record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now - 400)
    record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now - 350)
    tripped = record_failure("youtube_api", "HTTP_429", root=tmp_path, now=now)
    assert tripped is False  # first two are outside 300s window


def test_breaker_unknown_code_noop(tmp_path):
    assert record_failure("youtube_api", "TOTALLY_UNKNOWN", root=tmp_path) is False


# ============== P1-β Review Queue Auto-Routing ==============

def test_review_queue_receives_inferred(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    queue = tmp_path / "rq"
    services = build_mock_services(
        captions_map={"RQ0000001": {"source": "asr", "text": "자막 내용"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["규칙1"], "tags": ["t"]}],
        similarity=0.55,  # inferred band (0.50–0.60)
    )
    p = new_payload(video_id="RQ0000001", run_id="rq1")
    run_pipeline(p, services, store, logger, use_lock=False,
                 vault_root=tmp_path / "v", review_queue_root=queue)
    assert p["record_status"] == "reviewed_inferred"
    files = list(queue.glob("*.json"))
    assert len(files) == 1
    loaded = json.loads(files[0].read_text(encoding="utf-8"))
    assert loaded["source_key"] == "youtube:RQ0000001"


def test_review_queue_receives_unverified(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    queue = tmp_path / "rq"
    services = build_mock_services(
        captions_map={"RQ0000002": {"source": "asr", "text": "text"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["r"], "tags": []}],
        similarity=0.20,  # unverified
    )
    p = new_payload(video_id="RQ0000002", run_id="rq2")
    run_pipeline(p, services, store, logger, use_lock=False,
                 vault_root=tmp_path / "v", review_queue_root=queue)
    assert p["record_status"] == "reviewed_unverified"
    assert len(list(queue.glob("*.json"))) == 1


def test_review_queue_skips_promoted(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    queue = tmp_path / "rq"
    services = build_mock_services(
        captions_map={"RQ0000003": {"source": "manual", "text": "t"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["r"], "tags": ["x"]}],
        similarity=0.80,  # confirmed → promoted
    )
    p = new_payload(video_id="RQ0000003", run_id="rq3")
    run_pipeline(p, services, store, logger, use_lock=False,
                 vault_root=tmp_path / "v", review_queue_root=queue)
    assert p["record_status"] == "promoted"
    assert list(queue.glob("*.json")) == []  # promoted doesn't route here


# ============== P1-γ DLQ Replayer ==============

def _put_dlq(dlq_root: Path, source_key: str, retry_count: int = 0, code: str = "GIT_CONFLICT") -> Path:
    path = dlq_root / code / "20260420" / (source_key.replace(":", "__") + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"code": code, "payload": {
        "source_key": source_key,
        "video_id": source_key.split(":")[1],
        "record_status": "invalid",
        "retry_count": retry_count,
        "reviewer": "auto",
    }}
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_dlq_replayer_removes_recovered(tmp_path):
    dlq = tmp_path / "dlq"
    p = _put_dlq(dlq, "youtube:REC1")
    result = replay_all(dlq, retry_fn=lambda _pl: True,
                       review_queue_root=tmp_path / "rq")
    assert result.scanned == 1
    assert result.recovered == 1
    assert not p.exists()


def test_dlq_replayer_increments_retry_on_failure(tmp_path):
    dlq = tmp_path / "dlq"
    p = _put_dlq(dlq, "youtube:FAIL1", retry_count=1)
    result = replay_all(dlq, retry_fn=lambda _pl: False,
                       review_queue_root=tmp_path / "rq")
    assert result.still_failing == 1
    entry = json.loads(p.read_text(encoding="utf-8"))
    assert entry["payload"]["retry_count"] == 2


def test_dlq_replayer_promotes_to_human_after_max_retries(tmp_path):
    dlq = tmp_path / "dlq"
    queue = tmp_path / "rq"
    p = _put_dlq(dlq, "youtube:ESC1", retry_count=MAX_RETRIES_BEFORE_HUMAN - 1)
    result = replay_all(dlq, retry_fn=lambda _pl: False, review_queue_root=queue)
    assert result.routed_to_review == 1
    assert not p.exists()
    routed = list(queue.glob("*.json"))
    assert len(routed) == 1
    body = json.loads(routed[0].read_text(encoding="utf-8"))
    assert body["reviewer"] == "human"
    assert body["retry_count"] == MAX_RETRIES_BEFORE_HUMAN
