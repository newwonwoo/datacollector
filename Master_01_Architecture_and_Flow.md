# [Harness Master 01] Foundation, Data Model & Ops Policy (v10)

> **Scope**: YouTube Source Adapter 스펙. 플랫폼 상위 계약은 v2 문서(`architecture_v2.md`, `state_model_v2.md`, `data_contract_v2.md`) 참조.
> **Changelog**: `CHANGELOG_v10.md` 및 `archive/V9/*` (V9 원본 보존).

## 목적 (Purpose)
- 파이프라인 전체가 동일한 Payload를 기준으로 움직이도록 데이터 모델, 상태 체계, 우선순위 정책, 보안 원칙을 정의한다.
- 운영 중 재처리, 디버깅, 비용 통제, 동시성 충돌 방지를 가능하게 한다.
- v2 플랫폼 계약(schema_version, run_id, provenance, 3-tier status, event log)을 충족한다.

## 1. Core Payload (단일 데이터 객체)
```json
{
  "schema_version": "10.0.0",
  "source_key": "youtube:ABCDEFGHIJK",
  "video_id": "ABCDEFGHIJK",
  "channel_id": "UCxxxx",
  "title": "string",
  "published_at": "2026-04-19T03:25:10Z",
  "collected_at": "2026-04-19T03:40:22Z",
  "source_query": "단타 매매법",
  "language": "ko",
  "caption_source": "manual | asr | none",
  "transcript_hash": "sha256_hex",
  "provenance": {
    "source_id": "youtube:ABCDEFGHIJK",
    "segment_id": "youtube:ABCDEFGHIJK#full",
    "run_id": "run_2026-04-19T03:40Z_abc123"
  },
  "run_id": "run_2026-04-19T03:40Z_abc123",
  "stage_status": {
    "discover": "completed",
    "collect": "completed",
    "extract": "completed",
    "normalize": "completed",
    "review": "not_started",
    "promote": "not_started",
    "package": "not_started"
  },
  "record_status": "collected | extracted | normalized | reviewed_confirmed | reviewed_inferred | reviewed_unverified | reviewed_rejected | promoted | invalid",
  "archive_state": "ACTIVE | ARCHIVED | REMOVED",
  "retry_count": 0,
  "priority_score": 100,
  "payload_version": 1,
  "failure_reason_code": null,
  "failure_reason_detail": null,
  "llm_context": {
    "model_name": "gemini-1.5-flash",
    "model_version": "001",
    "temperature": 0.2,
    "prompt_version": "v1.3_saju",
    "input_tokens": 0,
    "output_tokens": 0,
    "cost_usd": 0.0
  },
  "confidence": "unverified | inferred | confirmed | rejected",
  "reviewer": "auto | human | none",
  "history": []
}
```

### history[] 원소 스키마
```json
{
  "at": "2026-04-19T05:00:00Z",
  "event_id": "evt_xxx",
  "reason": "transcript_changed | rollback | manual_reinject | review_downgrade",
  "prev_transcript_hash": "sha256_hex",
  "prev_summary": "...",
  "prev_rules_snapshot": ["..."],
  "prev_confidence": "confirmed"
}
```

### 필드 규정
- `schema_version`: semver. major 변경 시 마이그레이션 스크립트 필수.
- `source_key`: 전 플랫폼 공통 네임스페이싱. `youtube:{video_id}`. 추후 `web:`, `podcast:` 확장.
- `payload_version`: 콘텐츠 리비전(content revision). Rule B 재처리 시 증가. 롤백 시 감소 금지 — `history[]`에 rollback revision 추가.
- `run_id`: 이 Payload를 이동시킨 run 식별자. Event log 집계 키.
- `provenance`: v2 계약 필수. 중간 파생물(claim, summary)도 이 키로 역추적.

## 2. 상태 체계 (3-tier)
v2 `state_model_v2.md`와 정합. 세 수준의 상태를 분리해 보관한다.

### 2.1 Run 상태 (별도 저장: `runs/<run_id>.json`)
- `created → running → completed | failed | partially_completed`

### 2.2 Stage 상태 (Payload.stage_status)
- 각 stage: `not_started | started | completed | failed | skipped`
- 필수 stage: `discover, collect, extract, normalize, review, promote, package`

### 2.3 Record 상태 (Payload.record_status)
- 값: `collected | extracted | normalized | reviewed_confirmed | reviewed_inferred | reviewed_unverified | reviewed_rejected | promoted | invalid`
- 기존 V9 `status`(PROCESSED/FAILED 등) → v10 `record_status`로 대체.
- `invalid`는 데이터 삭제가 아니라 격리(DLQ)를 의미 (Master_03 §4 참조).

### 2.4 archive_state (보관축)
- `ACTIVE | ARCHIVED | REMOVED`
- `REMOVED`: YouTube 측 삭제/비공개 전환 감지 시 (Master_03 §10 참조).

## 3. 멱등성 및 중복 판정 규칙
인덱스 조회는 ACTIVE + ARCHIVED 모두 대상(아카이브 이동 후 재수집 방지).

- **Rule A (신규)**: `source_key`가 저장소에 없으면 신규 수집 진행.
- **Rule B (변경)**: `source_key` 동일 + `transcript_hash` 불일치면 자막 수정본으로 간주한다.
  - `payload_version += 1`
  - 이전 `transcript_hash`, 이전 분석 시각, 이전 요약/룰 스냅샷을 `history[]`에 보존한다.
- **Rule C (중복)**: `source_key` 동일 + `transcript_hash` 동일이면 완전 스킵한다.

### transcript_hash 정규화
동일 자막이 포맷 차이만으로 다른 해시가 되는 것을 막는다.
1. 타임코드/인덱스 제거 (SRT, VTT 헤더 모두 제거)
2. 연속 공백 1 space로 압축, 줄바꿈 제거
3. 유니코드 NFC 정규화
4. trim 후 SHA-256(UTF-8 bytes)

## 4. 상태 전이 규칙 (요약)
자세한 전이표는 `docs/Appendix_B_State_and_Event_Log.md` 참조.

### Record 전이 허용
- `collected → extracted → normalized → reviewed_{confirmed|inferred|unverified|rejected}`
- `reviewed_confirmed → promoted`
- `reviewed_inferred/unverified` → (human review 후) `reviewed_confirmed | reviewed_rejected`
- `any → invalid` (격리 사유 기록 필수)
- `invalid → collected` (관리자 수동 재투입, reason 필수)

### Stage 전이 허용
- `not_started → started → completed | failed | skipped`
- 선행 stage `completed` 없이 다음 stage `started` 진입 금지.
- stage 실패 시 다음 stage 자동 진입 금지. 프로젝트 설정 `continue_on_error=true` 시 예외.

### 금지 전이
- `reviewed_rejected → promoted` 직접 금지 (review re-run 필수).
- `promoted → any` 직접 금지 (rollback은 `history[]` revision으로 처리).

## 5. Priority Policy
`priority_score` 기본 공식. 범위 `[0, 200]`.
- 기본점수: `100`
- `target_channel_id` 지정: `+30`
- 최근 7일 이내 신규 영상: `+20` (기준: `published_at`, UTC)
- Aging: `record_status=collected`에서 1일 경과마다 `+5`, 최대 `+35`
- 실패 재처리 건: `-10`
- 낚시 의심: `-25`
- 장문(90분+): `-15`
- `cost_guard` 활성(Daily Budget 초과 근접): 신규 자연어 탐색 `priority_score=0` 강제.

처리 순위:
1. Fast-Track: `target_channel_id` 지정 건 (search step skip, 큐 1순위)
2. 최근 7일 신규
3. SYNC 재시도(Master_03 §3)
4. `invalid` 재투입 건
5. 일반 자연어 탐색

## 6. 보안 및 저작권
- API Key 하드코딩 금지. GitHub Secrets 또는 로컬 `.env`에서만 주입.
- Secret 로테이션 주기 90일 (`docs/Appendix_D_Security_and_Compliance.md`).
- 로그에 Secret 값 절대 미기록. `events.jsonl`에서도 금지.
- 수집 데이터 용도: 개인 지식베이스/내부 연구 한정. 외부 API 서빙·대량 재배포·상업적 재판매 제외.
- DMCA/삭제 대응: Appendix D 플로우.

## 7. 동시성 및 잠금 (Concurrency Control)
### 잠금 구조
```json
{
  "source_key": "youtube:ABCDEFGHIJK",
  "owner_id": "worker-<hostname>-<pid>",
  "acquired_at": "2026-04-19T03:40:22Z",
  "lease_expires_at": "2026-04-19T03:50:22Z",
  "heartbeat_at": "2026-04-19T03:42:22Z"
}
```
- Lease TTL: **10분**. Heartbeat: **2분**.
- `heartbeat_at`이 4분 이상 정체되면 다른 워커가 회수 가능.
- 저장: `locks/<source_key>.json` atomic write.

### 저장 원자성
- 임시 파일 작성 후 `rename`으로 교체 (POSIX atomic).
- 작성 전 lockfile 확인 필수.

### Kill Switch
- env `COLLECTOR_PAUSED=1` 감지 시 모든 stage는 즉시 `skipped`로 기록 후 종료.
- DMCA·비용 초과·계정 경고 등 돌발 상황 대응 단일 스위치.

## 8. 관측성 (Observability)
v2 event log 계약을 수용한다. `run_log.txt` 단일 축은 폐기.

- `logs/events.jsonl`: 구조화 이벤트 스트림 (append-only).
  ```json
  {"event_id":"evt_xxx","run_id":"run_...","entity_type":"record","entity_id":"youtube:...","from_status":"collected","to_status":"extracted","reason":"","metrics":{"elapsed_ms":420},"recorded_at":"..."}
  ```
- `metrics/daily.jsonl`: 일간 집계. 필드 `date, processed, failed, retry_wait, sync_failed, avg_runtime_sec, cost_usd, youtube_quota_used, llm_tokens_used`.
- `logs/traces.jsonl`: run_id 기반 stage 타임라인 (선택).
- Snapshot(Payload) + Events 병행. Snapshot 손상 시 Events로 재생 가능.

## 9. 개발/운영 환경
- **0-Cost Strategy**: GitHub Actions + GitHub Repo 기반 운영.
- **GitHub Actions 쿼터 감시**: 월 free 2,000분. 80% 도달 시 경보 (Appendix C).
- **Local Dev Mode**: `mock_data.json`으로 API 비용 없이 end-to-end 검증.
- **Daily Budget Guard**: `config/budget.yml`에 `daily_llm_cost_usd_max`, `daily_youtube_quota_max`. 초과 시 `COLLECTOR_PAUSED=1` 자동 설정 + 경보.
- **가벼운 인덱스**: `index/collector.sqlite` 1파일 허용. 원본은 JSON 유지. 조회 키: `source_key`, `transcript_hash`, `record_status`, `priority_score`.

## 10. Failure Code 접두어 규약
대시보드 집계를 위해 코드 계층화.
- `HTTP_*`: 범용 HTTP 오류 (예: `HTTP_429`, `HTTP_5XX`)
- `YT_*`: YouTube 특화 (예: `YT_NO_TRANSCRIPT`, `YT_VIDEO_REMOVED`)
- `LLM_*`: LLM 서비스 (예: `LLM_TIMEOUT`, `LLM_QUOTA_EXCEEDED`)
- `SEMANTIC_*`: 의미 검증 (예: `SEMANTIC_EMPTY_RULES`, `SEMANTIC_LOW_QUALITY_SUMMARY`)
- `GIT_*`: Git 동기화 (예: `GIT_CONFLICT`, `GIT_AUTH_FAIL`)
- `SYS_*`: 내부 시스템 (예: `SYS_LOCK_TIMEOUT`, `SYS_DISK_FULL`)

## 11. 참조
- Appendix A: Platform Mapping (v2 7-schema ↔ v10 Payload)
- Appendix B: State & Event Log (전이표, events.jsonl 스키마, 시나리오)
- Appendix C: Observability (metrics, 경보, 서킷브레이커)
- Appendix D: Security & Compliance (PII, Secret, DMCA)
