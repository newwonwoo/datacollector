# Appendix B — State & Event Log

> **목적**: 3-tier 상태(run/stage/record) 전이표, event log 스키마, 테스트 시나리오 9종을 fixture 형태로 정의한다.

## 1. 3-Tier 상태 전이표

### 1.1 Run
```
created → running → {completed | failed | partially_completed}
```
- `completed`: 모든 record stage 정상 종료.
- `failed`: 치명적 예외(Kill switch, 버그 등).
- `partially_completed`: 일부 record invalid. 가장 일반적인 종결 상태.

### 1.2 Stage (각 stage 독립)
```
not_started → started → {completed | failed | skipped}
```
- `skipped`: Fast-Track의 search, Kill switch, Daily budget 초과.
- 선행 stage `completed` 없이 후행 stage `started` 금지.
- `failed` 시 후행 stage 자동 진입 금지 (옵션 `continue_on_error`는 프로젝트 설정으로).

### 1.3 Record
허용 전이 그래프:
```
                                  ┌─ reviewed_confirmed → promoted
collected → extracted → normalized ┼─ reviewed_inferred
                                  ├─ reviewed_unverified
                                  └─ reviewed_rejected
any ─────────────────────────────→ invalid
invalid ──(manual_reinject, reason)──→ collected
reviewed_{inferred|unverified} ──(human review)──→ {reviewed_confirmed | reviewed_rejected}
promoted ──(rollback, payload_version++)──→ (new revision, same record_status)
```

### 1.4 금지 전이
- `reviewed_rejected → promoted` 직접 금지.
- `promoted → reviewed_*` 역행 금지 (rollback은 새 revision으로).
- `REMOVED` archive_state에서 `ACTIVE` 복귀 금지 (재수집 시 새 record로 취급).

## 2. Event Log Schema
경로: `logs/events.jsonl`. append-only. 1 line = 1 event.

```json
{
  "event_id": "evt_01HXXX",
  "run_id": "run_2026-04-19T03:40Z_abc123",
  "entity_type": "run | stage | record | manual_action | system",
  "entity_id": "youtube:ABCDEFGHIJK",
  "from_status": "collected",
  "to_status": "extracted",
  "reason": "",
  "metrics": {
    "elapsed_ms": 420,
    "tokens_in": 1200,
    "tokens_out": 400,
    "cost_usd": 0.0032
  },
  "actor": "worker-abc-pid1234 | user:alice | system",
  "recorded_at": "2026-04-19T03:41:02.123Z"
}
```

### 필드 규약
- `event_id`: ULID/uuid7 권장.
- `entity_type`: 4종 외 추가 불가(`manual_action`, `system` 포함).
- `from_status`, `to_status`: entity 종류에 따라 해당 상태 도메인 값 사용.
- `reason`: 전이 사유. `manual_reinject`, `rollback`, `review_downgrade`, `quota_exceeded`, `circuit_open` 등 표준 사유 권장.
- `metrics`: 선택. 기본 keys `elapsed_ms` 권장.

## 3. 테스트 시나리오 Fixture (9종)

각 시나리오는 `tests/fixtures/scenario_<id>/` 아래에 아래 3개 파일을 둔다.
- `input.json`: Payload 초기 상태 + 외부 모의 응답(mock)
- `expected_events.jsonl`: 발생해야 하는 event 시퀀스
- `expected_payload.json`: 최종 Payload 상태

### SC-01. 신규 수집 성공
- 입력: 신규 `video_id`, 수동 자막 존재, LLM 정상 응답.
- 기대 record 전이: `collected → extracted → normalized → reviewed_confirmed → promoted`.
- 기대 stage: 모든 stage `completed`.

### SC-02. 동일 해시 완전 스킵 (Rule C)
- 입력: 기존 Payload와 `transcript_hash` 동일.
- 기대: Collect stage 즉시 `skipped`, record 미생성. event `reason=rule_c_duplicate`.

### SC-03. 해시 변경 재처리 (Rule B)
- 입력: 기존 `source_key`, 새 `transcript_hash`.
- 기대: `payload_version += 1`, 기존 요약/룰이 `history[]`에 보존, 재분석 실행.

### SC-04. JSON 스키마 실패 후 재프롬프트 성공
- 입력: 1차 LLM 응답 줄글 포함.
- 기대: `SEMANTIC_JSON_SCHEMA_FAIL` 기록 → 재프롬프트 → 2차 성공 → `reviewed_confirmed`.

### SC-05. HTTP 429 → retry_wait
- 입력: Collect 단계에서 429 응답.
- 기대: stage `collect=failed`, `failure_reason_code=HTTP_429`, 다음 크론에서 재진입.

### SC-06. Promoted 후 Sync 5회 실패 → invalid
- 입력: Git push 5회 연속 conflict.
- 기대: 백오프 2/4/8/16/32분 이벤트, `record_status=invalid`, DLQ `dlq/GIT/<date>/<source_key>.json` 생성, 경보 이벤트 1건.

### SC-07. 관리자 수동 재투입
- 입력: `invalid` 상태 record에 대해 `manual_reinject` 이벤트 주입, reason `fix_applied`.
- 기대: `invalid → collected` 전이 1건, event `entity_type=manual_action`, `actor=user:<name>`.

### SC-08. 아카이브 포함 중복 판정
- 입력: ARCHIVED 폴더에만 존재하는 `source_key`와 동일한 `transcript_hash`의 신규 후보.
- 기대: Rule C 발동 → 스킵. 인덱스 조회가 ARCHIVED까지 포괄했음을 검증.

### SC-09. YouTube 측 영상 삭제
- 입력: 기존 promoted record에 대해 후속 run이 410 응답을 감지.
- 기대: `archive_state=REMOVED`, JSON 보존, Markdown 비공개 섹션 이동, event `reason=yt_video_removed`.

## 4. Fixture 예시 (SC-01)
### input.json (축약)
```json
{
  "payload": {
    "schema_version": "10.0.0",
    "source_key": "youtube:TEST0000001",
    "video_id": "TEST0000001",
    "record_status": "collected",
    "caption_source": "manual"
  },
  "mocks": {
    "youtube_transcript_api": {"return": "자막 원문..."},
    "llm.extract": {"return": {"summary":"...","rules":["..."],"tags":["단타"]}}
  }
}
```
### expected_events.jsonl (축약)
```
{"event_id":"evt_1","entity_type":"record","from_status":"collected","to_status":"extracted", ...}
{"event_id":"evt_2","entity_type":"record","from_status":"extracted","to_status":"normalized", ...}
{"event_id":"evt_3","entity_type":"record","from_status":"normalized","to_status":"reviewed_confirmed","metrics":{"cosine":0.72,"rules":3}}
{"event_id":"evt_4","entity_type":"record","from_status":"reviewed_confirmed","to_status":"promoted"}
```

## 5. Replay/감사
- Snapshot이 손상돼도 `events.jsonl`을 replay해 Payload 재생 가능해야 한다.
- replay 도구: `tools/events_replay.py` (예정).
- 정합성 검증: snapshot의 `record_status`가 events 최종 `to_status`와 일치해야 한다.
