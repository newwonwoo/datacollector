"""Tests for status_cli._latest_run_detail — used by dashboard pipeline live view."""
from __future__ import annotations

import json
from pathlib import Path

from collector.cli.status_cli import _latest_run_detail, build_status


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _run_event(run_id: str, to_status: str, ts: str) -> dict:
    return {
        "event_id": f"evt_{run_id}_{to_status}",
        "entity_type": "run", "entity_id": run_id,
        "from_status": None, "to_status": to_status,
        "run_id": run_id, "reason": "", "metrics": {},
        "recorded_at": ts,
    }


def _stage_event(run_id: str, source_key: str, stage: str, to_status: str, ts: str) -> dict:
    return {
        "event_id": f"evt_{run_id}_{source_key}_{stage}_{to_status}",
        "entity_type": "stage",
        "entity_id": f"{source_key}:{stage}",
        "from_status": None, "to_status": to_status,
        "run_id": run_id, "reason": "", "metrics": {},
        "recorded_at": ts,
    }


def test_latest_run_detail_empty_events(tmp_path):
    out = _latest_run_detail(tmp_path / "nope.jsonl")
    assert out["run_id"] is None
    assert out["run_status"] == "unknown"
    assert all(v["status"] == "not_started" for v in out["per_stage"].values())


def test_latest_run_detail_picks_most_recent_run_by_time(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [
        _run_event("run_old", "running",   "2026-04-19T00:00:00Z"),
        _run_event("run_old", "completed", "2026-04-19T00:00:05Z"),
        _run_event("run_new", "running",   "2026-04-19T00:01:00Z"),
        _stage_event("run_new", "youtube:A", "discover", "completed", "2026-04-19T00:01:01Z"),
    ])
    d = _latest_run_detail(events)
    assert d["run_id"] == "run_new"
    assert d["per_stage"]["discover"]["status"] == "completed"
    assert d["per_stage"]["discover"]["count"] == 1


def test_latest_run_detail_reports_all_seven_stages(tmp_path):
    events = tmp_path / "events.jsonl"
    lines = [_run_event("r1", "running", "2026-04-19T00:00:00Z")]
    for st in ("discover","collect","extract","normalize","review","promote","package"):
        lines.append(_stage_event("r1", "youtube:A", st, "completed", f"2026-04-19T00:00:{len(lines):02d}Z"))
    lines.append(_run_event("r1", "completed", "2026-04-19T00:10:00Z"))
    _write_events(events, lines)
    d = _latest_run_detail(events)
    assert d["run_status"] == "completed"
    for st in ("discover","collect","extract","normalize","review","promote","package"):
        assert d["per_stage"][st]["status"] == "completed"
        assert d["per_stage"][st]["count"] == 1


def test_latest_run_detail_marks_failed_stage(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [
        _run_event("r1", "running", "2026-04-19T00:00:00Z"),
        _stage_event("r1", "youtube:A", "collect", "started",   "2026-04-19T00:00:01Z"),
        _stage_event("r1", "youtube:A", "collect", "failed",    "2026-04-19T00:00:02Z"),
        _run_event("r1", "partially_completed", "2026-04-19T00:00:03Z"),
    ])
    d = _latest_run_detail(events)
    assert d["run_status"] == "partially_completed"
    assert d["per_stage"]["collect"]["status"] == "failed"
    assert d["per_stage"]["extract"]["status"] == "not_started"


def test_latest_run_detail_marks_skipped(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [
        _run_event("r1", "running", "2026-04-19T00:00:00Z"),
        _stage_event("r1", "youtube:A", "promote", "skipped", "2026-04-19T00:00:01Z"),
        _run_event("r1", "completed", "2026-04-19T00:00:02Z"),
    ])
    d = _latest_run_detail(events)
    assert d["per_stage"]["promote"]["status"] == "skipped"


def test_latest_run_detail_counts_multiple_records(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [
        _run_event("r1", "running", "2026-04-19T00:00:00Z"),
        _stage_event("r1", "youtube:A", "extract", "completed", "2026-04-19T00:00:01Z"),
        _stage_event("r1", "youtube:B", "extract", "completed", "2026-04-19T00:00:02Z"),
        _stage_event("r1", "youtube:C", "extract", "completed", "2026-04-19T00:00:03Z"),
        _run_event("r1", "completed", "2026-04-19T00:00:04Z"),
    ])
    d = _latest_run_detail(events)
    assert d["per_stage"]["extract"]["count"] == 3


def test_build_status_includes_latest_run_detail(tmp_path):
    events = tmp_path / "events.jsonl"
    _write_events(events, [
        _run_event("r1", "running", "2026-04-19T00:00:00Z"),
        _stage_event("r1", "youtube:A", "discover", "completed", "2026-04-19T00:00:01Z"),
    ])
    s = build_status(
        dlq_root=tmp_path / "dlq",
        review_queue_root=tmp_path / "rq",
        breakers_path=tmp_path / "brk.json",
        quota_usage=tmp_path / "q.jsonl",
        data_store=tmp_path / "ds",
        events=events,
    )
    assert "latest_run_detail" in s
    assert s["latest_run_detail"]["run_id"] == "r1"
    assert s["latest_run_detail"]["per_stage"]["discover"]["status"] == "completed"
