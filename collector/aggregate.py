"""Multi-video theme aggregation (Master_02 §5, Appendix B theme harness)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


def _iter_payloads(root: Path) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return
    for p in Path(root).rglob("*.json"):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue


def aggregate_by_tag(
    data_store_root: Path,
    *,
    tags: list[str] | None = None,
    min_confidence: str = "inferred",
) -> dict[str, Any]:
    """Aggregate promoted+confirmed records, optionally filtered by tag."""
    order = {"rejected": 0, "unverified": 1, "inferred": 2, "confirmed": 3}
    min_rank = order.get(min_confidence, 0)

    wanted_tags = set(tags) if tags else None

    channels: Counter = Counter()
    rules_counter: Counter = Counter()
    tag_counter: Counter = Counter()
    rules_by_tag: dict[str, list[str]] = defaultdict(list)
    total = 0

    for p in _iter_payloads(data_store_root):
        if order.get(p.get("confidence", ""), 0) < min_rank:
            continue
        ptags = p.get("tags") or []
        if wanted_tags and not (set(ptags) & wanted_tags):
            continue
        total += 1
        channels[p.get("channel_id", "")] += 1
        for r in (p.get("rules") or []):
            rules_counter[r] += 1
            for t in ptags:
                if not wanted_tags or t in wanted_tags:
                    rules_by_tag[t].append(r)
        for t in ptags:
            tag_counter[t] += 1

    return {
        "total_records": total,
        "filter_tags": sorted(wanted_tags) if wanted_tags else [],
        "min_confidence": min_confidence,
        "top_rules": rules_counter.most_common(20),
        "top_tags": tag_counter.most_common(20),
        "top_channels": channels.most_common(10),
        "rules_by_tag": {t: rs[:20] for t, rs in rules_by_tag.items()},
    }


def write_aggregate(result: dict[str, Any], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
