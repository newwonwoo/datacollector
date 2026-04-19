# [Harness Master 02] Ingestion & Intelligence Logic (v10)

> **Scope**: YouTube Source Adapter — Discover/Collect/Extract/Normalize/Review 단계.
> **Pipeline 매핑 (v2 `architecture_v2.md`)**: Discover → Collect → Extract → Normalize → Review → (Promote/Package는 Master_03).

## 목적 (Purpose)
- 검색어 생성, 자막 수집, 품질 필터링, LLM 분석, 구조/의미 검증, Review gate 규칙을 명세한다.
- 수집 노이즈를 줄이고 재현 가능한 추출 품질을 확보한다.
- v2 계약(ClaimRecord, NormalizedClaim, ReviewRecord, confidence 4단) 정합.

## 1. Entry Logic: Query Builder

### 생성 주체
- **1차(기본)**: 규칙 기반 변환. `config/synonyms.yml` 테이블 참조.
- **2차(보조)**: LLM 프롬프트 `prompts/query_builder_v1.md` 사용. 규칙만으로 표현 어려운 경우에만.
- **Fallback**: 템플릿 생성 실패 시 `topic` 단독 + `exclude_terms`만 적용해 1회 재시도.

### 템플릿
```json
{
  "topic": "단타",
  "synonyms": ["스캘핑", "데이 트레이딩"],
  "exclude_terms": ["코인", "해외선물"],
  "period": "this_month",
  "target_channel_id": null,
  "max_results": 20
}
```
- `target_channel_id`가 있으면 search step skip, `priority_score +30` 적용 후 Fast-Track.

## 2. Module A: Discover + Collect
### I/O 명세
- **Input**: Query Object (Discover) 또는 `target_channel_id` (Fast-Track)
- **Output**: Raw Transcript Payload (`record_status=collected`, `stage_status.collect=completed`)

### 2.1 Discover (검색)
- YouTube Data API v3 `search.list` 사용.
- 언어 필터: `relevanceLanguage` + 결과 `snippet.defaultAudioLanguage == "ko"` 재확인.
- 결과 각 항목에 `provenance.source_id = youtube:{video_id}` 부여.
- 쿼터 소스: `quota_source=youtube_data_api`.

### 2.2 Collect (자막 수집)
필수 수집 규칙:
- `caption_source: manual | asr | none` 판별 후 저장.
- `none`(자막 없음): `failure_reason_code=YT_NO_TRANSCRIPT`, 다음 후보로.
- `video_id`, `channel_id`, `title`, `published_at`, `source_query`, `collected_at`, `language`, `caption_source`, `transcript_hash` 채움.

수집 경로(fallback 순서):
1. `youtube-transcript-api`로 수동자막(`manual`) 우선.
2. 수동자막 없으면 자동자막(`asr`) 허용.
3. 차단/차단에 준하는 오류 시 `yt-dlp --write-auto-sub --skip-download` 우회.
4. 2)/3) 모두 실패 시 공식 Captions API(선택, OAuth 필요). 없으면 `YT_NO_TRANSCRIPT`.

차단 대응:
- HTTP 429 감지 시 지수 백오프(2s→4s→8s→16s) 후 `record_status=collected`로 rollback 대신 stage_status.collect=`failed` + `failure_reason_code=HTTP_429`.
- 일 5회 초과 시 서킷브레이커 발동(Appendix C §5).

### 2.3 Soft Filtering
하드 드롭보다 우선순위 조정이 필요한 항목은 감점.
임계값은 `config/filter.yml` override 가능.
- 4분 미만 쇼츠: 기본 드롭 (`drop_shorts=true`)
- 2시간 이상 스트리밍 원본: 기본 드롭 (`drop_long_stream=true`)
- 제목/자막 불일치 의심: `priority_score -25`
- 90분 이상 장문: `priority_score -15`
- `caption_source=asr`: `priority_score -10` (품질 가중)

### 2.4 낚시성 제목 판별
- 제목 명사와 자막 최빈도 명사 일치율 계산.
- 일치율 30% 미만이면 하드 드롭 대신 `priority_score -25`.
- 동일 채널 반복 감점은 `channel_quality.jsonl`에 누적.

### 2.5 중복/변경 판정
Master_01 §3 Rule A/B/C 적용. `source_key`는 ACTIVE + ARCHIVED 모두 조회.

## 3. Module B: Extract + Normalize + Review Gate
### I/O 명세
- **Input**: Raw Transcript Payload (`record_status=collected`)
- **Output**: Reviewed Payload (`record_status=reviewed_{confirmed|inferred|unverified|rejected}`)

### 3.1 Extract (LLM 분석)
LLM 출력 JSON 규격:
```json
{
  "summary": "...",
  "rules": ["..."],
  "tags": ["..."]
}
```
스키마 파일: `schemas/intelligence_output.schema.json`.

#### Strict JSON Handshake
- 줄글 응답 불허.
- 파싱 실패 시 `failure_reason_code=SEMANTIC_JSON_SCHEMA_FAIL`.
- 재프롬프트 1회: 원 프롬프트에 직전 에러 메시지 + 위반 필드명 append. temperature 0.2 유지.

#### 장문 청킹 (Chunking)
- 입력 토큰 > 모델 한도 × 60% → map-reduce.
- Chunk 단위 Extract → 상위에서 summary/rules 병합(Normalize 단계).
- Chunk 경계는 자막 발화 경계 존중.

### 3.2 Normalize (구조/의미 검증)
값 검증:
- `summary`: 50~300자. 금지어 예시 `이 영상은`, `전반적으로`. 원문 핵심 명사 1개 이상 포함.
- `rules`: 최소 1개. 빈 배열이면 `SEMANTIC_EMPTY_RULES`.
- `tags`: 최대 5개, 소문자 소문자화 후 공백 `_` 치환.

의미 검증 (환각 오탐 완화):
- `summary` 주요 명사 매칭 기준을 "정확 일치"에서 완화.
- 원문 자막과 `summary`의 문장 임베딩 cosine 유사도 ≥ 0.60 통과 인정.
- lemma 일치도 보조 지표로 병산.
- 실패 시 `failure_reason_code=SEMANTIC_LOW_QUALITY_SUMMARY`.

### 3.3 Review Gate (v2 확장)
자동 Review로 `confidence` 4단 판정:

| confidence | 조건 | record_status |
|---|---|---|
| `confirmed` | cosine ≥ 0.60 AND rules ≥ 1 AND 금지어 0 AND retry_count ≤ 1 | `reviewed_confirmed` |
| `inferred` | cosine ≥ 0.50 AND rules ≥ 1 | `reviewed_inferred` |
| `unverified` | cosine < 0.50 OR rules == 0 | `reviewed_unverified` |
| `rejected` | 재프롬프트 후에도 JSON 스키마 실패 | `reviewed_rejected` |

- `reviewed_confirmed`만 Promote 단계 자동 진입.
- `inferred/unverified`는 `review_queue/<date>/<source_key>.json`로 이동 (Human review hook).
- `reviewer=auto`로 저장. 수동 승격 시 `reviewer=human`으로 갱신.

## 4. 모델/프롬프트 추적
- `llm_context`에 기록 필수: `model_name`, `model_version`, `temperature`, `prompt_version`, `input_tokens`, `output_tokens`, `cost_usd`.
- 프롬프트 파일 Git 경로: `prompts/<name>_<version>.md`. 버전은 파일명 + 본문 front-matter 이중 기록.
- 품질 변동 원인을 프롬프트 변경 vs 모델 변경으로 분리 추적.

## 5. 대량 컨텍스트 분석 기준
- 장문/복수 영상 통합 분석은 별도 배치 (Promote 이후 상위 단계).
- 원본 단건 Payload 확정 뒤 집계 분석 수행.
- 단건 품질 불안정 상태에서 다건 집계 금지.

## 6. Failure Code (Module B 관련)
- `SEMANTIC_JSON_SCHEMA_FAIL`
- `SEMANTIC_EMPTY_RULES`
- `SEMANTIC_LOW_QUALITY_SUMMARY`
- `SEMANTIC_FORBIDDEN_WORD`
- `LLM_TIMEOUT`
- `LLM_QUOTA_EXCEEDED`
