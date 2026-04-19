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
- 성공 시 콘솔에 `135 passed` 라고 나오면 끝입니다.

## 3.1 앱으로 설치해서 쓰기 (권장)
```bash
# 저장소 루트에서
pip install -e .
```
이 한 줄이면 다음 4개의 **터미널 앱 명령어**가 PATH에 등록됩니다.
| 명령어 | 역할 |
|---|---|
| `collector app` | 원클릭 런처 (대시보드 빌드 + 로컬 서버 + 브라우저 자동 오픈) |
| `collector dashboard` | SQLite 인덱스 + HTML 리포트만 생성 |
| `collector review` | review_queue 대화형 검토 |
| `collector quota` | 쿼터/비용/경보 점검 |

### 원클릭 실행 (macOS/Linux)
```bash
./run.sh                 # 기본 포트 8765
./run.sh --watch 5       # data_store 변경 시 5초마다 자동 재빌드
```
### 원클릭 실행 (Windows)
```bat
run.bat
```
또는 Finder/Explorer에서 `run.sh` / `run.bat` **더블클릭**.

### 단일 바이너리 앱으로 빌드 (선택)
```bash
pip install .[bundle]
pyinstaller packaging/collector.spec
# 산출물: dist/collector (또는 dist/collector.exe)
./dist/collector app
```
별도 Python 없이도 배포 가능한 단일 실행 파일이 `dist/` 에 생성됩니다.

## 4. 디렉터리가 무엇인지 한 눈에
| 경로 | 역할 |
|---|---|
| `Master_01~03.md` | 설계서 본문 (v10) |
| `docs/Appendix_*.md` | 부록 (매핑/상태/관측성/보안) |
| `archive/V9/` | 이전 설계(V9) 원본 보존 |
| `collector/` | 참고 구현 (파이프라인 코어) |
| `collector/adapters/` | 실제 YouTube / Anthropic / Gemini / GitHub App 어댑터 |
| `collector/migrations/` | V9→v10, v10→v2 7-schema 마이그레이션 |
| `collector/cli/` | Human Review / Dashboard / Quota 모니터 CLI |
| `tests/` | 130개 테스트 (E2E 100 + 어댑터/마이그/CLI 30) |
| `CHANGELOG_v10.md` | v10 변경 내역 |
| `docs/USER_MANUAL.md` | 이 파일 |
| `docs/FINAL_REPORT.md` | 최종 보고서 |

## 4.1 신규 도구 사용법

### 무료 티어 실제 실행 (Gemini 1.5 Flash + YouTube Data API)
```bash
export YOUTUBE_API_KEY=...      # Google Cloud Console → 사용자 인증 정보 → API 키 (무료 할당량 10,000 units/day)
export GOOGLE_API_KEY=...       # aistudio.google.com/app/apikey (Gemini 무료: 15 RPM / 1500 RPD)
collector run --query "단테 단타매매" --count 5
```
설계서 Master_02의 `model_name="gemini-1.5-flash"`를 그대로 따릅니다. Anthropic Claude를 쓰고 싶으면 `--llm anthropic` 플래그 (유료).

### 무료 티어 한도 요약
| 서비스 | 무료 한도 | 초과 시 |
|---|---|---|
| YouTube Data API v3 | 10,000 units/day (검색 1회 ≈ 100 units) | HTTP 429 → `RETRY_WAIT` |
| Gemini 1.5 Flash | 15 RPM / 1,500 RPD | HTTP 429 → `RETRY_WAIT` |
| GitHub Actions | 2,000 runner minutes/month | Quota monitor가 경보 |

### Human Review CLI
```bash
python -m collector.cli.review --queue review_queue --data-store data_store
```
대기열의 각 파일에 대해 `a` (approve) / `r` (reject) / `s` (skip) 선택. approve 시 데이터 스토어로 이동하고 confidence=confirmed.

### Dashboard
```bash
python -m collector.cli.dashboard --data-store data_store --html index/dashboard.html
```
`data_store/*.json`을 SQLite 인덱스로 만든 뒤 단일 HTML 리포트 생성. 상태별/실패코드별 카운트 + 최근 20건.

### Quota Monitor
```bash
python -m collector.cli.quota --usage metrics/quota.jsonl
```
GitHub Actions runner-minute / YouTube 쿼터 / LLM 비용 누적치와 경보 플래그, kill-switch 권고 여부를 JSON으로 출력.

### V9 → v10 마이그레이션
```python
from collector.migrations import migrate_v9_to_v10
import json
v9 = json.load(open("legacy.json"))
v10 = migrate_v9_to_v10(v9)
```

### v10 → v2 7-schema 분해
```python
from collector.migrations import decompose_to_v2
records = decompose_to_v2(payload)
# {"SourceRecord": [...], "ClaimRecord": [...], ...}
```

### 실제 서비스 어댑터 연결
```python
from collector.adapters import YouTubeAdapter, AnthropicAdapter, GitSyncAdapter
from collector.services import Services

yt = YouTubeAdapter(api_key=os.environ["YOUTUBE_API_KEY"])
llm = AnthropicAdapter(api_key=os.environ["ANTHROPIC_API_KEY"])
git = GitSyncAdapter(
    app_id=os.environ["GH_APP_ID"],
    installation_id=os.environ["GH_INSTALL_ID"],
    private_key_pem=open(os.environ["GH_PRIVATE_KEY_PATH"]).read(),
    repo="newwonwoo/datacollector",
)

services = Services(
    youtube_search=yt.search,
    youtube_captions=yt.captions,
    youtube_video_alive=yt.video_alive,
    llm_extract=llm.extract,
    git_sync=git.sync,
)
```
이후 `run_pipeline(payload, services, store, logger)`가 실제 API를 호출합니다.

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
