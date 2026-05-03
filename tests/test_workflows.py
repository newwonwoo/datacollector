"""Tests for the workflow primitives + MCP stdio server.

The cheap-LLM call is monkey-patched in every test so we don't depend
on any network or API key in CI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from collector.payload import new_payload
from collector.workflows import (
    brainstorm_topics,
    export_notebook,
    research_batch,
    synthesize,
)


# -------- brainstorm --------

def test_brainstorm_parses_list_response(monkeypatch):
    captured: dict = {}

    def fake_call(prompt, *, expect_json=True, max_tokens_hint=4000):
        captured["prompt"] = prompt
        return [
            {
                "idea": "사주 결혼 매칭",
                "rationale": "수요 큼",
                "search_keywords": ["사주 결혼", "명리 궁합"],
                "target_audience": "20-40대",
            },
            {
                "idea": "사주 직업 추천",
                "rationale": "이직 시즌",
                "search_keywords": ["사주 직업운"],
                "target_audience": "이직층",
            },
        ]

    import collector.workflows.brainstorm as _bm
    monkeypatch.setattr(_bm, "call_workflow_llm", fake_call)
    ideas = brainstorm_topics("사주", count=2, focus="1인 사업", exclude=["광고"])
    assert len(ideas) == 2
    assert ideas[0]["idea"] == "사주 결혼 매칭"
    assert ideas[0]["search_keywords"] == ["사주 결혼", "명리 궁합"]
    assert "사주" in captured["prompt"]
    assert "1인 사업" in captured["prompt"]
    assert "광고" in captured["prompt"]


def test_brainstorm_accepts_dict_with_ideas_key(monkeypatch):
    """Some LLMs in JSON mode wrap arrays in {"ideas": [...]}."""
    import collector.workflows.brainstorm as _bm
    monkeypatch.setattr(
        _bm, "call_workflow_llm",
        lambda *a, **kw: {"ideas": [{
            "idea": "x", "rationale": "r",
            "search_keywords": ["a"], "target_audience": "t",
        }]},
    )
    ideas = brainstorm_topics("x", count=5)
    assert len(ideas) == 1


def test_brainstorm_drops_invalid_entries(monkeypatch):
    import collector.workflows.brainstorm as _bm
    monkeypatch.setattr(
        _bm, "call_workflow_llm",
        lambda *a, **kw: [
            {"idea": "good", "search_keywords": ["a"]},
            {"idea": "", "search_keywords": ["b"]},          # blank idea
            {"idea": "no_keywords", "search_keywords": []},   # no keywords
            "not a dict",                                     # malformed
        ],
    )
    ideas = brainstorm_topics("x", count=10)
    assert [i["idea"] for i in ideas] == ["good"]


# -------- research_batch --------

def test_research_batch_calls_run_query_per_keyword(monkeypatch, tmp_path):
    """Concurrency=1 keeps order deterministic so we can assert content."""
    seen: list[str] = []

    def fake_run_query(query, **kw):
        seen.append(query)
        return {"query": query, "candidates": 3, "promoted": 1, "processed": 1}

    import collector.workflows._batch as _rb
    monkeypatch.setattr("collector.cli.run.run_query", fake_run_query)
    monkeypatch.setattr(_rb, "_detect_warp", lambda timeout=2.0: False)

    results = research_batch(
        ["a", "b", "c"],
        count_per_keyword=5,
        max_concurrency=1,
        data_store_root=tmp_path / "ds",
        logs_root=tmp_path / "logs",
    )
    assert len(results) == 3
    assert sorted(seen) == ["a", "b", "c"]


def test_research_batch_warp_drops_concurrency_to_1(monkeypatch, tmp_path):
    import collector.workflows._batch as _rb
    monkeypatch.setattr(_rb, "_detect_warp", lambda timeout=2.0: True)

    captured = {"max_workers": None}
    real_pool = __import__("concurrent.futures").futures.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kw):
            captured["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kw)

    monkeypatch.setattr(_rb, "ThreadPoolExecutor", _Spy)
    monkeypatch.setattr(
        "collector.cli.run.run_query",
        lambda q, **kw: {"query": q, "promoted": 0, "processed": 0},
    )

    research_batch(["a"], count_per_keyword=1, max_concurrency=None,
                   data_store_root=tmp_path, logs_root=tmp_path)
    assert captured["max_workers"] == 1


def test_research_batch_clamps_concurrency_to_safe_range(monkeypatch, tmp_path):
    import collector.workflows._batch as _rb
    monkeypatch.setattr(_rb, "_detect_warp", lambda timeout=2.0: False)
    monkeypatch.setattr(
        "collector.cli.run.run_query",
        lambda q, **kw: {"query": q, "promoted": 0, "processed": 0},
    )
    captured = {"max_workers": None}
    real_pool = __import__("concurrent.futures").futures.ThreadPoolExecutor

    class _Spy(real_pool):
        def __init__(self, max_workers=None, **kw):
            captured["max_workers"] = max_workers
            super().__init__(max_workers=max_workers, **kw)

    monkeypatch.setattr(_rb, "ThreadPoolExecutor", _Spy)
    research_batch(["a"], max_concurrency=99,
                   data_store_root=tmp_path, logs_root=tmp_path)
    assert captured["max_workers"] == 5  # clamped


def test_research_batch_swallows_per_keyword_errors(monkeypatch, tmp_path):
    """One failing run_query shouldn't kill the batch."""
    def fake_run_query(query, **kw):
        if query == "boom":
            raise RuntimeError("nope")
        return {"query": query, "promoted": 1, "processed": 1}

    import collector.workflows._batch as _rb
    monkeypatch.setattr("collector.cli.run.run_query", fake_run_query)
    monkeypatch.setattr(_rb, "_detect_warp", lambda timeout=2.0: False)
    results = research_batch(["a", "boom", "b"], max_concurrency=1,
                             data_store_root=tmp_path, logs_root=tmp_path)
    by_q = {r["query"]: r for r in results}
    assert "error" in by_q["boom"]
    assert by_q["a"]["promoted"] == 1
    assert by_q["b"]["promoted"] == 1


# -------- synthesize --------

def test_synthesize_picks_best_index(monkeypatch):
    captured = {}

    def fake_call(prompt, *, expect_json=True, max_tokens_hint=4000):
        captured["prompt"] = prompt
        return {
            "best_idea_index": 1,
            "scores": [
                {"idea": "a", "score": 40, "why": "weak"},
                {"idea": "b", "score": 88, "why": "strong"},
            ],
            "reasoning": "b 자료 풍부",
            "recommended_next_steps": ["MVP 빌드"],
        }

    import collector.workflows._synth as _sm
    monkeypatch.setattr(_sm, "call_workflow_llm", fake_call)
    ideas = [
        {"idea": "a", "search_keywords": ["x"]},
        {"idea": "b", "search_keywords": ["y"]},
    ]
    research = [
        {"query": "x", "promoted": 0, "processed": 1, "per_video": []},
        {"query": "y", "promoted": 5, "processed": 5,
         "per_video": [{"channel_id": "C1"}, {"channel_id": "C2"}]},
    ]
    out = synthesize(ideas, research)
    assert out["best_idea_index"] == 1
    assert "MVP" in out["recommended_next_steps"][0]
    # Compact summary made it into the prompt
    assert "promoted" in captured["prompt"]


def test_synthesize_empty_ideas():
    out = synthesize([], [])
    assert out["best_idea_index"] == -1


# -------- export_notebook --------

def _seed_record(ds: Path, video_id: str, **overrides) -> None:
    p = new_payload(video_id=video_id, run_id="r", title=f"{video_id} title",
                    source_query="단타", channel_id="UC123")
    p.update(overrides)
    yyyymm = (p.get("collected_at") or "2026-04-01")[:7].replace("-", "")
    out = ds / yyyymm / f"{p['source_key'].replace(':', '__')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")


def test_export_notebook_writes_combined_md(tmp_path):
    ds = tmp_path / "ds"
    _seed_record(ds, "VID1", record_status="promoted",
                 summary="요약 1", rules=["r1"], knowledge=["k1"],
                 notes_md="## 본문\n자세한 내용", content_type="concept")
    _seed_record(ds, "VID2", record_status="promoted",
                 summary="요약 2", examples=["e1"])
    _seed_record(ds, "VID3", record_status="invalid")  # filtered out

    path = export_notebook(
        data_store_root=ds, out_dir=tmp_path / "exports",
        only_promoted=True, label="single_test",
    )
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "VID1 title" in body and "VID2 title" in body
    assert "VID3" not in body
    assert "## 핵심 개념" in body
    assert "## 사례" in body
    assert "## 상세 노트" in body
    assert "record_count: 2" in body


def test_export_notebook_filters_by_channel_and_tag(tmp_path):
    ds = tmp_path / "ds"
    _seed_record(ds, "A", channel_id="CH_A", record_status="promoted",
                 tags=["alpha"], summary="x")
    _seed_record(ds, "B", channel_id="CH_B", record_status="promoted",
                 tags=["beta"], summary="x")
    p_a = export_notebook(data_store_root=ds, out_dir=tmp_path / "out",
                          channel_id="CH_A")
    text_a = p_a.read_text(encoding="utf-8")
    assert "A title" in text_a and "B title" not in text_a

    p_b = export_notebook(data_store_root=ds, out_dir=tmp_path / "out", tag="beta")
    text_b = p_b.read_text(encoding="utf-8")
    assert "B title" in text_b and "A title" not in text_b


# -------- MCP server --------

def test_mcp_tools_list_includes_all():
    from collector.cli.mcp_server import _TOOLS, _handle

    out = _handle("tools/list", {})
    names = {t["name"] for t in out["tools"]}
    expected = {
        "run_query", "search_notes", "get_note", "list_recent",
        "list_channels", "get_pipeline_status",
        "brainstorm_topics", "research_batch", "synthesize", "export_notebook",
    }
    assert expected.issubset(names)
    # Every tool ships an inputSchema
    assert all("inputSchema" in t for t in out["tools"])
    assert _TOOLS  # not empty


def test_mcp_initialize_handshake():
    from collector.cli.mcp_server import _handle
    out = _handle("initialize", {})
    assert out["protocolVersion"]
    assert out["serverInfo"]["name"] == "collector"
    assert "tools" in out["capabilities"]
    assert "resources" in out["capabilities"]


def test_mcp_search_notes_finds_seeded_record(tmp_path, monkeypatch):
    ds = tmp_path / "ds"
    _seed_record(ds, "MCP1", summary="단타 매매 핵심 룰", record_status="promoted",
                 tags=["단타"])
    monkeypatch.setenv("COLLECTOR_DATA_STORE", str(ds))
    from collector.cli.mcp_server import _handle
    out = _handle("tools/call", {"name": "search_notes",
                                  "arguments": {"query": "단타"}})
    body = json.loads(out["content"][0]["text"])
    assert body["total"] == 1
    assert body["matches"][0]["source_key"] == "youtube:MCP1"


def test_mcp_get_note_returns_full_payload(tmp_path, monkeypatch):
    ds = tmp_path / "ds"
    _seed_record(ds, "DEEP1", summary="deep", record_status="promoted",
                 knowledge=["k"], notes_md="## sec\nbody")
    monkeypatch.setenv("COLLECTOR_DATA_STORE", str(ds))
    from collector.cli.mcp_server import _handle
    out = _handle("tools/call", {"name": "get_note",
                                  "arguments": {"source_key": "youtube:DEEP1"}})
    body = json.loads(out["content"][0]["text"])
    assert body["source_key"] == "youtube:DEEP1"
    assert body["knowledge"] == ["k"]
    assert "## sec" in body["notes_md"]


def test_mcp_unknown_tool_raises():
    from collector.cli.mcp_server import _handle
    with pytest.raises(ValueError):
        _handle("tools/call", {"name": "does_not_exist", "arguments": {}})


def test_mcp_resources_list(tmp_path, monkeypatch):
    vault = tmp_path / "vault" / "strategies"
    vault.mkdir(parents=True)
    (vault / "youtube__VID42.md").write_text("# title", encoding="utf-8")
    monkeypatch.setenv("COLLECTOR_VAULT", str(tmp_path / "vault"))

    from collector.cli.mcp_server import _handle
    out = _handle("resources/list", {})
    uris = [r["uri"] for r in out["resources"]]
    assert "vault://strategies/youtube__VID42" in uris


def test_mcp_resources_read(tmp_path, monkeypatch):
    vault = tmp_path / "vault" / "strategies"
    vault.mkdir(parents=True)
    (vault / "youtube__VID7.md").write_text("# hello", encoding="utf-8")
    monkeypatch.setenv("COLLECTOR_VAULT", str(tmp_path / "vault"))

    from collector.cli.mcp_server import _handle
    out = _handle("resources/read",
                  {"uri": "vault://strategies/youtube__VID7"})
    assert out["contents"][0]["text"] == "# hello"
    assert out["contents"][0]["mimeType"] == "text/markdown"


# -------- design_spec --------

def test_design_spec_returns_title_and_markdown(monkeypatch):
    captured = {}

    def fake_call(prompt, *, expect_json=True, max_tokens_hint=4000):
        captured["prompt"] = prompt
        return {
            "title": "사주 결혼 매칭 노트북",
            "spec_md": (
                "## 한 줄 정의\nNotebookLM 기반 사주 결혼 궁합 큐레이션\n\n"
                "## 스택 (환경 분석)\n- LLM: Gemini Flash (무료)\n"
                "- 인터페이스: NotebookLM (무료)\n"
            ),
        }

    import collector.workflows._spec as _sp
    monkeypatch.setattr(_sp, "call_workflow_llm", fake_call)
    best = {
        "idea": "사주 결혼 매칭 노트북",
        "rationale": "수요 큼",
        "search_keywords": ["사주 결혼", "명리 궁합"],
        "target_audience": "20-40대",
    }
    research = [
        {"query": "사주 결혼", "promoted": 5,
         "per_video": [{"video_id": "V1", "record_status": "promoted"}]},
    ]
    vault = [{
        "video_id": "V1", "record_status": "promoted",
        "summary": "결혼 궁합 핵심", "knowledge": ["오행 상생"],
        "rules": ["배우자 사주 비교"], "examples": [], "claims": [],
        "tags": ["사주"], "channel_id": "C1",
    }]
    out = _sp.design_spec(best, research, vault)
    assert out["title"] == "사주 결혼 매칭 노트북"
    assert "## 한 줄 정의" in out["spec_md"]
    # Prompt must include the gathered evidence
    assert "오행 상생" in captured["prompt"]
    assert "MVP" in captured["prompt"] and "NotebookLM" in captured["prompt"]
    assert "무료" in captured["prompt"]


def test_design_spec_handles_empty_idea(monkeypatch):
    import collector.workflows._spec as _sp
    out = _sp.design_spec({}, [], [])
    assert "(empty)" in out["title"] or out["title"] == "(empty)"


def test_design_spec_falls_back_to_all_results_when_keywords_dont_match(monkeypatch):
    """If the idea's search_keywords don't appear in research_results
    (e.g., user passed a one-off best_idea), still produce a spec from
    whatever research is provided."""
    import collector.workflows._spec as _sp
    captured = {}

    def fake_call(prompt, *, expect_json=True, max_tokens_hint=4000):
        captured["prompt"] = prompt
        return {"title": "X", "spec_md": "## 한 줄 정의\nok"}

    monkeypatch.setattr(_sp, "call_workflow_llm", fake_call)
    out = _sp.design_spec(
        {"idea": "X", "search_keywords": ["something_unmatched"]},
        [{"query": "totally_other", "promoted": 1,
          "per_video": [{"video_id": "VX", "record_status": "promoted"}]}],
        [{"video_id": "VX", "record_status": "promoted",
          "summary": "ok", "knowledge": ["k"]}],
    )
    assert out["spec_md"]
    # Evidence still made it in via the all-results fallback
    assert "k" in captured["prompt"]


def test_mcp_design_spec_tool_present():
    from collector.cli.mcp_server import _TOOLS
    assert "design_spec" in _TOOLS
    schema = _TOOLS["design_spec"]["schema"]
    assert "best_idea" in schema.get("required", [])
