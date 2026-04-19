# 사용자 매뉴얼 — YouTube Data Collector v10 E2E Tester

## 1. 이 문서가 필요한 경우
- 설계서(Master_01~03, Appendix A~D)대로 파이프라인이 정말 동작하는지 확인하고 싶을 때
- 코드를 건드리지 않고도 100개 시나리오가 전부 통과하는지 빠르게 점검하고 싶을 때
- 개발자가 아닌 사람이 "이게 뭐 하는 도구냐" 를 이해하고 싶을 때

## 2. 한 줄 요약
> 실제 YouTube·LLM·Git을 호출하지 않고, 파이프라인 전체(검색→수집→분석→검증→리뷰→저장→동기화)를 100개의 가상 시나리오로 자동 재생해보는 안전한 테스트 도구.

## 3. 사용 전 준비물
- Python 3.10 이상
- 설치: 터미널에서 아래 두 줄 실행
  ```bash
  pip install -r requirements-dev.txt
  python -m pytest
  ```
- 성공 시 콘솔에 `101 passed` 라고 나오면 끝입니다.

## 4. 디렉터리가 무엇인지 한 눈에
| 경로 | 역할 |
|---|---|
| `Master_01~03.md` | 설계서 본문 (v10) |
| `docs/Appendix_*.md` | 부록 (매핑/상태/관측성/보안) |
| `archive/V9/` | 이전 설계(V9) 원본 보존 |
| `collector/` | 테스트용 최소 참고 구현 (실제 API 호출은 하지 않음) |
| `tests/` | 100개 E2E 테스트 |
| `CHANGELOG_v10.md` | v10 변경 내역 |
| `docs/USER_MANUAL.md` | 이 파일 |
| `docs/FINAL_REPORT.md` | 최종 보고서 |

## 5. 테스트가 하는 일 (비개발자용 설명)
1. 가상의 YouTube 영상 정보를 만들어 둡니다.
2. 수집 단계부터 마지막 Git 저장까지 각 단계를 차례로 실행합니다.
3. 단계마다 "이 전이가 설계서 규칙에 맞는지" 확인합니다.
4. 마지막에 데이터가 설계서가 말한 상태(`promoted` 또는 `invalid` 등)로 귀결되었는지 확인합니다.
5. 이 과정을 100개의 서로 다른 상황(정상, 실패, 중복, 삭제된 영상 등)에 대해 반복합니다.

## 6. 실제 테스트 결과 보는 법
- 통과: `101 passed` → 모든 규칙이 설계와 코드 사이에서 깨진 곳이 없음.
- 실패: 실패 케이스 ID(`SUCCESS-07`, `DUP-03` 등)와 실패 이유가 출력됨.
- 특정 버킷만 실행:
  ```bash
  python -m pytest -k "DUP"        # 중복 처리만
  python -m pytest -k "H429"       # HTTP 429 케이스만
  python -m pytest tests/test_e2e_canonical.py   # 9개 핵심 시나리오만
  ```

## 7. 100개 케이스 구성 (간단 표)
| 상황 | 개수 | 기대 결과 |
|---|---|---|
| 정상 수집 | 25 | `promoted` + confirmed |
| 자동자막만 존재(ASR) | 10 | `inferred` (승격 전) |
| 낮은 유사도(환각 의심) | 6 | `unverified` |
| 동일 해시 (중복) | 8 | 스킵 |
| 해시 변경 (재처리) | 8 | 버전 +1, 이전 요약 히스토리 보존 |
| HTTP 429 | 8 | 수집 단계 실패, 다음 크론에서 재진입 |
| JSON 스키마 실패 → 재프롬프트 | 6 | 최종 `promoted` |
| 동기화 실패 5회 → 격리 | 6 | `invalid`, DLQ 적재 |
| 자막 없음 | 5 | `YT_NO_TRANSCRIPT` |
| 룰 0개 | 3 | `SEMANTIC_EMPTY_RULES` |
| 아카이브 포함 중복 | 3 | 아카이브까지 조회되어 스킵 |
| 영상 삭제 감지 | 2 | `archive_state=REMOVED` |
| 수동 재투입 | 1 | `invalid → collected` |
| 기본 9 핵심 시나리오 | 9 | 각각 별도 검증 |
| **합계** | **100** | |

## 8. 자주 묻는 질문
**Q. 테스트가 실제 YouTube 쿼터를 쓰나요?**
A. 아니요. 모든 외부 호출은 `collector/services.py`의 mock으로 대체됩니다. 쿼터 소모 0.

**Q. 실제 LLM API 키가 필요한가요?**
A. 필요 없습니다. LLM 응답도 가상 스크립트로 주입됩니다.

**Q. 왜 "101 passed" 인가요? 100개라면서요?**
A. 100 케이스 + "케이스 수가 정확히 100인지 검사하는 메타 테스트" 1개 = 101.

**Q. 결과가 실패하면 어떻게 하나요?**
A. 실패 케이스 ID를 설계서 Appendix_B의 매핑과 비교하세요. 대부분 mock 스크립트가 설계 가정과 맞지 않거나, 최근 설계 변경이 코드에 반영되지 않아 발생합니다.

**Q. 실제 YouTube/LLM에 붙여 돌릴 수는 있나요?**
A. `collector/services.py`의 callable 필드를 실제 API 호출로 교체하면 됩니다. 본 문서는 테스트 범위만 다룹니다.

## 9. 운영 체크리스트 (팀 내 공유용)
- [ ] 설계서 변경 시: `docs/Appendix_A_Platform_Mapping.md` 의 Reviewer Trace 표 업데이트
- [ ] 새 실패 코드 추가 시: `Master_01 §10` 접두어 규약에 맞게 명명
- [ ] 새 시나리오 추가 시: `tests/test_e2e_100.py`에 버킷 추가, 총합 assert 갱신
- [ ] PR 머지 전: `python -m pytest` 통과 필수 + Appendix D §6 보안 체크리스트
