# Appendix D — Security & Compliance

> **목적**: Secret 관리, PII 마스킹, 저작권/DMCA/삭제 영상 처리, 접근 감사 정책을 정의한다.

## 1. Secret 관리
### 저장
- GitHub Actions 환경: Repository Secrets (`YOUTUBE_API_KEY`, `LLM_API_KEY`, `GIT_SYNC_APP_TOKEN`, `SLACK_ALERT_URL`).
- 로컬 개발: `.env` (gitignore 확인). `.env.example`에 key 이름만 커밋.

### 로테이션
- 주기: **90일**.
- SOP:
  1. 새 key 발급 (YouTube Cloud Console / LLM provider).
  2. GitHub Secrets에 new key 저장(기존 덮어쓰기).
  3. 수동 trigger로 1회 dry-run 실행.
  4. 실 트래픽 성공 확인 후 이전 key 폐기.
  5. 로테이션 event를 `logs/events.jsonl`에 `entity_type=system, reason=secret_rotation` 기록.

### 금지 사항
- 코드/문서/로그/event metrics에 Secret 값 절대 포함 금지.
- PR·issue 본문에서 URL 파라미터에 key 포함한 예시 금지.
- `.env`를 실수 커밋 방지: `.gitignore` + pre-commit hook `tools/check_no_secrets.sh`.

### 접근 감사
- GitHub Actions run log는 Secret을 마스킹(`***`). 정기적(월 1회) 샘플 점검.
- 운영자 수동 열람 기록은 별도 `logs/audit.jsonl` (actor, resource, at).

## 2. PII 마스킹
### 마스킹 대상 패턴
Renderer 호출 직전 Markdown 본문/요약/rules 모두에 적용.
| 유형 | 정규식(축약) | 치환 |
|---|---|---|
| 전화번호(KR) | `\b0\d{1,2}-?\d{3,4}-?\d{4}\b` | `[전화번호]` |
| 휴대폰 | `\b01[016789]-?\d{3,4}-?\d{4}\b` | `[휴대폰]` |
| 이메일 | `[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}` | `[이메일]` |
| 주민등록번호 | `\b\d{6}-\d{7}\b` | `[주민번호]` |
| 카드번호(포맷) | `\b\d{4}-?\d{4}-?\d{4}-?\d{4}\b` | `[카드번호]` |

### 처리 원칙
- 원본 JSON(`data_store/`)에는 마스킹 전/후 구분해 `raw_text`/`masked_text` 둘 다 저장. Markdown에만 마스킹 후를 사용.
- 원본 노출이 필요한 내부 감사 목적일 때만 `raw_text` 접근 허용.

## 3. 저작권 및 사용 범위
### 선언
- 수집 데이터는 개인 지식베이스/내부 연구 한정.
- 외부 API 서빙, 대량 재배포, 상업적 재판매, 광고성 요약 재출력 금지.
- YouTube 이용약관과 저작권법 준수.

### DMCA / 삭제 요청 대응
신고 접수(또는 YouTube가 영상을 삭제) 시 SOP:
1. 대상 `source_key` 식별 → `archive_state=REMOVED` 전이.
2. 관련 Markdown을 "비공개 섹션"(접힘)으로 이동. Git commit `data(takedown): remove ...`.
3. JSON 원본은 유지하되 `archive/removed/<source_key>.json`으로 이동, 원 경로는 tombstone(빈 객체 + `reason`).
4. event `reason=dmca_takedown | yt_video_removed` 기록.
5. 30일 경과 후 영구 파기 옵션. 파기 시에도 event log는 보존(사건 기록).

### YouTube 측 삭제 감지
- 일일 health-check job이 ACTIVE records에 대해 `videos.list` 호출 샘플링.
- 410/403 응답 시 자동 §3.2 플로우.

## 4. 데이터 분류 및 보존
| 분류 | 예시 | 보존 기간 | 접근 |
|---|---|---|---|
| 원본 자막 | `raw_text` | 기본 365일, 설정 가능 | 내부 전용 |
| 마스킹 산출물 | Markdown | 무기한 | 개인 지식베이스 |
| Event log | `logs/events.jsonl` | 무기한 (대용량 시 로테이션 고려) | 내부 |
| Audit log | `logs/audit.jsonl` | 3년 | 내부 감사자 |
| DLQ | `dlq/**` | 90일 후 자동 정리 | 내부 |

## 5. 백업
- 기본: Git history (repository가 1차 저장소).
- 보조(선택): 월 1회 repository tar.gz를 별도 스토리지(GitHub Releases asset 또는 사용자가 지정한 외부 경로)로 수동 업로드.
- 복구 시나리오: `archive/V9/` 과거 버전은 별도 보존되므로 V9 회귀 가능. v10 스냅샷은 Git main에서 복원.

## 6. 보안 리뷰 체크리스트 (PR 머지 전)
- [ ] 코드·문서·샘플 JSON에 실제 Secret 부재
- [ ] 신규 외부 API 호출에 timeout/backoff 존재
- [ ] 새 로그 필드가 PII를 유출하지 않음
- [ ] 새 수집 대상이 개인 지식베이스 용도에 부합
- [ ] `.env.example`와 `config/*.yml.example` 동기화

## 7. 법적 고지
- 본 프로젝트 산출물은 개인 연구 목적에 한정된다.
- 저작권 침해 의도가 없음. 권리자 요청 시 §3 SOP에 따라 즉시 대응.
- 사용자는 본 시스템을 통해 얻은 정보에 근거한 의사결정의 결과에 대해 전적으로 스스로 책임진다.
