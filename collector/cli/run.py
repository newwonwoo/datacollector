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
from ..services import MockError, Services, build_mock_services
from ..store import JSONStore


def _real_services_or_none(llm_choice: str | None = None) -> Services | None:
    """Build real adapters honoring the free-tier default (Gemini Flash).

    - Gemini 1.5 Flash: 무료 티어 (15 RPM / 1500 RPD). 기본값.
    - YouTube Data API v3: 무료 티어 (10,000 quota units/day).
    - Anthropic Claude: 유료. `--llm anthropic` 또는 COLLECTOR_LLM=anthropic로만 선택.
    """
    yt_key = os.environ.get("YOUTUBE_API_KEY")
    goog_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    anth_key = os.environ.get("ANTHROPIC_API_KEY")

    if not yt_key:
        return None

    from ..adapters.youtube import YouTubeAdapter
    yt = YouTubeAdapter(yt_key)

    want = (llm_choice or os.environ.get("COLLECTOR_LLM", "gemini")).lower()
    llm = None
    if want == "gemini" and goog_key:
        from ..adapters.llm_gemini import GeminiAdapter
        llm = GeminiAdapter(goog_key, model="gemini-1.5-flash")
    elif want == "anthropic" and anth_key:
        from ..adapters.llm_anthropic import AnthropicAdapter
        llm = AnthropicAdapter(anth_key)
    elif goog_key:  # safe fallback to free-tier
        from ..adapters.llm_gemini import GeminiAdapter
        llm = GeminiAdapter(goog_key, model="gemini-1.5-flash")
    elif anth_key:
        from ..adapters.llm_anthropic import AnthropicAdapter
        llm = AnthropicAdapter(anth_key)
    else:
        return None

    return Services(
        youtube_search=yt.search,
        youtube_captions=yt.captions,
        youtube_video_alive=yt.video_alive,
        llm_extract=llm.extract,
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
        candidates = services.youtube_search(q_obj.to_dict())[:count]
        mode = f"real:{llm_choice or os.environ.get('COLLECTOR_LLM', 'gemini')}"
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

    per_video_status = []
    for payload in payloads:
        run_pipeline(
            payload, services, store, logger,
            fast_track=bool(q_obj.target_channel_id and payload.get("channel_id") == q_obj.target_channel_id),
        )
        per_video_status.append({
            "video_id": payload["video_id"],
            "title": payload["title"],
            "record_status": payload["record_status"],
            "confidence": payload.get("confidence"),
            "failure_reason_code": payload.get("failure_reason_code"),
            "rules_n": len(payload.get("rules") or []),
            "priority_score": payload.get("priority_score"),
        })

    summary = {
        "query": query,
        "mode": mode,
        "run_id": run_id,
        "candidates": len(candidates),
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
    ap.add_argument("--count", type=int, default=5, help="후보 영상 수")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--logs", default="logs")
    ap.add_argument("--llm", choices=["gemini", "anthropic"], default=None,
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
