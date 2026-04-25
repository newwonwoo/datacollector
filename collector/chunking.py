"""Long-transcript chunking (Master_02 §5 — map-reduce 분석 전략).

Approximates tokens as 0.25 token per char for Korean-heavy text (so a
4500-char chunk ≈ 1100 tokens, fitting even small models like
llama-3.1-8b-instant whose per-minute cap is 6k tokens).

Defaults are conservative on purpose — they fit every adapter we ship.
Callers that know they're talking to a wider-context model (Gemini,
Claude) can pass larger `chunk_chars` directly.
"""
from __future__ import annotations

# Conservative defaults that fit ALL provider TPM caps in our chain
# (Groq 8b's 6k TPM is the bottleneck). Exceeded by Gemini/Claude;
# they just see more, smaller calls.
MAX_CHARS_SINGLE = 6_000
CHUNK_CHARS = 4_500
OVERLAP_CHARS = 400


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

    Strings (summary, notes_md): concatenate, summary trimmed to 280 chars.
    Lists (rules/tags/knowledge/examples/claims/unclear): de-dup union
    preserving first-seen order. Tags capped at 5.
    Enums:
      - content_type: majority vote across chunks; tie → "mixed".
      - llm_confidence: minimum (most pessimistic) across chunks.
    """
    summary_parts: list[str] = []
    notes_parts: list[str] = []
    list_fields = ("rules", "tags", "knowledge", "examples", "claims", "unclear")
    merged_lists: dict[str, list] = {f: [] for f in list_fields}
    seen: dict[str, set] = {f: set() for f in list_fields}
    type_votes: dict[str, int] = {}
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    min_conf_rank: int | None = None
    min_conf_label = ""

    for o in outputs:
        s = (o.get("summary") or "").strip()
        if s:
            summary_parts.append(s)
        n = (o.get("notes_md") or "").strip()
        if n:
            notes_parts.append(n)
        for f in list_fields:
            for v in o.get(f) or []:
                if v and v not in seen[f]:
                    merged_lists[f].append(v)
                    seen[f].add(v)
        ct = (o.get("content_type") or "").strip().lower()
        if ct:
            type_votes[ct] = type_votes.get(ct, 0) + 1
        lc = (o.get("llm_confidence") or "").strip().lower()
        if lc in confidence_rank:
            r = confidence_rank[lc]
            if min_conf_rank is None or r < min_conf_rank:
                min_conf_rank = r
                min_conf_label = lc

    combined_summary = " ".join(summary_parts)
    if len(combined_summary) > 280:
        cut = combined_summary.rfind(".", 0, 280)
        combined_summary = combined_summary[: cut + 1 if cut > 50 else 280].strip()
    combined_notes = "\n\n".join(notes_parts)

    if not type_votes:
        content_type = ""
    else:
        # majority — ties resolve to 'mixed' so a noisy chunk doesn't tip
        ranked = sorted(type_votes.items(), key=lambda kv: kv[1], reverse=True)
        content_type = ranked[0][0]
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            content_type = "mixed"

    return {
        "summary": combined_summary,
        "rules": merged_lists["rules"],
        "tags": merged_lists["tags"][:5],
        "notes_md": combined_notes,
        "content_type": content_type,
        "knowledge": merged_lists["knowledge"],
        "examples": merged_lists["examples"],
        "claims": merged_lists["claims"],
        "unclear": merged_lists["unclear"],
        "llm_confidence": min_conf_label,
    }
