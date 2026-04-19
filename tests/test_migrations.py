"""Migration tests: V9→v10 and v10→v2 7-schema."""
from __future__ import annotations

from collector.migrations.v9_to_v10 import migrate_v9_to_v10
from collector.migrations.youtube_to_v2 import decompose_to_v2


V9_SAMPLE = {
    "video_id": "ABC",
    "channel_id": "ch1",
    "title": "title",
    "published_at": "2026-04-19T00:00:00Z",
    "collected_at": "2026-04-19T00:00:10Z",
    "source_query": "단타",
    "language": "ko",
    "transcript_hash": "hash",
    "status": "PROCESSED",
    "archive_state": "ACTIVE",
    "retry_count": 0,
    "priority_score": 120,
    "payload_version": 2,
    "failure_reason_code": None,
    "failure_reason_detail": None,
    "llm_context": {"model_name": "gemini-1.5-flash", "temperature": 0.2, "prompt_version": "v1.3_saju"},
    "history": [{"at": "t", "note": "prev"}],
}


def test_v9_to_v10_adds_required_fields():
    p = migrate_v9_to_v10(V9_SAMPLE)
    assert p["schema_version"] == "10.0.0"
    assert p["source_key"] == "youtube:ABC"
    assert p["run_id"].startswith("run_")
    assert p["provenance"]["source_id"] == "youtube:ABC"
    assert p["provenance"]["segment_id"] == "youtube:ABC#full"
    assert p["stage_status"]["package"] == "completed"
    assert p["record_status"] == "promoted"
    assert p["confidence"] == "confirmed"
    assert p["history"] == [{"at": "t", "note": "prev"}]


def test_v9_to_v10_maps_failed_to_invalid():
    v = dict(V9_SAMPLE, status="FAILED")
    p = migrate_v9_to_v10(v)
    assert p["record_status"] == "invalid"
    assert p["confidence"] == "unverified"


def test_v9_to_v10_maps_sync_failed():
    v = dict(V9_SAMPLE, status="SYNC_FAILED")
    p = migrate_v9_to_v10(v)
    assert p["record_status"] == "promoted"
    assert p["stage_status"]["package"] == "failed"


def test_v9_to_v10_llm_context_defaults():
    v = dict(V9_SAMPLE)
    v.pop("llm_context")
    p = migrate_v9_to_v10(v)
    assert p["llm_context"]["input_tokens"] == 0
    assert p["llm_context"]["cost_usd"] == 0.0


def test_v9_to_v10_is_nondestructive():
    import copy
    original = copy.deepcopy(V9_SAMPLE)
    migrate_v9_to_v10(V9_SAMPLE)
    assert V9_SAMPLE == original


# ----- v10 → v2 -----

def _v10_fixture():
    return {
        "schema_version": "10.0.0",
        "source_key": "youtube:XYZ",
        "video_id": "XYZ",
        "channel_id": "CH",
        "title": "t",
        "published_at": "2026-01-01T00:00:00Z",
        "collected_at": "2026-01-01T00:00:10Z",
        "run_id": "run_x",
        "record_status": "promoted",
        "archive_state": "ACTIVE",
        "confidence": "confirmed",
        "reviewer": "auto",
        "transcript": "자막",
        "summary": "요약",
        "rules": ["규칙1", "규칙2"],
        "tags": ["t1"],
        "llm_context": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
        "failure_reason_detail": None,
    }


def test_decompose_emits_seven_schemas():
    out = decompose_to_v2(_v10_fixture())
    assert set(out.keys()) == {
        "SourceRecord", "SegmentRecord", "ClaimRecord",
        "NormalizedClaim", "ReviewRecord", "PromotedArtifact",
    }
    assert len(out["ClaimRecord"]) == 2
    assert len(out["NormalizedClaim"]) == 2
    assert len(out["ReviewRecord"]) == 2
    assert len(out["PromotedArtifact"]) == 1


def test_decompose_includes_provenance_chain():
    out = decompose_to_v2(_v10_fixture())
    claim = out["ClaimRecord"][0]
    norm = out["NormalizedClaim"][0]
    assert claim["source_id"] == "youtube:XYZ"
    assert claim["segment_id"] == "youtube:XYZ#full"
    assert norm["claim_id"] == claim["claim_id"]


def test_decompose_skips_promoted_artifact_when_not_promoted():
    v = _v10_fixture()
    v["record_status"] = "reviewed_inferred"
    v["confidence"] = "inferred"
    out = decompose_to_v2(v)
    assert out["PromotedArtifact"] == []


def test_decompose_all_records_have_schema_version_and_run_id():
    out = decompose_to_v2(_v10_fixture())
    for recs in out.values():
        for r in recs:
            assert r["schema_version"] == "2.0.0"
            assert r["run_id"] == "run_x"
