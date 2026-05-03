# 사용자 매뉴얼 — YouTube Data Collector v10 E2E Tester

## 0. 로컬 웹앱 모드 (권장) — YouTube 차단 우회

> **중요 (2026-04 기준):** YouTube는 클라우드 제공자 IP(GitHub Actions 포함)에서의
> 자막 요청을 광범위하게 차단합니다. 안정적인 수집은 **본인 PC의 가정 IP**에서 돌려야
> 합니다. 이를 위해 아래 로컬 웹앱 모드가 기본 사용법입니다.

### 0.0 3단계로 시작

1. **설치 (한 번만)**
   ```bash
   pip install -e .
   ```
2. **실행** — `./run.sh` (mac/Linux) 또는 `run.bat` (Windows) 더블클릭.
   브라우저가 `http://127.0.0.1:8765` 를 자동으로 엽니다.
3. **첫 실행**에서 화면 상단의 **🔑 초기 설정** 카드에 두 API 키를 붙여넣기 → **저장 후 시작 ▶**.
   키는 이 PC의 `.env` 파일에만 저장되며 Git/외부 서버로 전송되지 않습니다.
   이후 실행에서는 마법사가 뜨지 않습니다.
4. 검색어 입력 → **실행 ▶** → 완료되면 지식 카드 + Obsidian Markdown 링크가 생깁니다.

재설정은 설정 섹션의 **🔑 API 키 재설정** 버튼으로 언제든 가능합니다.

### 0.1 클라우드 모드 — 웹 브라우저만 (모바일 최적, 제한적)
> 아무것도 설치 안 함. 다만 YouTube 자막 수집이 GH Actions IP에서 실패할 수 있음.

1. **Secrets 등록 (최초 1회)** — https://github.com/newwonwoo/datacollector/settings/secrets/actions
   - New repository secret → `YOUTUBE_API_KEY` / `GOOGLE_API_KEY` 추가
2. **Pages 활성화 (최초 1회)** — https://github.com/newwonwoo/datacollector/settings/pages
   - Source 를 **GitHub Actions** 로 선택 → Save
3. **실행** — https://github.com/newwonwoo/datacollector/actions/workflows/collect.yml
   - 오른쪽 **Run workflow** → 검색어 입력 → 초록 **Run workflow**
   - 1~2분 기다림
4. **대시보드 열기** — https://newwonwoo.github.io/datacollector/
   - 매 실행마다 이 URL이 최신 결과로 자동 갱신됨
   - 북마크 하나면 끝

> 매일 KST 06:00 자동 실행도 같이 돕니다. Pages URL만 북마크해두면 매일 알아서 갱신된 대시보드를 봄.

### 0.2 PC용 exe/바이너리 (Python 없이 더블클릭)
1. https://github.com/newwonwoo/datacollector/releases → 최신 릴리스
2. OS별 파일 다운로드:
   - Windows: `collector-windows.exe`
   - macOS: `collector-macos`
   - Linux: `collector-linux`
3. 같은 폴더에 `.env` 파일 만들기 (키 2개 입력)
4. 파일 더블클릭 → 브라우저 자동 오픈

> 릴리스가 아직 없으면 한 번만 태그 푸시하면 자동 빌드됩니다:
> https://github.com/newwonwoo/datacollector/actions/workflows/build-binaries.yml → Run workflow

---

## 0.5 워크플로 한 줄 명령 / MCP 에이전트 자동화

### 0.5.1 `collector workflow` — 4단계 자동 체인

도메인을 주면 cheap LLM (Gemini Flash / Groq 8b / Claude Haiku) 이
**아이디어 10개 + 검색 키워드 30개** 를 만들고, 본 파이프라인이 키워드
별로 영상을 모은 뒤, 다시 cheap LLM 이 best 1개를 골라
NotebookLM 친화 합본 .md 까지 자동 생성합니다. 1세션 ≈ $0.005.

```bash
collector workflow full --domain "사주" --count 10
```

산출물 (`exports/run/`):
- `step1_ideas.json` — 아이디어 + 키워드
- `step2_research.json` — 키워드별 run_query 결과
- `step3_synthesize.json` — best 1개 + 점수표 + 다음 단계
- `step4_spec_<idx>.md` — best 아이디어의 **제품 설계서** (NotebookLM 기반 MVP, 무료 티어 스택 분석 포함)
- `notebook_<timestamp>_<도메인>.md` — NotebookLM 한 번 끌어 놓기용 합본

부분 명령:
```bash
collector workflow brainstorm --domain "사주" --count 10 --out ideas.json
collector workflow research    --keywords-file ideas.json --concurrency 3
collector workflow synthesize  --ideas-file ideas.json --research-file research.json
collector workflow design      --ideas-file ideas.json --research-file research.json --synth-file step3.json --out spec.md
collector workflow export      --channel UC... --content-type concept
```

`research --concurrency` 미지정 시 WARP 켜져있으면 자동으로 1, 그 외엔 3
(가정 IP 기준 sweet spot).

#### 중간에 뻗었을 때 — 같은 명령으로 재실행하면 이어감

`full` 은 step 끝날 때마다 결과를 `exports/run/step{N}_*.json` 에 저장한다.
LLM 한도 초과 등으로 어떤 step 이 실패해도:
1. 그 시점까지의 결과는 모두 디스크에 남는다 (vault 노트 + step1/2 JSON)
2. 의존성 없는 다음 step (예: step 5 export) 은 그대로 진행된다
3. 같은 명령을 다시 돌리면 **저장된 step 은 자동으로 스킵** 하고 실패한 step
   부터 이어서 실행한다 (`--restart` 로 강제 전체 재실행 가능)

예: `step 3 synthesize` 가 quota 로 실패 → 1시간 후 같은 명령 재실행 →
step 1/2 스킵, step 3 부터 다시 시도 → 성공하면 step 4/5 진행.

#### 설계서에 추가 자료 반영 — `--notes-file`

NotebookLM 채팅에서 받은 요약·도메인 메모·외부 자료를 텍스트 파일로 저장한 뒤
`design` / `full` 에 `--notes-file` 로 넘기면, vault 추출본과 **동등 비중** 으로
설계서 작성 LLM 에 입력됩니다 (8k자 자동 절단). 설계서는 그 메모를
"사용자 메모에 따르면 …" 형식으로 인용합니다.

```bash
# NotebookLM 에서 복사한 텍스트를 my_notes.md 로 저장 후
collector workflow design \
  --ideas-file step1_ideas.json --research-file step2_research.json \
  --synth-file step3_synthesize.json \
  --notes-file my_notes.md --out spec.md

# 또는 full 체인에서:
collector workflow full --domain "사주" --count 10 --notes-file my_notes.md
```

MCP 도구 `design_spec` 에서도 `user_notes` 인자로 동일하게 전달 가능합니다.

### 0.5.2 `collector mcp` — 외부 에이전트가 collector 를 자율 호출

Claude Desktop, Cursor, Codex CLI, AntiGravity 등이 MCP stdio 서버로 collector
를 도구처럼 부를 수 있습니다. 노출 도구 10종:
`run_query` / `search_notes` / `get_note` / `list_recent` / `list_channels` /
`get_pipeline_status` / `brainstorm_topics` / `research_batch` / `synthesize` /
`export_notebook`. 추가로 `vault://strategies/{source_key}` 리소스도 직접 읽음.

**Claude Desktop 등록 예시** (`~/.claude/claude_desktop_config.json` 또는
Windows 의 `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "collector": {
      "command": "python",
      "args": ["-m", "collector", "mcp"],
      "env": {
        "COLLECTOR_DATA_STORE": "C:/Users/USER/datacollector/data_store",
        "COLLECTOR_VAULT": "C:/Users/USER/datacollector/vault"
      }
    }
  }
}
```

저장 후 Claude Desktop 재시작 → 채팅창에서:

> "사주 분야 사업 아이디어 5개 자동 리서치한 다음 best 1개로 NotebookLM
> 합본 만들어줘"

→ Claude 가 `brainstorm_topics → research_batch → synthesize → export_notebook`
순서로 자동 호출, 결과 채팅에 보고. 1세션 비용 ≈ $0.05~$1 (모델별).

내부 collector 파이프라인 (extract 단계의 Gemini/Groq) 토큰은 별도이며 무료
티어 안에서 동작 — 에이전트 비용 = 외부 모델만.

`collector mcp --list-tools` 로 stdio 안 띄우고 도구 스키마 JSON 만 확인 가능
(설정/디버깅용).

---

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

#### 1단계. API 키 2개 발급 (각 3분)
| 키 | 발급 주소 | 참고 |
|---|---|---|
| `YOUTUBE_API_KEY` | https://console.cloud.google.com/apis/credentials | "API 키 만들기" → 복사. YouTube Data API v3를 "라이브러리"에서 활성화. |
| `GOOGLE_API_KEY`  | https://aistudio.google.com/app/apikey | "Create API key" → 복사. |

#### 2단계. 입력 — **.env 파일 한 번만 만들기 (권장)**
저장소 루트(`datacollector/` 폴더)에서:
```bash
cp .env.example .env
```
메모장/VS Code로 `.env` 열고 두 줄을 실제 값으로 교체:
```
YOUTUBE_API_KEY=AIzaSy...실제키
GOOGLE_API_KEY=AIzaSy...실제키
```
저장만 하면 끝. `collector` 명령이 자동으로 `.env`를 읽습니다.
`.env`는 `.gitignore`에 포함되어 Git에 올라가지 않습니다.

#### 3단계. 실행
```bash
collector run --query "단테 단타매매" --count 5
```
출력 배너에 `real · Gemini 1.5 Flash (무료 티어)` 로 나오면 성공.

#### (대안) 터미널 session에만 임시 설정
- macOS/Linux:
  ```bash
  export YOUTUBE_API_KEY=AIza...
  export GOOGLE_API_KEY=AIza...
  collector run --query "단테 단타매매"
  ```
- Windows PowerShell:
  ```powershell
  $env:YOUTUBE_API_KEY="AIza..."
  $env:GOOGLE_API_KEY="AIza..."
  collector run --query "단테 단타매매"
  ```
- Windows 명령 프롬프트:
  ```bat
  set YOUTUBE_API_KEY=AIza...
  set GOOGLE_API_KEY=AIza...
  collector run --query "단테 단타매매"
  ```

> Anthropic Claude로 돌리려면 `ANTHROPIC_API_KEY` 를 `.env`에 추가 후 `collector run --llm anthropic` (유료).

### 무료 티어 자동 실행 (GitHub Secrets + Actions)
로컬 `.env` 대신 클라우드에서 무료로 매일 돌리는 방법. Master_01 §8 0-Cost 전략.

#### 1단계. GitHub Secrets 등록
GitHub 리포지토리 페이지에서:
1. **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. 아래 2개(필수) + 1개(선택) 추가:

| Name | Value |
|---|---|
| `YOUTUBE_API_KEY` | Google Cloud에서 받은 키 |
| `GOOGLE_API_KEY`  | AI Studio에서 받은 Gemini 키 |
| `ANTHROPIC_API_KEY` | (선택) Claude 쓸 때만 |

> Secrets는 한 번 저장하면 다시 볼 수 없습니다. 값이 바뀌면 **Update** 또는 새로 만듭니다. 로그/PR에 노출되지 않도록 GitHub가 자동 마스킹합니다.

#### 2단계. 워크플로우 파일
이미 `.github/workflows/collect.yml` 에 포함되어 있습니다. 이 파일이 하는 일:
- **스케줄**: 매일 UTC 21:00 (KST 06:00) 자동 실행
- **수동**: Actions 탭 → "collector run" → **Run workflow** → 검색어 입력
- Secrets를 env로 주입 → `collector run` 실행 → `data_store/` 커밋 → dashboard.html 아티팩트 업로드

#### 3단계. 실행 확인
1. GitHub 리포지토리 → **Actions** 탭
2. "collector run" 워크플로우 선택
3. 최신 run 클릭 → **Artifacts** 에서 `collector-dashboard` 다운로드
4. 압축 풀어 `dashboard.html` 더블클릭하면 브라우저에서 결과 확인

#### 비용 0원 확인
- Gemini 1.5 Flash: 1,500 요청/일 무료, 파이프라인은 영상당 1~2회 호출 → 하루 5~20개 돌려도 무료
- YouTube Data API: 10,000 units/일 무료, 검색 1회 = 100 units → 하루 100회 검색 가능
- GitHub Actions: 2,000 runner-min/월 무료, 워크플로우 1회 ≈ 1분 → 월 2,000회까지 무료

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
