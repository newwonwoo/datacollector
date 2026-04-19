# Changelog — v10

> 이전 버전(V9) 원본은 `archive/V9/` 보존. v10은 V9의 리뷰 결과와 v2 플랫폼 계약을 반영한 YouTube Source Adapter 스펙이다.

## 요약
- **Scope 재정의**: Master_01~03은 v2 플랫폼 아래 "YouTube Source Adapter" 스펙으로 위치.
- **v2 계약 흡수**: `schema_version`, `run_id`, `provenance`, 3-tier status, event log, review gate (4단 confidence).
- **Pipeline 용어 통일**: Discover → Collect → Extract → Normalize → Review → Promote → Package.
- **부록 4종 신규**: Appendix A(매핑), B(상태/이벤트), C(관측성), D(보안).
- **O1~O15 설계자 제안 반영**.

## 본문 변경

### Master_01 (Foundation)
- Core Payload 확장: `schema_version`, `source_key`, `run_id`, `provenance`, `stage_status`, `record_status`, `caption_source`, `confidence`, `reviewer` 추가.
- `history[]` 원소 스키마 명시.
- `transcript_hash` 정규화 절차 4단계 명시.
- 3-tier 상태(Run/Stage/Record) 도입. 허용/금지 전이 정리.
- Priority: floor/ceiling, aging(+5/day), quota source 구분, Fast-Track과의 관계 명시.
- Concurrency: lease 10분 + heartbeat 2분 JSON lockfile.
- Kill Switch(`COLLECTOR_PAUSED=1`) 표준화.
- 관측 3축 정의(logs/metrics/traces).
- Daily Budget Guard + GitHub Actions runner-minute 감시.
- Failure code 접두어 규약(HTTP_/YT_/LLM_/SEMANTIC_/GIT_/SYS_).
- SQLite sidecar 인덱스 허용.

### Master_02 (Processing)
- Module A: Discover + Collect로 재정의.
- Module B: Extract + Normalize + Review Gate.
- Query Builder 생성 주체(규칙+LLM) 및 fallback 실체 정의.
- 자막 수집 fallback 순서: youtube-transcript-api → yt-dlp → 공식 Captions API. `caption_source` 필드 도입.
- 장문 청킹(map-reduce, 60% 토큰 임계).
- Strict JSON Handshake: 스키마 파일 경로, 재프롬프트 상세.
- Semantic 검증: 임베딩 cosine ≥ 0.60 보조, lemma 병산.
- Review Gate 4단 confidence + Human review hook(`review_queue/`).
- Soft filter 임계값 config 분리.
- LLM 토큰/비용 기록.

### Master_03 (Persistence/Sync/Resilience/QA)
- Module C: Promote(JSON 영속화), Module D: Package(Renderer + Sync Worker).
- Git 정책: `data/main` 분리 브랜치, GitHub App token, rebase-onto.
- Sync 재시도 상한 5회(2/4/8/16/32분 백오프).
- DLQ 실물 경로/재시도 워커 정의.
- 아카이브 파일명 seq 충돌 회피. ACTIVE + ARCHIVED 인덱스 조회 의무.
- 경보 채널(GitHub Issues + Slack optional).
- QA 지표 정의 + metrics/daily.jsonl.
- Rollback 절차(payload_version++, history 기록).
- PII 마스킹 전처리 단계.
- 삭제 영상 처리(`archive_state=REMOVED`).
- 테스트 시나리오 9종(Appendix B fixture).

## 신규 부록

### Appendix A — Platform Mapping
- Invariant: v10 Payload는 v2 7-schema로 무손실 분해 가능해야 함.
- SourceRecord/SegmentRecord/ClaimRecord/NormalizedClaim/ReviewRecord/ConflictRecord/PromotedArtifact 각각 v10 필드 매핑.
- 분해 예시 JSON.
- 마이그레이션 체크리스트.
- Reviewer Trace: 리뷰 Part1~3의 누락/보완/개선 + O1~O15 → v10 반영 위치 역추적 표.

### Appendix B — State & Event Log
- 3-tier 전이 그래프.
- events.jsonl 필드 규약.
- 테스트 시나리오 9종 fixture 구조 및 SC-01 예시.
- Snapshot + Events replay 정합성 정책.

### Appendix C — Observability
- metrics/daily.jsonl 스키마.
- YouTube Data API / LLM / CI 쿼터 추적 방식.
- Daily Budget Guard.
- 경보 임계값 테이블 + 채널.
- 서킷브레이커(youtube/llm/git_sync) 기준.
- SQLite 샘플 쿼리.

### Appendix D — Security & Compliance
- Secret 90일 로테이션 SOP.
- PII 마스킹 정규식 표.
- 저작권 선언 + DMCA/삭제 SOP + YouTube 측 삭제 감지.
- 데이터 분류/보존 표.
- 백업 전략.
- PR 머지 전 보안 체크리스트.

## 미해결/보류 (Out of Scope)
- Plugin 추상화 코드 설계 (v2 플랫폼 책임).
- ConflictRecord 구현 상세 (매매 전략 domain).
- Postgres/S3 storage 인터페이스 (0-Cost 전략과 충돌; v2 플랫폼 영역).

## 마이그레이션 가이드
- V9 Payload → v10 Payload: 별도 스크립트 `migrate/v9_to_v10.py`(예정)에서 아래 변환.
  - `status` → `record_status`(대응 맵: PROCESSED→promoted, FAILED→invalid 등)
  - `video_id` → `source_key = "youtube:" + video_id` 생성
  - `schema_version = "10.0.0"` 주입
  - `provenance` 채움, `run_id`는 synthetic `legacy-v9`로 부여
- V9 Markdown은 재렌더 불필요. 신규 프로모트부터 v10 템플릿 적용.
