# GOTCHAS — 세션 간 기억이 없는 Claude가 매번 반복하던 실수 모음

> 이 파일은 Claude Code가 작업 시작 시 **CLAUDE.md와 함께 먼저 읽어야 한다**.
> 여기 적힌 실수를 다시 내는 것은 개인 실수가 아니라 시스템 실패.

---

## G-01. `.env.example` 에 실제 API 키 들어감 (Security)
### 증상
- 유저가 Key를 `.env` 대신 `.env.example`에 써서 git에 push → 공개 히스토리에 영구 기록.
### 원인
- `.env.example`는 가이드 파일이라 git-tracked (`!.env.example` in `.gitignore`).
- 유저는 파일명만 보면 둘 다 "env 설정"으로 인식.
### 예방
- `.env.example` 상단 주석에 **"여기에 실제 키 쓰면 Git에 공개됨. 반드시 .env 파일에만 넣을 것"** 을 굵게.
- 실제 키처럼 보이는 값(AIzaSy로 시작하는 39자 등)이 `.env.example`로 커밋되려 할 때 pre-commit hook이 차단.
### 대응 완료?
- ✅ 주석 추가됨 (`.env.example`)
- ❌ pre-commit hook 아직 없음 → **TODO**

---

## G-02. GitHub Pages 자동 활성화 불가
### 증상
- 워크플로우에서 `actions/configure-pages@v5` with `enablement: true` 설정해도 404
- `Get Pages site failed. Please verify that the repository has Pages enabled`
### 원인
- **GitHub 정책상 Pages 활성화는 리포 소유자의 수동 UI 클릭이 필수.** 어떤 API/Action으로도 우회 불가.
### 예방
- Pages 배포 워크플로우는 반드시 `continue-on-error: true` 로 감싸 Pages OFF 상태에서도 나머지 단계 성공.
- 유저 안내에는 **"Pages 설정 URL → Source: GitHub Actions 선택 (수동 1회)"** 을 명시.
### 대응 완료?
- ✅ `continue-on-error: true` 반영됨
- ✅ Summary에 활성화 안내 메시지 자동 출력

---

## G-03. Private 리포는 raw.githack/htmlpreview/Pages 모두 안 됨
### 증상
- Private 리포에서 htmlpreview URL → 무한 로딩
- raw.githack URL → 404 (raw.githubusercontent.com이 인증 요구)
- Pages → "Upgrade or make this repository public" 페이월
### 원인
- Private 리포 정적 호스팅은 **유료 GitHub Pro ($4/mo) 또는 외부 서비스(Vercel/CF Pages OAuth 연결)가 필수**.
### 예방
- 유저의 리포 visibility 먼저 확인 (`gh api repos/<o>/<r>` 또는 Settings 스크린샷 요청).
- Public이면 문제 없음, Private이면 4가지 옵션 (Public 전환 / Pro / CF / 로컬) 제시.
### 대응 완료?
- ✅ README·매뉴얼에 4 옵션 명시

---

## G-04. PAT scope 오해: `workflow` ≠ workflow_dispatch 권한
### 증상
- 대시보드에서 실행 버튼 → `403 Must have admin rights to Repository`
### 원인
- Classic PAT 기준:
  - `workflow` scope = `.github/workflows/*.yml` **파일 편집** 권한
  - workflow_dispatch 호출 = `repo` (또는 public 리포면 `public_repo`) 필요
- 이름이 혼동을 유발함.
### 예방
- PAT 발급 링크는 항상 `scopes=repo,workflow` 로 프리셋.
- 문서/에러 메시지에서 scope 차이를 명시.
### 대응 완료?
- ✅ 대시보드 링크 `?scopes=repo,workflow`로 수정됨 (커밋 c62f63c)

---

## G-05. Python default parameter = 정의 시점 평가
### 증상
- `monkeypatch.setattr("time.sleep", ...)` 했는데 여전히 실제 sleep 발생 → 테스트 타임아웃
### 원인
```python
def foo(*, sleep_fn=time.sleep):  # 여기서 time.sleep이 즉시 평가됨
    sleep_fn(s)                    # 이후 time.sleep을 monkeypatch해도 foo는 원래 객체 참조
```
### 예방
- monkeypatch 필요한 함수는 모듈 레벨 import만 해두고 **호출 시점에 조회**.
```python
def foo(*, sleep_fn=None):
    (sleep_fn or time.sleep)(s)   # 또는 time.sleep(s) 직접 호출
```
### 대응 완료?
- ✅ stages.stage_package에서 time.sleep 직접 호출하도록 수정 (커밋 5135c64)

---

## G-06. 새 validation이 기존 test fixture를 깨뜨림
### 증상
- Summary 50~300자 검증 추가 → 기존 테스트 50건 이상 regression
### 원인
- 설계 원칙은 맞았으나 fixture의 summary가 짧은 placeholder ("요약", "s")라 즉시 실패.
### 예방
- 새 validation 추가 전 **먼저** `grep -r 'summary.*\":\s*\"[^\"]\{,30\}\"' tests/` 로 짧은 fixture 전수 스캔.
- 설계 의도가 기존 fixture와 충돌하면 fixture부터 현실적 길이로 보강 커밋 → 그 다음 validation PR 분리.
### 대응 완료?
- ✅ fixture 보강 후 validation 유지

---

## G-07. 상태 전이 순서 = 스토어 저장 시점과 맞물림
### 증상
- `store.upsert(payload)` 를 `_set_record(payload, "promoted")` 호출 전에 해 저장된 JSON이 `reviewed_confirmed`로 고정됨
### 원인
- 파이프라인 각 stage의 순서 중 "언제 저장하느냐"가 불명확. 상태 전이 후 저장이 원칙인데 순서가 뒤바뀜.
### 예방
- 원칙: **`_set_record` → `_set_stage(completed)` → `store.upsert`** 순서 고정.
- Stage 구현 시 이 순서를 주석으로 표시.
### 대응 완료?
- ✅ stage_promote 순서 수정 + 파이프라인 끝에 최종 upsert 추가 (커밋 2eacf4b)

---

## G-08. 유저 환경 모를 때 "작동한다"고 말하지 말 것
### 증상
- 모바일 전용 유저에게 `pip install -e .` 시키기
- Private 리포 유저에게 raw.githack URL 공유
### 원인
- 유저 환경 가정을 확인 없이 함.
### 예방
- 첫 답변 전 반드시 확인: **① 모바일/PC, ② 터미널 사용 가능?, ③ 리포 Public/Private, ④ 이미 어디까지 설정?**
- 확인 불명확하면 `AskUserQuestion`으로 묻기.
### 대응 완료?
- 부분. 이 체크리스트로 대체.

---

## G-09. 설계서 ↔ 코드 drift
### 증상
- 설계서에 명시됐는데 코드엔 없음 (Kill switch, Lockfile, DLQ persistence 등)
### 원인
- 설계 문서 쓰고 코드로 넘어갈 때 "무엇을 구현했는지" 대조 안 함.
### 예방
- Appendix_A_Platform_Mapping.md의 "Reviewer Trace" 표 상시 업데이트.
- PR 머지 전 체크: **"설계서 몇 절에 대응하는지, 테스트 ID는 뭔지"** 코멘트 필수.
### 대응 완료?
- ✅ test_p0_p1_p2.py, test_p3.py, test_p4.py 각 테스트가 설계 조항을 명시

---

## G-11. Obsidian Vault 출력 누락 — 설계에 있는데 코드에서 빠짐
### 증상
- 유저: "레포에서 바로 읽고 볼트에도 옮기라고 다 지시했을 텐데 읽을 게 왜 없어"
- Master_03 §2에 "Obsidian Renderer & Sync Worker" 명시. GitSyncAdapter만 구현됐고, vault/ 폴더에 Markdown이 실제로 안 써짐.
### 원인
- G-09 (설계 ↔ 코드 drift)의 한 사례. Renderer 로직이 GitSyncAdapter 안에만 있고, Git sync가 no-op일 때는 아무 Markdown도 생성 안 됨.
- 유저가 직접 "vault/ 폴더 검사해봐라" 하지 않는 이상 발견 어려움.
### 예방
- 파이프라인 기본 출력은 항상 **로컬 파일시스템**(`vault/`) 이다. Git sync는 선택 오버레이.
- 테스트에 `vault/<source_key>.md` 파일 존재 검증 필수.
### 대응 완료?
- ✅ collector/vault.py 신규, pipeline.run_pipeline이 vault_root 기본 "vault/"로 write_note + regenerate_moc (커밋 TBD)
- ✅ collect.yml 이 vault/ 도 함께 커밋

## G-10. 커밋 전 배포 경로 실제로 눌러보지 않음
### 증상
- "대시보드 URL입니다" 라고 안내 → 유저가 눌러보니 무한 로딩 / 404
### 원인
- 커밋만 하고 실사용 흐름을 브라우저/curl로 한 번도 안 찍어봄.
### 예방
- **사용자 경로를 코드 바꿀 때마다 curl 또는 브라우저 시뮬레이션으로 확인.**
- 예: `collector run` 수정 → `collector run --query "test" --count 1` 실제 실행 → `data_store/` 파일 확인 → dashboard.html 생성 확인.
### 대응 완료?
- 부분 — 로컬 검증은 함. 원격 Pages 배포 끝 URL 확인은 아직 수동 의존.

---

## G-13. YouTube 자막 수집이 GitHub Actions에서 전멸 (403/Forbidden)
### 증상
- 대시보드에서 실행 → `collect` 스테이지에서 전부 `YT_NO_TRANSCRIPT` / `HTTP_403`
- 같은 키·같은 코드로 로컬(가정망)에서 돌리면 정상
### 원인
- YouTube가 2024~2026년에 걸쳐 AWS/GCP/Azure/GitHub Actions 등 cloud provider IP 블록에 대해
  timedtext·yt-dlp·youtube-transcript-api 접근을 공격적으로 차단.
- YOUTUBE_API_KEY(Data API v3)는 메타데이터만 받고, 실제 자막은 캡션 다운로드 경로라
  API 키로 우회 불가.
### 예방
- 기본 실행 경로를 **로컬 웹앱**(`collector app`)으로 명시. GH Actions는 스케줄링·백업용으로만.
- 초기 설정 마법사(`docs/index.html`)에서 API 키 2개를 브라우저로 받아 `.env` 저장 → 즉시 사용.
### 대응 완료?
- ✅ `/api/run` 로컬 엔드포인트, 로컬 모드 감지, 초기 설정 마법사 (커밋 TBD)
- ✅ USER_MANUAL §0 로컬 웹앱 모드를 기본 사용법으로 전면 배치
- ❌ Residential proxy / OAuth YouTube Captions API 통합 — 스코프 밖

---

## G-14. 웹 UI에서 받은 API 키를 안전하게 저장/재사용
### 증상
- 유저가 매번 터미널에서 `.env` 손으로 편집 → 오타, 플레이스홀더 그대로 두기(G-01 재발)
### 원인
- `.env` 편집은 터미널/에디터 사용 능력 전제. 모바일·초보 유저에겐 큰 장벽.
### 예방
- `POST /api/config` 엔드포인트 — body에 받은 키를 `collector/env_io.merge_env()`로 병합 저장.
  - 기존 `.env`의 주석·다른 키를 보존 (re-entry 가능).
  - `os.environ`에도 반영하여 재시작 없이 즉시 `/api/run` 이 실제 어댑터 사용.
- `GET /api/config` 는 **key 값을 절대 반환하지 않고** `has_*` 플래그만. 로그에도 마스킹.
- 서버 바인딩은 `127.0.0.1` 고정 — LAN 노출 금지.
### 대응 완료?
- ✅ `collector/env_io.py` + `tests/test_env_io.py` (플레이스홀더 감지 포함)
- ✅ `collector/cli/api_handler.py` + `tests/test_app_api.py` (directory traversal 차단 검증 포함)
- ✅ 브라우저 쪽은 저장 후 즉시 input 값 지움 (DOM에도 남지 않음)

---

## 시스템적 개선 요구

이 파일이 **다음 세션의 Claude에게도 읽히려면**:

1. **`CLAUDE.md`에 "작업 시작 시 이 파일부터 읽어라" 명시.**
2. **PR 템플릿에 "GOTCHAS.md 대조 완료" 체크박스 추가.**
3. **pre-commit hook**으로 `.env*` 파일에 실제 키처럼 생긴 문자열 커밋 차단.
4. **regression 감지**: 매 커밋마다 `python -m pytest` 필수 통과.

---

## 증분 규칙

새 실수가 발견되면:
1. 이 파일에 `G-NN` 으로 추가.
2. 가능한 예방책(설계·테스트·자동화) 병기.
3. "대응 완료?" 체크.

이 파일은 **영구 누적**. 지우지 않는다.
