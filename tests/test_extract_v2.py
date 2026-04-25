"""Extra coverage for the extract_generic_v2 wiring."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.adapters.llm_groq import _normalize_schema
from collector.chunking import reduce_outputs
from collector.events import EventLogger
from collector.payload import new_payload
from collector.pipeline import run_pipeline
from collector.services import build_mock_services
from collector.store import JSONStore


_LONG = "단타 매매 전략 요약입니다. 장중 고점 돌파 분할 진입, 손절 직전 저점, 익절 분할 수행."


# ---------- _normalize_schema ----------

def test_normalize_schema_fills_v2_defaults():
    out = _normalize_schema({"summary": "s", "rules": ["r"]})
    assert out["content_type"] == "mixed"        # fallback when missing
    assert out["llm_confidence"] == "medium"     # fallback when missing
    assert out["knowledge"] == [] and out["examples"] == []
    assert out["claims"] == [] and out["unclear"] == []


def test_normalize_schema_clamps_unknown_enums():
    out = _normalize_schema({
        "summary": "s", "rules": [],
        "content_type": "garbage",
        "llm_confidence": "totally-unsure",
    })
    assert out["content_type"] == "mixed"
    assert out["llm_confidence"] == "medium"


# ---------- reduce_outputs ----------

def test_reduce_outputs_majority_content_type():
    chunks = [
        {"summary": "a", "rules": [], "content_type": "concept"},
        {"summary": "b", "rules": [], "content_type": "concept"},
        {"summary": "c", "rules": [], "content_type": "howto"},
    ]
    out = reduce_outputs(chunks)
    assert out["content_type"] == "concept"


def test_reduce_outputs_tie_yields_mixed():
    chunks = [
        {"summary": "a", "rules": [], "content_type": "concept"},
        {"summary": "b", "rules": [], "content_type": "howto"},
    ]
    out = reduce_outputs(chunks)
    assert out["content_type"] == "mixed"


def test_reduce_outputs_min_confidence():
    chunks = [
        {"summary": "a", "rules": [], "llm_confidence": "high"},
        {"summary": "b", "rules": [], "llm_confidence": "low"},
        {"summary": "c", "rules": [], "llm_confidence": "medium"},
    ]
    out = reduce_outputs(chunks)
    assert out["llm_confidence"] == "low"


def test_reduce_outputs_dedups_v2_lists():
    chunks = [
        {"summary": "a", "rules": [], "knowledge": ["k1", "k2"], "claims": ["c1"]},
        {"summary": "b", "rules": [], "knowledge": ["k2", "k3"], "claims": ["c1", "c2"]},
    ]
    out = reduce_outputs(chunks)
    assert out["knowledge"] == ["k1", "k2", "k3"]
    assert out["claims"] == ["c1", "c2"]


# ---------- normalize gate ----------

def _payload(vid="V123"):
    return new_payload(video_id=vid, run_id="r1")


def test_normalize_passes_with_only_knowledge(tmp_path):
    """Concept video: empty rules but rich knowledge → must promote."""
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("CONCEPT0001")
    services = build_mock_services(
        captions_map={"CONCEPT0001": {"source": "manual", "text": "text"}},
        llm_script=[{
            "summary": _LONG, "rules": [], "tags": ["t"],
            "knowledge": ["A는 B다", "C는 D다"],
            "notes_md": "## 개념\n자세히",
        }],
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["record_status"] != "invalid"
    assert p["knowledge"] == ["A는 B다", "C는 D다"]


def test_normalize_archives_ad_content(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("ADVID00001")
    services = build_mock_services(
        captions_map={"ADVID00001": {"source": "manual", "text": "text"}},
        llm_script=[{
            "summary": _LONG, "rules": ["r"], "tags": ["t"],
            "content_type": "ad",
        }],
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["record_status"] == "invalid"
    assert p["archive_state"] == "ARCHIVED"
    assert p["failure_reason_code"] == "AD_CHAT_AUTO_SKIP"


def test_normalize_archives_chat_content(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("CHATVID0001")
    services = build_mock_services(
        captions_map={"CHATVID0001": {"source": "manual", "text": "text"}},
        llm_script=[{
            "summary": _LONG, "rules": ["r"], "tags": ["t"],
            "content_type": "chat",
        }],
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["record_status"] == "invalid"
    assert p["archive_state"] == "ARCHIVED"


# ---------- review llm_confidence ----------

def test_review_low_llm_confidence_blocks_confirm(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("LOWCONF0001")
    services = build_mock_services(
        captions_map={"LOWCONF0001": {"source": "manual", "text": "text"}},
        llm_script=[{
            "summary": _LONG, "rules": ["r1"], "tags": ["t1"],
            "llm_confidence": "low",
        }],
        similarity=0.95,  # would normally confirm
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    # Low llm_confidence → never auto-confirmed even with high cosine
    assert p["confidence"] in ("inferred", "unverified")


def test_review_high_llm_confidence_relaxes_threshold(tmp_path):
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    p = _payload("HIGHCONF001")
    services = build_mock_services(
        captions_map={"HIGHCONF001": {"source": "manual", "text": "text"}},
        llm_script=[{
            "summary": _LONG, "rules": ["r1"], "tags": ["t1"],
            "llm_confidence": "high",
        }],
        similarity=0.56,  # below default 0.60 but above relaxed 0.55
    )
    run_pipeline(p, services, store, logger, use_lock=False)
    assert p["confidence"] == "confirmed"


# ---------- vault rendering ----------

def test_vault_renders_v2_sections():
    from collector.vault import render_note
    p = new_payload(video_id="V001", run_id="r1", title="t")
    p["summary"] = "요약"
    p["knowledge"] = ["개념1"]
    p["rules"] = ["행동1"]
    p["examples"] = ["사례1"]
    p["claims"] = ["주장1"]
    p["unclear"] = ["불명확1"]
    p["content_type"] = "concept"
    p["llm_confidence"] = "high"
    md = render_note(p)
    assert "## 핵심 개념" in md and "개념1" in md
    assert "## 행동 지침" in md
    assert "## 사례" in md and "사례1" in md
    assert "## 화자의 주장" in md and "주장1" in md
    assert "## 명확하지 않은 부분" in md and "불명확1" in md
    assert "concept" in md and "high" in md


def test_vault_skips_empty_v2_sections():
    from collector.vault import render_note
    p = new_payload(video_id="V002", run_id="r1")
    p["summary"] = "ok"
    p["rules"] = ["r"]
    md = render_note(p)
    # No empty sections
    assert "## 사례" not in md
    assert "## 화자의 주장" not in md
    assert "## 명확하지 않은 부분" not in md


# ---------- rule-based fallback ----------

def test_rule_based_extract_fallback_when_chain_all_429(monkeypatch):
    """When every LLM in the chain returns HTTP_429, the run still
    completes — a rule-based dict is emitted from the transcript so
    nothing is lost. The record will land in unverified/inferred and
    can be re-extracted after quota reset."""
    from collector.cli.run import _rule_based_extract

    out = _rule_based_extract(
        "단타 매매 전략의 기본은 거래량 확인이다. 손절 직전 저점은 핵심.",
        "HTTP_429",
    )
    assert out["llm_confidence"] == "low"
    assert out["content_type"] == "mixed"
    assert "단타" in out["notes_md"]
    assert len(out["summary"]) >= 30
    assert any("HTTP_429" in u for u in out["unclear"])
    # Tags came from noun extractor, not LLM
    assert isinstance(out["tags"], list)


def test_rule_based_extract_handles_empty_transcript():
    from collector.cli.run import _rule_based_extract
    out = _rule_based_extract("", "HTTP_429")
    assert len(out["summary"]) >= 30  # synthetic placeholder
    assert out["notes_md"] == ""


def test_llm_chain_falls_back_to_rule_based_after_all_quota(monkeypatch):
    """End-to-end: chain of two adapters both return 429 → llm_extract
    returns the rule-based dict instead of raising."""
    from collector.services import MockError
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt_fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    from collector.cli.run import _real_services_or_none
    services = _real_services_or_none()

    def always_429(*a, **kw):
        raise MockError("HTTP_429", "limit")

    for a in services.llm_extract.adapters:
        a.extract = always_429

    out = services.llm_extract("자막 본문 단타 거래량 손절 익절 분할", 0)
    # Rule-based fallback shape — never raises
    assert out["llm_confidence"] == "low"
    assert out["content_type"] == "mixed"
    assert "단타" in out["notes_md"]


# ---------- adapter-aware chunking ----------

def test_adapter_max_chars_attribute_on_each():
    from collector.adapters.llm_groq import GroqAdapter
    from collector.adapters.llm_gemini import GeminiAdapter
    from collector.adapters.llm_anthropic import AnthropicAdapter
    g70 = GroqAdapter("k", model="llama-3.3-70b-versatile")
    g8  = GroqAdapter("k", model="llama-3.1-8b-instant")
    gm  = GeminiAdapter("k")
    an  = AnthropicAdapter("k")
    # 8b is the bottleneck — must be the smallest
    assert g8.max_chars_per_request < g70.max_chars_per_request
    assert g8.max_chars_per_request <= 5_000
    assert gm.max_chars_per_request >= 50_000
    assert an.max_chars_per_request >= 50_000


def test_chain_uses_smallest_adapter_max_chars(monkeypatch):
    """When the chain is gemini → groq-70b → groq-8b, chunking should
    follow groq-8b (the smallest, ~4.5k)."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt_fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza_fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COLLECTOR_LLM", raising=False)

    from collector.cli.run import _real_services_or_none
    from collector.stages import _smallest_chain_max_chars
    services = _real_services_or_none()
    smallest = _smallest_chain_max_chars(services)
    assert smallest is not None
    assert smallest <= 5_000  # groq-8b's bucket


def test_chunk_default_size_fits_groq_8b():
    """The default chunking constants must fit even Groq llama-3.1-8b's
    6k-TPM cap (so a 5500-char transcript splits into ≥2 chunks at the
    default and each chunk is ≤ 4500 chars)."""
    from collector.chunking import MAX_CHARS_SINGLE, CHUNK_CHARS, chunk
    assert MAX_CHARS_SINGLE <= 6_000
    assert CHUNK_CHARS <= 4_500
    body = "한" * 5_500
    pieces = chunk(body)
    assert len(pieces) >= 2
    assert all(len(p) <= CHUNK_CHARS + 200 for p in pieces)  # +200 break-window slack
