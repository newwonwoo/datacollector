"""CLI: `collector replay` — replay DLQ entries through the pipeline.

Uses the Services injected via env (real if YOUTUBE/LLM keys present, mock
otherwise). For each DLQ entry, re-invokes run_pipeline on the stored payload;
on success the DLQ file is removed, after 5 failures the entry is moved to
review_queue/ for human inspection.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..dlq_replayer import ReplayResult, replay_all
from ..events import EventLogger
from ..pipeline import run_pipeline
from ..store import JSONStore


def _retry_fn_factory(services, store, logger):
    def retry(payload: dict) -> bool:
        try:
            run_pipeline(payload, services, store, logger, use_lock=False)
        except Exception:
            return False
        return payload.get("record_status") == "promoted"
    return retry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector replay")
    ap.add_argument("--dlq", default="dlq")
    ap.add_argument("--review-queue", default="review_queue")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--logs", default="logs")
    args = ap.parse_args(argv)

    # Reuse the same service wiring as `collector run`
    from .run import _real_services_or_none, _scripted_services, _scripted_candidates
    services = _real_services_or_none() or _scripted_services("replay", _scripted_candidates("replay", 1))[0]

    store = JSONStore(root=Path(args.data_store))
    logger = EventLogger(Path(args.logs) / "events.jsonl")

    result: ReplayResult = replay_all(
        Path(args.dlq),
        retry_fn=_retry_fn_factory(services, store, logger),
        review_queue_root=Path(args.review_queue),
    )

    print(f"scanned:         {result.scanned}")
    print(f"retried:         {result.retried}")
    print(f"recovered:       {result.recovered}")
    print(f"still_failing:   {result.still_failing}")
    print(f"routed_to_human: {result.routed_to_review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
