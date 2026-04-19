"""Human Review queue walker.

Queue convention:
  review_queue/<source_key>.json   (file per payload)

Decisions:
  approve → record_status=promoted, confidence=confirmed, move to data_store/
  reject  → record_status=reviewed_rejected, confidence=rejected, move to dlq/human_rejected/
  skip    → no change
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from ..events import EventLogger
from ..payload import utcnow_iso


def review_queue(root: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    root = Path(root)
    if not root.exists():
        return
    for p in sorted(root.glob("*.json")):
        yield p, json.loads(p.read_text(encoding="utf-8"))


def apply_review_decision(
    payload_path: Path,
    decision: str,
    *,
    data_store_root: Path,
    rejected_root: Path,
    reviewer: str = "human",
    reason: str = "",
    logger: EventLogger | None = None,
) -> dict[str, Any]:
    payload_path = Path(payload_path)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    prev = payload.get("record_status")

    if decision == "approve":
        payload["record_status"] = "promoted"
        payload["confidence"] = "confirmed"
        payload["reviewer"] = reviewer
        target_dir = Path(data_store_root)
    elif decision == "reject":
        payload["record_status"] = "reviewed_rejected"
        payload["confidence"] = "rejected"
        payload["reviewer"] = reviewer
        target_dir = Path(rejected_root)
    elif decision == "skip":
        return payload
    else:
        raise ValueError(f"unknown decision: {decision}")

    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / payload_path.name
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload_path.unlink()

    if logger:
        logger.log(
            entity_type="manual_action",
            entity_id=payload["source_key"],
            from_status=prev,
            to_status=payload["record_status"],
            run_id=payload.get("run_id", ""),
            reason=f"human_review:{decision}:{reason}",
            actor=f"user:{reviewer}",
        )
    return payload


def _cli_interactive(queue_root: Path, data_store: Path, rejected: Path) -> int:
    logger = EventLogger()
    handled = 0
    for path, p in list(review_queue(queue_root)):
        print(f"\n=== {path.name} ===")
        print(f"title: {p.get('title', '')}")
        print(f"confidence: {p.get('confidence')}  rules: {len(p.get('rules', []))}")
        print(f"summary: {p.get('summary', '')[:200]}")
        choice = input("[a]pprove / [r]eject / [s]kip > ").strip().lower()
        mapping = {"a": "approve", "r": "reject", "s": "skip"}
        if choice not in mapping:
            print("skipped")
            continue
        apply_review_decision(
            path, mapping[choice],
            data_store_root=data_store, rejected_root=rejected, logger=logger,
        )
        handled += 1
    print(f"\nhandled: {handled}  (events logged: {len(logger.events)})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector-review")
    ap.add_argument("--queue", default="review_queue")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--rejected", default="dlq/human_rejected")
    args = ap.parse_args(argv)
    return _cli_interactive(Path(args.queue), Path(args.data_store), Path(args.rejected))


if __name__ == "__main__":
    sys.exit(main())
