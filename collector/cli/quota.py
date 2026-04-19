"""Runner-minute / quota usage snapshot.

Sources:
- GitHub Actions Billing API (if GITHUB_TOKEN + owner provided) — optional.
- Local usage file `metrics/quota.jsonl` (authoritative, always read).

Emits current usage + remaining + alert flags per the thresholds in
Appendix_C (80% free tier = 1600 minutes).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

FREE_MINUTES = 2000
ALERT_PCT = 0.80


def read_local_usage(path: Path) -> dict[str, float]:
    """Sum `minutes` field across all lines of the quota jsonl file."""
    totals = {"actions_minutes": 0.0, "youtube_units": 0.0, "llm_cost_usd": 0.0}
    if not Path(path).exists():
        return totals
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for k in totals:
            totals[k] += float(rec.get(k, 0) or 0)
    return totals


def snapshot_quota(
    usage_path: Path = Path("metrics/quota.jsonl"),
    *,
    free_minutes: int = FREE_MINUTES,
    youtube_daily_limit: int = 10000,
    llm_daily_budget_usd: float = 1.0,
) -> dict[str, Any]:
    usage = read_local_usage(usage_path)
    actions_pct = usage["actions_minutes"] / free_minutes if free_minutes else 0
    yt_pct = usage["youtube_units"] / youtube_daily_limit if youtube_daily_limit else 0
    llm_pct = usage["llm_cost_usd"] / llm_daily_budget_usd if llm_daily_budget_usd else 0
    snap = {
        "actions_minutes_used": usage["actions_minutes"],
        "actions_minutes_remaining": max(0.0, free_minutes - usage["actions_minutes"]),
        "actions_minutes_pct": round(actions_pct, 3),
        "actions_alert": actions_pct >= ALERT_PCT,
        "youtube_units_used": usage["youtube_units"],
        "youtube_units_pct": round(yt_pct, 3),
        "youtube_alert": yt_pct >= ALERT_PCT,
        "llm_cost_usd": usage["llm_cost_usd"],
        "llm_cost_pct": round(llm_pct, 3),
        "llm_alert": llm_pct >= 1.0,
        "kill_switch_recommended": bool(
            actions_pct >= 0.95 or yt_pct >= 0.95 or llm_pct >= 1.0
        ),
    }
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector-quota")
    ap.add_argument("--usage", default="metrics/quota.jsonl")
    ap.add_argument("--free-minutes", type=int, default=FREE_MINUTES)
    ap.add_argument("--youtube-limit", type=int, default=10000)
    ap.add_argument("--llm-budget", type=float, default=1.0)
    args = ap.parse_args(argv)
    snap = snapshot_quota(
        Path(args.usage),
        free_minutes=args.free_minutes,
        youtube_daily_limit=args.youtube_limit,
        llm_daily_budget_usd=args.llm_budget,
    )
    print(json.dumps(snap, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
