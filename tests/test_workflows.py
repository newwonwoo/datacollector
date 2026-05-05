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

def test_research_batch_iterates_keyword_x_channel_matrix(monkeypatch, tmp_path):
    """target_channel_ids 가 N 개면 (keywords × channels) 매트릭스로 호출."""
    import collector.workflows._batch as _rb
    calls: list[tuple[str, str | None]] = []

    def fake_run_query(kw, **kw_args):
        calls.append((kw, kw_args.get("target_channel_id")))
        return {"query": kw, "promoted": 1, "processed": 1, "candidates": 1,
                "skipped_duplicates": 0, "per_video": []}

    monkeypatch.setattr("collector.cli.run.run_query", fake_run_query)
    out = _rb.research_batch(
        ["k1", "k2"],
        target_channel_ids=["UCa", "UCb"],
        max_concurrency=1,
        data_store_root=tmp_path / "ds", logs_root=tmp_path / "lg",
    )
    pairs = sorted([(kw, ch) for kw, ch in calls])
    assert pairs == [("k1", "UCa"), ("k1", "UCb"), ("k2", "UCa"), ("k2", "UCb")]
    assert len(out) == 4
    for r in out:
        assert "target_channel_id" in r


def test_research_batch_channels_only_no_keywords(monkeypatch, tmp_path):
    """키워드 없이 채널만 주어진 경우 → 빈 키워드 1개 × N 채널 = N sub-runs."""
    import collector.workflows._batch as _rb
    calls = []

    def fake_run_query(kw, **kw_args):
        calls.append((kw, kw_args.get("target_channel_id")))
        return {"query": kw, "promoted": 0, "processed": 0,
                "candidates": 0, "skipped_duplicates": 0, "per_video": []}

    monkeypatch.setattr("collector.cli.run.run_query", fake_run_query)
    _rb.research_batch(
        [],
        target_channel_ids=["UCa", "UCb", "UCc"],
        max_concurrency=1,
        data_store_root=tmp_path / "ds", logs_root=tmp_path / "lg",
    )
    assert sorted(c[1] for c in calls) == ["UCa", "UCb", "UCc"]
    assert all(c[0] == "" for c in calls)  # empty keyword


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


def test_design_spec_includes_user_notes_when_provided(monkeypatch):
    """`extra_notes` should appear in the prompt verbatim (truncated)
    so the LLM can ground the spec in the user-supplied brief too."""
    import collector.workflows._spec as _sp
    captured = {}

    def fake_call(prompt, *, expect_json=True, max_tokens_hint=4000):
        captured["prompt"] = prompt
        return {"title": "X", "spec_md": "## 한 줄 정의\nok"}

    monkeypatch.setattr(_sp, "call_workflow_llm", fake_call)
    notes = "사용자 도메인 메모: 핵심 페르소나는 30대 직장인."
    out = _sp.design_spec(
        {"idea": "X", "search_keywords": ["k"]},
        [],
        [],
        extra_notes=notes,
    )
    assert out["spec_md"]
    assert "user_notes" in captured["prompt"]
    assert "30대 직장인" in captured["prompt"]


def test_design_spec_truncates_huge_user_notes(monkeypatch):
    import collector.workflows._spec as _sp
    captured = {}

    def fake_call(prompt, *, expect_json=True, max_tokens_hint=4000):
        captured["prompt"] = prompt
        return {"title": "X", "spec_md": "## 한 줄 정의\nok"}

    monkeypatch.setattr(_sp, "call_workflow_llm", fake_call)
    huge = "A" * 50000
    _sp.design_spec({"idea": "X"}, [], [], extra_notes=huge)
    assert captured["prompt"].count("A") <= _sp._USER_NOTES_MAX_CHARS + 100


def test_mcp_design_spec_tool_present():
    from collector.cli.mcp_server import _TOOLS
    assert "design_spec" in _TOOLS
    schema = _TOOLS["design_spec"]["schema"]
    assert "best_idea" in schema.get("required", [])
    assert "user_notes" in schema.get("properties", {})


def test_workflow_cli_design_accepts_notes_file(tmp_path, monkeypatch):
    """--notes-file content should be threaded through to design_spec."""
    import collector.cli.workflow as wf

    notes_path = tmp_path / "notes.md"
    notes_path.write_text("FREE_TEXT_MARKER_42", encoding="utf-8")

    ideas_path = tmp_path / "ideas.json"
    ideas_path.write_text(json.dumps({"ideas": [
        {"idea": "X", "search_keywords": ["k"], "rationale": "r",
         "target_audience": "t"}
    ]}), encoding="utf-8")

    research_path = tmp_path / "research.json"
    research_path.write_text(json.dumps({"results": []}), encoding="utf-8")

    out_path = tmp_path / "spec.md"

    captured = {}

    def fake_design_spec(best_idea, research, vault, extra_notes=None):
        captured["extra_notes"] = extra_notes
        return {"title": "T", "spec_md": "## 한 줄 정의\nok"}

    monkeypatch.setattr(wf, "design_spec", fake_design_spec)
    rc = wf.main([
        "design",
        "--ideas-file", str(ideas_path),
        "--research-file", str(research_path),
        "--data-store", str(tmp_path / "no_such_dir"),
        "--notes-file", str(notes_path),
        "--out", str(out_path),
    ])
    assert rc == 0
    assert "FREE_TEXT_MARKER_42" in (captured.get("extra_notes") or "")
    assert out_path.exists()


# -------- _cmd_full resume + failure isolation --------

def _stub_full_deps(monkeypatch, *, ideas=None, research=None,
                    synth=None, fail_synth=False,
                    fail_design=False, fail_export=False,
                    export_path="exports/notebook.md"):
    """Replace every external in workflow.py with a tiny stub so we can
    drive _cmd_full deterministically without LLMs / yt-dlp / network."""
    import collector.cli.workflow as wf

    calls: dict[str, int] = {"brain": 0, "research": 0, "synth": 0,
                             "design": 0, "export": 0}

    def fake_brainstorm(**kw):
        calls["brain"] += 1
        return ideas if ideas is not None else [
            {"idea": "X", "search_keywords": ["k"], "rationale": "r",
             "target_audience": "t"}
        ]

    def fake_research(keywords, **kw):
        calls["research"] += 1
        return research if research is not None else [
            {"query": "k", "processed": 1, "promoted": 1,
             "per_video": [{"video_id": "V1", "record_status": "promoted"}]}
        ]

    def fake_synth(ideas_, research_):
        calls["synth"] += 1
        if fail_synth:
            raise RuntimeError("simulated quota exhaustion")
        return synth if synth is not None else {
            "best_idea_index": 0, "score_per_idea": [1.0],
            "reasoning": "ok", "recommended_next_steps": [],
        }

    def fake_design(best, research_, vault, extra_notes=None):
        calls["design"] += 1
        if fail_design:
            raise RuntimeError("simulated design failure")
        return {"title": "T", "spec_md": "## 한 줄 정의\nok"}

    def fake_export(**kw):
        calls["export"] += 1
        if fail_export:
            raise RuntimeError("simulated export failure")
        return Path(kw.get("out_dir", ".")) / "notebook.md"

    monkeypatch.setattr(wf, "brainstorm_topics", fake_brainstorm)
    monkeypatch.setattr(wf, "research_batch", fake_research)
    monkeypatch.setattr(wf, "synthesize", fake_synth)
    monkeypatch.setattr(wf, "design_spec", fake_design)
    monkeypatch.setattr(wf, "export_notebook", fake_export)
    return calls


def test_cmd_full_step3_failure_preserves_step1_2_and_runs_step5(tmp_path, monkeypatch):
    """When synthesize blows up (real-world: all LLMs at 429),
    step1+step2 outputs must be on disk and step 5 (export) must
    still run from the vault."""
    import collector.cli.workflow as wf

    calls = _stub_full_deps(monkeypatch, fail_synth=True)
    out_dir = tmp_path / "run"

    rc = wf.main([
        "full", "--domain", "사주", "--count", "1",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(out_dir),
    ])
    assert rc == 0  # graceful, not a crash
    assert (out_dir / "step1_ideas.json").exists()
    assert (out_dir / "step2_research.json").exists()
    assert not (out_dir / "step3_synthesize.json").exists()
    assert calls["export"] == 1  # step 5 still ran despite step 3 failure
    assert calls["design"] == 0  # step 4 skipped (no best idea)


def test_cmd_full_resumes_from_saved_step1_step2(tmp_path, monkeypatch):
    """Re-running `full` after a step-3 failure should NOT re-call
    brainstorm / research_batch — the saved JSON files are reused."""
    import collector.cli.workflow as wf

    out_dir = tmp_path / "run"
    out_dir.mkdir(parents=True)

    # Pre-seed the artifacts a previous (failed) run would have left.
    (out_dir / "step1_ideas.json").write_text(json.dumps({
        "domain": "사주",
        "ideas": [{"idea": "Y", "search_keywords": ["k"], "rationale": "r",
                   "target_audience": "t"}],
    }), encoding="utf-8")
    (out_dir / "step2_research.json").write_text(json.dumps({
        "results": [{"query": "k", "processed": 1, "promoted": 1,
                     "per_video": [{"video_id": "V", "record_status": "promoted"}]}]
    }), encoding="utf-8")

    calls = _stub_full_deps(monkeypatch)  # synth succeeds this time

    rc = wf.main([
        "full", "--domain", "사주", "--count", "1",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(out_dir),
    ])
    assert rc == 0
    assert calls["brain"] == 0       # reused
    assert calls["research"] == 0    # reused
    assert calls["synth"] == 1       # actually ran this time
    assert calls["design"] == 1
    assert calls["export"] == 1
    assert (out_dir / "step3_synthesize.json").exists()
    assert (out_dir / "step4_spec_0.md").exists()


def test_cmd_full_restart_flag_re_runs_everything(tmp_path, monkeypatch):
    import collector.cli.workflow as wf

    out_dir = tmp_path / "run"
    out_dir.mkdir(parents=True)
    # Seed cache that --restart should ignore.
    (out_dir / "step1_ideas.json").write_text(json.dumps({
        "domain": "x", "ideas": [{"idea": "STALE", "search_keywords": ["k"]}],
    }), encoding="utf-8")

    calls = _stub_full_deps(monkeypatch)
    rc = wf.main([
        "full", "--domain", "사주", "--count", "1",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(out_dir),
        "--restart",
    ])
    assert rc == 0
    assert calls["brain"] == 1  # re-ran despite cache present


def test_cmd_full_brainstorm_failure_aborts_with_clear_message(tmp_path, monkeypatch, capsys):
    """If step 1 fails, no point trying step 2-5 — exit non-zero so
    callers/CI can detect it, but never crash with a traceback."""
    import collector.cli.workflow as wf

    def fake_brain(**kw):
        raise RuntimeError("brainstorm boom")

    monkeypatch.setattr(wf, "brainstorm_topics", fake_brain)
    out_dir = tmp_path / "run"
    rc = wf.main([
        "full", "--domain", "x", "--count", "1",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(out_dir),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "STEP 1 FAILED" in err


# -------- NotebookLM driver --------

def test_notebooklm_unavailable_when_cli_missing(monkeypatch):
    import collector.adapters.notebooklm as nb
    monkeypatch.setattr(nb.shutil, "which", lambda _name: None)
    assert nb.is_available() is False
    with pytest.raises(nb.NotebookLMUnavailable):
        nb._run(["foo"], 1.0)


def test_notebooklm_create_parses_id(monkeypatch):
    import collector.adapters.notebooklm as nb

    class _R:
        returncode = 0
        stdout = '{"id": "abc-12345678", "title": "x"}'
        stderr = ""

    monkeypatch.setattr(nb.shutil, "which", lambda _n: "/usr/local/bin/nlm")
    monkeypatch.setattr(nb.subprocess, "run", lambda *a, **k: _R())
    nb_id = nb.create_notebook("foo")
    assert nb_id == "abc-12345678"


def test_notebooklm_create_handles_bare_id_output(monkeypatch):
    import collector.adapters.notebooklm as nb

    class _R:
        returncode = 0
        stdout = "Created notebook\nnb-abcdef12\n"
        stderr = ""

    monkeypatch.setattr(nb.shutil, "which", lambda _n: "/usr/local/bin/nlm")
    monkeypatch.setattr(nb.subprocess, "run", lambda *a, **k: _R())
    assert nb.create_notebook("foo") == "nb-abcdef12"


def test_notebooklm_query_returns_stripped_stdout(monkeypatch):
    import collector.adapters.notebooklm as nb

    class _R:
        returncode = 0
        stdout = "  ## hello\n\nworld  \n"
        stderr = ""

    monkeypatch.setattr(nb.shutil, "which", lambda _n: "/usr/local/bin/nlm")
    monkeypatch.setattr(nb.subprocess, "run", lambda *a, **k: _R())
    assert nb.query("nb-1", "p") == "## hello\n\nworld"


def test_notebooklm_subprocess_failure_raises(monkeypatch):
    import collector.adapters.notebooklm as nb

    class _R:
        returncode = 2
        stdout = ""
        stderr = "auth expired"

    monkeypatch.setattr(nb.shutil, "which", lambda _n: "/usr/local/bin/nlm")
    monkeypatch.setattr(nb.subprocess, "run", lambda *a, **k: _R())
    with pytest.raises(nb.NotebookLMUnavailable, match="auth expired"):
        nb.create_notebook("foo")


def test_notebooklm_add_source_uses_file_flag(tmp_path, monkeypatch):
    """Regression for the 'unexpected extra argument' error: nlm CLI
    requires --file, not a positional path."""
    import collector.adapters.notebooklm as nb
    captured: dict = {}

    class _R:
        returncode = 0
        stdout = '{"id": "src-1"}'
        stderr = ""

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _R()

    monkeypatch.setattr(nb.shutil, "which", lambda _n: "/usr/local/bin/nlm")
    monkeypatch.setattr(nb.subprocess, "run", fake_run)
    p = tmp_path / "bundle.md"
    p.write_text("# x", encoding="utf-8")
    nb.add_source("nb-XYZ", p)
    assert captured["argv"][1:] == ["source", "add", "nb-XYZ", "--file", str(p), "--wait"]


def test_notebooklm_remove_source_uses_delete_subcommand(monkeypatch):
    import collector.adapters.notebooklm as nb
    captured: dict = {}

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _R()

    monkeypatch.setattr(nb.shutil, "which", lambda _n: "/usr/local/bin/nlm")
    monkeypatch.setattr(nb.subprocess, "run", fake_run)
    nb.remove_source("ignored-nb-id", "src-42")
    # nlm uses `source delete SOURCE_ID --confirm` — no notebook id needed.
    assert captured["argv"][1:] == ["source", "delete", "src-42", "--confirm"]


# -------- _nb_specs --------

def test_nb_specs_parse_modes_validates():
    from collector.workflows._nb_specs import parse_modes
    assert parse_modes("") == []
    assert parse_modes("monetize") == ["monetize"]
    assert parse_modes("monetize,textbook") == ["monetize", "textbook"]
    assert parse_modes("monetize, monetize, textbook") == ["monetize", "textbook"]
    with pytest.raises(ValueError):
        parse_modes("not-a-mode")


def test_nb_specs_load_prompt_returns_template():
    from collector.workflows._nb_specs import load_prompt, MODES
    for m in MODES:
        body = load_prompt(m)
        assert body.strip(), f"prompt for {m} is empty"


def test_nb_specs_generate_specs_writes_one_file_per_mode(tmp_path, monkeypatch):
    from collector.workflows import _nb_specs
    bundle = tmp_path / "bundle.md"
    bundle.write_text("# raw\nhi", encoding="utf-8")

    calls = {"create": 0, "add": 0, "query": []}

    def fake_create(title, **k):
        calls["create"] += 1
        return "nb-XYZ"

    def fake_add(nb_id, path, **k):
        calls["add"] += 1
        return "src-1"

    def fake_query(nb_id, prompt, **k):
        calls["query"].append(prompt[:30])
        return f"# answer for {prompt[:20]}"

    monkeypatch.setattr(_nb_specs.notebooklm, "create_notebook", fake_create)
    monkeypatch.setattr(_nb_specs.notebooklm, "add_source", fake_add)
    monkeypatch.setattr(_nb_specs.notebooklm, "query", fake_query)

    out_dir = tmp_path / "out"
    written = _nb_specs.generate_specs(
        domain="X", bundle_path=bundle,
        modes=["monetize", "summary"], out_dir=out_dir,
    )
    assert calls["create"] == 1
    assert calls["add"] == 1
    assert len(calls["query"]) == 2
    assert set(written.keys()) == {"monetize", "summary"}
    for mode, p in written.items():
        body = p.read_text(encoding="utf-8")
        assert "mode: " + mode in body
        assert "notebook: nb-XYZ" in body


def test_nb_specs_generate_specs_no_modes_returns_empty(tmp_path, monkeypatch):
    from collector.workflows import _nb_specs
    bundle = tmp_path / "bundle.md"
    bundle.write_text("hi", encoding="utf-8")
    written = _nb_specs.generate_specs(
        domain="X", bundle_path=bundle, modes=[], out_dir=tmp_path / "out",
    )
    assert written == {}


def test_nb_specs_propagates_unavailable(tmp_path, monkeypatch):
    """Driver failure surfaces as NotebookLMUnavailable so the
    calling workflow can fall back instead of crashing."""
    from collector.workflows import _nb_specs
    from collector.adapters.notebooklm import NotebookLMUnavailable
    bundle = tmp_path / "bundle.md"
    bundle.write_text("hi", encoding="utf-8")

    def boom(*a, **k):
        raise NotebookLMUnavailable("nlm missing")

    monkeypatch.setattr(_nb_specs.notebooklm, "create_notebook", boom)
    with pytest.raises(NotebookLMUnavailable):
        _nb_specs.generate_specs(
            domain="X", bundle_path=bundle,
            modes=["monetize"], out_dir=tmp_path / "out",
        )


# -------- export_raw_bundle --------

def test_export_raw_bundle_includes_transcript_field(tmp_path):
    from collector.workflows.export import export_raw_bundle
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "k1__v1.json").write_text(json.dumps({
        "source_key": "k1__v1", "video_id": "v1", "channel_id": "C1",
        "title": "T1", "record_status": "promoted",
        "transcript": "원본 자막 텍스트 ABC",
        "collected_at": "2026-05-04T00:00:00Z",
    }), encoding="utf-8")
    out_dir = tmp_path / "exp"
    p = export_raw_bundle(data_store_root=ds, out_dir=out_dir,
                          only_promoted=True, label="사주")
    body = p.read_text(encoding="utf-8")
    assert "원본 자막 텍스트 ABC" in body
    assert "T1" in body
    assert p.name.startswith("notebook_raw_")


def test_export_raw_bundle_skips_non_promoted_by_default(tmp_path):
    from collector.workflows.export import export_raw_bundle
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "good.json").write_text(json.dumps({
        "source_key": "good", "video_id": "v1", "record_status": "promoted",
        "transcript": "GOOD_TX",
    }), encoding="utf-8")
    (ds / "bad.json").write_text(json.dumps({
        "source_key": "bad", "video_id": "v2", "record_status": "invalid",
        "transcript": "BAD_TX",
    }), encoding="utf-8")
    p = export_raw_bundle(data_store_root=ds, out_dir=tmp_path / "out",
                          only_promoted=True)
    body = p.read_text(encoding="utf-8")
    assert "GOOD_TX" in body
    assert "BAD_TX" not in body


# -------- _cmd_full integration with --modes --------

def test_cmd_full_modes_calls_generate_specs(tmp_path, monkeypatch):
    """When --modes is given and NotebookLM is reachable, _cmd_full
    runs the new step 6 and produces spec_NB_*.md files."""
    import collector.cli.workflow as wf

    out_dir = tmp_path / "run"
    calls = _stub_full_deps(monkeypatch)

    captured: dict = {}

    def fake_export_raw(**kw):
        p = Path(kw["out_dir"]) / "notebook_raw_X.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# raw bundle", encoding="utf-8")
        captured["raw"] = p
        return p

    def fake_generate_specs(domain, bundle_path, modes, out_dir, **kw):
        captured["modes"] = list(modes)
        captured["bundle"] = bundle_path
        written = {}
        for m in modes:
            p = Path(out_dir) / f"spec_NB_{m}.md"
            p.write_text(f"# {m} spec", encoding="utf-8")
            written[m] = p
        return written

    monkeypatch.setattr(wf, "export_raw_bundle", fake_export_raw)
    # patch the lazy-imported generate_specs
    import collector.workflows._nb_specs as _nbs
    monkeypatch.setattr(_nbs, "generate_specs", fake_generate_specs)

    rc = wf.main([
        "full", "--domain", "사주", "--count", "1",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(out_dir),
        "--modes", "monetize,summary",
    ])
    assert rc == 0
    assert captured["modes"] == ["monetize", "summary"]
    assert (out_dir / "spec_NB_monetize.md").exists()
    assert (out_dir / "spec_NB_summary.md").exists()


def test_cmd_full_falls_back_when_nlm_unavailable(tmp_path, monkeypatch, capsys):
    import collector.cli.workflow as wf
    from collector.adapters.notebooklm import NotebookLMUnavailable

    out_dir = tmp_path / "run"
    _stub_full_deps(monkeypatch)

    def fake_export_raw(**kw):
        p = Path(kw["out_dir"]) / "raw.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hi", encoding="utf-8")
        return p

    def boom(*a, **kw):
        raise NotebookLMUnavailable("nlm not installed")

    monkeypatch.setattr(wf, "export_raw_bundle", fake_export_raw)
    import collector.workflows._nb_specs as _nbs
    monkeypatch.setattr(_nbs, "generate_specs", boom)

    rc = wf.main([
        "full", "--domain", "x", "--count", "1",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(out_dir),
        "--modes", "monetize",
    ])
    assert rc == 0  # graceful: cheap-LLM step4 spec is still there
    err = capsys.readouterr().err
    assert "NotebookLM unavailable" in err
    assert (out_dir / "step4_spec_0.md").exists()  # cheap-LLM spec preserved


def test_cmd_full_no_modes_skips_notebooklm_step(tmp_path, monkeypatch):
    import collector.cli.workflow as wf
    out_dir = tmp_path / "run"
    _stub_full_deps(monkeypatch)

    called = {"raw": False, "specs": False}

    def fake_export_raw(**kw):
        called["raw"] = True
        return Path(kw["out_dir"]) / "raw.md"

    def fake_specs(*a, **kw):
        called["specs"] = True
        return {}

    monkeypatch.setattr(wf, "export_raw_bundle", fake_export_raw)
    import collector.workflows._nb_specs as _nbs
    monkeypatch.setattr(_nbs, "generate_specs", fake_specs)

    rc = wf.main([
        "full", "--domain", "x", "--count", "1",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(out_dir),
    ])
    assert rc == 0
    assert called["raw"] is False
    assert called["specs"] is False
