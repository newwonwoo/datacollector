"""Tests for runs snapshot writer, channel quality score, yt-dlp fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from collector.channel_quality import (
    ChannelScore, as_serializable, compute_channel_scores, top_channels,
)
from collector.payload import new_payload
from collector.runs import save_run_snapshot


# ============== runs snapshot ==============

def test_save_run_snapshot_creates_file(tmp_path):
    p1 = new_payload(video_id="A", run_id="run_x", channel_id="CH1", title="t1")
    p1["record_status"] = "promoted"
    p1["confidence"] = "confirmed"
    p1["rules"] = ["r1", "r2"]
    p1["llm_context"]["cost_usd"] = 0.001
    p2 = new_payload(video_id="B", run_id="run_x", channel_id="CH2")
    p2["record_status"] = "invalid"
    p2["failure_reason_code"] = "HTTP_429"

    out = save_run_snapshot("run_x", [p1, p2], query="단타", root=tmp_path)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["run_id"] == "run_x"
    assert data["query"] == "단타"
    assert data["total_videos"] == 2
    assert data["record_status_counts"]["promoted"] == 1
    assert data["record_status_counts"]["invalid"] == 1
    assert data["total_cost_usd"] == 0.001
    assert data["per_video"][0]["source_key"] == "youtube:A"


def test_save_run_snapshot_handles_empty(tmp_path):
    out = save_run_snapshot("run_empty", [], root=tmp_path)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total_videos"] == 0
    assert data["record_status_counts"]["promoted"] == 0


def test_save_run_snapshot_no_temp_leak(tmp_path):
    save_run_snapshot("r", [], root=tmp_path)
    # Should not have .run.* temp files
    assert list(tmp_path.glob(".run.*")) == []


# ============== channel quality ==============

def _rec(source_key, channel, status, conf="confirmed", clickbait=False):
    base = {
        "source_key": source_key, "video_id": source_key.split(":")[1],
        "channel_id": channel, "record_status": status, "confidence": conf,
        "collected_at": "2026-04-19T00:00:00Z",
    }
    if clickbait:
        base["_flag_clickbait"] = True
    return base


def _ds(tmp_path, records):
    root = tmp_path / "ds" / "202604"
    root.mkdir(parents=True)
    for r in records:
        name = r["source_key"].replace(":", "__") + ".json"
        (root / name).write_text(json.dumps(r), encoding="utf-8")
    return tmp_path / "ds"


def test_channel_score_all_promoted_is_plus_one(tmp_path):
    ds = _ds(tmp_path, [
        _rec("youtube:A", "CH1", "promoted"),
        _rec("youtube:B", "CH1", "promoted"),
    ])
    scores = compute_channel_scores(ds)
    assert scores["CH1"].score == 1.0
    assert scores["CH1"].tier == "green"


def test_channel_score_all_invalid_is_minus_one(tmp_path):
    ds = _ds(tmp_path, [
        _rec("youtube:A", "CH2", "invalid"),
        _rec("youtube:B", "CH2", "invalid"),
    ])
    scores = compute_channel_scores(ds)
    assert scores["CH2"].score == -1.0
    assert scores["CH2"].tier == "red"


def test_channel_score_clickbait_drags_down(tmp_path):
    ds = _ds(tmp_path, [
        _rec("youtube:A", "CH3", "promoted", clickbait=True),
        _rec("youtube:B", "CH3", "promoted", clickbait=True),
    ])
    scores = compute_channel_scores(ds)
    # raw = 2*1.0 + 2*(-0.5) = 1.0 / 2 = 0.5 (yellow-green edge)
    assert scores["CH3"].score == 0.5
    assert scores["CH3"].tier == "green"


def test_channel_score_mixed_classification(tmp_path):
    ds = _ds(tmp_path, [
        _rec("youtube:A", "CH4", "reviewed_inferred", conf="inferred"),
        _rec("youtube:B", "CH4", "reviewed_unverified", conf="unverified"),
    ])
    scores = compute_channel_scores(ds)
    # raw = 0.3 - 0.3 = 0.0 → yellow
    assert scores["CH4"].score == 0.0
    assert scores["CH4"].tier == "yellow"


def test_top_channels_sorts_descending(tmp_path):
    ds = _ds(tmp_path, [
        _rec("youtube:A", "CH_GOOD", "promoted"),
        _rec("youtube:B", "CH_BAD", "invalid"),
        _rec("youtube:C", "CH_NEUTRAL", "reviewed_inferred", conf="inferred"),
    ])
    top = top_channels(compute_channel_scores(ds), n=3, reverse=True)
    assert top[0].channel_id == "CH_GOOD"
    assert top[-1].channel_id == "CH_BAD"


def test_channel_score_serializable(tmp_path):
    ds = _ds(tmp_path, [_rec("youtube:A", "CH1", "promoted")])
    out = as_serializable(compute_channel_scores(ds))
    assert len(out) == 1
    assert "score" in out[0] and "tier" in out[0]
    assert out[0]["channel_id"] == "CH1"


# ============== yt-dlp fallback ==============

def test_ytdlp_fallback_not_invoked_by_default(monkeypatch):
    from collector.adapters.youtube import YouTubeAdapter
    monkeypatch.delenv("COLLECTOR_YT_DLP", raising=False)
    calls = []
    def fake_http(method, url, **kw):
        calls.append(url)
        return {"status": 404, "body": ""}
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.captions("vid")
    assert out == {"source": "none", "text": ""}
    # Only timedtext URLs were tried (4 variants)
    assert all("timedtext" in u for u in calls)


def test_ytdlp_fallback_gated_by_env(monkeypatch):
    from collector.adapters.youtube import YouTubeAdapter
    monkeypatch.setenv("COLLECTOR_YT_DLP", "1")
    # Guarantee the shutil.which lookup fails so we exercise the gate cleanly
    monkeypatch.setattr("shutil.which", lambda binary: None)
    def fake_http(method, url, **kw):
        return {"status": 404, "body": ""}
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.captions("vid")
    assert out == {"source": "none", "text": ""}
