"""Step 4 — turn the chosen idea + harvested vault knowledge into a
markdown design document. One cheap-LLM call.

The spec is written from the *user's chosen idea* + a compact view of
the research evidence (top knowledge/rules/examples from the vault),
not free-form ideation. Every section is grounded in something the
research_batch step actually collected, so the output is reproducible
and cites no out-of-vault facts.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from ._llm import call_workflow_llm


_SYSTEM = """\
너는 1인 사업 설계서 어시스턴트다. 사용자가 고른 아이디어 하나와, 그 아이디어를
검증할 때 모은 YouTube 자료 (요약·핵심 개념·행동 지침·사례) 가 주어진다.

너의 일: 그 자료에 근거해 한 페이지짜리 제품 설계서를 마크다운으로 작성한다.

전제 (반드시 지킬 것):
- **MVP = NotebookLM 기반**. 별도 앱·서버를 짓지 않는다. 사용자 접점은 NotebookLM
  채팅·요약·오디오 개요. 우리가 만드는 것은 그 NotebookLM 노트북에 들어갈
  자료(콘텐츠)와 그 자료를 자동 갱신하는 collector 파이프라인일 뿐이다.
- **모든 스택 추천은 무료 티어 우선**. 유료 도구를 추천해야 한다면 명시적으로
  "(유료)" 표시 + 무료 대안 함께 제시. 1인 운영자가 0원으로 시작 가능해야 함.

반드시 다음 JSON 한 개만 출력 (다른 설명·코드펜스 금지):

{
  "title": "string",
  "spec_md": "string (마크다운 본문 — 아래 섹션 구조)"
}

spec_md 의 섹션 구조 (이 순서, ## 헤딩 사용):

## 한 줄 정의
- 제품을 한 문장으로 (≤80자). NotebookLM 위에 무엇을 얹는가가 분명할 것.

## 타깃 유저
- 누가 (인구통계 + 상황) — 2~3 bullet.
- 그들의 페인 포인트 — 자료에 등장한 표현으로 인용.

## 핵심 기능 (MVP — NotebookLM 기반)
- 5개 이내.
- 각 기능은 "무엇 + 왜" 한 줄.
- 모든 기능은 NotebookLM 의 입력(소스 업로드)·출력(채팅/요약/마인드맵/오디오)
  으로 표현 가능해야 함. 별도 앱 UI 가정 금지.

## 사용자 여정
- 3 시나리오 (이상적인 첫 사용 / 반복 사용 / 이탈 방지).
- 각 시나리오 3~5 단계 bullet. NotebookLM 화면 상의 행동으로 묘사.

## 데이터·알고리즘
- 어떤 지식을 활용하는가 — 자료의 knowledge/rules 에서 인용.
- collector 파이프라인이 어떤 검색어로 자료를 자동 갱신할지 명시.
- 자료에 없는 알고리즘 추가 금지.

## 스택 (환경 분석)
- 무료 티어 우선. 각 컴포넌트마다 선택안 1개 + 이유 + 한도/제약.
- 다음 항목 필수 포함:
  - 데이터 수집: collector (이 저장소) + YouTube Data API v3 (무료 10k units/일)
  - LLM 추출: Gemini Flash 무료 (1500 RPD) → Groq 70b/8b 무료 폴백 → 룰베이스
  - 자막 fetch: yt-dlp + cookies.txt (무료, 가정 IP)
  - 지식 인터페이스: NotebookLM 무료 (소스 50개·노트북 100개 제한)
  - 저장소: 로컬 vault/ + git (private repo 무료, 또는 GitHub Pages 무료)
  - 자동화: cron / Windows 작업 스케줄러 (무료) / GitHub Actions 무료 분량
- 유료가 필요한 경우만 "(유료, optional)" 표시 + 무료 우회안.

## 차별화
- 자료에 등장한 기존 접근/채널 대비 무엇이 다른가.

## 수익 모델
- 가능한 모델 2~3개 + 가장 현실적 1개 추천 + 근거.
- 모든 모델은 0원 인프라 가정에서 가능해야 함 (광고·후원·유료 노트북 공유 등).

## 구현 단계
- Phase 1 MVP (1~2주): collector 검색어 셋업 + NotebookLM 노트북 1개 시드. 끝.
- Phase 2 (1~3개월): 자동 갱신 (cron) + 노트북 분야별 분리.
- Phase 3 (3~6개월): 외부 사용자에게 NotebookLM 공유 링크 배포 또는 컨설팅.

## 리스크
- 자료에서 보인 시장·기술·사용자 리스크 3~5개 + 대응.
- 무료 티어 한도 초과 시나리오 1개는 반드시 포함.

## 다음 액션
- 1주일 안에 할 일 5개. 검증 가능한 수준으로 구체.
- 5개 중 최소 3개는 "0원으로 즉시 가능" 한 행동.

규칙:
- 자료에 없는 사실 추가 금지. 인용은 "자료에 따르면 …" 같이 명시.
- 유료 도구 추천 시 무료 대안 의무.
- 분량: spec_md 약 2000~3500자.
- title 은 idea 를 깔끔히 다듬은 한국어 제품명.
"""


def _gather_evidence(
    best_idea: dict[str, Any],
    research_results: list[dict[str, Any]],
    vault_records: Iterable[dict[str, Any]] | None = None,
    max_items_per_field: int = 12,
) -> dict[str, Any]:
    """Compact, token-budgeted view of what the research batch found
    for *this* idea. We feed the LLM only:
      - the chosen idea's metadata
      - dedup'd top knowledge / rules / examples / claims across all
        videos that came in via the idea's search keywords
      - top tags
      - aggregated channel + video counts
    Full transcripts and notes_md are NOT included — the spec is
    grounded in extracted structure, not raw text.
    """
    keyword_set = {kw.strip() for kw in best_idea.get("search_keywords") or [] if kw.strip()}

    knowledge: list[str] = []
    rules: list[str] = []
    examples: list[str] = []
    claims: list[str] = []
    summaries: list[str] = []
    tag_counts: Counter = Counter()
    channel_counts: Counter = Counter()
    seen_k: set[str] = set()
    seen_r: set[str] = set()
    seen_e: set[str] = set()
    seen_c: set[str] = set()

    relevant_results = [
        r for r in research_results
        if (r.get("query") or "").strip() in keyword_set
    ]
    if not relevant_results:
        relevant_results = research_results  # fall back to everything

    promoted_ids: list[str] = []
    for r in relevant_results:
        for v in r.get("per_video") or []:
            if v.get("record_status") == "promoted":
                promoted_ids.append(v.get("video_id", ""))

    if vault_records is not None:
        promoted_set = set(promoted_ids)
        for rec in vault_records:
            vid = rec.get("video_id", "")
            if promoted_set and vid not in promoted_set:
                continue
            s = (rec.get("summary") or "").strip()
            if s and len(summaries) < max_items_per_field:
                summaries.append(s[:200])
            for k in (rec.get("knowledge") or [])[:max_items_per_field]:
                if k and k not in seen_k:
                    knowledge.append(k); seen_k.add(k)
            for r_ in (rec.get("rules") or [])[:max_items_per_field]:
                if r_ and r_ not in seen_r:
                    rules.append(r_); seen_r.add(r_)
            for e in (rec.get("examples") or [])[:max_items_per_field]:
                if e and e not in seen_e:
                    examples.append(e); seen_e.add(e)
            for c in (rec.get("claims") or [])[:max_items_per_field]:
                if c and c not in seen_c:
                    claims.append(c); seen_c.add(c)
            for t in rec.get("tags") or []:
                tag_counts[t] += 1
            ch = rec.get("channel_id") or ""
            if ch:
                channel_counts[ch] += 1

    return {
        "idea": best_idea,
        "evidence": {
            "summaries": summaries[:max_items_per_field],
            "knowledge": knowledge[:max_items_per_field],
            "rules": rules[:max_items_per_field],
            "examples": examples[:max_items_per_field],
            "claims": claims[:max_items_per_field],
            "top_tags": [t for t, _ in tag_counts.most_common(8)],
            "channel_count": len(channel_counts),
            "promoted_video_count": len(promoted_ids),
        },
    }


def design_spec(
    best_idea: dict[str, Any],
    research_results: list[dict[str, Any]],
    vault_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Return {'title': str, 'spec_md': str}. Single cheap-LLM call."""
    if not best_idea or not best_idea.get("idea"):
        return {"title": "(empty)",
                "spec_md": "best idea 가 비어있어 설계서를 만들 수 없습니다."}
    payload = _gather_evidence(best_idea, research_results, vault_records)
    prompt = (
        _SYSTEM + "\n\n"
        "아래 입력을 바탕으로 설계서를 작성:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    out = call_workflow_llm(prompt, expect_json=True)
    if not isinstance(out, dict):
        raise RuntimeError(f"design_spec: unexpected shape {type(out).__name__}")
    title = (out.get("title") or best_idea.get("idea") or "untitled").strip()
    spec_md = (out.get("spec_md") or "").strip()
    if not spec_md:
        raise RuntimeError("design_spec: empty spec_md returned")
    return {"title": title, "spec_md": spec_md}
