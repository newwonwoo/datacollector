"""Step 3 — pick the highest-potential idea given the research_batch output.

Single cheap-LLM call. Input is a compact summary of every keyword's
collector.run_query result (channel diversity, promoted ratio, top tags,
top knowledge headlines). The LLM picks one idea and explains why.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ._llm import call_workflow_llm


_SYSTEM = """\
너는 사업 기획 검토 어시스턴트다. 여러 아이디어 각각에 대해 YouTube 자료를
모은 결과를 받아, 가장 가능성 높은 1개를 골라 근거와 다음 단계를 제시한다.

반드시 다음 JSON 한 개만 출력 (다른 설명·코드펜스 금지):

{
  "best_idea_index": int,
  "scores": [
    {"idea": str, "score": int (0-100), "why": str}
  ],
  "reasoning": str,
  "recommended_next_steps": [str, ...]
}

판단 기준:
- 자료가 풍부한가 (promoted 영상 수, 채널 다양성, knowledge/rules 양)
- 검색 결과가 일관된 주제로 모이는가
- 1인 사업 가능성, 진입 장벽, 차별화 가능성
- 자료 부족하거나 흩어진 아이디어는 점수 낮춤
"""


def _summarize_for_llm(
    ideas: list[dict[str, Any]],
    research_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a compact, token-budgeted view of the research output keyed
    by idea. We don't pass full vault notes — only aggregate signals.
    """
    # Map keyword → research summary
    by_kw: dict[str, dict[str, Any]] = {}
    for r in research_results:
        kw = r.get("query") or ""
        by_kw[kw] = r

    compact = []
    for idx, idea in enumerate(ideas):
        kws = idea.get("search_keywords", []) or []
        agg_promoted = 0
        agg_processed = 0
        agg_inferred = 0
        agg_invalid = 0
        channels = Counter()
        all_tags = Counter()
        for kw in kws:
            r = by_kw.get(kw, {})
            agg_promoted += r.get("promoted", 0)
            agg_processed += r.get("processed", 0)
            agg_inferred += r.get("inferred", 0)
            agg_invalid += r.get("invalid", 0)
            for v in r.get("per_video", []) or []:
                ch = v.get("channel_id") or ""
                if ch:
                    channels[ch] += 1
        compact.append({
            "idx": idx,
            "idea": idea.get("idea", ""),
            "rationale_seed": idea.get("rationale", ""),
            "search_keywords": kws,
            "stats": {
                "promoted": agg_promoted,
                "processed": agg_processed,
                "inferred": agg_inferred,
                "invalid": agg_invalid,
                "unique_channels": len(channels),
            },
        })
    return {"ideas": compact}


def synthesize(
    ideas: list[dict[str, Any]],
    research_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Returns the parsed JSON: {best_idea_index, scores, reasoning,
    recommended_next_steps}."""
    if not ideas:
        return {
            "best_idea_index": -1,
            "scores": [],
            "reasoning": "no ideas provided",
            "recommended_next_steps": [],
        }
    payload = _summarize_for_llm(ideas, research_results)
    prompt = (
        _SYSTEM + "\n\n"
        "아래 입력에서 best 를 골라라:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    out = call_workflow_llm(prompt, expect_json=True)
    if not isinstance(out, dict):
        raise RuntimeError(f"synthesize: unexpected shape {type(out).__name__}")
    # Defensive defaults
    out.setdefault("best_idea_index", -1)
    out.setdefault("scores", [])
    out.setdefault("reasoning", "")
    out.setdefault("recommended_next_steps", [])
    return out
