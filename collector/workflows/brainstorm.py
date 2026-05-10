"""Step 1 of the workflow chain — generate ideas + YouTube search keywords.

One cheap-LLM call. Output schema:
    [
      {
        "idea": str,
        "rationale": str,
        "search_keywords": [str, ...],
        "target_audience": str
      },
      ...
    ]
"""
from __future__ import annotations

from typing import Any

from ._llm import call_workflow_llm


_SYSTEM = """\
너는 사업 기획 어시스턴트다. 사용자가 준 도메인에서 1인 운영 가능한 사업
아이디어 N개를 뽑고, 각 아이디어를 검증할 YouTube 검색 키워드 2~3개를
함께 제시한다.

반드시 다음 JSON 스키마 한 개만 출력 (다른 설명·코드펜스 금지):

[
  {
    "idea": "string (구체적 제품·서비스명)",
    "rationale": "string (왜 가능성 있는지 1~2문장)",
    "search_keywords": ["string", "string", ...],
    "target_audience": "string (구체적 타깃)"
  }
]

규칙:
- search_keywords 는 한국어 유튜브에서 실제로 검색했을 때 관련 영상이
  나올 만한 자연어 어구. 너무 짧거나 너무 추상적이면 안 됨.
- 아이디어끼리 서로 겹치지 않게 다양화.
- 도메인을 벗어난 아이디어 금지.
- focus 가 주어지면 그 방향에 맞춤.
- exclude 가 주어진 키워드는 들어간 아이디어 제외.
"""


def brainstorm_topics(
    domain: str,
    *,
    count: int = 10,
    focus: str = "",
    exclude: list[str] | None = None,
    keywords_per_idea: int = 3,
) -> list[dict[str, Any]]:
    """Generate `count` ideas with `keywords_per_idea` YouTube search
    keywords each. Returns the parsed JSON array.
    """
    bits = [
        _SYSTEM,
        "",
        f"도메인: {domain}",
        f"아이디어 개수: {count}",
        f"키워드 개수/아이디어: {keywords_per_idea}",
    ]
    if focus:
        bits.append(f"focus: {focus}")
    if exclude:
        bits.append(f"exclude: {', '.join(exclude)}")
    prompt = "\n".join(bits)

    result = call_workflow_llm(prompt, expect_json=True)

    # Some models return {"ideas": [...]} when responseMimeType=json_object;
    # accept both shapes.
    if isinstance(result, dict) and "ideas" in result:
        ideas = result["ideas"]
    elif isinstance(result, list):
        ideas = result
    else:
        raise RuntimeError(f"brainstorm: unexpected shape {type(result).__name__}")

    cleaned: list[dict[str, Any]] = []
    for it in ideas[:count]:
        if not isinstance(it, dict):
            continue
        idea = (it.get("idea") or "").strip()
        if not idea:
            continue
        kws = it.get("search_keywords") or []
        kws = [str(k).strip() for k in kws if str(k).strip()][:keywords_per_idea]
        if not kws:
            continue
        cleaned.append({
            "idea": idea,
            "rationale": (it.get("rationale") or "").strip(),
            "search_keywords": kws,
            "target_audience": (it.get("target_audience") or "").strip(),
        })
    return cleaned
