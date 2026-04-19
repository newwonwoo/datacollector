# Appendix C — Observability

> **목적**: 관측성 3축(logs/metrics/traces), 쿼터·비용 추적, 경보 임계값, 서킷브레이커를 정의한다.

## 1. 관측 3축
| 축 | 경로 | 형식 | 용도 |
|---|---|---|---|
| Logs | `logs/events.jsonl` | append-only JSONL | 모든 상태 전이/예외 원시 기록 |
| Metrics | `metrics/daily.jsonl` | append-only JSONL | 일간 집계 (대시보드/경보) |
| Traces | `logs/traces.jsonl` | append-only JSONL | run_id 기준 stage 타임라인 (선택) |

## 2. metrics/daily.jsonl 스키마
```json
{
  "date": "2026-04-19",
  "run_count": 4,
  "records_processed": 128,
  "records_promoted": 98,
  "records_reviewed_unverified": 14,
  "records_reviewed_rejected": 6,
  "records_invalid": 10,
  "rule_c_skipped": 32,
  "rule_b_reprocessed": 4,
  "avg_stage_runtime_sec": {
    "discover": 3.1,
    "collect": 12.4,
    "extract": 28.7,
    "normalize": 4.0,
    "review": 0.9,
    "promote": 0.8,
    "package": 6.3
  },
  "cost_usd": 1.42,
  "youtube_quota_used": 2340,
  "youtube_quota_limit": 10000,
  "llm_tokens_in": 412000,
  "llm_tokens_out": 89000,
  "ci_runner_minutes_used_month": 118,
  "ci_runner_minutes_limit_month": 2000
}
```

### 집계 방식
- 매 run 종료 시 `tools/metrics_aggregate.py`가 당일 line을 갱신/추가 (append 후 dedup).
- daily job(UTC 00:00 KST 09:00)이 하루치 최종 확정.

## 3. 쿼터/비용 추적
### YouTube Data API
- `search.list` 1회 = 100 units. `videos.list` 1회 = 1 unit. `captions.list` 1회 = 50 units.
- 일일 무료 한도 10,000 units.
- 실측: 각 호출 전 `quota_used` 추정값을 event `metrics.quota_cost`에 기록, daily 집계.

### LLM 비용
- 모델별 단가는 `config/llm_pricing.yml`에 관리.
- 토큰 수 × 단가 = `cost_usd`. Payload `llm_context.cost_usd`에 기록 후 metrics 집계.

### Daily Budget Guard
- `config/budget.yml`:
  ```yaml
  daily_llm_cost_usd_max: 1.00
  daily_youtube_quota_max: 8000
  ci_runner_minutes_month_max: 1600
  ```
- 80% 도달 시 `alert:budget_warning` 이슈 생성.
- 100% 초과 시 `COLLECTOR_PAUSED=1` 환경변수 자동 쓰기(GitHub Actions workflow step) + `alert:budget_exceeded`.

## 4. 경보 임계값 테이블
| 메트릭 | 조건 | 알림 라벨 | 채널 |
|---|---|---|---|
| `records_invalid / records_processed` | > 10% (3일 연속) | `alert:invalid_rate_high` | GitHub Issue + Slack |
| HTTP_429 이벤트 수 | > 5 / day | `alert:rate_limit` | Issue + 서킷브레이커 |
| `GIT_CONFLICT` 누적 | > 20 | `alert:git_health` | Issue |
| `avg_stage_runtime_sec.extract` | > 기준 × 2 | `alert:perf_regression` | Issue |
| Daily cost_usd | > 80% budget | `alert:budget_warning` | Issue |
| Daily cost_usd | > 100% budget | `alert:budget_exceeded` | Issue + Kill Switch |
| GitHub Actions runner minutes | > 80% month budget | `alert:ci_quota` | Issue |
| `reviewed_unverified / total` | > 25% | `alert:quality_drop` | Issue |

### 전달 채널
- 기본: GitHub Issues 자동 생성. 라벨 `alert:<code>`, 본문에 직전 24시간 metrics 요약 + 관련 event_id 링크.
- 선택: env `SLACK_ALERT_URL` 설정 시 Slack incoming webhook로 요약 전송.
- 알림 중복 억제: 동일 라벨 open issue 존재 시 코멘트만 추가.

## 5. 서킷브레이커
외부 API 연속 실패 시 즉시 차단해 쿼터/비용 낭비 차단.

| Breaker | Open 조건 | 차단 시간 | Half-open 검증 |
|---|---|---|---|
| `youtube_api` | 5분 내 HTTP_429 3회 | 10분 | 1건 호출 성공 시 Close |
| `llm_api` | 5분 내 `LLM_TIMEOUT` 3회 | 15분 | 1건 호출 성공 시 Close |
| `git_sync` | 10분 내 `GIT_AUTH_FAIL` 2회 | 30분 | 수동 해제 |

- Breaker 상태는 `state/breakers.json`에 저장.
- Open 시 관련 stage 즉시 `skipped` + event `reason=circuit_open`.

## 6. CI(GitHub Actions) 관측
- 각 workflow job 종료 시 `actions/measure-runtime` step이 runtime을 기록.
- 월 누적은 `metrics/daily.jsonl.ci_runner_minutes_used_month`로 집계.
- 80%에서 `alert:ci_quota`.

## 7. 대시보드 샘플 쿼리 (SQLite sidecar)
인덱스 DB `index/collector.sqlite`에 `metrics_daily` view를 만들어 빠른 조회.
```sql
-- 최근 7일 promote 성공률
SELECT date,
       records_promoted * 1.0 / NULLIF(records_processed, 0) AS promote_rate
FROM metrics_daily
WHERE date >= date('now', '-7 days')
ORDER BY date DESC;

-- 채널별 invalid 비율 상위 10
SELECT channel_id,
       SUM(CASE WHEN record_status='invalid' THEN 1 ELSE 0 END) AS invalid_cnt,
       COUNT(*) AS total_cnt,
       1.0 * SUM(CASE WHEN record_status='invalid' THEN 1 ELSE 0 END) / COUNT(*) AS ratio
FROM records
GROUP BY channel_id
ORDER BY ratio DESC
LIMIT 10;
```

## 8. 운영 체크리스트 (주간)
- [ ] `metrics/daily.jsonl` 지난 7일 라인 확인
- [ ] `reviewed_unverified` 비율 추이 확인
- [ ] open `alert:*` 이슈 잔량 확인
- [ ] CI runner minutes 월간 누적 확인
- [ ] DLQ `retry_count >= 5` 건 수동 리뷰
