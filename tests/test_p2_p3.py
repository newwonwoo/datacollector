"""Tests for P2/P3 batch: status snapshot, slack emit, archive md split, secret rotation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.alerts import Alert, emit_slack
from collector.archive import archive_quarter_markdown
from collector.cli.status_cli import build_status
from collector.events import EventLogger
from collector.secrets_rotation import (
    SecretRotationError, fingerprint, log_rotation, days_since_last_rotation,
)


_LONG_SUMMARY = "단타 매매 전략 요약입니다. 장중 고점 돌파 분할 진입, 손절 직전 저점, 익절 분할 수행."


# ============== P2-a: collector status ==============

def test_status_snapshot_empty_env(tmp_path):
    snap = build_status(
        dlq_root=tmp_path / "dlq",
        review_queue_root=tmp_path / "rq",
        breakers_path=tmp_path / "brk.json",
        quota_usage=tmp_path / "quota.jsonl",
        data_store=tmp_path / "ds",
        events=tmp_path / "events.jsonl",
    )
    assert snap["dlq_count"] == 0
    assert snap["review_queue_count"] == 0
    assert snap["breakers"] == {}
    assert snap["records"] == {"total": 0, "promoted": 0, "invalid": 0}
    assert snap["latest_run"] is None
    assert "updated_at" in snap
    assert isinstance(snap["kill_switch"], bool)


def test_status_counts_dlq_and_queue(tmp_path):
    (tmp_path / "dlq" / "GIT_CONFLICT" / "20260420").mkdir(parents=True)
    (tmp_path / "dlq" / "GIT_CONFLICT" / "20260420" / "a.json").write_text('{"x":1}')
    (tmp_path / "dlq" / "GIT_CONFLICT" / "20260420" / "b.json").write_text('{"x":2}')
    (tmp_path / "rq").mkdir()
    (tmp_path / "rq" / "z.json").write_text('{"y":1}')

    snap = build_status(
        dlq_root=tmp_path / "dlq",
        review_queue_root=tmp_path / "rq",
        breakers_path=tmp_path / "brk.json",
        quota_usage=tmp_path / "q.jsonl",
        data_store=tmp_path / "ds",
        events=tmp_path / "events.jsonl",
    )
    assert snap["dlq_count"] == 2
    assert snap["review_queue_count"] == 1


def test_status_breakers_parse_open(tmp_path):
    import time
    brk = tmp_path / "brk.json"
    brk.parent.mkdir(parents=True, exist_ok=True)
    future = time.time() + 600
    brk.write_text(json.dumps({
        "youtube_api": {"failures": [], "open_until": future},
    }))
    snap = build_status(
        dlq_root=tmp_path / "dlq",
        review_queue_root=tmp_path / "rq",
        breakers_path=brk,
        quota_usage=tmp_path / "q.jsonl",
        data_store=tmp_path / "ds",
        events=tmp_path / "events.jsonl",
    )
    assert snap["breakers"]["youtube_api"]["open"] is True
    assert snap["breakers"]["youtube_api"]["seconds_remaining"] > 500


def test_status_records_count_by_status(tmp_path):
    ds = tmp_path / "ds" / "202604"
    ds.mkdir(parents=True)
    (ds / "a.json").write_text(json.dumps({"source_key": "youtube:A", "record_status": "promoted"}))
    (ds / "b.json").write_text(json.dumps({"source_key": "youtube:B", "record_status": "invalid"}))
    (ds / "c.json").write_text(json.dumps({"source_key": "youtube:C", "record_status": "promoted"}))
    snap = build_status(
        dlq_root=tmp_path / "dlq",
        review_queue_root=tmp_path / "rq",
        breakers_path=tmp_path / "brk.json",
        quota_usage=tmp_path / "q.jsonl",
        data_store=tmp_path / "ds",
        events=tmp_path / "events.jsonl",
    )
    assert snap["records"] == {"total": 3, "promoted": 2, "invalid": 1}


# ============== P3-a: archive markdown split ==============

def test_archive_markdown_moves_quarter_notes(tmp_path):
    vault = tmp_path / "vault"
    strat = vault / "strategies"
    strat.mkdir(parents=True)
    (strat / "youtube__A.md").write_text(
        "---\ncollected: 2026-04-05T00:00:00Z\n---\n# A\n"
    )
    (strat / "youtube__B.md").write_text(
        "---\ncollected: 2026-01-10T00:00:00Z\n---\n# B\n"
    )
    arc = tmp_path / "arc"
    moved = archive_quarter_markdown(vault, arc, year=2026, quarter=2)
    # Only April (Q2) note moves
    assert len(moved) == 1
    assert (arc / "2026_Q2" / "youtube__A.md").exists()
    assert (strat / "youtube__B.md").exists()  # Q1 stays
    assert not (strat / "youtube__A.md").exists()


def test_archive_markdown_empty_vault(tmp_path):
    moved = archive_quarter_markdown(tmp_path / "nope", tmp_path / "arc", year=2026, quarter=2)
    assert moved == []


def test_archive_markdown_ignores_bad_frontmatter(tmp_path):
    vault = tmp_path / "vault"
    strat = vault / "strategies"
    strat.mkdir(parents=True)
    (strat / "youtube__X.md").write_text("no frontmatter here\n")
    moved = archive_quarter_markdown(vault, tmp_path / "arc", year=2026, quarter=2)
    assert moved == []


# ============== P3-b: slack emit ==============

def test_emit_slack_posts_alert_payload():
    captured = {}
    def fake_http(method, url, *, headers=None, data=None):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = json.loads(data.decode())
        return {"status": 200, "body": "ok"}
    alert = Alert(code="FAILED_RATIO_HIGH", severity="critical", title="실패율 높음", body="본문")
    emit_slack(alert, webhook_url="https://hooks.slack.com/services/TEST", http=fake_http)
    assert captured["method"] == "POST"
    att = captured["payload"]["attachments"][0]
    assert att["title"].startswith("[CRITICAL]")
    assert att["color"] == "#dc2626"
    assert any(f["value"] == "FAILED_RATIO_HIGH" for f in att["fields"])


# ============== P3-c: secret rotation tracking ==============

def test_fingerprint_stable():
    assert fingerprint("hello") == fingerprint("hello")
    assert fingerprint("a") != fingerprint("b")
    assert fingerprint("") == ""


def test_log_rotation_writes_event():
    logger = EventLogger()
    ev = log_rotation(
        "YOUTUBE_API_KEY", logger=logger,
        old_value="AIza_old", new_value="AIza_new", actor="user:alice",
    )
    assert ev["entity_type"] == "secret_rotation"
    assert ev["entity_id"] == "secret:YOUTUBE_API_KEY"
    assert ev["from_status"] == fingerprint("AIza_old")
    assert ev["to_status"] == fingerprint("AIza_new")
    assert "90d" in ev["reason"] or ev["reason"]


def test_log_rotation_rejects_noop():
    logger = EventLogger()
    with pytest.raises(SecretRotationError):
        log_rotation("K", logger=logger, old_value="same", new_value="same")


def test_log_rotation_does_not_leak_secret():
    logger = EventLogger()
    log_rotation("SECRET_X", logger=logger, old_value="TOP_SECRET_VALUE", new_value="NEW_TOP_SECRET")
    raw = json.dumps(logger.events)
    assert "TOP_SECRET_VALUE" not in raw
    assert "NEW_TOP_SECRET" not in raw


def test_days_since_last_rotation_reads_events_file(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join([
        json.dumps({"event_id": "e1", "entity_type": "secret_rotation",
                    "entity_id": "secret:K1", "recorded_at": "2026-04-10T00:00:00Z"}),
        json.dumps({"event_id": "e2", "entity_type": "secret_rotation",
                    "entity_id": "secret:K1", "recorded_at": "2026-04-15T00:00:00Z"}),
        json.dumps({"event_id": "e3", "entity_type": "secret_rotation",
                    "entity_id": "secret:K2", "recorded_at": "2026-04-01T00:00:00Z"}),
    ]), encoding="utf-8")
    d1 = days_since_last_rotation("K1", path)
    d2 = days_since_last_rotation("K2", path)
    d3 = days_since_last_rotation("NEVER", path)
    assert d1 is not None and d1 >= 0
    assert d2 is not None and d2 >= d1  # K2 older than K1
    assert d3 is None
