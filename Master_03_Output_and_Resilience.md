# [Harness Master 03] Persistence, Sync, Resilience & QA (v10)

> **Scope**: YouTube Source Adapter — Promote/Package 단계 + Resilience + QA.
> **Pipeline 매핑 (v2)**: Promote (JSON 영속화) → Package (Markdown 렌더 + Git Sync).

## 목적 (Purpose)
- JSON 영속화, Markdown 렌더링, Git 동기화, DLQ 처리, 아카이빙, 운영 지표와 경보 기준을 정의한다.
- v2 `PromotedArtifact` 계약과 정합.

## 1. Module C: Promote (JSON 영속화)
### I/O 명세
- **Input**: `record_status=reviewed_confirmed` Payload
- **Output**: Repository 내 저장 완료 Payload (`record_status=promoted`, `stage_status.promote=completed`)

### 저장 원칙
- 경로: `data_store/<yyyymm>/<source_key>.json`
- atomic write (임시파일 → rename).
- `reviewed_inferred/unverified`는 Promote 진입 금지 (Master_02 §3.3).
- 성공 시 `record_status=promoted` + event log 기록.
- 실패 시 `failure_reason_code` 기록 후 상태 분기.

### Rollback
- `promoted` 데이터 정정 시 `payload_version += 1`으로 신규 revision 저장 + `history[]`에 이전 스냅샷 + `reason=rollback`.
- `payload_version` 감소 금지.

## 2. Module D: Package (Renderer + Sync Worker)
### 역할 분리
- **Renderer**: Promoted JSON을 옵시디언 템플릿에 맞는 Markdown으로 렌더링.
- **Sync Worker**: Markdown 쓰기 + Git Sync만 담당.
- 동기화 실패는 Sync Worker가 재시도. 분석 워커는 관여 금지.

### 템플릿
- 위치: `templates/obsidian_note.md.j2` (Jinja2).
- Frontmatter 표준: `title`, `source_key`, `source_query`, `published_at`, `collected_at`, `tags`, `confidence`, `run_id`.
- 본문: summary → rules → 원본 링크 → history 섹션.

### Git 정책
- 브랜치: `data/main` (코드/설계와 영향 격리).
- 인증: GitHub App token (PAT 금지).
- 커밋 단위: source_key 1건당 1 commit. 메시지 `data(youtube): <video_id> promoted`.
- 충돌 전략: `rebase-onto`. 실패 시 `record_status=invalid` 격리 경로가 아닌 `failure_reason_code=GIT_CONFLICT` 후 재시도 큐.

### PII 전처리
- Renderer 호출 전 정규식 마스킹: 전화번호, 이메일, 주민번호 패턴.
- 상세 패턴은 Appendix D.

## 3. Sync Worker 재시도
- 상한: **5회**.
- 백오프: 2분 → 4분 → 8분 → 16분 → 32분.
- 초과 시 `record_status=invalid`로 DLQ 격리 + 경보.
- Sync 실패는 분석 비용 재사용 금지. JSON 영속본 유지.

## 4. DLQ 실물 설계
- 경로: `dlq/<code>/<yyyymmdd>/<source_key>.json`
- `code`는 `failure_reason_code`의 접두어 단위로 분류.
- Payload 전체 + 실패 시점 events snapshot 복사.
- 별도 워커 `dlq_replayer`가 일 1회 재시도. 재시도 성공 시 원 폴더 복귀, 실패 시 `retry_count += 1` + 재저장.
- `retry_count >= 5`에 도달한 항목은 `reviewer=human`으로 전환, `review_queue/`로 이동.

### 오류 분류
| 유형 | 예시 | 처리 |
|---|---|---|
| 인프라 | `HTTP_429`, `HTTP_5XX`, `NETWORK_TIMEOUT` | stage_status.<x>=failed, next crontab에서 재진입 |
| 시맨틱/파싱 | `SEMANTIC_JSON_SCHEMA_FAIL`, `SEMANTIC_EMPTY_RULES` | 재프롬프트 1회, 실패 시 `reviewed_rejected` |
| 동기화 | `GIT_CONFLICT`, `GIT_AUTH_FAIL` | Sync Worker 재시도. 상한 초과 시 `invalid` |
| 시스템 | `SYS_LOCK_TIMEOUT`, `SYS_DISK_FULL` | 즉시 경보. 자동 재시도 금지 |

## 5. 아카이빙 전략
### 분할 기준
아래 중 하나라도 만족하면 분할.
- 라인 수 1,000줄 초과
- 파일 크기 100KB 초과
- 분기(Quarter) 변경

### 처리 방식
- 파일명: `[주제]_<YYYY>_Q<N>_<seq>_Archive.md` (동일 Q에 재발생 시 `seq` 증가).
- 경로: `Archive/`.
- 기존 문서 메타 `archive_state=ARCHIVED`.
- 중복 판정(Rule A/B/C)은 ACTIVE + ARCHIVED 모두 조회. `index/collector.sqlite`로 일괄 조회.

## 6. 삭제/비공개 영상 처리
- YouTube API 410/403 감지 시 `archive_state=REMOVED`.
- JSON 영속본은 보존. Markdown에서는 "비공개 섹션(`<details>` blockquote)" 아래로 이동.
- `failure_reason_code=YT_VIDEO_REMOVED` + event log 기록.

## 7. QA 운영 지표
### 처리/비용 지표
- 단계별 실패율 (`record_status=invalid` 비율, `reviewed_unverified` 비율)
- 평균 처리 시간 (stage별)
- 중복 스킵률 (Rule C)
- 재처리율 (Rule B)
- 일일 LLM cost_usd 합
- 일일 YouTube Data API quota 사용량

### 품질 지표
- `confirmed` 승격률 (전체 대비)
- Actionable Rule 비율
- Hallucination 의심률 (`reviewed_unverified` 비율)
- 채널별 품질 점수 (`channel_quality.jsonl`)
- 낚시성 감점률

### 저장
- `metrics/daily.jsonl` append. Appendix C 스키마 참조.

## 8. 경보 기준 (Alerts)
| 조건 | 처리 |
|---|---|
| `invalid` 비율 > 10% (3회 연속) | GitHub Issue `alert:invalid_rate_high` |
| `HTTP_429` 일 5회 초과 | 서킷브레이커 10분 차단 + GitHub Issue `alert:rate_limit` |
| `GIT_CONFLICT` 누적 20건 | GitHub Issue `alert:git_health` |
| 평균 처리 시간 2× 증가 | GitHub Issue `alert:perf_regression` |
| Daily budget 80% 도달 | GitHub Issue `alert:budget_warning` |
| Daily budget 초과 | `COLLECTOR_PAUSED=1` 자동 설정 + `alert:budget_exceeded` |
| GitHub Actions runner minutes 80% | GitHub Issue `alert:ci_quota` |

### 채널
- 기본: GitHub Issues(라벨 `alert:<code>`).
- 선택: env `SLACK_ALERT_URL` 있을 경우 Slack webhook 병행.

## 9. 상태 전이 시뮬레이터 필수 케이스
Appendix B 9종 시나리오 중 v2 fixture 형태 자동 재생 권장.
1. 신규 수집 성공
2. 동일 해시 완전 스킵
3. 해시 변경 재처리 (Rule B)
4. JSON/semantic 실패 후 1회 재프롬프트
5. HTTP_429 → stage_status.collect=failed → 다음 크론 재진입
6. Promoted 후 Sync 실패 → 상한 5회 후 `invalid` 격리
7. 관리자 수동 재투입 (`invalid → collected`, reason 기록)
8. 아카이브 포함 중복 판정 (ARCHIVED 대상 Rule C)
9. YouTube 측 영상 삭제 → `archive_state=REMOVED`

## 10. 운영 기록
- 모든 예외·수동 조치 내역은 `logs/events.jsonl`에 entity_type=`manual_action`으로 남긴다.
- 동일 장애 재발 여부 판단을 위해 `failure_reason_code`와 처리 결과 동반 기록.

## 11. 참조
- Appendix A: v2 Schema 매핑 및 reviewer trace (리뷰 항목 → 본문 위치 매핑)
- Appendix B: 상태 전이표 + 9개 테스트 시나리오 fixture
- Appendix C: metrics 스키마, 경보 임계값, 서킷브레이커
- Appendix D: PII 마스킹, Secret 로테이션, DMCA/삭제 처리
