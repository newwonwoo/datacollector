"""Per-run snapshot writer (Master_01 §2.1).

Writes `runs/<run_id>.json` at the end of each pipeline invocation.
Complements events.jsonl by giving a single-file rollup of the run.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .events import EventLogger
from .payload import utcnow_iso


def save_run_snapshot(
    run_id: str,
    payloads: list[dict[str, Any]],
    *,
    query: str = "",
    root: Path = Path("runs"),
    logger: EventLogger | None = None,
) -> Path:
    """Write a single JSON file summarizing the run."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    per_video = []
    total_cost = 0.0
    total_in_tok = 0
    total_out_tok = 0
    for p in payloads:
        llm = p.get("llm_context") or {}
        total_cost += float(llm.get("cost_usd", 0) or 0)
        total_in_tok += int(llm.get("input_tokens", 0) or 0)
        total_out_tok += int(llm.get("output_tokens", 0) or 0)
        per_video.append({
            "source_key": p.get("source_key"),
            "video_id": p.get("video_id"),
            "title": p.get("title", ""),
            "record_status": p.get("record_status"),
            "confidence": p.get("confidence"),
            "failure_reason_code": p.get("failure_reason_code"),
            "rules_n": len(p.get("rules") or []),
            "priority_score": p.get("priority_score"),
            "channel_id": p.get("channel_id", ""),
        })

    def _counts(key: str, buckets: tuple[str, ...]) -> dict[str, int]:
        out: dict[str, int] = {b: 0 for b in buckets}
        for v in per_video:
            k = v.get(key) or ""
            if k in out:
                out[k] += 1
        return out

    snap = {
        "run_id": run_id,
        "query": query,
        "created_at": utcnow_iso(),
        "total_videos": len(payloads),
        "per_video": per_video,
        "record_status_counts": _counts("record_status", (
            "promoted", "invalid", "reviewed_confirmed", "reviewed_inferred",
            "reviewed_unverified", "reviewed_rejected",
            "collected", "extracted", "normalized",
        )),
        "confidence_counts": _counts("confidence", (
            "confirmed", "inferred", "unverified", "rejected",
        )),
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_in_tok,
        "total_output_tokens": total_out_tok,
    }

    out_path = root / f"{run_id}.json"
    fd, tmp = tempfile.mkstemp(prefix=".run.", dir=str(root), suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)

    if logger is not None:
        logger.log(
            entity_type="run",
            entity_id=run_id,
            from_status=None, to_status="snapshot_written",
            run_id=run_id,
            reason=f"runs/{run_id}.json",
            metrics={"videos": len(payloads), "cost_usd": round(total_cost, 6)},
        )
    return out_path
