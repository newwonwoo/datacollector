"""Clickbait detection by title-noun vs transcript-noun overlap (Master_02 §2A)."""
from __future__ import annotations

import re
from collections import Counter

# Very lightweight noun-ish token extraction:
# - split on non-Korean-letter, non-alphanumeric boundaries
# - Korean 2+ char chunks are treated as candidate nouns (most Korean nouns are 2+ chars)
# - English 3+ letter word
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")

# Very common Korean / English stopwords
_STOPWORDS = {
    "이것", "그것", "저것", "여러분", "이영상", "영상", "오늘", "지금", "정말",
    "그래서", "그러면", "그런데", "때문에", "있다", "하다", "됩니다",
    "this", "that", "with", "from", "have", "will", "just", "been",
}


def extract_nouns(text: str, *, top_n: int = 10) -> list[str]:
    if not text:
        return []
    tokens = [t for t in _TOKEN_RE.findall(text) if t not in _STOPWORDS]
    if not tokens:
        return []
    most = [w for w, _ in Counter(tokens).most_common(top_n)]
    return most


def title_match_ratio(title: str, transcript: str, *, top_n: int = 10) -> float:
    """Return ratio of title nouns that also appear in transcript's top_n nouns.

    Empty title or transcript → 1.0 (cannot judge, don't penalize).
    """
    t_nouns = set(extract_nouns(title, top_n=50))  # title can be small, take all
    if not t_nouns:
        return 1.0
    body_nouns = set(extract_nouns(transcript, top_n=top_n))
    if not body_nouns:
        return 1.0
    hit = len(t_nouns & body_nouns)
    return hit / max(1, len(t_nouns))


def is_clickbait(title: str, transcript: str, *, threshold: float = 0.30) -> bool:
    return title_match_ratio(title, transcript) < threshold
