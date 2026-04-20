"""Priority score calculation + Aging (Master_01 §5)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

BASE = 100
FLOOR = 0
CEIL = 200

BONUS_TARGET_CHANNEL = 30
BONUS_RECENT_7D = 20
AGING_PER_DAY = 5
AGING_MAX = 35
PENALTY_RETRY = -10
PENALTY_CLICKBAIT = -25
PENALTY_LONG = -15


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def compute_priority(
    payload: dict[str, Any],
    *,
    target_channel_ids: set[str] | None = None,
    now: datetime | None = None,
    cost_guard_active: bool = False,
) -> int:
    """Return clamped priority_score."""
    now = now or datetime.now(timezone.utc)

    # Fast-Track / cost_guard take precedence
    if cost_guard_active and not (target_channel_ids and payload.get("channel_id") in target_channel_ids):
        return 0

    score = BASE

    if target_channel_ids and payload.get("channel_id") in target_channel_ids:
        score += BONUS_TARGET_CHANNEL

    pub = _parse_iso(payload.get("published_at", "") or "")
    if pub and (now - pub).days <= 7:
        score += BONUS_RECENT_7D

    # Aging: while still 'collected', add AGING_PER_DAY per day since collected_at
    if payload.get("record_status") == "collected":
        col = _parse_iso(payload.get("collected_at", "") or "")
        if col:
            days_waiting = max(0, (now - col).days)
            score += min(AGING_PER_DAY * days_waiting, AGING_MAX)

    if int(payload.get("retry_count", 0)) > 0:
        score += PENALTY_RETRY

    # Clickbait / long penalties piggyback on caller signals
    if payload.get("_flag_clickbait"):
        score += PENALTY_CLICKBAIT
    if payload.get("_flag_long"):
        score += PENALTY_LONG

    return max(FLOOR, min(CEIL, score))


def sort_queue(
    payloads: list[dict[str, Any]],
    *,
    target_channel_ids: set[str] | None = None,
    now: datetime | None = None,
    cost_guard_active: bool = False,
) -> list[dict[str, Any]]:
    """Rank payloads by Priority Policy (Master_01 §5 processing order)."""
    def rank(p: dict[str, Any]) -> tuple[int, int]:
        score = compute_priority(
            p, target_channel_ids=target_channel_ids, now=now,
            cost_guard_active=cost_guard_active,
        )
        # Fast-Track tier (target channel) at top
        tier = 0 if (target_channel_ids and p.get("channel_id") in target_channel_ids) else 1
        return (tier, -score)

    return sorted(payloads, key=rank)
