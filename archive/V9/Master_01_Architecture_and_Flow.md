# [Harness Master 01] Foundation, Data Model & Ops Policy (V9)

## 목적 (Purpose)
- 파이프라인 전체가 동일한 Payload를 기준으로 움직이도록 데이터 모델, 상태 체계, 우선순위 정책, 보안 원칙을 정의한다.
- 운영 중 재처리, 디버깅, 비용 통제, 동시성 충돌 방지를 가능하게 한다.

## 1. Core Payload (단일 데이터 객체)
```json
{
  "video_id": "string",
  "channel_id": "string",
  "title": "string",
  "published_at": "2026-04-19T03:25:10Z",
  "collected_at": "2026-04-19T03:40:22Z",
  "source_query": "단타 매매법",
  "language": "ko",
  "transcript_hash": "sha256_hex",
  "status": "PENDING | COLLECTED | VALIDATED | PROCESSED | SYNC_FAILED | RETRY_WAIT | FAILED",
  "archive_state": "ACTIVE | ARCHIVED",
  "retry_count": 0,
  "priority_score": 100,
  "payload_version": 1,
  "failure_reason_code": null,
  "failure_reason_detail": null,
  "llm_context": {
    "model_name": "gemini-1.5-flash",
    "model_version": "001",
    "temperature": 0.2,
    "prompt_version": "v1.3_saju"
  },
  "history": []
}
```

## 2. 상태값 및 보관축 분리
- `status`는 처리 상태만 표현한다.
  - `PENDING`: 수집 대기
  - `COLLECTED`: 자막 수집 완료
  - `VALIDATED`: LLM 구조/의미 검증 완료
  - `PROCESSED`: JSON 저장 완료
  - `SYNC_FAILED`: JSON 저장은 끝났으나 Markdown/Git 동기화 실패
  - `RETRY_WAIT`: 인프라 오류로 다음 스케줄 대기
  - `FAILED`: 재시도 종료 후 실패 확정
- `archive_state`는 보관 여부만 표현한다.
  - `ACTIVE`: 현재 운용 문서
  - `ARCHIVED`: 아카이브 문서로 이동 완료

## 3. 멱등성 및 중복 판정 규칙
- **Rule A (신규)**: `video_id`가 저장소에 없으면 신규 수집 진행.
- **Rule B (변경)**: `video_id` 동일 + `transcript_hash` 불일치면 자막 수정본으로 간주한다.
  - `payload_version += 1`
  - 이전 `transcript_hash`, 이전 분석 시각, 이전 요약/룰 일부를 `history[]`에 보존한다.
- **Rule C (중복)**: `video_id` 동일 + `transcript_hash` 동일이면 완전 스킵한다.

## 4. 상태 전이 규칙
- 허용 전이
  - `PENDING -> COLLECTED -> VALIDATED -> PROCESSED`
  - `PENDING -> RETRY_WAIT`
  - `COLLECTED -> FAILED`
  - `VALIDATED -> FAILED`
  - `PROCESSED -> SYNC_FAILED`
  - `SYNC_FAILED -> PROCESSED` (재동기화 성공 시)
  - `RETRY_WAIT -> PENDING` (다음 크론 시작 시)
- 금지 전이
  - `FAILED -> PROCESSED` 직접 복귀 금지
  - `SYNC_FAILED -> VALIDATED` 역행 금지
  - `PROCESSED -> COLLECTED` 역행 금지

## 5. Priority Policy (우선순위 큐)
`priority_score`는 아래 기본 공식을 사용한다.
- 기본점수: `100`
- `target_channel_id` 지정: `+30`
- 최근 7일 이내 신규 영상: `+20`
- 실패 재처리 건: `-10`
- 낚시 의심 점수 발생: `-25`
- 과도한 길이(예: 90분 이상): `-15`
- 할당량 80% 이상 소진 시 일반 자연어 탐색 모드: 자동 드롭

처리 우선순위는 다음과 같다.
1. `target_channel_id`가 있는 교과서 모드
2. 최근 7일 신규 영상
3. `SYNC_FAILED` 재동기화 워커
4. `RETRY_WAIT` 복귀 대상
5. 일반 자연어 탐색 모드

## 6. 보안 및 저작권
- API Key는 하드코딩 금지. GitHub Secrets 또는 로컬 `.env`에서만 주입한다.
- 로그 파일에 Secret 값을 절대 남기지 않는다.
- 수집 데이터 사용 목적은 개인 지식 베이스 및 내부 연구로 제한한다.
- 외부 API 서빙, 대량 재배포, 상업적 재판매 구조는 설계 범위에서 제외한다.

## 7. 동시성 및 잠금(Concurrency Control)
- 동일 크론 중복 실행 또는 수동 재실행에 대비해 `processing lock`을 둔다.
- `video_id` 단위 lease 또는 lock file을 통해 동시 처리 충돌을 막는다.
- 저장은 atomic write 원칙을 적용한다. 임시 파일 작성 후 rename으로 교체한다.

## 8. 개발/운영 환경
- **0-Cost Strategy**: GitHub Actions + GitHub Repo 기반 운영.
- **Local Dev Mode**: `mock_data.json`으로 API 비용 없이 end-to-end 검증 가능해야 한다.
- **추적 로그**: `run_log.txt`에 단계별 이벤트, 상태 전이, 실패 코드, 처리 시간을 남긴다.
