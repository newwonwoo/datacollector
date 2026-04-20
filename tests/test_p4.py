"""Tests for P4: chunking, clickbait, aggregate, archive, fallback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.aggregate import aggregate_by_tag, write_aggregate
from collector.archive import archive_quarter, month_in_quarter, previous_quarter
from collector.chunking import (
    CHUNK_CHARS,
    MAX_CHARS_SINGLE,
    chunk,
    reduce_outputs,
    should_chunk,
)
from collector.clickbait import extract_nouns, is_clickbait, title_match_ratio
from collector.events import EventLogger
from collector.payload import new_payload
from collector.pipeline import run_pipeline
from collector.services import build_mock_services
from collector.store import JSONStore


_LONG_SUMMARY = "단타 매매 전략의 핵심 흐름 요약. 장중 고점 돌파 시 분할 진입."


# ============== P4-1 Chunking ==============

def test_should_chunk_threshold():
    assert should_chunk("a" * (MAX_CHARS_SINGLE + 1)) is True
    assert should_chunk("a" * 100) is False


def test_chunk_single_piece_for_short_text():
    text = "hello world"
    assert chunk(text) == [text]


def test_chunk_splits_with_overlap():
    text = "단어 " * 20000  # 100k chars
    pieces = chunk(text)
    assert len(pieces) > 1
    # overlap guarantees continuity
    joined = pieces[0] + pieces[1]
    assert len(joined) > CHUNK_CHARS


def test_reduce_outputs_dedups_and_trims():
    outs = [
        {"summary": "요약 1", "rules": ["r1", "r2"], "tags": ["t1"]},
        {"summary": "요약 2", "rules": ["r2", "r3"], "tags": ["t2"]},
        {"summary": "요약 3", "rules": ["r4"], "tags": ["t3"]},
    ]
    r = reduce_outputs(outs)
    assert r["rules"] == ["r1", "r2", "r3", "r4"]
    assert set(r["tags"]) == {"t1", "t2", "t3"}
    assert "요약 1" in r["summary"]


def test_pipeline_chunks_long_transcript(tmp_path):
    long_text = "단타 매매 전략의 핵심 설명입니다. " * 4000
    assert len(long_text) > MAX_CHARS_SINGLE

    calls: list[int] = []
    def llm(text, attempt):
        calls.append(len(text))
        return {"summary": _LONG_SUMMARY + " 본문.", "rules": ["r"], "tags": ["t"]}

    from collector.services import Services
    services = Services(
        youtube_search=lambda q: [],
        youtube_captions=lambda vid: {"source": "manual", "text": long_text},
        youtube_video_alive=lambda vid: True,
        llm_extract=llm,
        semantic_similarity=lambda a, b: 0.8,
        git_sync=lambda p: None,
    )
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = new_payload(video_id="LONG0000001", run_id="r1")
    run_pipeline(p, services, store, logger, use_lock=False)
    # multiple LLM calls → chunked
    assert len(calls) >= 2


# ============== P4-2 Clickbait ==============

def test_extract_nouns_filters_stopwords():
    nouns = extract_nouns("오늘 이영상 단타매매 전략 알려드립니다")
    assert "오늘" not in nouns
    assert "단타매매" in nouns or "단타" in "".join(nouns)


def test_title_match_ratio_full_overlap():
    title = "단타 매매 전략"
    transcript = "단타 매매 전략 설명 이어서 다양한 이야기를 풀어나갑니다"
    r = title_match_ratio(title, transcript)
    assert r >= 0.5


def test_title_match_ratio_no_overlap_flags_clickbait():
    title = "대박 떡상 영상"
    transcript = "오늘 날씨 요리 여행 풍경 얘기 수다"
    assert is_clickbait(title, transcript) is True


def test_stage_collect_sets_clickbait_flag(tmp_path):
    from collector.services import Services
    services = Services(
        youtube_search=lambda q: [],
        youtube_captions=lambda vid: {"source": "manual", "text": "오늘 날씨 점심 메뉴 얘기"},
        youtube_video_alive=lambda vid: True,
        llm_extract=lambda t, a: {"summary": _LONG_SUMMARY + " 내용.", "rules": ["r"], "tags": []},
        semantic_similarity=lambda a, b: 0.8,
        git_sync=lambda p: None,
    )
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = new_payload(video_id="BAIT0000001", run_id="r1", title="대박 떡상 전략 공개")
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p.get("_flag_clickbait") is True


# ============== P4-3 Aggregate ==============

def _write_record(ds, source_key, tags, rules, confidence, channel):
    path = ds / "202604"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{source_key.replace(':', '__')}.json").write_text(json.dumps({
        "source_key": source_key, "video_id": source_key.split(":")[1],
        "channel_id": channel, "confidence": confidence,
        "tags": tags, "rules": rules,
        "record_status": "promoted",
        "collected_at": "2026-04-19T00:00:00Z",
        "llm_context": {"cost_usd": 0, "input_tokens": 0, "output_tokens": 0},
    }), encoding="utf-8")


def test_aggregate_by_tag_groups_rules(tmp_path):
    ds = tmp_path / "ds"
    _write_record(ds, "youtube:A", ["단타"], ["r1", "r2"], "confirmed", "CH1")
    _write_record(ds, "youtube:B", ["단타", "돌파"], ["r1", "r3"], "confirmed", "CH2")
    _write_record(ds, "youtube:C", ["눌림목"], ["r9"], "inferred", "CH3")
    _write_record(ds, "youtube:D", ["단타"], ["r5"], "unverified", "CH4")
    out = aggregate_by_tag(ds, tags=["단타"], min_confidence="inferred")
    assert out["total_records"] == 2  # D is unverified, C has no 단타
    top_rules = dict(out["top_rules"])
    assert top_rules.get("r1") == 2


def test_aggregate_without_tag_filter_includes_all(tmp_path):
    ds = tmp_path / "ds"
    _write_record(ds, "youtube:A", ["단타"], ["r1"], "confirmed", "CH1")
    _write_record(ds, "youtube:B", ["눌림목"], ["r2"], "confirmed", "CH2")
    out = aggregate_by_tag(ds, tags=None, min_confidence="confirmed")
    assert out["total_records"] == 2


# ============== P4-4 Archive ==============

def test_month_in_quarter():
    assert month_in_quarter(1, 1) and month_in_quarter(3, 1)
    assert month_in_quarter(4, 2) and month_in_quarter(6, 2)
    assert not month_in_quarter(4, 1)


def test_archive_quarter_moves_files(tmp_path):
    ds = tmp_path / "ds"
    _write_record(ds, "youtube:A", ["t"], ["r"], "confirmed", "CH")  # in 202604 (Q2)
    _write_record(ds, "youtube:B", ["t"], ["r"], "confirmed", "CH")  # also 202604
    arc = tmp_path / "arc"
    moved = archive_quarter(ds, arc, year=2026, quarter=2)
    assert len(moved) == 2
    # Originals gone
    assert list(ds.rglob("*.json")) == []
    # Targets exist with ARCHIVED state
    for p in moved:
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["archive_state"] == "ARCHIVED"


def test_archive_quarter_skips_other_quarters(tmp_path):
    ds = tmp_path / "ds"
    # 202601 = Q1
    path = ds / "202601"
    path.mkdir(parents=True, exist_ok=True)
    (path / "youtube__Z.json").write_text(json.dumps({
        "source_key": "youtube:Z", "video_id": "Z",
        "collected_at": "2026-01-15T00:00:00Z",
    }), encoding="utf-8")
    arc = tmp_path / "arc"
    moved = archive_quarter(ds, arc, year=2026, quarter=2)
    assert moved == []
    # Original remains
    assert len(list(ds.rglob("*.json"))) == 1


def test_previous_quarter_wraps_year():
    import datetime
    d = datetime.date(2026, 2, 10)  # Q1 → previous = 2025 Q4
    y, q = previous_quarter(d)
    assert (y, q) == (2025, 4)


# ============== P4-5 Fallback query ==============

def test_cli_run_uses_fallback_on_empty_search(tmp_path, monkeypatch):
    """When YouTube search returns nothing, run() should retry with fallback."""
    # Force real-adapter path by setting keys
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake_yt")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake_llm")

    call_counter = {"n": 0}
    def fake_http(method, url, **kw):
        call_counter["n"] += 1
        # Return empty items on both calls — run should gracefully finish
        return {"status": 200, "body": json.dumps({"items": []})}

    # Monkeypatch adapters to use fake_http
    from collector.adapters import youtube as yt_mod
    monkeypatch.setattr(yt_mod.YouTubeAdapter, "__init__",
                        lambda self, api_key, http=None: setattr(self, "api_key", api_key)
                        or setattr(self, "http", fake_http))

    from collector.cli import run as run_mod
    summary = run_mod.run_query(
        "비어있는 검색어 xxyyzz",
        count=2,
        data_store_root=tmp_path / "ds",
        logs_root=tmp_path / "logs",
    )
    # both initial and fallback search attempts counted → 2+
    assert call_counter["n"] >= 2
    assert summary["candidates"] == 0
