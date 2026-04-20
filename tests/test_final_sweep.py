"""Final sweep tests: prompt loader, actionable rule metric, rotation alert, failure distribution."""
from __future__ import annotations

import json
from pathlib import Path

from collector.alerts import evaluate
from collector.metrics import _is_actionable_rule, aggregate_daily
from collector.prompt_loader import load_prompt


# ============== prompt_loader ==============

def test_prompt_loader_returns_defaults_when_missing(tmp_path):
    p = load_prompt("nope", prompts_root=tmp_path)
    assert "summary" in p["system"]
    assert "{original_transcript}" in p["reprompt"]


def test_prompt_loader_parses_sections(tmp_path):
    (tmp_path / "x.md").write_text(
        "# header\n"
        "## system\n"
        "CUSTOM SYSTEM BODY\n"
        "\n"
        "## reprompt_on_schema_fail\n"
        "CUSTOM REPROMPT {original_transcript}\n",
        encoding="utf-8",
    )
    p = load_prompt("x", prompts_root=tmp_path)
    assert p["system"].strip() == "CUSTOM SYSTEM BODY"
    assert "CUSTOM REPROMPT" in p["reprompt"]


def test_prompt_loader_ignores_unknown_sections(tmp_path):
    (tmp_path / "y.md").write_text(
        "## random\nignored\n## system\nREAL SYSTEM\n",
        encoding="utf-8",
    )
    p = load_prompt("y", prompts_root=tmp_path)
    assert "REAL SYSTEM" in p["system"]


# ============== actionable rule metric ==============

def test_is_actionable_rule_detects_trading_keywords():
    assert _is_actionable_rule("고점 돌파 시 분할 진입한다") is True
    assert _is_actionable_rule("손절은 직전 저점") is True
    assert _is_actionable_rule("5-20 골든크로스 확인") is True


def test_is_actionable_rule_rejects_abstract():
    assert _is_actionable_rule("다양한 전략을 다룬다") is False
    assert _is_actionable_rule("") is False
    assert _is_actionable_rule("일반적인 설명") is False


def test_aggregate_daily_computes_actionable_ratio(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join([
        json.dumps({"event_id":"e1","entity_type":"run","entity_id":"r1",
                    "to_status":"completed","run_id":"r1",
                    "recorded_at":"2026-04-19T00:00:00Z"}),
        json.dumps({"event_id":"e2","entity_type":"record","entity_id":"youtube:A",
                    "to_status":"promoted","run_id":"r1",
                    "recorded_at":"2026-04-19T00:00:01Z"}),
    ]), encoding="utf-8")
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "a.json").write_text(json.dumps({
        "source_key":"youtube:A","video_id":"A","record_status":"promoted",
        "collected_at":"2026-04-19T00:00:00Z",
        "llm_context":{"cost_usd":0.001},
        "rules":["분할 진입한다", "손절은 직전 저점", "일반적인 설명"],
        "failure_reason_code":None,
    }), encoding="utf-8")
    rows = aggregate_daily(events, ds)
    assert len(rows) == 1
    r = rows[0]
    assert r["rules_total"] == 3
    assert r["rules_actionable"] == 2
    assert r["actionable_rule_ratio"] == round(2/3, 4)


def test_aggregate_daily_tracks_failure_codes(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "event_id":"e1","entity_type":"run","entity_id":"r1",
        "to_status":"completed","run_id":"r1",
        "recorded_at":"2026-04-19T00:00:00Z",
    }) + "\n", encoding="utf-8")
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "a.json").write_text(json.dumps({
        "source_key":"youtube:A","video_id":"A","record_status":"invalid",
        "collected_at":"2026-04-19T00:00:00Z",
        "llm_context":{"cost_usd":0},"rules":[],
        "failure_reason_code":"HTTP_429",
    }), encoding="utf-8")
    (ds / "b.json").write_text(json.dumps({
        "source_key":"youtube:B","video_id":"B","record_status":"invalid",
        "collected_at":"2026-04-19T00:00:00Z",
        "llm_context":{"cost_usd":0},"rules":[],
        "failure_reason_code":"HTTP_429",
    }), encoding="utf-8")
    (ds / "c.json").write_text(json.dumps({
        "source_key":"youtube:C","video_id":"C","record_status":"invalid",
        "collected_at":"2026-04-19T00:00:00Z",
        "llm_context":{"cost_usd":0},"rules":[],
        "failure_reason_code":"GIT_CONFLICT",
    }), encoding="utf-8")
    rows = aggregate_daily(events, ds)
    fc = rows[0]["failure_codes"]
    assert fc.get("HTTP_429") == 2
    assert fc.get("GIT_CONFLICT") == 1


# ============== rotation + quality alerts ==============

def test_alerts_emits_quality_drop():
    dailies = [{
        "date": "2026-04-19",
        "runs_failed": 0, "runs_completed": 5, "runs_partial": 0,
        "sync_failed": 0, "avg_runtime_sec": 0,
        "rules_total": 10, "rules_actionable": 2,
        "actionable_rule_ratio": 0.20,
    }]
    alerts = evaluate(dailies)
    assert any(a.code == "QUALITY_DROP" for a in alerts)


def test_alerts_emits_rotation_due():
    dailies = [{
        "date": "2026-04-19", "runs_failed": 0, "runs_completed": 1,
        "runs_partial": 0, "sync_failed": 0, "avg_runtime_sec": 0,
        "rules_total": 0, "rules_actionable": 0, "actionable_rule_ratio": 1.0,
    }]
    alerts = evaluate(dailies, rotation_ages_days={"YOUTUBE_API_KEY": 100})
    rot = [a for a in alerts if a.code == "ROTATION_DUE"]
    assert len(rot) == 1
    assert "YOUTUBE_API_KEY" in rot[0].title


def test_alerts_does_not_flag_fresh_secret():
    alerts = evaluate(
        [{"date":"2026-04-19","runs_failed":0,"runs_completed":1,"runs_partial":0,
          "sync_failed":0,"avg_runtime_sec":0,"rules_total":0,
          "rules_actionable":0,"actionable_rule_ratio":1.0}],
        rotation_ages_days={"FRESH_KEY": 10},
    )
    assert not any(a.code == "ROTATION_DUE" for a in alerts)
