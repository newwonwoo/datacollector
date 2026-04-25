"""Tests for P0 (kill switch, lockfile, DLQ persist, atomic), P1 (invalid
quarantine, soft filter, priority+aging, query builder), P2 (exp backoff).
"""
from __future__ import annotations

import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from collector.events import EventLogger
from collector.hashing import transcript_hash
from collector.killswitch import PAUSE_ENV, is_paused
from collector.locks import LEASE_SECONDS, STALE_SECONDS, acquire, release, heartbeat
from collector.payload import new_payload
from collector.pipeline import run_pipeline
from collector.priority import (
    BASE, BONUS_TARGET_CHANNEL, BONUS_RECENT_7D, AGING_MAX,
    compute_priority, sort_queue,
)
from collector.query import build_query, fallback_query, QueryObject
from collector.services import MockError, build_mock_services
from collector.store import JSONStore


_LONG_SUMMARY = (
    "단타 매매 전략 요약. 장중 고점 돌파 시 분할 진입하고 거래량·저항선을 "
    "필수적으로 확인하며, 손절은 직전 저점에 두고 익절은 분할로 수행한다."
)


def _payload(vid="VID00000001", **kw):
    return new_payload(video_id=vid, run_id=f"run_{vid}", **kw)


# ============== P0-a Kill Switch ==============

def test_kill_switch_preflight_skips_all_stages(tmp_path, monkeypatch):
    monkeypatch.setenv(PAUSE_ENV, "1")
    assert is_paused() is True
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload()
    run_pipeline(p, build_mock_services(), store, logger, use_lock=False)
    assert p["failure_reason_code"] == "SYS_KILL_SWITCH"
    assert all(v == "skipped" for v in p["stage_status"].values())


def test_kill_switch_recognizes_truthy_values(monkeypatch):
    for val in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(PAUSE_ENV, val)
        assert is_paused() is True
    for val in ("0", "false", "", "no"):
        monkeypatch.setenv(PAUSE_ENV, val)
        assert is_paused() is False


# ============== P0-b Lockfile ==============

def test_lock_acquire_release_roundtrip(tmp_path):
    lock = acquire("youtube:V1", root=tmp_path)
    assert lock is not None
    assert lock.path.exists()
    release(lock)
    assert not lock.path.exists()


def test_lock_blocks_second_acquire(tmp_path):
    a = acquire("youtube:V1", root=tmp_path, owner="w1")
    b = acquire("youtube:V1", root=tmp_path, owner="w2")
    assert a is not None and b is None
    release(a)


def test_lock_steals_after_stale_heartbeat(tmp_path, monkeypatch):
    a = acquire("youtube:V1", root=tmp_path, owner="w1")
    assert a is not None
    # Age the heartbeat beyond STALE_SECONDS
    stale_time = _time.time() - STALE_SECONDS - 1
    data = json.loads(a.path.read_text())
    data["heartbeat_at"] = stale_time
    a.path.write_text(json.dumps(data))

    b = acquire("youtube:V1", root=tmp_path, owner="w2")
    assert b is not None, "should steal after stale heartbeat"


def test_pipeline_refuses_when_locked(tmp_path, monkeypatch):
    import collector.pipeline as pp
    lock_dir = tmp_path / "locks-test"
    lock_dir.mkdir()
    orig = pp.acquire
    monkeypatch.setattr(pp, "acquire", lambda sk, **kw: orig(sk, root=lock_dir))

    # Pre-hold the lock
    held = acquire("youtube:VID00000001", root=lock_dir, owner="other-worker")
    assert held is not None

    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload()
    run_pipeline(p, build_mock_services(), store, logger)
    assert p["failure_reason_code"] == "SYS_LOCK_HELD"


# ============== P0-c DLQ persistence ==============

def test_dlq_persists_to_filesystem(tmp_path, monkeypatch):
    # Skip real backoff waits
    import collector.stages as stg
    monkeypatch.setattr(stg.time, "sleep", lambda _s: None)

    store = JSONStore(root=tmp_path / "ds")
    assert store.dlq_root is not None
    logger = EventLogger()
    p = _payload("DLQ00000001")
    services = build_mock_services(
        captions_map={"DLQ00000001": {"source": "manual", "text": "text"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["r"], "tags": ["t"]}],
        similarity=0.8,
        git_script=[MockError("GIT_CONFLICT", "conflict")] * 10,
    )
    run_pipeline(p, services, store, logger, use_lock=False)

    assert p["record_status"] == "invalid"
    assert len(store.dlq) == 1

    # File on disk under dlq/GIT_CONFLICT/<YYYYMMDD>/
    files = list((tmp_path / "dlq").rglob("*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert entry["code"] == "GIT_CONFLICT"
    assert entry["payload"]["source_key"] == "youtube:DLQ00000001"


# ============== P0-d Atomic write ==============

def test_store_atomic_write_no_partial_file(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    p = _payload("ATOMIC00001")
    p["collected_at"] = "2026-04-19T00:00:00Z"
    store.upsert(p)
    out_files = list((tmp_path / "ds").rglob("*.json"))
    # No leftover temp file
    temp_files = list((tmp_path / "ds").rglob(".tmp.*"))
    assert temp_files == []
    # Single valid JSON
    assert len(out_files) == 1
    json.loads(out_files[0].read_text(encoding="utf-8"))


# ============== P1-a any -> invalid ==============

def test_extract_failure_quarantines_as_invalid(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("EXTFAIL0001")
    services = build_mock_services(
        captions_map={"EXTFAIL0001": {"source": "manual", "text": "text"}},
        llm_script=[
            MockError("SEMANTIC_JSON_SCHEMA_FAIL", "bad"),
            MockError("SEMANTIC_JSON_SCHEMA_FAIL", "still bad"),
        ],
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["record_status"] == "invalid"
    assert p["failure_reason_code"] == "SEMANTIC_JSON_SCHEMA_FAIL"


def test_normalize_failure_quarantines_as_invalid(tmp_path):
    """Empty rules + empty notes_md + a too-short summary → invalid.

    The original gate was 'rules empty == invalid'; we relaxed it for the
    knowledge-library case where a video legitimately has no actionable
    rules but rich notes_md. This test now exercises the harder failure
    where there's nothing to archive at all.
    """
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("NORMFAIL001")
    services = build_mock_services(
        captions_map={"NORMFAIL001": {"source": "manual", "text": "text"}},
        # No rules, no notes, summary too short to carry information →
        # SEMANTIC_EMPTY_RULES quarantine.
        llm_script=[{"summary": "짧음", "rules": [], "tags": [], "notes_md": ""}],
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["record_status"] == "invalid"
    assert p["failure_reason_code"] == "SEMANTIC_EMPTY_RULES"


def test_normalize_passes_when_rules_empty_but_notes_rich(tmp_path):
    """Knowledge-library case: empty rules but substantial notes_md → pass."""
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("NORMPASS001")
    services = build_mock_services(
        captions_map={"NORMPASS001": {"source": "manual", "text": "text"}},
        llm_script=[{
            "summary": _LONG_SUMMARY,
            "rules": [],
            "tags": ["t1"],
            "notes_md": "## 핵심\n" + ("자세한 마크다운 노트 본문 " * 20),
        }],
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    # Either reviewed_* (mock similarity ≥ 0.5) or normalized — anything
    # but invalid means the stage didn't quarantine on empty rules.
    assert p["record_status"] != "invalid"
    assert p.get("notes_md", "").startswith("## 핵심")


# ============== P1-b Soft filter ==============

def test_soft_filter_drops_shorts(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("SHORT000001")
    p["duration_sec"] = 180  # 3 min
    services = build_mock_services(
        captions_map={"SHORT000001": {"source": "manual", "text": "text"}}
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["failure_reason_code"] == "YT_SHORTS_DROP"


def test_soft_filter_drops_streams(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("STREAM00001")
    p["duration_sec"] = 7300  # > 2h
    services = build_mock_services(
        captions_map={"STREAM00001": {"source": "manual", "text": "t"}}
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["failure_reason_code"] == "YT_STREAM_DROP"


def test_soft_filter_flags_long(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("LONG0000001")
    p["duration_sec"] = 5500  # > 90 min
    services = build_mock_services(
        captions_map={"LONG0000001": {"source": "manual", "text": "text"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["r"], "tags": ["t"]}],
        similarity=0.75,
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p.get("_flag_long") is True


# ============== P1-c Priority + Aging ==============

def test_priority_base_is_100():
    p = new_payload(video_id="X", run_id="r")
    p["published_at"] = ""
    p["collected_at"] = ""
    assert compute_priority(p, now=datetime(2000, 1, 1, tzinfo=timezone.utc)) == BASE


def test_priority_target_channel_bonus():
    p = new_payload(video_id="X", run_id="r", channel_id="CH_STAR")
    p["published_at"] = ""
    score = compute_priority(p, target_channel_ids={"CH_STAR"},
                             now=datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert score == BASE + BONUS_TARGET_CHANNEL


def test_priority_recent_7d_bonus():
    now = datetime(2026, 4, 20, tzinfo=timezone.utc)
    p = new_payload(video_id="X", run_id="r")
    p["published_at"] = (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    score = compute_priority(p, now=now)
    assert score == BASE + BONUS_RECENT_7D


def test_priority_aging_capped():
    now = datetime(2026, 4, 20, tzinfo=timezone.utc)
    p = new_payload(video_id="X", run_id="r")
    p["record_status"] = "collected"
    p["collected_at"] = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    p["published_at"] = ""
    score = compute_priority(p, now=now)
    assert score == BASE + AGING_MAX


def test_priority_cost_guard_zeroes_non_target():
    p = new_payload(video_id="X", run_id="r", channel_id="CH_REG")
    assert compute_priority(p, cost_guard_active=True) == 0
    # target channel bypasses
    assert compute_priority(p, target_channel_ids={"CH_REG"}, cost_guard_active=True) > 0


def test_sort_queue_puts_target_first():
    ps = [
        new_payload(video_id="a", run_id="r", channel_id="C_A"),
        new_payload(video_id="b", run_id="r", channel_id="C_TARGET"),
        new_payload(video_id="c", run_id="r", channel_id="C_A"),
    ]
    out = sort_queue(ps, target_channel_ids={"C_TARGET"})
    assert out[0]["video_id"] == "b"


# ============== P1-d Query Builder ==============

def test_build_query_expands_synonyms():
    q = build_query("단타 매매법")
    assert "단타" in q.topic
    assert "스캘핑" in q.synonyms
    assert q.target_channel_id is None


def test_build_query_default_excludes():
    q = build_query("무슨 쿼리든")
    assert "코인" in q.exclude_terms
    assert "리딩방" in q.exclude_terms


def test_build_query_target_channel():
    q = build_query("X", target_channel_id="UC_abc")
    assert q.target_channel_id == "UC_abc"


def test_fallback_query_has_no_synonyms():
    q = fallback_query("raw")
    assert q.synonyms == []
    assert q.period == "this_month"


# ============== P2-a Exponential backoff ==============

def test_exp_backoff_calls_sleep_between_attempts(tmp_path, monkeypatch):
    import collector.stages as stg
    sleeps: list[float] = []
    monkeypatch.setattr(stg.time, "sleep", lambda s: sleeps.append(s))

    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("BKOFF000001")
    services = build_mock_services(
        captions_map={"BKOFF000001": {"source": "manual", "text": "text"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["r"], "tags": ["t"]}],
        similarity=0.8,
        git_script=[MockError("GIT_CONFLICT", "c")] * 10,
    )
    run_pipeline(p, services, store, logger, use_lock=False)

    # 5 retry intervals between 6 attempts (attempt 0..5)
    assert len(sleeps) == 5
    assert sleeps == [2.0, 4.0, 8.0, 16.0, 32.0]
