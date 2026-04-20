# 설계-구현 대조 검증 방법론 (Spec vs. Delivered Artifact Verification)

> 설계서에 적혀 있는 것이 실제 산출물에 반영됐는지 매번 **같은 방식**으로 확인하기 위한 방법론.
> 이 문서는 Collector 프로젝트 전용이지만 구조는 다른 프로젝트에도 그대로 재사용 가능.

---

## 1. 전제

- **최종 산출물 = 유튜브 자막을 가공한 지식 내용물**
  - 1차 포맷: `vault/strategies/*.md` (Obsidian 노트 — summary + rules + tags)
  - 0차 포맷: `data_store/YYYYMM/*.json` (원시 Payload)
  - **대시보드(docs/index.html)는 이 산출물로 도달하는 포털/뷰어일 뿐**. 대시보드 그 자체는 산출물이 아님.
- **성공 판정**: "검색어 하나 넣고 → 가공된 요약·규칙이 내 볼트에 쌓이고 → 언제든 읽을 수 있음" 이 충족되면 성공.
- **검증 기준 = 설계서 조항**. 설계서에 없는 기능은 검증 대상 아님 (필요하면 설계서 먼저 업데이트).
- **검증 산출물 = Coverage Report** (`docs/coverage.md`). 각 조항 → 상태(✅/🟡/⬜) → 코드 위치 → 테스트 ID 매핑.

---

## 2. 5-Step 검증 프로세스

### Step 1. 분해 (Decompose)
설계서 각 절을 **체크 가능한 단위**로 쪼갠다. 한 줄당 하나의 "검증 가능한 명제".

예 (Master_01 §5 Priority Policy):
- `priority_score` 기본값 100
- `target_channel_id` 지정 시 +30
- 최근 7일 이내 신규 영상 +20
- Aging: 1일 경과마다 +5, 최대 +35
- 실패 재처리 건 -10
- 낚시 의심 -25
- 90분 이상 장문 -15
- 범위 [0, 200] 클램프
- Cost Guard 활성 시 일반 자연어 탐색은 priority_score=0 강제

### Step 2. 매핑 (Map)
각 명제마다:
- **코드 위치** (파일:함수)
- **자동 테스트 ID** (pytest node ID)
- **수동 검증 절차** (있으면)

### Step 3. 상태 판정 (Assess)
3단계만:
- ✅ 완전 — 명제가 코드에 그대로 구현됐고 자동 테스트가 이를 증명
- 🟡 부분 — 일부만 구현 / 테스트는 없음 / 엣지 케이스 미검증
- ⬜ 미구현 — 코드 없음

### Step 4. 도달성 (Reachability)
대시보드가 최종 산출물(`vault/*.md`, `data_store/*.json`)로 유저를 **몇 번의 클릭으로** 데려다주는지 검증한다.

- 최종 산출물 자체: 대시보드의 레코드 카드 → 모달 → "Markdown 열기" / "YouTube 원본" 버튼으로 **2클릭 이내 도달**
- 상태/메트릭 증거: 대시보드의 차트/KPI에 표시
- 도달 불가한 항목 = Gap.

### Step 5. 보완 (Remediate)
Gap은 우선순위 rubric(P0~P4)으로 정렬:
- **P0 기능중단급** — 이 기능 없으면 시스템 전체가 안 돈다
- **P1 주요** — 핵심 경로의 일부가 깨짐
- **P2 비주요** — 주변 기능이 깨짐
- **P3 개선** — 설계에는 있으나 실사용 영향 작음
- **P4 신규** — 설계에 없는 추가 제안

병렬 가능한 항목은 한 번에, 종속 있는 항목은 순차.

---

## 3. Coverage Report 포맷

`docs/coverage.md` 는 아래 표 하나로 관리.

| 설계 조항 | 명제 | 상태 | 코드 위치 | 테스트 ID | 대시보드 위치 | 비고 |
|---|---|---|---|---|---|---|
| Master_01 §5 | priority_score 기본 100 | ✅ | `collector/priority.py:compute_priority` | `tests/test_p0_p1_p2.py::test_priority_base_is_100` | (내부 — 대시보드 미노출) | |
| Master_01 §5 | target_channel_id +30 | ✅ | 동 | `test_priority_target_channel_bonus` | (내부) | |
| Master_01 §7.3 | Kill Switch (COLLECTOR_PAUSED) | ✅ | `collector/killswitch.py` | `test_kill_switch_preflight_skips_all_stages` | 대시보드 Budget 경고 배너 | |
| Master_03 §2 | Obsidian Markdown 렌더 | ✅ | `collector/vault.py:render_note` | `tests/test_vault.py::test_render_note_*` | "📝 Obsidian 노트" 링크 버튼 | |
| Master_03 §3 | Sync 5회 재시도 exp backoff | ✅ | `collector/stages.py:stage_package` | `test_exp_backoff_calls_sleep_between_attempts` | 최근 실행 pill + 에러 로그 | |
| Master_02 §1 | Query Template 생성 실패 fallback | 🟡 | `collector/query.py:fallback_query` | `test_fallback_query_has_no_synonyms` | 실행 버튼 | 생성 실패 감지 로직은 CLI에만 |
| Appendix C §5 | Circuit Breaker youtube_api | ✅ | `collector/circuit_breaker.py` | `test_breaker_trips_after_threshold` | (내부) | 차단 상태 대시보드 노출 필요 ⚠ |

...

---

## 4. 대시보드 = "최종 산출물로 가는 2-클릭 포털"

대시보드는 그 자체가 지식이 아니다. **유저가 대시보드에서 출발해 2 클릭 안에 가공된 요약·규칙(최종 산출물)을 읽을 수 있어야 한다.**

### 2-클릭 도달 경로 (핵심)
| 클릭 1 | 클릭 2 | 도달물 |
|---|---|---|
| 레코드 카드 | (자동 확장) | **요약 + 규칙 리스트** (대시보드 내부 인라인) |
| 레코드 카드 | 📝 Markdown | 전체 Obsidian 노트 |
| 레코드 카드 | ▶ YouTube | 원본 영상 |

### 보조 시각화 (운영 지표, 산출물은 아님)
| 지표 | 대시보드 표현 |
|---|---|
| 파이프라인 진행 | 7단 flow diagram |
| 레코드 상태 분포 | 스택 바 차트 |
| Confidence 분포 | 스택 바 차트 |
| 실행 이력 | 최근 실행 리스트 |
| 신규 레코드 | NEW 배지 + 완료 배너 |
| LLM 비용 | KPI 카드 |
| Budget 상태 | (미구현 — TODO) |
| Circuit Breaker 상태 | (미구현 — TODO) |
| DLQ 건수 | (미구현 — TODO) |
| Review Queue 대기 | (미구현 — TODO) |

위 "미구현" 행은 Coverage Report의 Gap 섹션에 그대로 반영.

---

## 5. 실행 주기

- **PR 머지 전**: 해당 PR이 건드린 설계 조항에 대해 Coverage Report 갱신. Reviewer Trace 표가 PR 설명에 포함.
- **분기 1회**: 전체 설계서 재스캔, Gap 리스트 재계산, 우선순위 재할당.
- **릴리스 전**: Coverage Report 상태가 모두 ✅ 또는 명시적으로 연기(❌→🟡 with justification)인지 확인.

---

## 6. 자동화 포인트

- `scripts/check-coverage.py` (TODO): 설계서 파일 파싱 → `## G-NN.` 혹은 명시적 앵커를 찾아 Coverage Report 행 템플릿 생성.
- Coverage Report 누락 행 감지 → PR blocker.
- GitHub Actions에서 매 PR마다 `docs/coverage.md` 갱신 여부 확인.

---

## 7. 기록 이력 (Changelog)

| 날짜 | 책임자 | 조항 수 | 완전 구현 | 부분 | 미구현 | 특이 |
|---|---|---|---|---|---|---|
| 2026-04-20 | Claude + user | ~70 | 45 | 15 | 10 | Circuit breaker·review queue routing·DLQ replayer 신규 반영. 대시보드 시각화 추가. |

(이후 이 표를 매 분기 추가)

---

## 8. 이 방법론이 해결하는 과거 실수

GOTCHAS.md 의 G-09 (설계 ↔ 코드 drift), G-11 (설계엔 있는데 코드 누락)은 전부 "설계서 한번 훑고 대충 구현" 때문에 생김. Coverage Report를 하나의 진실의 원천으로 두면:

1. 매 조항이 어디 있는지, 어느 테스트가 증명하는지 명확.
2. 구현 상태가 대시보드에 자연스럽게 드러남 (사용자 가시성).
3. 다음 세션 Claude(혹은 사람)도 Coverage Report부터 읽으면 중복 작업·누락 없음.

---

## 9. 요약

**"설계서 → 체크리스트 → 코드/테스트 매핑 → 대시보드 도달성 확인 → Gap 보완"** 의 5 단계 루프.

핵심 원칙:
1. **최종 산출물은 가공된 지식(`vault/*.md`)이지 대시보드가 아니다.**
2. **대시보드에서 2 클릭 이내 도달 가능하지 않으면 "없는 것"으로 간주한다.**
3. **설계서에 없는 기능은 검증 대상이 아니다.** 필요하면 설계서 먼저 업데이트 후 구현.
