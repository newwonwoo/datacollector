"""CLI: `collector status` — emits a single JSON snapshot of operational state.

Fields:
  - updated_at
  - kill_switch (bool)
  - dlq_count, review_queue_count
  - breakers: per-service { open_until, seconds_remaining, failures_recent }
  - budget: output of quota.snapshot_quota()
  - records: { total, promoted, invalid }
  - latest_run: { status, conclusion, created_at } (from last events.jsonl run line)

Used by the workflow to write `docs/status.json`, which the Pages dashboard
fetches (public URL, no auth required).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ..channel_quality import compute_channel_scores, top_channels
from ..killswitch import is_paused
from .quota import snapshot_quota


def _count_glob(root: Path, pattern: str = "*.json") -> int:
    if not root.exists():
        return 0
    return sum(1 for _ in root.rglob(pattern))


def _read_breakers(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    now = time.time()
    out: dict = {}
    for svc, st in raw.items():
        until = float((st or {}).get("open_until", 0))
        failures = list((st or {}).get("failures", []))
        out[svc] = {
            "open": until > now,
            "open_until_epoch": until,
            "seconds_remaining": max(0, int(until - now)),
            "failures_in_window": len(failures),
        }
    return out


def _record_counts(data_store: Path) -> dict:
    total = promoted = invalid = 0
    if data_store.exists():
        for p in data_store.rglob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            total += 1
            rs = rec.get("record_status", "")
            if rs == "promoted":
                promoted += 1
            elif rs == "invalid":
                invalid += 1
    return {"total": total, "promoted": promoted, "invalid": invalid}


def _latest_run(events: Path) -> dict | None:
    if not events.exists():
        return None
    last = None
    for line in events.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("entity_type") == "run":
            last = e
    return last


def build_status(
    *,
    dlq_root: Path = Path("dlq"),
    review_queue_root: Path = Path("review_queue"),
    breakers_path: Path = Path("state/breakers.json"),
    quota_usage: Path = Path("metrics/quota.jsonl"),
    data_store: Path = Path("data_store"),
    events: Path = Path("logs/events.jsonl"),
    runs_root: Path = Path("runs"),
) -> dict:
    scores = compute_channel_scores(data_store)
    top = [s.to_dict() for s in top_channels(scores, n=5, reverse=True)]
    bottom = [s.to_dict() for s in top_channels(scores, n=3, reverse=False)]
    return {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kill_switch": is_paused(),
        "dlq_count": _count_glob(dlq_root),
        "review_queue_count": _count_glob(review_queue_root),
        "runs_count": _count_glob(runs_root),
        "breakers": _read_breakers(breakers_path),
        "budget": snapshot_quota(quota_usage),
        "records": _record_counts(data_store),
        "latest_run": _latest_run(events),
        "top_channels": top,
        "bottom_channels": bottom,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector status")
    ap.add_argument("--out", default=None, help="write JSON to file (default: stdout)")
    ap.add_argument("--dlq", default="dlq")
    ap.add_argument("--review-queue", default="review_queue")
    ap.add_argument("--breakers", default="state/breakers.json")
    ap.add_argument("--quota-usage", default="metrics/quota.jsonl")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--events", default="logs/events.jsonl")
    args = ap.parse_args(argv)

    snap = build_status(
        dlq_root=Path(args.dlq),
        review_queue_root=Path(args.review_queue),
        breakers_path=Path(args.breakers),
        quota_usage=Path(args.quota_usage),
        data_store=Path(args.data_store),
        events=Path(args.events),
    )
    body = json.dumps(snap, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"wrote {args.out}: dlq={snap['dlq_count']} review={snap['review_queue_count']}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
