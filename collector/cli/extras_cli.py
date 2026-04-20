"""CLI: collector aggregate | collector archive."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..aggregate import aggregate_by_tag, write_aggregate
from ..archive import archive_quarter, previous_quarter


def main_aggregate(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector aggregate")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--tags", default="", help="쉼표 구분. 없으면 전체.")
    ap.add_argument("--min-confidence", default="inferred",
                    choices=["unverified", "inferred", "confirmed"])
    ap.add_argument("--out", default="aggregates/result.json")
    args = ap.parse_args(argv)
    tags = [t for t in (args.tags.split(",") if args.tags else []) if t.strip()]
    result = aggregate_by_tag(Path(args.data_store), tags=tags or None, min_confidence=args.min_confidence)
    out = write_aggregate(result, Path(args.out))
    print(f"aggregate → {out}: {result['total_records']} records, "
          f"{len(result['top_rules'])} top rules")
    return 0


def main_archive(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector archive")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], default=None)
    args = ap.parse_args(argv)
    if args.year is None or args.quarter is None:
        y, q = previous_quarter()
        args.year = args.year or y
        args.quarter = args.quarter or q
    moved = archive_quarter(Path(args.data_store), Path(args.archive),
                            year=args.year, quarter=args.quarter)
    print(f"archived {len(moved)} files → {args.archive}/{args.year}_Q{args.quarter}/")
    return 0


if __name__ == "__main__":
    sys.exit(main_aggregate())
