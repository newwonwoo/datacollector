"""`collector run --query <q>` — end-to-end pipeline on a query.

- If YOUTUBE_API_KEY + (ANTHROPIC_API_KEY or GOOGLE_API_KEY) are set,
  uses the real adapters.
- Otherwise runs a scripted mock simulation tied to the query so users can
  see the full pipeline behaviour without spending quota.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from ..events import EventLogger
from ..payload import new_payload
from ..pipeline import run_pipeline
from ..priority import compute_priority, sort_queue
from ..query import build_query, fallback_query
from ..runs import save_run_snapshot
from ..services import MockError, Services, build_mock_services
from ..store import JSONStore


def _rule_based_extract(transcript: str, last_code: str) -> dict:
    """Minimal payload built from the transcript when every LLM is
    quota-exhausted. Schema-shaped so downstream stages don't crash.

    summary: first ~250 chars of transcript (trimmed at sentence boundary
        when possible).
    notes_md: full transcript wrapped in a fenced quote so vault still
        archives the raw text.
    tags: extracted via collector.clickbait.extract_nouns on the
        transcript (no LLM, regex over Korean noun-shaped tokens).
    llm_confidence: "low" so review never auto-confirms it.
    content_type: "mixed" — we don't know.
    A note in `unclear` records why this fallback fired so re-extraction
    later (after quota reset) is easy to identify.
    """
    from ..clickbait import extract_nouns

    text = (transcript or "").strip()
    if not text:
        summary = "자막이 비어있어 추출할 내용이 없음. (룰베이스 폴백 — 데이터 보존용 placeholder)"
    else:
        snippet = text[:300]
        cut = max(snippet.rfind("."), snippet.rfind("。"), snippet.rfind("?"),
                  snippet.rfind("!"))
        if cut >= 50:
            summary = snippet[: cut + 1].strip()
        else:
            summary = snippet[:250].strip()
        if len(summary) < 30:
            summary = (summary + " — 자막 일부").strip()

    tags = [n.lower() for n in extract_nouns(text, top_n=5) if n][:5]
    return {
        "summary": summary,
        "content_type": "mixed",
        "knowledge": [],
        "rules": [],
        "examples": [],
        "claims": [],
        "unclear": [
            f"LLM 전체 chain 이 {last_code} 로 quota 소진되어 룰베이스 폴백 적용. "
            "quota 리셋 후 재추출 권장."
        ],
        "tags": tags,
        "llm_confidence": "low",
        "notes_md": text,
    }


def _real_services_or_none(llm_choice: str | None = None) -> Services | None:
    """Build real adapters with a free-tier-friendly LLM fallback chain.

    LLM priority — picked from env keys + an optional `llm_choice`:
      gemini  : Gemini 2.5 Flash    (1500 RPD / 15 RPM, project-wide)
      groq    : Llama 3.3 70B       (separate RPD pool, very fast)
      anthropic: Claude (paid)

    Default order if `llm_choice` is None: gemini → groq → anthropic, taking
    the first one whose key is set. Explicit `--llm <name>` forces one.
    YouTube Data API v3: free tier (10k quota units/day).
    """
    yt_key = os.environ.get("YOUTUBE_API_KEY")
    goog_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")
    anth_key = os.environ.get("ANTHROPIC_API_KEY")

    if not yt_key:
        return None

    from ..adapters.youtube import YouTubeAdapter
    yt = YouTubeAdapter(yt_key)

    want = (llm_choice or os.environ.get("COLLECTOR_LLM", "")).lower()

    def _make(name: str):
        if name == "gemini" and goog_key:
            from ..adapters.llm_gemini import GeminiAdapter
            return [GeminiAdapter(goog_key, model="gemini-2.5-flash")]
        if name == "groq" and groq_key:
            from ..adapters.llm_groq import GroqAdapter
            # Two adapters in the same provider: the quality model first
            # (lower TPD on Groq free tier — 100k for llama-3.3-70b), then
            # a smaller, higher-TPD bulk model so a daily-cap 429 on the
            # primary just falls through to the secondary instead of the
            # whole run dying.
            return [
                GroqAdapter(groq_key, model="llama-3.3-70b-versatile"),
                GroqAdapter(groq_key, model="llama-3.1-8b-instant"),
            ]
        if name == "anthropic" and anth_key:
            from ..adapters.llm_anthropic import AnthropicAdapter
            return [AnthropicAdapter(anth_key)]
        return []

    # Build the adapter chain. When the user explicitly forces a single
    # provider via --llm/COLLECTOR_LLM we still expand it into one or more
    # adapters (e.g., 'groq' yields two models). Otherwise we collect
    # every provider whose key is set so a mid-run quota/auth failure on
    # the primary one transparently rolls over to the next.
    chain: list = []
    if want:
        chain.extend(_make(want))
    else:
        for name in ("gemini", "groq", "anthropic"):
            chain.extend(_make(name))
    if not chain:
        return None

    def llm_extract(transcript: str, attempt: int) -> dict:
        """Try each adapter in order; on quota/expired errors fall through.

        Errors that point at THIS adapter — HTTP_429, LLM_HTTP_400 (Gemini's
        signal for "API key expired"), HTTP_5XX — trigger a fallback to the
        next adapter. Schema/semantic errors mean the model answered, so we
        re-raise immediately (the pipeline's own retry layer handles them).

        After the chain is exhausted with quota-class errors only, emit a
        rule-based minimal payload from the transcript itself instead of
        failing — data is preserved and a later run (after quota reset)
        can re-extract the same source_key.
        """
        ROLLOVER_CODES = {
            "HTTP_429", "HTTP_5XX",
            "LLM_HTTP_400", "LLM_HTTP_401", "LLM_HTTP_403",
            "LLM_HTTP_413",  # request too large for the current model's TPM
        }
        last_err = None
        all_quota = True
        for i, adapter in enumerate(chain):
            try:
                return adapter.extract(transcript, attempt)
            except MockError as e:
                last_err = e
                if e.code in ROLLOVER_CODES:
                    if i < len(chain) - 1:
                        sys.stderr.write(
                            f"[llm] {adapter.__class__.__name__} {e.code} → fallback to "
                            f"{chain[i+1].__class__.__name__}\n"
                        )
                    continue
                all_quota = False
                raise
        # Chain exhausted. If every failure was quota-class, emit a
        # rule-based minimal extraction so the record is preserved
        # rather than marked invalid.
        if all_quota and last_err is not None and last_err.code in ROLLOVER_CODES:
            sys.stderr.write(
                f"[llm] all adapters quota-exhausted ({last_err.code}); "
                f"using rule-based fallback to preserve transcript\n"
            )
            return _rule_based_extract(transcript, last_err.code)
        if last_err is not None:
            raise last_err
        raise MockError("LLM_NO_PROVIDER", "no LLM adapter available")

    # Expose the chain on the closure so tests / introspection can see the
    # adapter sequence (closure is otherwise opaque).
    llm_extract.adapters = chain  # type: ignore[attr-defined]

    return Services(
        youtube_search=yt.search,
        youtube_captions=yt.captions,
        youtube_video_alive=yt.video_alive,
        llm_extract=llm_extract,
        semantic_similarity=lambda s, t: 0.75,
        git_sync=lambda p: None,
    )


def _scripted_candidates(query: str, count: int) -> list[dict]:
    """Deterministic candidates derived from the query hash."""
    h = hashlib.sha256(query.encode()).hexdigest()
    channels = ["단타왕TV", "스캘핑연구소", "개미투자일기", "실전트레이더", "차트북"]
    out = []
    for i in range(count):
        vid = f"Q{h[:6].upper()}{i:02d}"
        out.append({
            "video_id": vid,
            "channel_id": f"UC_{h[i:i+10]}",
            "title": f"[{query}] {channels[i % len(channels)]} 편 #{i+1}",
            "published_at": f"2026-04-{10+i:02d}T09:00:00Z",
        })
    return out


def _scripted_services(query: str, candidates: list[dict]) -> tuple[Services, list[dict]]:
    """Assign 5 different outcomes to showcase the pipeline paths."""
    captions = {}
    transcripts = []
    for i, c in enumerate(candidates):
        vid = c["video_id"]
        path = i % 5
        if path == 0:
            # manual + confirmed
            text = (
                f"{query}의 핵심은 장중 고점 돌파다. "
                "거래량 증가와 함께 저항선을 뚫을 때 분할 진입한다. "
                "손절은 직전 저점. 익절은 +2% 분할 매도."
            )
            captions[vid] = {"source": "manual", "text": text}
            transcripts.append((vid, text, "confirmed"))
        elif path == 1:
            # ASR → inferred (low similarity)
            text = f"{query} 전략을 설명하는 자동 자막 기반 영상 내용"
            captions[vid] = {"source": "asr", "text": text}
            transcripts.append((vid, text, "asr_inferred"))
        elif path == 2:
            # HTTP 429
            captions[vid] = MockError("HTTP_429", "quota exceeded")
            transcripts.append((vid, "", "http_429"))
        elif path == 3:
            # No transcript
            captions[vid] = {"source": "none", "text": ""}
            transcripts.append((vid, "", "no_transcript"))
        elif path == 4:
            # Another success with different rules
            text = (
                f"{query} 추가 전략. 눌림목 매수와 반등 시 익절. "
                "이동평균선 5-20 골든크로스에서 진입."
            )
            captions[vid] = {"source": "manual", "text": text}
            transcripts.append((vid, text, "confirmed"))

    # Per-video LLM outputs (round-robin)
    llm_outputs = {
        "confirmed_0": {
            "summary": f"{query} 장중 고점 돌파 분할 진입 전략. 저항선 + 거래량 필수.",
            "rules": ["고점 돌파 시 분할 진입", "손절 직전 저점", "익절 +2% 분할"],
            "tags": ["단타", "돌파매매"],
        },
        "confirmed_4": {
            "summary": f"{query} 눌림목 매수 + 5-20 골든크로스 병행 전략.",
            "rules": ["눌림목 매수", "5-20 골든크로스 확인", "반등 시 익절"],
            "tags": ["눌림목", "이평선"],
        },
        "asr_inferred": {
            "summary": f"{query} 관련 자동자막 기반 요약.",
            "rules": ["자동자막 기반 룰 후보"],
            "tags": ["asr"],
        },
    }

    def captions_fn(vid: str):
        out = captions.get(vid, {"source": "none", "text": ""})
        if isinstance(out, MockError):
            raise out
        return out

    def llm_fn(text: str, attempt: int):
        for vid, t, kind in transcripts:
            if t == text:
                if kind == "confirmed" and vid.endswith("00"):
                    return llm_outputs["confirmed_0"]
                if kind == "confirmed":
                    return llm_outputs["confirmed_4"]
                if kind == "asr_inferred":
                    return llm_outputs["asr_inferred"]
        return {"summary": "기본 요약", "rules": ["기본 규칙"], "tags": []}

    def sim_fn(src: str, summary: str) -> float:
        return 0.35 if "asr" in (summary or "").lower() or "자동자막" in summary else 0.78

    services = Services(
        youtube_search=lambda q: candidates,
        youtube_captions=captions_fn,
        youtube_video_alive=lambda v: True,
        llm_extract=llm_fn,
        semantic_similarity=sim_fn,
        git_sync=lambda p: None,
    )
    return services, transcripts


def run_query(
    query: str,
    *,
    count: int = 5,
    data_store_root: Path = Path("data_store"),
    logs_root: Path = Path("logs"),
    llm_choice: str | None = None,
    target_channel_id: str | None = None,
    min_views: int = 0,
    min_subscribers: int = 0,
) -> dict:
    """Run the pipeline for `query`. Returns a summary dict.

    - Uses `query.build_query()` to normalize raw NL → QueryObject (Master_02 §1).
    - Ranks candidate payloads via `priority.sort_queue` before execution
      (Master_01 §5 processing order).
    """
    logs_root.mkdir(parents=True, exist_ok=True)
    events_path = logs_root / "events.jsonl"
    logger = EventLogger(events_path)
    store = JSONStore(root=data_store_root)

    # Build structured query (P1-d)
    q_obj = build_query(query, target_channel_id=target_channel_id)

    real = _real_services_or_none(llm_choice)
    if real is not None:
        services = real
        # Oversample so that after pre-COLLECT dedup we still have ~`count`
        # genuinely new videos. YouTube search pagination is paid quota
        # (100 units/page) so we cap the multiplier at 3× — enough for the
        # common case of "most top results already in store" without
        # blowing through 10k daily units.
        q_dict = q_obj.to_dict()
        q_dict["max_results"] = max(count * 3, count)
        try:
            candidates = services.youtube_search(q_dict)
        except Exception:
            candidates = []
        # P4-5: fallback query on empty result (Master_02 §1)
        if not candidates:
            fb = fallback_query(query)
            fb_dict = fb.to_dict()
            fb_dict["max_results"] = max(count * 3, count)
            try:
                candidates = services.youtube_search(fb_dict)
            except Exception:
                candidates = []
        mode = f"real:{llm_choice or os.environ.get('COLLECTOR_LLM', 'gemini')}"

        # Quality filter: enrich with view/subscriber counts and drop
        # anything below user-supplied thresholds. Cheap (1 unit per 50
        # video IDs + 1 unit per 50 channel IDs).
        if candidates and (min_views > 0 or min_subscribers > 0):
            try:
                from ..adapters.youtube import YouTubeAdapter
                yt_key = os.environ.get("YOUTUBE_API_KEY")
                if yt_key:
                    YouTubeAdapter(yt_key).enrich_stats(candidates)
                    before = len(candidates)
                    candidates = [
                        c for c in candidates
                        if c.get("view_count", 0) >= min_views
                        and c.get("subscriber_count", 0) >= min_subscribers
                    ]
                    logger.log(
                        entity_type="run", entity_id="pre_filter",
                        from_status=None, to_status="quality_filtered",
                        reason=f"min_views={min_views} min_subs={min_subscribers}",
                        metrics={"before": before, "after": len(candidates)},
                    )
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[run] quality filter skipped: {e}\n")
    else:
        candidates = _scripted_candidates(query, count)
        services, _ = _scripted_services(query, candidates)
        mode = "mock"

    run_id = f"run_{uuid.uuid4().hex[:8]}"

    # Build Payloads and rank by priority (P1-c)
    payloads = []
    for c in candidates:
        p = new_payload(
            video_id=c["video_id"], run_id=run_id,
            channel_id=c.get("channel_id", ""), title=c.get("title", ""),
            published_at=c.get("published_at", ""), source_query=query,
        )
        p["priority_score"] = compute_priority(
            p, target_channel_ids={q_obj.target_channel_id} if q_obj.target_channel_id else None
        )
        payloads.append(p)
    payloads = sort_queue(
        payloads, target_channel_ids={q_obj.target_channel_id} if q_obj.target_channel_id else None
    )

    # Pre-COLLECT dedup (G-15-related: don't waste caption fetches on
    # already-known videos — saves bot-score, time, and Gemini quota).
    # Pipeline-level Rule C still runs for transcript-change detection on
    # records we DO process; this filter just skips ones we've already
    # *successfully* processed. Records previously left in `invalid`,
    # `collected`, `extracted`, or `normalized` are retried — those failed
    # mid-pipeline (e.g., a transient LLM_HTTP_404 or stale model name)
    # and silently skipping them forever surprises the user.
    SUCCESS_STATES = {
        "promoted",
        "reviewed_confirmed",
        "reviewed_inferred",
        "reviewed_unverified",
        "reviewed_rejected",
    }
    pre_dedup = len(payloads)
    seen: set[str] = set()
    deduped: list = []
    for p in payloads:
        sk = p["source_key"]
        if sk in seen:  # de-dup within the same search response
            continue
        existing = store.get(sk)
        if existing is not None and existing.get("record_status") in SUCCESS_STATES:
            continue
        seen.add(sk)
        deduped.append(p)
    # User asked for `count` NEW videos — keep at most that many from the
    # oversampled batch. (The oversample factor is set in the YouTube
    # search call above.)
    if len(deduped) > count:
        deduped = deduped[:count]
    skipped_duplicates = pre_dedup - len(deduped)
    if skipped_duplicates:
        logger.log(
            entity_type="run",
            entity_id=run_id,
            from_status=None,
            to_status="dedup_skipped",
            run_id=run_id,
            reason=f"{skipped_duplicates} already-processed",
            metrics={"skipped": skipped_duplicates, "kept": len(deduped)},
        )
    payloads = deduped

    per_video_status = []
    processed_payloads: list = []
    for payload in payloads:
        run_pipeline(
            payload, services, store, logger,
            fast_track=bool(q_obj.target_channel_id and payload.get("channel_id") == q_obj.target_channel_id),
        )
        processed_payloads.append(payload)
        per_video_status.append({
            "video_id": payload["video_id"],
            "title": payload["title"],
            "record_status": payload["record_status"],
            "confidence": payload.get("confidence"),
            "failure_reason_code": payload.get("failure_reason_code"),
            "rules_n": len(payload.get("rules") or []),
            "priority_score": payload.get("priority_score"),
        })

    # Per-run snapshot (Master_01 §2.1)
    try:
        save_run_snapshot(run_id, processed_payloads, query=query, logger=logger)
    except Exception:
        pass

    summary = {
        "query": query,
        "mode": mode,
        "run_id": run_id,
        "requested_count": count,
        "candidates": len(candidates),
        "skipped_duplicates": skipped_duplicates,
        "processed": len(payloads),
        "promoted": sum(1 for r in per_video_status if r["record_status"] == "promoted"),
        "inferred": sum(1 for r in per_video_status if r["record_status"] == "reviewed_inferred"),
        "unverified": sum(1 for r in per_video_status if r["record_status"] == "reviewed_unverified"),
        "invalid": sum(1 for r in per_video_status if r["record_status"] == "invalid"),
        "failed_reasons": sorted({r["failure_reason_code"] for r in per_video_status if r["failure_reason_code"]}),
        "events": len(logger.events),
        "per_video": per_video_status,
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector run", description="Run the v10 pipeline on a query.")
    ap.add_argument("--query", required=True, help="검색어 (예: 단타매매전략)")
    ap.add_argument("--count", type=int, default=10, help="후보 영상 수 (기본 10, 상한 없음 — YouTube 쿼터 주의)")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--llm", choices=["gemini", "groq", "anthropic"], default=None,
                    help="LLM 선택 (기본: gemini 무료 티어)")
    ap.add_argument("--target-channel", default=None,
                    help="Fast-Track 대상 channel_id (지정 시 해당 채널 우선)")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    args = ap.parse_args(argv)

    summary = run_query(
        args.query, count=args.count,
        data_store_root=Path(args.data_store),
        logs_root=Path(args.logs),
        llm_choice=args.llm,
        target_channel_id=args.target_channel,
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    banner = {
        "mock": "mock (API 키 미설정 — 시뮬레이션)",
        "real:gemini": "real · Gemini 1.5 Flash (무료 티어)",
        "real:anthropic": "real · Claude (유료)",
    }.get(summary["mode"], summary["mode"])

    print(f"\n=== collector run: {summary['query']} [{banner}] ===")
    print(f"run_id:     {summary['run_id']}")
    print(f"candidates: {summary['candidates']}")
    print(f"promoted:   {summary['promoted']}")
    print(f"inferred:   {summary['inferred']}")
    print(f"unverified: {summary['unverified']}")
    print(f"invalid:    {summary['invalid']}")
    if summary["failed_reasons"]:
        print(f"failures:   {', '.join(summary['failed_reasons'])}")
    print(f"events:     {summary['events']}")
    print("\nper-video:")
    for r in summary["per_video"]:
        code = r["failure_reason_code"] or "-"
        print(f"  {r['video_id']}  {r['record_status']:22}  conf={r['confidence'] or '-':10}  rules={r['rules_n']}  fail={code}  :: {r['title']}")
    print(f"\n→ data_store: {args.data_store}")
    print(f"→ events.jsonl: {args.logs}/events.jsonl")
    print(f"→ 대시보드 확인: collector app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
