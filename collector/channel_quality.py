"""Channel quality score (Master_02 §2.4).

Design intent: '동일 채널에서 반복적으로 감점되면 채널 품질 점수에 반영'.

Score = f(promoted, inferred, unverified, invalid, clickbait_flags).
Range [-1.0, +1.0] where +1.0 = all promoted, -1.0 = all invalid/clickbait.

Walks `data_store/` to compute once; suitable for daily batch.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class ChannelScore:
    channel_id: str
    total: int = 0
    promoted: int = 0
    inferred: int = 0
    unverified: int = 0
    rejected: int = 0
    invalid: int = 0
    clickbait_flags: int = 0

    @property
    def score(self) -> float:
        if self.total == 0:
            return 0.0
        raw = (
            self.promoted * 1.0
            + self.inferred * 0.3
            + self.unverified * (-0.3)
            + self.rejected * (-0.7)
            + self.invalid * (-1.0)
            + self.clickbait_flags * (-0.5)
        )
        return max(-1.0, min(1.0, raw / self.total))

    @property
    def tier(self) -> str:
        s = self.score
        if s >= 0.5:
            return "green"
        if s >= 0.0:
            return "yellow"
        return "red"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["score"] = round(self.score, 3)
        d["tier"] = self.tier
        return d


def _iter_payloads(root: Path):
    if not root.exists():
        return
    for p in Path(root).rglob("*.json"):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue


def compute_channel_scores(data_store_root: Path) -> dict[str, ChannelScore]:
    out: dict[str, ChannelScore] = defaultdict(lambda: ChannelScore(channel_id=""))
    for p in _iter_payloads(data_store_root):
        ch = p.get("channel_id", "") or "(unknown)"
        sc = out[ch]
        sc.channel_id = ch
        sc.total += 1
        status = p.get("record_status", "")
        conf = p.get("confidence", "")
        if status == "promoted":
            sc.promoted += 1
        elif status == "invalid":
            sc.invalid += 1
        elif conf == "inferred":
            sc.inferred += 1
        elif conf == "unverified":
            sc.unverified += 1
        elif conf == "rejected":
            sc.rejected += 1
        if p.get("_flag_clickbait") is True:
            sc.clickbait_flags += 1
    return dict(out)


def top_channels(scores: dict[str, ChannelScore], *, n: int = 5, reverse: bool = True) -> list[ChannelScore]:
    items = [s for s in scores.values() if s.total > 0]
    items.sort(key=lambda s: s.score, reverse=reverse)
    return items[:n]


def as_serializable(scores: dict[str, ChannelScore]) -> list[dict[str, Any]]:
    return [s.to_dict() for s in sorted(scores.values(), key=lambda s: -s.score)]
