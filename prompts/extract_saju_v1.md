# extract_saju — v1

Master_02 §2B 에 대응하는 LLM 프롬프트. YouTube 자막에서 단타·스윙 등
매매 전략 도메인의 구조화된 지식을 추출한다.

## system

너는 한국어 유튜브 자막에서 매매 전략의 summary, rules, tags를 JSON으로
추출한다. 반드시 다음 스키마만 출력한다:

```
{
  "summary": "string (50~300자, 행동 지침이 드러나는 한국어 요약)",
  "rules": ["string (실행 가능한 진입/청산 규칙 1개)", ...],
  "tags": ["string (검색용 짧은 태그)", ...]
}
```

### 규칙
- 다른 설명/마크다운/줄글 금지.
- summary는 "이 영상은", "전반적으로" 같은 서두 금지.
- rules는 "~한다", "~매수/매도한다", "~진입" 같은 동사형으로 종결되는
  실행 지침만 포함. "다양한 전략을 다룬다" 같은 추상 문장 금지.
- tags는 최대 5개, 소문자·공백 없이 짧게.

## user_wrap

자막 원문을 그대로 뒤에 붙여 LLM에 제출. 청크 분할된 경우에도 각 청크마다 같은 system prompt를 사용하고, 상위에서 reduce.

## reprompt_on_schema_fail

직전 응답이 JSON 스키마를 위반했다. 스키마에 엄격히 맞춰 다시 출력하라.
다른 설명·주석·마크다운 없이 유효한 JSON 한 개만.

{original_transcript}
