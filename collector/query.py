"""Query Builder template (Master_02 §1).

Normalizes a user natural-language request into the canonical Query Object
before hitting YouTube search.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_SYNONYM_TABLE: dict[str, list[str]] = {
    "단타": ["스캘핑", "데이 트레이딩", "데이트레이딩"],
    "단타매매": ["스캘핑", "데이 트레이딩"],
    "눌림목": ["pullback", "지지선 매수"],
    "돌파": ["브레이크아웃", "breakout"],
    "이평선": ["이동평균선", "MA"],
}

_DEFAULT_EXCLUDE: list[str] = ["코인", "해외선물", "리딩방", "광고"]


@dataclass
class QueryObject:
    topic: str
    synonyms: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    period: str = "this_month"  # this_week | this_month | this_quarter | all
    target_channel_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "synonyms": self.synonyms,
            "exclude_terms": self.exclude_terms,
            "period": self.period,
            "target_channel_id": self.target_channel_id,
        }


def build_query(
    raw: str,
    *,
    target_channel_id: str | None = None,
    period: str = "this_month",
    extra_exclude: list[str] | None = None,
) -> QueryObject:
    """Convert raw user string into a QueryObject.

    Never throws — caller can always hit `fallback_query(raw)` if dissatisfied.
    """
    topic = raw.strip()
    synonyms: list[str] = []
    for k, vs in _SYNONYM_TABLE.items():
        if k in topic:
            synonyms.extend(vs)

    exclude = list(_DEFAULT_EXCLUDE)
    if extra_exclude:
        exclude.extend(extra_exclude)

    return QueryObject(
        topic=topic,
        synonyms=list(dict.fromkeys(synonyms)),  # de-dup preserve order
        exclude_terms=list(dict.fromkeys(exclude)),
        period=period,
        target_channel_id=target_channel_id,
    )


def fallback_query(raw: str) -> QueryObject:
    """Template 생성 실패 시 허용되는 1회 fallback (Master_02 §1)."""
    return QueryObject(
        topic=raw.strip(),
        synonyms=[],
        exclude_terms=list(_DEFAULT_EXCLUDE),
        period="this_month",
        target_channel_id=None,
    )
