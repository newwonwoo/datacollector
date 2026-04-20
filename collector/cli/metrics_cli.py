"""CLI: `collector metrics` + `collector traces`."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..metrics import aggregate_daily, write_daily
from ..traces import build_from_events_file


def main_metrics(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector metrics")
    ap.add_argument("--events", default="logs/events.jsonl")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--out", default="metrics/daily.jsonl")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    records = aggregate_daily(Path(args.events), Path(args.data_store))
    write_daily(records, Path(args.out))
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        print(f"wrote {len(records)} daily rows → {args.out}")
    return 0


def main_traces(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector traces")
    ap.add_argument("--events", default="logs/events.jsonl")
    ap.add_argument("--out", default="logs/traces.jsonl")
    args = ap.parse_args(argv)

    out = build_from_events_file(Path(args.events), Path(args.out))
    lines = out.read_text(encoding="utf-8").count("\n") if out.exists() else 0
    print(f"wrote {lines} traces → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main_metrics())
