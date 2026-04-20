"""Long-transcript chunking (Master_02 §5 — map-reduce 분석 전략).

Approximates tokens as 0.25 token per char for Korean-heavy text.
"""
from __future__ import annotations

MAX_CHARS_SINGLE = 40_000       # ~10k tokens — still safe for Claude 200k / Gemini 1M
CHUNK_CHARS = 30_000
OVERLAP_CHARS = 500


def should_chunk(text: str, threshold: int = MAX_CHARS_SINGLE) -> bool:
    return len(text or "") > threshold


def chunk(text: str, *, chunk_chars: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Split text into overlapping windows, snapping to whitespace when possible."""
    if not text:
        return []
    n = len(text)
    if n <= chunk_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < n:
        end = min(start + chunk_chars, n)
        # Try to break at the nearest whitespace within the last 200 chars
        if end < n:
            window = text[max(end - 200, start): end]
            sp = window.rfind(" ")
            if sp > 0:
                end = (end - 200) + sp
        pieces.append(text[start:end])
        if end >= n:
            break
        start = max(0, end - overlap)
    return pieces


def reduce_outputs(outputs: list[dict]) -> dict:
    """Combine per-chunk LLM outputs into one payload-shape dict.

    - summary: concat top snippets then trim to 280 chars.
    - rules: flat de-dup preserving order.
    - tags: union, cap 5.
    """
    summary_parts: list[str] = []
    rules: list[str] = []
    tags: list[str] = []
    seen_rules: set[str] = set()
    seen_tags: set[str] = set()
    for o in outputs:
        s = (o.get("summary") or "").strip()
        if s:
            summary_parts.append(s)
        for r in o.get("rules") or []:
            if r and r not in seen_rules:
                rules.append(r)
                seen_rules.add(r)
        for t in o.get("tags") or []:
            if t and t not in seen_tags:
                tags.append(t)
                seen_tags.add(t)
                if len(tags) >= 5:
                    break
    combined_summary = " ".join(summary_parts)
    # Trim keeping sentence boundaries when possible
    if len(combined_summary) > 280:
        cut = combined_summary.rfind(".", 0, 280)
        combined_summary = combined_summary[: cut + 1 if cut > 50 else 280].strip()
    return {"summary": combined_summary, "rules": rules, "tags": tags[:5]}
