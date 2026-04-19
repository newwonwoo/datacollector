# 최종 보고서 — YouTube Data Collector v10 설계 + E2E 테스터

> 작성일: 2026-04-19 · 브랜치: `claude/review-youtube-collector-design-0blBB`

## A. 요약 (한 페이지)
- V9 설계서 3종을 리뷰해 **누락/보완/개선 41건**과 **설계자 제안 15건**을 도출.
- v2 플랫폼 문서 4종을 흡수해 Master_01~03을 **v10으로 전면 재작성** + 부록 4종 신설.
- 설계가 코드와 일치함을 증명하기 위해 **100개 E2E 테스트 하네스**를 작성·실행.
- **결과: 101/101 전부 통과 (0.54초)**.

---

## B. 비개발자용 설명 (3분 버전)
1. 이 프로젝트는 YouTube 영상 자막을 자동으로 수집·요약해 지식 노트를 만드는 파이프라인의 **설계서**입니다.
2. 기존 설계서(V9)에는 "현실에서 운영하면 깨질" 구멍이 여러 군데 있었습니다. 예: 자막 수집이 막혔을 때 어떻게 재시도할지, 영상이 삭제됐을 때 어떻게 표시할지, 비용 초과 시 누가 멈출지.
3. 새 설계서(v10)에서 그 구멍들을 전부 메우고, 비상 스위치(Kill Switch), 비용 상한, 사람이 직접 검토할 수 있는 경로, 복구 절차까지 넣었습니다.
4. 설계만 바꾼 게 아니라, "정말 설계대로 돌아가는지" 를 **100가지 가상 상황**에서 자동으로 확인하는 테스트까지 만들었습니다.
5. **테스트는 인터넷 연결이나 API 비용이 전혀 들지 않습니다.** 모든 외부 호출은 가짜(mock)로 대체됩니다.
6. 실행 시간 0.54초, 전부 통과. 이는 설계와 구현이 한 덩어리로 말이 된다는 증거입니다.

---

## C. 성과 (Achievements)
### C.1 설계 품질
- V9 대비 누락 14건 / 보완 15건 / 개선 12건 / 설계자 추가 제안 15건 → **총 56개 개선 포인트 반영**.
- v2 플랫폼 계약과 정합: `schema_version`, `run_id`, `provenance`, 3-tier status, event log, Review gate 4단 confidence 모두 흡수.
- 문서 구조를 "본문 3개 + 부록 4개"로 재편해 본문 가독성 유지 + 확장 항목은 부록으로 격리.

### C.2 테스트 커버리지
- 100개 E2E 케이스가 다음 경로를 실측 검증:
  - 7단계 파이프라인 (Discover → Package) 전이
  - 3-tier 상태 (Run / Stage / Record) 정합
  - Rule A/B/C 중복 판정 (ACTIVE + ARCHIVED 모두 조회)
  - LLM 재프롬프트 1회 제약
  - Git Sync 5회 재시도 후 DLQ 격리
  - Kill Switch·비용 가드·상태 역행 금지
  - 관리자 수동 재투입, 삭제 영상 감지

### C.3 운영 안전망
- 설계서에 **Daily Budget Guard** + **Kill Switch**(`COLLECTOR_PAUSED=1`)를 명시.
- 모든 실패는 삭제 대신 `invalid` 격리 + DLQ. 감사/재시도 가능한 설계.
- Secret 90일 로테이션 SOP, DMCA 대응 플로우, PII 마스킹 규정 문서화.

### C.4 장기 확장성
- `source_key = "youtube:{video_id}"` 네임스페이싱으로 v2 플랫폼의 타 source adapter(web/podcast/manual) 추가 여지 확보.
- Payload → v2 7-schema 무손실 분해 Invariant를 Appendix A에 명시 → 미래 통합 마이그레이션 시 회귀 방지 체크리스트화.

---

## D. 예상치 못한 난관 & 해결 (Challenges & Solutions)
### D.1 "환각 판별"이 이분법일 때 오탐 폭증
- **문제**: V9은 summary의 핵심 명사가 원문에 그대로 있는지 문자열 매칭 한 번으로 성공/실패를 가렸음. 그러나 LLM은 동의어·패러프레이즈를 자연스럽게 사용 → 멀쩡한 요약도 다 실패.
- **해결**: Review Gate 4단(confirmed / inferred / unverified / rejected)과 **임계식 기반 승격** 도입. 임베딩 cosine 유사도를 보조 지표로 묶어 `confirmed` 조건을 수식으로 못 박음. "실패"가 아닌 "격리 후 사람 검토"라는 제3의 경로가 생김.

### D.2 Rule C "완전 스킵"이 아카이브를 놓치는 구조였음
- **문제**: 기존 규칙은 ACTIVE 저장소만 조회. 일단 ARCHIVED로 이동하고 나면 같은 영상을 재수집하는 경로가 뚫려 있었음.
- **해결**: Master_03 §5에서 "ACTIVE + ARCHIVED 동시 조회" 를 의무화하고 `index/collector.sqlite` sidecar를 허용해 전체 순회 비용을 막음. 테스트 케이스 `ARC-*`로 회귀 방지.

### D.3 `FAILED → PROCESSED` 직접 복귀 금지 vs 관리자 수동 재처리 욕구의 충돌
- **문제**: 설계가 엄격해서 운영자가 수동 재투입할 방법이 없음. 현실에서는 상시 필요.
- **해결**: 상태 머신을 `invalid`로 수렴시키고 `invalid → collected` 전이만 **사유 기록 필수**로 허용. 결과 payload는 `retry_count` 가 1 증가하고 event log에 `entity_type=manual_action`이 기록됨. 감사 추적 가능한 탈출구.

### D.4 "0-Cost 전략"과 "관측 3축" 동시 만족
- **문제**: GitHub Actions/Repo만으로 운영해야 하는데 logs+metrics+traces까지 분리하면 돈·시간이 듦.
- **해결**: 세 파일을 모두 JSONL append-only로 통일. 읽기·집계 편의를 위해 SQLite 단일 파일 sidecar만 허용. 2000분 무료 CI 한도 감시 경보까지 함께 설계.

### D.5 100개 케이스 작성 공수
- **문제**: 시나리오 100개를 수작업으로 정합성까지 맞춰 쓰는 건 비현실적.
- **해결**: **9개 핵심 시나리오(정확 검증) + 91개 파라미터화 생성(불변 속성 검증)** 의 하이브리드. 각 버킷이 설계서 상태 머신의 특정 경로에 1:1 매칭되고, 불변 속성(예: "promoted 되었다면 stage_status.package == completed") 만 확인하므로 구현 변형에 견고.

### D.6 Python 코드와 설계 문서의 표류(drift) 위험
- **문제**: 설계를 바꿨는데 코드가 그대로라면 테스트가 "거짓 통과" 할 수 있음.
- **해결**: Appendix A에 "Reviewer Trace" 표를 넣어 리뷰 지적사항 41건 + 제안 15건 **각각의 반영 위치**를 명시. 설계 변경 시 이 표를 먼저 갱신하는 것을 관례화.

### D.7 플랜 모드에서 실제 실행까지의 전환
- **문제**: 초기에 분석·플래닝 루프에 갇혀 실제 코드 착수가 지연됨.
- **해결**: 사용자의 직접 피드백("구현은 언제?")을 수용해 설계 완료 직후 즉시 병렬 파일 생성 모드로 전환. 이후 10개 파일을 2회 병렬 write로 작성 완료.

---

## E. 실측 결과
```
tests/test_e2e_canonical.py   9 tests
tests/test_e2e_100.py         92 tests   (91 generated + 1 meta count check)
총계                           101 tests
결과                           101 passed in 0.54s
외부 비용                      0 (모든 API mock)
```

### 케이스 버킷 분포
| 버킷 | # | 기대 귀결 |
|---|---|---|
| 정상 수집 | 25 | record_status=promoted, confidence=confirmed |
| ASR 자막 | 10 | confidence=inferred (승격 전) |
| 낮은 유사도 | 6 | confidence=unverified |
| Rule C 중복(ACTIVE) | 8 | extract 이후 skipped |
| Rule B 재처리 | 8 | payload_version=2, history 보존 |
| HTTP_429 | 8 | stage_status.collect=failed |
| LLM 재프롬프트 성공 | 6 | record_status=promoted |
| Sync 5회 실패 | 6 | record_status=invalid, DLQ 1건 |
| 자막 없음 | 5 | failure_reason_code=YT_NO_TRANSCRIPT |
| 룰 0개 | 3 | failure_reason_code=SEMANTIC_EMPTY_RULES |
| Rule C 중복(ARCHIVED) | 3 | 아카이브 조회로 스킵 |
| 영상 삭제 | 2 | archive_state=REMOVED |
| 수동 재투입 | 1 | invalid → collected |
| 핵심 시나리오(Appendix B) | 9 | 개별 정확 검증 |
| **합계** | **100** | |

---

## F. 향후 과제 (Out of Scope지만 추천)
1. **실제 Service 연결**: `collector/services.py`의 callable을 YouTube Data API v3, Gemini/Claude 클라이언트, GitHub App token으로 교체하는 실서비스 어댑터 레이어 추가.
2. **v2 플랫폼 편입**: Appendix A Reviewer Trace의 마이그레이션 체크리스트에 따라 `migrate/v9_to_v10.py`, `migrate/youtube_to_v2.py` 작성.
3. **Human Review UI**: `review_queue/` 폴더를 순회하며 승격/반려를 결정하는 간단한 CLI. 기본 구조는 이미 설계에 포함됨.
4. **대시보드**: Appendix C의 SQLite sidecar 쿼리를 Grafana 또는 단일 HTML 리포트로.
5. **가시성 확장**: runner-minute 월간 사용량 실측 연동.

---

## G. 산출물 목록
| 범주 | 파일 |
|---|---|
| 설계서 본문 (v10) | `Master_01_Architecture_and_Flow.md`, `Master_02_Processing_Logic.md`, `Master_03_Output_and_Resilience.md` |
| 부록 | `docs/Appendix_A_Platform_Mapping.md`, `docs/Appendix_B_State_and_Event_Log.md`, `docs/Appendix_C_Observability.md`, `docs/Appendix_D_Security_and_Compliance.md` |
| 원본 보존 | `archive/V9/Master_01~03.md` |
| 변경 이력 | `CHANGELOG_v10.md` |
| 참고 구현 | `collector/__init__.py`, `payload.py`, `hashing.py`, `events.py`, `store.py`, `services.py`, `stages.py`, `pipeline.py` |
| 테스트 | `tests/test_e2e_canonical.py`, `tests/test_e2e_100.py`, `tests/conftest.py`, `pytest.ini`, `requirements-dev.txt` |
| 문서 | `docs/USER_MANUAL.md`, `docs/FINAL_REPORT.md` (본 문서) |

---

## H. 한 줄 결론
> v10은 "돌려서 증명되는" 설계서입니다. 100개의 가상 상황에서 전부 통과했고, 앞으로의 실제 구현은 이 테스트를 계속 통과시키는 방식으로 이어가면 됩니다.
