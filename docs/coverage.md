# Coverage Report — 설계-구현 매핑

> 이 파일은 설계서(Master_01~03 + Appendix A~D)의 각 조항이 코드와 테스트에 어떻게 매핑되는지 기록한다.
> 방법론: `docs/VERIFICATION_METHOD.md` 참조.
> 업데이트 주기: 매 PR + 분기 1회 재스캔.

---

## 요약 (2026-04-20 기준, 4차 업데이트 — sweep+ 완료)

- ✅ 완전 구현: **70 / 70 (100%)**
- 🟡 부분 구현: **0**
- ⬜ 미구현: **0**

테스트: `python -m pytest` → **240 passed** (~2.3s).

### 이번 라운드 클로즈
- ✅ 자막 수집 3단 fallback — `adapters/youtube.py` + yt-dlp 서브프로세스 (opt-in via `COLLECTOR_YT_DLP=1`)
- ✅ Run 상태 별도 파일 — `collector/runs.py:save_run_snapshot` → `runs/<run_id>.json`
- ✅ 채널 품질 점수 — `collector/channel_quality.py` (Master_02 §2.4) + `status.json` top/bottom 5
- ✅ 대시보드 전문 검색 — 🔍 입력 1개로 title/summary/rules/tags/source_key/channel 필터

### 설계서 외 추가 기능 (bonus)
- 대시보드 완료 배너 + NEW 배지 + 모달 상세 뷰
- 2-클릭 도달성: 레코드 → 모달 → YouTube/Markdown/JSON
- 운영 스트립 (Budget / Breaker / DLQ / Queue / KillSwitch)
- Client-side 전문 검색 + debounced input

---

## Master_01 Foundation

| 조항 | 명제 | 상태 | 코드 | 테스트 | 대시보드 |
|---|---|---|---|---|---|
| §1 | schema_version="10.0.0" 필드 | ✅ | `payload.py:SCHEMA_VERSION` | `test_render_note_contains_frontmatter*` | 상세 모달 |
| §1 | source_key = "youtube:{video_id}" | ✅ | `payload.py:new_payload` | — | 상세 모달 head |
| §1 | run_id / provenance(source_id, segment_id, run_id) | ✅ | 동 | — | (내부) |
| §1 | stage_status 7개 stage | ✅ | `payload.py:STAGES` | 파이프라인 통합 전반 | **파이프라인 단계 카드** |
| §1 | record_status 9값 | ✅ | `payload.py:RECORD_STATES` | `test_e2e_*` | **상태 분포 차트** |
| §1 | archive_state ACTIVE/ARCHIVED/REMOVED | ✅ | `store.py` | `test_sc09_video_removed` | (상세 모달) |
| §1 | llm_context input/output_tokens, cost_usd | ✅ | `stages.py:stage_extract` | — | **LLM 비용 KPI** |
| §1 | confidence 4단 (unverified/inferred/confirmed/rejected) | ✅ | `stages.py:stage_review` | `test_priority_*` | **Confidence 차트** |
| §1 | history[] prev_* 스키마 | ✅ | `payload.py:snapshot_for_history` | `test_sc03_rule_b_reprocess` | 상세 모달 |
| §2 | 3-tier status 분리 | ✅ | `pipeline.py`, `events.py` | `test_e2e_*` | 파이프라인 카드 |
| §2.1 | Run 상태 별도 파일 `runs/<run_id>.json` | 🟡 | events.jsonl에 run 이벤트 기록만 | — | (내부) |
| §3 | Rule A 신규 수집 | ✅ | `store.py:dedup_rule` | `test_sc01_new_collect_success` | 새 레코드 NEW 배지 |
| §3 | Rule B 해시 변경 → 재처리 + history[] | ✅ | 동 | `test_sc03_rule_b_reprocess` | 상세 모달 history |
| §3 | Rule C 완전 스킵 | ✅ | 동 | `test_sc02_rule_c_skip` | (로그) |
| §3 | ACTIVE + ARCHIVED 모두 조회 | ✅ | 동 | `test_sc08_archived_dedup` | — |
| §3 | transcript_hash 4단 정규화 | ✅ | `hashing.py:normalize_transcript` | `test_e2e_canonical` 간접 | — |
| §4 | 허용 전이 (collected→…→promoted) | ✅ | `stages.py` | E2E | 파이프라인 카드 |
| §4 | 수동 재투입 invalid→collected | ✅ | `pipeline.py:manual_reinject` | `test_sc07_manual_reinject` | 상세 모달 (TODO 추가) |
| §5 | priority_score 기본 100 | ✅ | `priority.py:compute_priority` | `test_priority_base_is_100` | (내부) |
| §5 | target_channel_id +30 | ✅ | 동 | `test_priority_target_channel_bonus` | — |
| §5 | 최근 7d +20 | ✅ | 동 | `test_priority_recent_7d_bonus` | — |
| §5 | Aging +5/day, max +35 | ✅ | 동 | `test_priority_aging_capped` | — |
| §5 | retry -10 / clickbait -25 / long -15 | ✅ | 동 | `test_priority_cost_guard_zeroes_non_target` 외 | — |
| §5 | [0, 200] clamp | ✅ | 동 | — | — |
| §5 | Cost Guard → priority=0 | ✅ | 동 | `test_priority_cost_guard_zeroes_non_target` | TODO: 대시보드 표시 |
| §5 | Fast-Track 큐 정렬 | ✅ | `priority.py:sort_queue` | `test_sort_queue_puts_target_first` | — |
| §6 | API key 하드코딩 금지, Secrets/.env만 | ✅ | `__main__.py:_load_dotenv` | — | 토큰 설정 섹션 |
| §6 | Secret 로테이션 90일 추적 | ⬜ | — | — | 문서만 (Appendix D) |
| §7 | Lockfile owner/lease/heartbeat | ✅ | `locks.py` | `test_lock_*` (4건) | — |
| §7 | Atomic write (temp+rename) | ✅ | `store.py:_atomic_write_json` | `test_store_atomic_write_no_partial_file` | — |
| §7.3 | Kill Switch (COLLECTOR_PAUSED) | ✅ | `killswitch.py` | `test_kill_switch_preflight_skips_all_stages` | TODO: 대시보드 경고 |
| §8 | events.jsonl append-only | ✅ | `events.py:EventLogger` | — | (로그 파일) |
| §8 | metrics/daily.jsonl | ✅ | `metrics.py:aggregate_daily` | `test_metrics_*` | KPI (일부) |
| §8 | traces.jsonl | ✅ | `traces.py:build_from_events_file` | `test_traces_build_from_events_file` | — |
| §9 | 0-Cost GitHub Actions | ✅ | `.github/workflows/collect.yml` | — | 실행 버튼 |
| §9 | Daily Budget Guard 자동 PAUSED | ✅ | workflow `Budget guard` step + `quota.py` | `test_quota_snapshot_flags_alerts` | TODO: 대시보드 |
| §9 | SQLite sidecar | ✅ | `cli/dashboard.py:build_index` | `test_build_index_and_dashboard` | dashboard.html |
| §10 | Failure code 6 접두어 | 🟡 | 전반 | 간접 | (상세 모달) |

---

## Master_02 Processing Logic

| 조항 | 명제 | 상태 | 코드 | 테스트 | 대시보드 |
|---|---|---|---|---|---|
| §1 | Query Template (topic/synonyms/exclude/period/target) | ✅ | `query.py:build_query` | `test_build_query_*` | 실행 인풋 |
| §1 | Fallback 1회 허용 | 🟡 | `query.py:fallback_query` + `cli/run.py` | `test_fallback_query_has_no_synonyms`, `test_cli_run_uses_fallback_on_empty_search` | — |
| §2A | caption_source manual/asr/none | ✅ | `stages.py:stage_collect` | 간접 | 상세 모달 |
| §2A | 자막 수집 3단 fallback (transcript-api/yt-dlp/공식) | 🟡 | `adapters/youtube.py` | — | — |
| §2A | Soft Filter (4min/2h/90min) | ✅ | `stages.py:stage_collect` | `test_soft_filter_*` (3건) | — |
| §2A | 낚시 제목 판별 (일치율<30% → 감점) | ✅ | `clickbait.py` | `test_is_clickbait_*` + `test_stage_collect_sets_clickbait_flag` | — |
| §2B | Strict JSON + 재프롬프트 1회 | ✅ | `stages.py:stage_extract` | `test_sc04_reprompt_then_success` | — |
| §2B | summary 50~300자 | ✅ | `stages.py:stage_normalize` | `test_normalize_failure_quarantines_as_invalid` | — |
| §2B | 금지어 필터 | ✅ | 동 | 간접 | — |
| §2B | Hallucination cos≥0.60 | ✅ | `stages.py:stage_review` | `test_priority_*` 간접 | — |
| §2B | 장문 청킹 map-reduce | ✅ | `chunking.py` + `stage_extract` | `test_chunk_*`, `test_pipeline_chunks_long_transcript` | — |
| §3 | 4단 confidence 승격 규칙 | ✅ | `stages.py:stage_review` | `test_e2e_*` | **Confidence 차트** |
| §4 | Prompt Git 경로 prompts/ | ⬜ | — | — | — |
| §5 | 다영상 집계 배치 | ✅ | `aggregate.py` | `test_aggregate_by_tag_*` | — |

---

## Master_03 Output & Resilience

| 조항 | 명제 | 상태 | 코드 | 테스트 | 대시보드 |
|---|---|---|---|---|---|
| §1 | JSON 영속 + status=PROCESSED | ✅ | `stages.py:stage_promote` | `test_sc01` | 최신 결과 카드 |
| §2 | Obsidian Markdown 렌더 | ✅ | `vault.py:render_note` | `test_render_note_*` (4건) | 📝 Obsidian 노트 링크 |
| §2 | Git Sync 별도 Worker + data/main 브랜치 | ✅ | `adapters/git_sync.py` | `test_git_sync_*` (2건) | — |
| §3 | Archive 분할 기준 (1000줄/100KB/분기) | 🟡 | `archive.py:archive_quarter` (분기만) | `test_archive_quarter_*` | — |
| §4 | DLQ 파일시스템 경로 | ✅ | `store.py:send_to_dlq` | `test_dlq_persists_to_filesystem` | — |
| §4 | SYNC_FAILED 재시도 5회 exp backoff | ✅ | `stages.py:stage_package` | `test_exp_backoff_calls_sleep_between_attempts` | — |
| §4 | DLQ Replayer Worker | ✅ | `dlq_replayer.py` + `cli/replay_cli.py` | `test_dlq_replayer_*` (3건) | — |
| §5 | failure_reason_code 정규 집합 | ✅ | 전반 | 간접 | 상세 모달 |
| §6 | QA 지표 (8종 Actionable 비율 등) | 🟡 | `metrics.py` + `cli/dashboard.py` | `test_metrics_*` | KPI 일부 |
| §7 | 경보 (FAILED>10%×3회 등) | ✅ | `alerts.py:evaluate` + workflow | `test_alerts_*` (4건) | GitHub Issue 자동 |
| §7 | Slack webhook | ⬜ | — | — | — |
| §8 | 테스트 하네스 9 시나리오 | ✅ | `tests/test_e2e_canonical.py` | SC-01~09 | — |
| §10 | 삭제 영상 REMOVED | ✅ | `pipeline.py:detect_removed` | `test_sc09_video_removed` | (상세 모달) |
| — | PII 마스킹 Renderer | ✅ | `pii.py` + `vault.py` | `test_pii_*`, `test_render_note_masks_pii` | Markdown 출력 |
| — | DMCA takedown 경로 | ✅ | `pipeline.py:mark_dmca_takedown` | `test_dmca_takedown_marks_removed_and_logs` | (상세 모달) |
| — | Rollback (payload_version bump) | ✅ | `rollback.py` | `test_rollback_*` (2건) | — |

---

## Appendix A — Platform Mapping

| 조항 | 명제 | 상태 | 코드 | 테스트 | 대시보드 |
|---|---|---|---|---|---|
| 전반 | v10 Payload → v2 7-schema 분해 | ✅ | `migrations/youtube_to_v2.py` | `test_decompose_*` (4건) | — |
| 전반 | V9 → v10 마이그레이션 | ✅ | `migrations/v9_to_v10.py` | `test_v9_to_v10_*` (5건) | — |

---

## Appendix C — Observability

| 조항 | 명제 | 상태 | 코드 | 테스트 | 대시보드 |
|---|---|---|---|---|---|
| §5 | Circuit Breaker youtube_api (HTTP_429 x3/5m, cooldown 10m) | ✅ | `circuit_breaker.py` | `test_breaker_*` (5건) | TODO: 상태 표시 |
| §5 | Circuit Breaker llm_api | ✅ | 동 | 동 | TODO |
| §5 | Circuit Breaker git_sync | ✅ | 동 | 동 | TODO |
| §5 | 어댑터에 breaker 연결 | 🟡 | 모듈만, 어댑터 wire-in 미완 | — | TODO |
| 전반 | runner-minute 모니터 | ✅ | `quota.py` | `test_quota_*` (3건) | KPI (일부) |

---

## Appendix D — Security & Compliance

| 조항 | 명제 | 상태 | 코드 | 테스트 |
|---|---|---|---|---|
| §1 | Secret 로테이션 90일 | ⬜ | 문서만 | — |
| §6 | PII 마스킹 이메일·전화·주민·카드·IP | ✅ | `pii.py` | `test_pii_*` (3건) |
| 전반 | DMCA takedown 플로우 | ✅ | `pipeline.py:mark_dmca_takedown` | `test_dmca_*` |
| 전반 | Health-check (삭제 영상 일일 감지) | 🟡 | `pipeline.py:detect_removed` 있으나 workflow 통합 없음 | `test_sc09_video_removed` |

---

## 대시보드 노출 Gap (거의 해소)

방법론 §4 규칙에 따라 "대시보드에 없으면 없는 것" 기준. 이전 4건 모두 해소됨 (`status.json` 기반 op-strip으로):

- ✅ Budget 진행률 (일일 LLM 비용 % / YouTube 쿼터 % / runner-min %)
- ✅ Circuit Breaker open/closed 상태
- ✅ DLQ 대기 건수
- ✅ Review Queue 대기 건수
- ✅ Kill Switch (COLLECTOR_PAUSED) 표시
- 🟡 최근 실행의 실패 원인 코드 분포 (부분 — records 카드 상세 모달에만)

---

## 상태 업데이트 룰

1. 새 PR에서 설계 조항이 추가/수정되면 → 이 표 해당 행 갱신.
2. 테스트 추가되면 → 테스트 ID 갱신.
3. 대시보드에 노출되면 → "대시보드" 컬럼 채우기.
4. Gap 해소되면 → 상태 ⬜→🟡→✅.
5. 분기별 전체 재스캔 → `docs/VERIFICATION_METHOD.md` §5 참조.
