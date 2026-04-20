"""DLQ replayer worker (Master_03 §4).

Walks `dlq/<code>/<YYYYMMDD>/*.json` and retries each entry through the
pipeline. On success, moves the entry out of DLQ. On repeated failure,
increments retry_count and promotes the entry to `review_queue/` after
5 tries so a human can inspect.

Designed to run as a daily GitHub Actions step.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

MAX_RETRIES_BEFORE_HUMAN = 5


@dataclass
class ReplayResult:
    scanned: int = 0
    retried: int = 0
    recovered: int = 0
    routed_to_review: int = 0
    still_failing: int = 0


def _iter_dlq(root: Path):
    if not root.exists():
        return
    for p in root.rglob("*.json"):
        yield p


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def replay_all(
    dlq_root: Path,
    *,
    retry_fn: Callable[[dict], bool],
    review_queue_root: Path = Path("review_queue"),
) -> ReplayResult:
    """Retry every DLQ entry. `retry_fn(payload) -> True if recovered`.

    - Recovered: remove DLQ file.
    - Failed again: increment retry_count; if >= 5, move to review_queue.
    """
    out = ReplayResult()
    for dlq_file in _iter_dlq(dlq_root):
        out.scanned += 1
        entry = _load(dlq_file)
        if entry is None:
            continue
        payload = entry.get("payload") or {}
        out.retried += 1
        try:
            recovered = bool(retry_fn(payload))
        except Exception:
            recovered = False
        if recovered:
            dlq_file.unlink(missing_ok=True)
            out.recovered += 1
            continue
        # Still failing
        payload["retry_count"] = int(payload.get("retry_count", 0)) + 1
        entry["payload"] = payload
        if payload["retry_count"] >= MAX_RETRIES_BEFORE_HUMAN:
            # Move out of DLQ → review_queue with reviewer=human
            payload["reviewer"] = "human"
            qname = payload.get("source_key", dlq_file.stem).replace(":", "__") + ".json"
            target = review_queue_root / qname
            _write(target, payload)
            dlq_file.unlink(missing_ok=True)
            out.routed_to_review += 1
        else:
            _write(dlq_file, entry)
            out.still_failing += 1
    return out
