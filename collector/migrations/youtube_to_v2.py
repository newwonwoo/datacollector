"""Decompose a v10 Payload into v2 platform 7-schema records.

v2 contracts (see docs/data_contract_v2.md):
  SourceRecord, SegmentRecord, ClaimRecord, NormalizedClaim,
  ReviewRecord, ConflictRecord, PromotedArtifact

ConflictRecord is domain-specific and not auto-emitted here.
"""
from __future__ import annotations

import uuid
from typing import Any

V2_SCHEMA_VERSION = "2.0.0"


def _base_meta(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": V2_SCHEMA_VERSION,
        "project_id": "youtube-saju",
        "domain": "trading_strategy",
        "run_id": payload["run_id"],
        "created_at": payload.get("collected_at", ""),
        "updated_at": payload.get("collected_at", ""),
    }


def decompose_to_v2(payload: dict[str, Any]) -> dict[str, list[dict]]:
    """Return dict of schema_name → list of records. Always lists for uniform iteration."""
    meta = _base_meta(payload)
    source_key = payload["source_key"]
    segment_id = f"{source_key}#full"

    source = {
        **meta,
        "source_id": source_key,
        "source_type": "youtube",
        "origin_url": f"https://www.youtube.com/watch?v={payload['video_id']}",
        "collected_at": payload.get("collected_at", ""),
        "title": payload.get("title", ""),
        "description": "",
        "body": payload.get("transcript", ""),
        "published_at": payload.get("published_at", ""),
        "creator": payload.get("channel_id", ""),
    }

    segment = {
        **meta,
        "segment_id": segment_id,
        "source_id": source_key,
        "segment_type": "transcript_full",
        "text": payload.get("transcript", ""),
        "start": 0,
        "end": 0,
    }

    claims = []
    normalized = []
    reviews = []
    for idx, rule in enumerate(payload.get("rules") or []):
        claim_id = f"{source_key}#claim_{idx:03d}"
        claims.append({
            **meta,
            "claim_id": claim_id,
            "source_id": source_key,
            "segment_id": segment_id,
            "claim_type": "entry_rule",
            "raw_quote": rule,
            "strategy_family": "",
            "parsed_values": {},
            "tags": payload.get("tags", []),
            "creator": payload.get("channel_id", ""),
        })
        normalized_id = f"{claim_id}#norm"
        normalized.append({
            **meta,
            "normalized_claim_id": normalized_id,
            "claim_id": claim_id,
            "canonical_term": rule,  # passthrough (canonicalization is domain work)
            "confidence": payload.get("confidence", "unverified"),
            "canonical_family": "",
            "normalizer_version": "passthrough-v1",
        })
        reviews.append({
            **meta,
            "review_id": f"{normalized_id}#rev_{uuid.uuid4().hex[:6]}",
            "normalized_claim_id": normalized_id,
            "reviewer": payload.get("reviewer", "none"),
            "decision": _decision_from_record(payload),
            "updated_confidence": payload.get("confidence", "unverified"),
            "reason": payload.get("failure_reason_detail") or "",
        })

    promoted = []
    if payload.get("record_status") == "promoted":
        promoted.append({
            **meta,
            "artifact_id": f"{source_key}#artifact",
            "artifact_type": "youtube_note_markdown",
            "source_claims": [c["claim_id"] for c in claims],
            "review_refs": [r["review_id"] for r in reviews],
            "status": "promoted",
        })

    return {
        "SourceRecord": [source],
        "SegmentRecord": [segment],
        "ClaimRecord": claims,
        "NormalizedClaim": normalized,
        "ReviewRecord": reviews,
        "PromotedArtifact": promoted,
    }


def _decision_from_record(payload: dict[str, Any]) -> str:
    rs = payload.get("record_status", "")
    if rs == "promoted":
        return "approved"
    if rs.startswith("reviewed_"):
        return rs.replace("reviewed_", "")
    if rs == "invalid":
        return "rejected"
    return "pending"
