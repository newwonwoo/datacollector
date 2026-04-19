"""Tests for Human Review CLI, Dashboard, and Quota monitor."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.cli.review import apply_review_decision, review_queue
from collector.cli.dashboard import build_index, build_dashboard
from collector.cli.quota import snapshot_quota, read_local_usage
from collector.events import EventLogger


def _mk_payload(tmp_path: Path, source_key: str = "youtube:VID", record_status: str = "reviewed_unverified") -> Path:
    p = tmp_path / f"{source_key.replace(':', '__')}.json"
    p.write_text(json.dumps({
        "schema_version": "10.0.0",
        "source_key": source_key,
        "video_id": source_key.split(":")[1],
        "title": "title",
        "record_status": record_status,
        "archive_state": "ACTIVE",
        "confidence": "unverified",
        "reviewer": "auto",
        "run_id": "run_x",
        "transcript_hash": "h",
        "payload_version": 1,
        "failure_reason_code": None,
        "llm_context": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
        "collected_at": "2026-04-19T00:00:00Z",
        "summary": "s",
        "rules": ["r"],
        "tags": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ----- Review CLI -----

def test_review_queue_iterates_sorted(tmp_path):
    q = tmp_path / "review_queue"
    q.mkdir()
    _mk_payload(q, "youtube:A")
    _mk_payload(q, "youtube:B")
    names = [p.name for p, _ in review_queue(q)]
    assert names == sorted(names)


def test_review_approve_moves_to_data_store(tmp_path):
    q = tmp_path / "review_queue"
    q.mkdir()
    ds = tmp_path / "data_store"
    rej = tmp_path / "rejected"
    path = _mk_payload(q, "youtube:VID")
    logger = EventLogger()
    out = apply_review_decision(
        path, "approve", data_store_root=ds, rejected_root=rej,
        reviewer="alice", logger=logger,
    )
    assert out["record_status"] == "promoted"
    assert out["confidence"] == "confirmed"
    assert out["reviewer"] == "alice"
    assert not path.exists()
    assert (ds / path.name).exists()
    assert any(e["entity_type"] == "manual_action" and "approve" in e["reason"] for e in logger.events)


def test_review_reject_moves_to_rejected(tmp_path):
    q = tmp_path / "review_queue"
    q.mkdir()
    ds = tmp_path / "data_store"
    rej = tmp_path / "rejected"
    path = _mk_payload(q, "youtube:BAD")
    out = apply_review_decision(path, "reject", data_store_root=ds, rejected_root=rej)
    assert out["record_status"] == "reviewed_rejected"
    assert out["confidence"] == "rejected"
    assert (rej / path.name).exists()


def test_review_skip_is_noop(tmp_path):
    q = tmp_path / "review_queue"
    q.mkdir()
    ds = tmp_path / "data_store"
    rej = tmp_path / "rejected"
    path = _mk_payload(q, "youtube:SKIP")
    out = apply_review_decision(path, "skip", data_store_root=ds, rejected_root=rej)
    assert path.exists()
    assert out["record_status"] == "reviewed_unverified"


def test_review_rejects_unknown_decision(tmp_path):
    q = tmp_path / "review_queue"
    q.mkdir()
    path = _mk_payload(q)
    with pytest.raises(ValueError):
        apply_review_decision(path, "maybe", data_store_root=tmp_path / "ds", rejected_root=tmp_path / "r")


# ----- Dashboard -----

def test_build_index_and_dashboard(tmp_path):
    ds = tmp_path / "data_store"
    ds.mkdir()
    # 3 records: 2 promoted, 1 invalid
    (ds / "a.json").write_text(json.dumps({
        "source_key": "youtube:A", "video_id": "A", "title": "t",
        "record_status": "promoted", "archive_state": "ACTIVE",
        "confidence": "confirmed", "reviewer": "auto",
        "transcript_hash": "h1", "payload_version": 1,
        "failure_reason_code": None,
        "llm_context": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.002},
        "collected_at": "2026-04-19T00:00:01Z",
    }), encoding="utf-8")
    (ds / "b.json").write_text(json.dumps({
        "source_key": "youtube:B", "video_id": "B", "title": "t",
        "record_status": "promoted", "archive_state": "ACTIVE",
        "confidence": "confirmed", "reviewer": "auto",
        "transcript_hash": "h2", "payload_version": 1,
        "failure_reason_code": None,
        "llm_context": {"input_tokens": 8, "output_tokens": 3, "cost_usd": 0.001},
        "collected_at": "2026-04-19T00:00:02Z",
    }), encoding="utf-8")
    (ds / "c.json").write_text(json.dumps({
        "source_key": "youtube:C", "video_id": "C", "title": "t",
        "record_status": "invalid", "archive_state": "ACTIVE",
        "confidence": "unverified", "reviewer": "auto",
        "transcript_hash": "h3", "payload_version": 1,
        "failure_reason_code": "GIT_CONFLICT",
        "llm_context": {"input_tokens": 5, "output_tokens": 2, "cost_usd": 0.0005},
        "collected_at": "2026-04-19T00:00:03Z",
    }), encoding="utf-8")
    db = tmp_path / "idx.sqlite"
    html = tmp_path / "out.html"
    n = build_index(ds, db)
    assert n == 3
    out = build_dashboard(db, html)
    body = out.read_text(encoding="utf-8")
    assert "total records" in body
    assert "promoted" in body and "invalid" in body
    assert "GIT_CONFLICT" in body


# ----- Quota -----

def test_quota_snapshot_reads_jsonl(tmp_path):
    path = tmp_path / "quota.jsonl"
    path.write_text("\n".join([
        json.dumps({"actions_minutes": 800}),
        json.dumps({"actions_minutes": 900, "llm_cost_usd": 0.5, "youtube_units": 2000}),
    ]), encoding="utf-8")
    totals = read_local_usage(path)
    assert totals["actions_minutes"] == 1700
    assert totals["llm_cost_usd"] == 0.5
    assert totals["youtube_units"] == 2000


def test_quota_snapshot_flags_alerts(tmp_path):
    path = tmp_path / "quota.jsonl"
    path.write_text(json.dumps({"actions_minutes": 1700, "llm_cost_usd": 1.1, "youtube_units": 9000}) + "\n", encoding="utf-8")
    snap = snapshot_quota(path, free_minutes=2000, youtube_daily_limit=10000, llm_daily_budget_usd=1.0)
    assert snap["actions_alert"] is True   # 0.85 >= 0.80
    assert snap["llm_alert"] is True       # cost exceeded budget
    assert snap["youtube_alert"] is True   # 0.90 >= 0.80
    assert snap["kill_switch_recommended"] is True   # llm >= 1.0


def test_quota_snapshot_handles_missing_file(tmp_path):
    missing = tmp_path / "nope.jsonl"
    snap = snapshot_quota(missing)
    assert snap["actions_minutes_used"] == 0
    assert snap["kill_switch_recommended"] is False
