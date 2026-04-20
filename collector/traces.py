"""Per-run stage timeline, derived from events.jsonl (Master_01 §8)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0


def build_trace(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse events into one trace per run_id.

    Each trace: {run_id, start, end, total_ms, stages: {stage: {start, end, ms, final_status}}}
    """
    by_run: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "run_id": None,
        "start": None,
        "end": None,
        "stages": defaultdict(lambda: {"start": None, "end": None, "final_status": None}),
    })
    for e in events:
        rid = e.get("run_id")
        if not rid:
            continue
        slot = by_run[rid]
        slot["run_id"] = rid
        ts = e.get("recorded_at", "")
        t = _epoch(ts)
        if e.get("entity_type") == "run":
            if e.get("to_status") == "running" and slot["start"] is None:
                slot["start"] = ts
            elif e.get("to_status") in ("completed", "partially_completed", "failed"):
                slot["end"] = ts
                slot["run_final"] = e.get("to_status")
        elif e.get("entity_type") == "stage":
            # entity_id = "source_key:stage"
            stage = (e.get("entity_id") or "").split(":")[-1]
            if not stage:
                continue
            sr = slot["stages"][stage]
            if e.get("to_status") == "started" and sr["start"] is None:
                sr["start"] = ts
            elif e.get("to_status") in ("completed", "failed", "skipped"):
                sr["end"] = ts
                sr["final_status"] = e.get("to_status")

    out = []
    for rid, slot in by_run.items():
        start_t = _epoch(slot["start"]) if slot["start"] else 0
        end_t = _epoch(slot["end"]) if slot["end"] else 0
        stages_out = {}
        for name, sr in slot["stages"].items():
            s, ee = _epoch(sr["start"]) if sr["start"] else 0, _epoch(sr["end"]) if sr["end"] else 0
            stages_out[name] = {
                "start": sr["start"],
                "end": sr["end"],
                "ms": int((ee - s) * 1000) if (s and ee) else 0,
                "final_status": sr["final_status"],
            }
        out.append({
            "run_id": rid,
            "start": slot["start"],
            "end": slot["end"],
            "run_final": slot.get("run_final"),
            "total_ms": int((end_t - start_t) * 1000) if (start_t and end_t) else 0,
            "stages": stages_out,
        })
    return out


def write_traces(traces: list[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return out_path


def build_from_events_file(events_path: Path, out_path: Path) -> Path:
    events = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return write_traces(build_trace(events), out_path)
