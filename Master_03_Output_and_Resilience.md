# [Harness Master 03] Persistence, Sync, Resilience & QA (V9)

## 목적 (Purpose)
- JSON 영속화, Markdown 렌더링, Git 동기화, DLQ 처리, 아카이빙, 운영 지표와 경보 기준을 정의한다.

## 1. Module C: Persistence
### I/O 명세
- **Input**: Validated JSON Payload
- **Output**: Repository 내 저장 완료 Payload (`status=PROCESSED`)

### 저장 원칙
- `data_store/`에 JSON을 저장한다.
- 저장은 atomic write로 수행한다.
- 성공 시 `status=PROCESSED`로 전이한다.
- 실패 시 `failure_reason_code`를 남기고 상태 전이를 분기한다.

## 2. Module D: Obsidian Renderer & Sync Worker
### 역할 분리
- **Renderer**: JSON을 옵시디언 템플릿에 맞는 Markdown으로 렌더링한다.
- **Sync Worker**: Markdown 쓰기와 Git Sync만 담당한다.
- `SYNC_FAILED` 재처리는 분석 워커가 아니라 Sync Worker가 담당한다.

### Sync 규칙
- Frontmatter, 태그, 타임스탬프, 원본 링크를 표준 템플릿으로 생성한다.
- Markdown 쓰기 또는 Git Sync 실패 시 `status=SYNC_FAILED`로 분리한다.
- 분석 결과 JSON은 유지하고, 동기화만 재시도한다.

## 3. 문서 아카이빙 전략
### 분할 기준
아래 중 하나라도 만족하면 분할한다.
- 라인 수 1,000줄 초과
- 파일 크기 100KB 초과
- 분기(Quarter) 변경

### 처리 방식
- 기존 파일을 `[주제]_2026_Q2_Archive.md` 형식으로 `Archive/` 폴더에 이동한다.
- 새 파일을 생성하고 이후 증분 데이터를 이어서 기록한다.
- 이때 `archive_state=ARCHIVED`는 기존 문서 메타에만 적용하고, 처리 상태(`status`)와 혼합하지 않는다.

## 4. DLQ 및 재처리 정책
### 인프라성 오류
- 예: `HTTP_429`, `HTTP_5XX`, `NETWORK_TIMEOUT`
- 처리: `status=RETRY_WAIT`
- 동작: 당일 처리를 종료하고 다음 크론에서 `PENDING`으로 복귀

### 시맨틱/파싱 오류
- 예: `JSON_SCHEMA_FAIL`, `SEMANTIC_EMPTY_RULES`, `SEMANTIC_LOW_QUALITY_SUMMARY`
- 처리: `retry_count += 1`
- 최대 1회 재프롬프팅 후 계속 실패하면 `status=FAILED`

### 동기화 오류
- 예: `GIT_CONFLICT`, `MARKDOWN_WRITE_FAIL`
- 처리: `status=SYNC_FAILED`
- 분석 비용을 다시 쓰지 않고 Sync Worker가 재시도

## 5. 실패 코드 정규화
### 권장 `failure_reason_code`
- `HTTP_429`
- `HTTP_5XX`
- `NETWORK_TIMEOUT`
- `NO_TRANSCRIPT`
- `JSON_SCHEMA_FAIL`
- `SEMANTIC_EMPTY_RULES`
- `SEMANTIC_LOW_QUALITY_SUMMARY`
- `GIT_CONFLICT`
- `MARKDOWN_WRITE_FAIL`

### `failure_reason_detail`
- 원문 에러 메시지, 응답 일부, 충돌 파일명 등 세부 내용을 기록한다.
- 대시보드 집계는 `failure_reason_code`, 현장 디버깅은 `failure_reason_detail`을 사용한다.

## 6. QA 운영 지표
### 처리/비용 지표
- 단계별 실패율 (`RETRY_WAIT`, `SYNC_FAILED`, `FAILED` 비율)
- 평균 처리 시간
- 중복 스킵률 (`Rule C` 발동 비율)
- 재처리율 (`Rule B` 발동 비율)

### 품질 지표
- Actionable Rule 비율
- Hallucination 의심률
- 채널별 품질 점수
- 낚시성 감점률

## 7. 경보 기준 (Alerts)
- `FAILED > 10%`가 3회 연속 발생하면 알림
- `HTTP_429`가 일일 5회 초과면 쿼터 축소 모드 진입
- `SYNC_FAILED` 20건 누적 시 Git/파일시스템 점검 알림
- 평균 처리 시간이 기준치 대비 2배 이상 증가하면 성능 점검 알림

## 8. 상태 전이 시뮬레이터 필수 케이스
다음 시나리오는 테스트 하네스에서 반드시 통과해야 한다.
1. 신규 수집 성공
2. 동일 해시 완전 스킵
3. 해시 변경 재처리
4. JSON/semantic 실패 후 1회 재프롬프트
5. `HTTP_429` 발생 후 `RETRY_WAIT`
6. `PROCESSED` 후 Sync 실패로 `SYNC_FAILED`

## 9. 운영 기록
- 모든 예외와 수동 조치 내역은 `Harness_08_Log.md`에 남긴다.
- 동일 장애 재발 여부를 판단할 수 있도록 실패 코드와 처리 결과를 함께 적는다.
