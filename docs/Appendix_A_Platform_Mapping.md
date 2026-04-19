# Appendix A — Platform Mapping (v2 ↔ v10 YouTube Adapter)

> **목적**: v2 `data_contract_v2.md`의 7종 공용 스키마와 v10 YouTube Payload의 대응 관계를 명시한다. 장래 플랫폼 통합 시 마이그레이션 체크리스트.

## 1. 불변식 (Invariant)
**v10 YouTube Payload는 언제든 v2 7-schema 레코드 집합으로 무손실 분해 가능해야 한다.**
이 불변식이 깨지면 플랫폼 통합 시 데이터 손실이 발생한다. 필드 추가/삭제 시 이 표를 먼저 업데이트하고 통합 테스트에 매핑 회귀 케이스를 추가.

## 2. 공통 메타 필드 대응
| v2 필수 메타 | v10 위치 |
|---|---|
| `schema_version` | `Payload.schema_version` |
| `run_id` | `Payload.run_id` + `provenance.run_id` |
| `project_id` | (상위 플랫폼에서 주입. v10에서는 고정값 `youtube_collector`) |
| `domain` | (플랫폼 도메인 설정. 예: `trading_strategy`) |
| `created_at` | `Payload.collected_at` |
| `updated_at` | `Payload.history[].at` 최신값 |

## 3. 7-Schema 분해 매핑

### 3.1 SourceRecord
| v2 필드 | v10 Payload 원천 |
|---|---|
| `source_id` | `provenance.source_id` (=`source_key`) |
| `source_type` | 고정 `"youtube"` |
| `origin_url` | `https://www.youtube.com/watch?v={video_id}` (유도) |
| `collected_at` | `collected_at` |
| `title` | `title` |
| `creator` | `channel_id` (또는 채널명 조회 시 확장) |
| `published_at` | `published_at` |
| `body` | 자막 원문 전체 (전처리 전) |

### 3.2 SegmentRecord
v10은 기본 "영상 1개 = 세그먼트 1개"를 원칙으로 하되, 장문 청킹 시 N개 생성.
| v2 필드 | v10 원천 |
|---|---|
| `segment_id` | `{source_key}#full` 또는 `{source_key}#chunk_{n}` |
| `source_id` | `provenance.source_id` |
| `segment_type` | `"transcript"` |
| `text` | (정규화된) 자막 본문 |
| `start`, `end` | 청크 시간 범위(초). 단일 세그먼트면 null 허용 |

### 3.3 ClaimRecord
v10의 `rules[]` 각 원소가 하나의 Claim.
| v2 필드 | v10 원천 |
|---|---|
| `claim_id` | `{segment_id}#claim_{idx}` |
| `source_id` | `provenance.source_id` |
| `segment_id` | `provenance.segment_id` |
| `claim_type` | 도메인 분류. v10 기본 `"rule"` |
| `raw_quote` | LLM이 rule의 근거로 인용한 자막 구간 (프롬프트에서 요구해 획득) |
| `tags` | `tags[]` |
| `creator` | `channel_id` |

### 3.4 NormalizedClaim
| v2 필드 | v10 원천 |
|---|---|
| `normalized_claim_id` | `{claim_id}#norm` |
| `claim_id` | 위와 동일 |
| `canonical_term` | 플랫폼 domain 책임 (v10 범위 외). 기본은 원문 그대로 |
| `confidence` | `Payload.confidence` |
| `normalizer_version` | `Payload.llm_context.prompt_version` 또는 별도 normalizer 버전 |

### 3.5 ReviewRecord
Review gate(Master_02 §3.3) 결과.
| v2 필드 | v10 원천 |
|---|---|
| `review_id` | `{normalized_claim_id}#review` |
| `normalized_claim_id` | 위 |
| `reviewer` | `Payload.reviewer` (`auto | human | none`) |
| `decision` | `Payload.record_status`의 `reviewed_*` 접미어 |
| `updated_confidence` | `Payload.confidence` |
| `reason` | `Payload.failure_reason_detail` 또는 review 메모 |

### 3.6 ConflictRecord
v10 기본 범위 외. 매매 전략 도메인에서 다건 통합 분석 시 생성.
| v2 필드 | v10 원천 |
|---|---|
| `conflict_id` | 상위 플랫폼 생성 |
| `canonical_term` | 상위 플랫폼 참조 |
| `claim_ids` | v10에서 공급된 `claim_id` 배열 |
| `conflict_type` | 상위 플랫폼 분류 |

### 3.7 PromotedArtifact
Package 단계의 Markdown 산출물.
| v2 필드 | v10 원천 |
|---|---|
| `artifact_id` | Markdown 파일 경로 hash |
| `artifact_type` | `"obsidian_note"` |
| `source_claims` | 포함된 `claim_id` 배열 |
| `review_refs` | `review_id` 배열 |
| `status` | `"published"` |

## 4. 분해 예시 (v10 → v2)
Payload 1건 → SourceRecord 1 + SegmentRecord 1 + ClaimRecord N(=rules 개수) + NormalizedClaim N + ReviewRecord N + PromotedArtifact 1.

### v10 Payload (축약)
```json
{
  "schema_version": "10.0.0",
  "source_key": "youtube:ABCDEFGHIJK",
  "video_id": "ABCDEFGHIJK",
  "channel_id": "UCxxxx",
  "title": "단타 매매 원칙 5가지",
  "published_at": "2026-04-15T00:00:00Z",
  "collected_at": "2026-04-19T03:40:22Z",
  "transcript_hash": "abc123",
  "caption_source": "manual",
  "provenance": {"source_id": "youtube:ABCDEFGHIJK", "segment_id": "youtube:ABCDEFGHIJK#full", "run_id": "run_xxx"},
  "record_status": "reviewed_confirmed",
  "confidence": "confirmed",
  "reviewer": "auto",
  "rules": ["장중 고점 돌파 시 분할 진입", "-3% 손절 고정"],
  "tags": ["단타","스캘핑"]
}
```

### v2 분해 결과 (개념)
- SourceRecord(`source_id=youtube:ABCDEFGHIJK`)
- SegmentRecord(`segment_id=youtube:ABCDEFGHIJK#full`)
- ClaimRecord × 2 (rules 개수)
- NormalizedClaim × 2
- ReviewRecord × 2 (decision=`reviewed_confirmed`, reviewer=`auto`)
- PromotedArtifact × 1 (Markdown 파일)

## 5. 마이그레이션 체크리스트
플랫폼으로 흡수 시 검증 항목.
- [ ] v10 Payload 1건을 분해 함수(`migrate/youtube_to_v2.py`)에 통과.
- [ ] 모든 필수 v2 필드가 null이 아닌지 assert.
- [ ] `source_key` ↔ `source_id` 1:1 매핑.
- [ ] 라운드트립 테스트: 분해 후 역재조립이 semantic 동일.
- [ ] archive/V9 원본은 별도 legacy 마이그레이션 스크립트 필요 (`migrate/v9_to_v10.py`).

## 6. Reviewer Trace (리뷰 항목 → 재작성 반영 위치)
리뷰 리포트의 각 "누락/보완/개선" 항목이 v10에서 어디에 반영됐는지 역추적.

### Part 1 (Master_01)
| # | 리뷰 항목 | v10 반영 위치 |
|---|---|---|
| A1 | `history[]` 스키마 부재 | Master_01 §1 history 원소 스키마 |
| A2 | `transcript_hash` 정규화 | Master_01 §3 정규화 절차 |
| A3 | 락 TTL·소유자 | Master_01 §7 잠금 구조 |
| A4 | 상태 전이 누락 | Master_01 §4 + Appendix B |
| B5 | `priority_score` 범위/aging | Master_01 §5 |
| B6 | 쿼터 출처 구분 | Master_01 §5 + Appendix C |
| B7 | Fast-Track vs 우선순위 | Master_01 §5 |
| B8 | schema 버전 분리 | Master_01 §1 `schema_version` |
| C9 | Secret 로테이션 | Master_01 §6 + Appendix D |
| C10 | CI quota 한도 | Master_01 §9 + Appendix C |
| C11 | 구조화 로그 | Master_01 §8 events.jsonl |

### Part 2 (Master_02)
| # | 리뷰 항목 | v10 반영 위치 |
|---|---|---|
| A1 | Query Template 생성 주체 | Master_02 §1 |
| A2 | fallback 실제 값 | Master_02 §1 |
| A3 | caption 종류 구분 | Master_02 §2.2 |
| A4 | 수집 라이브러리/fallback | Master_02 §2.2 |
| A5 | 장문 청킹 | Master_02 §3.1 |
| A6 | 재프롬프트 상세 | Master_02 §3.1 |
| B7 | 환각 판정 완화 | Master_02 §3.2 + §3.3 |
| B8 | Soft Filter override | Master_02 §2.3 |
| B9 | 금지어 관리 | Master_02 §3.2 |
| B10 | tags 규격 | Master_02 §3.2 |
| B11 | 토큰/비용 | Master_02 §4 |
| C12 | Prompt Git 경로 | Master_02 §4 |
| C13 | 언어 필터 | Master_02 §2.1 |
| C14 | 재검사 주기 | Master_02 §2.5 (Rule B/Appendix B) |

### Part 3 (Master_03)
| # | 리뷰 항목 | v10 반영 위치 |
|---|---|---|
| A1 | Git 인증/브랜치 | Master_03 §2 Git 정책 |
| A2 | DLQ 실물 | Master_03 §4 |
| A3 | Sync 재시도 상한 | Master_03 §3 |
| A4 | 아카이브 조회 | Master_03 §5 + Appendix B |
| A5 | 경보 채널 | Master_03 §8 |
| A6 | 메트릭 저장소 | Master_03 §7 + Appendix C |
| B7 | 삭제 영상 처리 | Master_03 §6 + Appendix D |
| B8 | PII 마스킹 | Master_03 §2 PII 전처리 + Appendix D |
| B9 | 롤백 절차 | Master_03 §1 Rollback |
| B10 | 테스트 하네스 | Master_03 §9 + Appendix B |
| B11 | 비용 기록 | Master_03 §7 |
| C12 | 템플릿 실체 | Master_03 §2 템플릿 |
| C13 | 아카이브 파일명 충돌 | Master_03 §5 seq |
| C14 | Circuit Breaker | Master_03 §8 + Appendix C |
| C15 | 백업 전략 | (보류, Appendix D 범위 언급) |

### Cross-cutting
| 항목 | 반영 |
|---|---|
| 시간대 | Master_01 §5 UTC 고정 |
| Schema 마이그레이션 | Appendix A §5 체크리스트 |
| 관측 3축 | Master_01 §8 logs/metrics/traces |

### Opinionated Additions (O1-O15)
| # | 반영 위치 |
|---|---|
| O1 포지셔닝 | 각 Master 문두 Scope 선언 |
| O2 source_key | Master_01 §1, 전 문서 키 네임스페이싱 |
| O3 snapshot+events | Master_01 §8 |
| O4 Human Review | Master_02 §3.3 + Master_03 §4 review_queue |
| O5 Confidence 임계식 | Master_02 §3.3 표 |
| O6 Daily Budget | Master_01 §9 + Master_03 §8 |
| O7 Kill Switch | Master_01 §7 |
| O8 Invariant | Appendix A §1 |
| O9 SQLite sidecar | Master_01 §9 + Master_03 §5 |
| O10 Time window 기준 | Master_01 §5 |
| O11 Failure code 접두어 | Master_01 §10 |
| O12 V9 보존 | `archive/V9/` + CHANGELOG_v10.md |
| O13 Fixture 중심 | Appendix B |
| O14 관측 3축 | Master_01 §8 + Appendix C |
| O15 단일 교체 | 이번 커밋 단위 |
