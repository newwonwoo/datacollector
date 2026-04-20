"""Tests for P3: metrics, PII, traces, rollback, alerts, DMCA."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from collector.alerts import Alert, emit_github_issue, evaluate
from collector.events import EventLogger
from collector.metrics import aggregate_daily, write_daily
from collector.payload import new_payload
from collector.pii import mask, mask_payload
from collector.pipeline import mark_dmca_takedown
from collector.rollback import RollbackError, rollback
from collector.store import JSONStore
from collector.traces import build_from_events_file, build_trace


_LONG_SUMMARY = "단타 전략 요약. " * 5


# ============== P3-1 Metrics ==============

def test_metrics_aggregate_empty(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    ds = tmp_path / "ds"
    ds.mkdir()
    rows = aggregate_daily(events, ds)
    assert rows == []


def test_metrics_aggregate_counts_runs_and_costs(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join([
        json.dumps({"event_id": "e1", "entity_type": "run", "entity_id": "r1", "from_status": None, "to_status": "running", "run_id": "r1", "recorded_at": "2026-04-19T00:00:00Z"}),
        json.dumps({"event_id": "e2", "entity_type": "run", "entity_id": "r1", "from_status": "running", "to_status": "completed", "run_id": "r1", "recorded_at": "2026-04-19T00:00:05Z"}),
        json.dumps({"event_id": "e3", "entity_type": "record", "entity_id": "youtube:A", "from_status": "normalized", "to_status": "promoted", "run_id": "r1", "recorded_at": "2026-04-19T00:00:04Z"}),
    ]), encoding="utf-8")
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "a.json").write_text(json.dumps({
        "source_key": "youtube:A", "video_id": "A",
        "record_status": "promoted",
        "llm_context": {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.002},
        "collected_at": "2026-04-19T00:00:03Z",
    }), encoding="utf-8")

    rows = aggregate_daily(events, ds)
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2026-04-19"
    assert r["runs_completed"] == 1
    assert r["promoted"] == 1
    assert abs(r["cost_usd"] - 0.002) < 1e-9
    assert r["llm_input_tokens"] == 100
    assert r["avg_runtime_sec"] == 5.0


def test_metrics_write_daily_creates_file(tmp_path):
    out = tmp_path / "m" / "daily.jsonl"
    write_daily([{"date": "2026-04-19", "processed": 1}], out)
    assert out.exists()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["processed"] == 1


# ============== P3-3 PII ==============

def test_pii_masks_email_and_phone():
    text = "문의: alice@example.com 또는 010-1234-5678 로 연락주세요"
    out = mask(text)
    assert "alice@example.com" not in out
    assert "010-1234-5678" not in out
    assert "[이메일]" in out
    assert "[전화]" in out


def test_pii_masks_ssn_and_card():
    text = "주민번호 123456-1234567 카드 1234-5678-9012-3456"
    out = mask(text)
    assert "[주민번호]" in out and "[카드번호]" in out


def test_pii_mask_payload_fields():
    p = {
        "title": "연락 01012345678",
        "summary": "문의 bob@test.com",
        "rules": ["IP 192.168.1.1 차단"],
        "tags": ["t"],
    }
    m = mask_payload(p)
    assert "01012345678" not in m["title"]
    assert "bob@test.com" not in m["summary"]
    assert "192.168.1.1" not in m["rules"][0]
    # Original untouched
    assert "01012345678" in p["title"]


# ============== P3-5 Traces ==============

def test_traces_build_from_events_file(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join([
        json.dumps({"event_id": "e1", "entity_type": "run", "entity_id": "r1", "to_status": "running", "run_id": "r1", "recorded_at": "2026-04-19T00:00:00Z"}),
        json.dumps({"event_id": "e2", "entity_type": "stage", "entity_id": "youtube:A:collect", "to_status": "started", "run_id": "r1", "recorded_at": "2026-04-19T00:00:01Z"}),
        json.dumps({"event_id": "e3", "entity_type": "stage", "entity_id": "youtube:A:collect", "to_status": "completed", "run_id": "r1", "recorded_at": "2026-04-19T00:00:02Z"}),
        json.dumps({"event_id": "e4", "entity_type": "run", "entity_id": "r1", "to_status": "completed", "run_id": "r1", "recorded_at": "2026-04-19T00:00:05Z"}),
    ]), encoding="utf-8")
    out = tmp_path / "traces.jsonl"
    build_from_events_file(events, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    trace = json.loads(lines[0])
    assert trace["run_id"] == "r1"
    assert trace["run_final"] == "completed"
    assert trace["total_ms"] == 5000
    assert trace["stages"]["collect"]["final_status"] == "completed"
    assert trace["stages"]["collect"]["ms"] == 1000


# ============== P3-4 Rollback ==============

def test_rollback_restores_prior_summary(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = new_payload(video_id="RB1", run_id="r1")
    p["collected_at"] = "2026-04-19T00:00:00Z"
    p["summary"] = "옛 요약"
    p["rules"] = ["옛 규칙"]
    p["history"] = [{
        "at": "2026-04-18T00:00:00Z",
        "event_id": "evt_prev",
        "reason": "initial",
        "prev_summary": "원래 요약",
        "prev_rules_snapshot": ["원래 규칙"],
        "prev_transcript_hash": "h0",
        "prev_confidence": "confirmed",
    }]
    store.upsert(p)

    restored = rollback(p["source_key"], store=store, logger=logger, reason="bad_analysis")
    assert restored["summary"] == "원래 요약"
    assert restored["rules"] == ["원래 규칙"]
    assert restored["payload_version"] == p["payload_version"] + 1
    assert len(restored["history"]) == 2  # original + rollback snapshot
    assert any(e["reason"].startswith("rollback:") for e in logger.events)


def test_rollback_raises_when_no_history(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = new_payload(video_id="RB2", run_id="r1")
    p["collected_at"] = "2026-04-19T00:00:00Z"
    store.upsert(p)
    with pytest.raises(RollbackError):
        rollback(p["source_key"], store=store, logger=logger, reason="x")


# ============== P3-2 Alerts ==============

def _daily(date: str, runs_failed=0, runs_completed=0, sync_failed=0, avg_runtime_sec=0.0):
    return {
        "date": date, "runs_failed": runs_failed, "runs_completed": runs_completed,
        "runs_partial": 0, "sync_failed": sync_failed, "avg_runtime_sec": avg_runtime_sec,
    }


def test_alerts_failed_ratio_triggers_after_3_days():
    dailies = [
        _daily("2026-04-17", runs_failed=2, runs_completed=8),  # 20%
        _daily("2026-04-18", runs_failed=3, runs_completed=7),  # 30%
        _daily("2026-04-19", runs_failed=2, runs_completed=8),  # 20%
    ]
    alerts = evaluate(dailies)
    codes = [a.code for a in alerts]
    assert "FAILED_RATIO_HIGH" in codes


def test_alerts_sync_cumulative_triggers():
    dailies = [
        _daily(f"2026-04-{d:02d}", sync_failed=5) for d in range(13, 20)
    ]
    alerts = evaluate(dailies)
    assert any(a.code == "SYNC_FAILED_CUMULATIVE" for a in alerts)


def test_alerts_runtime_spike():
    dailies = [
        _daily("2026-04-13", runs_completed=5, avg_runtime_sec=10.0),
        _daily("2026-04-14", runs_completed=5, avg_runtime_sec=11.0),
        _daily("2026-04-15", runs_completed=5, avg_runtime_sec=9.0),
        _daily("2026-04-16", runs_completed=5, avg_runtime_sec=10.0),
        _daily("2026-04-17", runs_completed=5, avg_runtime_sec=10.5),
        _daily("2026-04-18", runs_completed=5, avg_runtime_sec=12.0),
        _daily("2026-04-19", runs_completed=5, avg_runtime_sec=25.0),
    ]
    alerts = evaluate(dailies)
    assert any(a.code == "RUNTIME_SPIKE" for a in alerts)


def test_alerts_emit_github_issue_uses_http():
    captured: dict = {}
    def fake_http(method, url, *, headers=None, data=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json.loads(data.decode())
        return {"status": 201, "body": json.dumps({"number": 42, "html_url": "https://github.com/o/r/issues/42"})}

    a = Alert(code="FAILED_RATIO_HIGH", severity="critical", title="테스트", body="본문")
    res = emit_github_issue(a, owner="o", repo="r", token="ghp_x", http=fake_http)
    assert captured["method"] == "POST"
    assert "/repos/o/r/issues" in captured["url"]
    assert captured["payload"]["title"].startswith("[critical]")
    assert "alert:FAILED_RATIO_HIGH" in captured["payload"]["labels"]


# ============== P3-6 DMCA ==============

def test_dmca_takedown_marks_removed_and_logs(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = new_payload(video_id="DMCA01", run_id="r1")
    p["collected_at"] = "2026-04-19T00:00:00Z"
    p["record_status"] = "promoted"
    store.upsert(p)
    updated = mark_dmca_takedown(p["source_key"], store=store, logger=logger,
                                  reason="takedown#123", actor="user:legal")
    assert updated["archive_state"] == "REMOVED"
    assert updated["failure_reason_code"] == "DMCA_TAKEDOWN"
    assert any(e["entity_type"] == "manual_action" and "dmca_takedown" in e["reason"] for e in logger.events)
