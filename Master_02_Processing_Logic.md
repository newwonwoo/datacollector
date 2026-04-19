# [Harness Master 02] Ingestion & Intelligence Logic (V9)

## 목적 (Purpose)
- 검색어 생성, 자막 수집, 품질 필터링, LLM 분석, 구조/의미 검증 규칙을 명세한다.
- 수집 노이즈를 줄이고 재현 가능한 추출 품질을 확보한다.

## 1. Entry Logic: Query Builder 템플릿화
사용자 자연어를 바로 검색에 쓰지 않고, 아래 템플릿으로 정규화한다.

```json
{
  "topic": "단타",
  "synonyms": ["스캘핑", "데이 트레이딩"],
  "exclude_terms": ["코인", "해외선물"],
  "period": "this_month",
  "target_channel_id": null
}
```

- `target_channel_id`가 있으면 전체 검색을 생략하고 Fast-Track으로 진입한다.
- 템플릿 생성 실패 시 즉시 `FAILED`가 아니라 기본 검색어 1회 fallback을 허용한다.

## 2. Module A: Ingestion
### I/O 명세
- **Input**: Query Object
- **Output**: Raw Transcript Payload (`status=COLLECTED`)

### 필수 수집 규칙
- 자막이 없는 영상은 수집 실패로 기록하고 다음 후보로 넘어간다.
- `video_id`, `channel_id`, `title`, `published_at`, `source_query`, `collected_at`, `language`, `transcript_hash`를 함께 채운다.

### Soft Filtering 규칙
하드 드롭보다 우선순위 조정이 필요한 항목은 감점 처리한다.
- 4분 미만 쇼츠: 기본 드롭
- 2시간 이상 스트리밍 원본: 기본 드롭
- 제목/자막 불일치 의심: `priority_score -25`
- 90분 이상 장문형 콘텐츠: `priority_score -15`

### 낚시성 제목 판별
- 제목 명사와 자막 최빈도 명사의 일치율을 계산한다.
- 일치율 30% 미만이면 하드 드롭하지 않고 감점한다.
- 동일 채널에서 반복적으로 감점되면 채널 품질 점수에 반영한다.

### 중복/변경 판정
- `video_id` 신규: 수집 계속
- `video_id` 동일 + `transcript_hash` 변경: 재처리 계속
- `video_id` 동일 + `transcript_hash` 동일: 스킵

## 3. Module B: Intelligence
### I/O 명세
- **Input**: Raw Transcript Payload
- **Output**: Validated JSON Payload (`status=VALIDATED`)

### 출력 JSON 규격
```json
{
  "summary": "...",
  "rules": ["..."],
  "tags": ["..."]
}
```

### Strict JSON Handshake
- 줄글 응답은 허용하지 않는다.
- 파싱 실패 시 `failure_reason_code=JSON_SCHEMA_FAIL`로 기록한다.
- 재프롬프팅은 최대 1회만 허용한다.

### 값(Value) 검증
- `summary`
  - 50자 이상 300자 이하
  - 금지어 예시: `이 영상은`, `전반적으로`
  - 원문 핵심 명사 1개 이상 포함 필수
- `rules`
  - 최소 1개 이상
  - 빈 배열이면 `SEMANTIC_EMPTY_RULES`
- `tags`
  - 최대 5개

### 의미(Semantic) 검증
- `summary`의 주요 명사가 원문 자막에 없으면 환각 의심으로 실패 처리한다.
- `rules`가 너무 포괄적이거나 실행 불가능 문장만 나열되면 실패 처리한다.
- 실패 시 1회 재프롬프팅 후 종료한다.

## 4. 모델/프롬프트 추적
- 모든 분석 결과는 `llm_context`에 아래를 반드시 기록한다.
  - `model_name`
  - `model_version`
  - `temperature`
  - `prompt_version`
- 품질 변동 원인을 프롬프트 변경과 모델 변경으로 분리 추적한다.

## 5. 대량 컨텍스트 분석 기준
- 장문/복수 영상 통합 분석은 별도 배치에서 수행한다.
- 원본 단건 Payload를 먼저 확정한 뒤, 그 위에 집계 분석을 올린다.
- 단건 품질이 불안정한 상태에서 다건 집계를 바로 돌리지 않는다.
