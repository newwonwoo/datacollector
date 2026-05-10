"""`collector apitest` — standalone API health probe.

Runs each external integration in isolation and reports:
  - YouTube Data API v3 (search.list, videos.list)
  - youtube-transcript-api library
  - yt-dlp library (multiple player_client variants)
  - timedtext direct HTTP
  - Gemini / Anthropic LLM

Output:
  - Human-readable table to stdout
  - docs/apitest.json for dashboard consumption (--out)

Exit code:
  0 — at least one captions path works
  1 — no captions path works (pipeline would fail)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

# Known public videos likely to have captions (for probing)
PROBE_VIDEOS = [
    "jNQXAC9IVRw",  # "Me at the zoo" — first YouTube video ever
    "dQw4w9WgXcQ",  # Rick Astley — famously captioned
]


@dataclass
class TestResult:
    name: str
    ok: bool
    detail: str = ""
    duration_ms: int = 0
    skip: bool = False


def _timed(fn: Callable[[], tuple[bool, str]]) -> TestResult:
    """Execute fn, capture duration + result."""
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {str(e)[:200]}"
    return TestResult(name="", ok=ok, detail=detail, duration_ms=int((time.time() - t0) * 1000))


# ---------- individual tests ----------

def test_youtube_data_api() -> TestResult:
    """Does the API key work for search?"""
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        r = TestResult(name="YouTube Data API v3", ok=False, skip=True,
                       detail="YOUTUBE_API_KEY not set")
        return r
    import urllib.parse, urllib.request, urllib.error
    def call():
        url = ("https://www.googleapis.com/youtube/v3/search?"
               + urllib.parse.urlencode({
                   "key": key, "part": "snippet", "type": "video",
                   "maxResults": "3", "q": "test",
               }))
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = json.loads(resp.read().decode())
            items = body.get("items", [])
            if items:
                return True, f"search returned {len(items)} items"
            return False, "search returned 0 items (key ok, maybe query empty)"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            return False, f"HTTP {e.code}: {body}"

    r = _timed(call)
    r.name = "YouTube Data API v3"
    return r


def test_transcript_api() -> TestResult:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        r = TestResult(name="youtube-transcript-api", ok=False, skip=True,
                       detail="not installed (pip install youtube-transcript-api)")
        return r

    def call():
        api = YouTubeTranscriptApi()
        for vid in PROBE_VIDEOS:
            try:
                tr_list = list(api.list(vid))
                if tr_list:
                    return True, f"{vid}: {len(tr_list)} transcript(s) available"
            except Exception as e:
                err_detail = f"{type(e).__name__}: {str(e)[:100]}"
                # keep trying next video
                continue
        return False, err_detail if 'err_detail' in locals() else "no probe succeeded"

    r = _timed(call)
    r.name = "youtube-transcript-api"
    return r


def test_ytdlp(player_clients: list[str]) -> TestResult:
    label = f"yt-dlp ({','.join(player_clients)})"
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return TestResult(name=label, ok=False, skip=True, detail="not installed")

    def call():
        opts = {
            "skip_download": True, "writesubtitles": True, "writeautomaticsub": True,
            "subtitleslangs": ["ko", "en"],
            "quiet": True, "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": player_clients}},
        }
        cookies = os.environ.get("COLLECTOR_YT_COOKIES_FILE", "")
        if cookies and os.path.exists(cookies):
            opts["cookiefile"] = cookies
        for vid in PROBE_VIDEOS:
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(
                        f"https://www.youtube.com/watch?v={vid}", download=False
                    )
                auto = (info or {}).get("automatic_captions") or {}
                manual = (info or {}).get("subtitles") or {}
                if auto or manual:
                    return True, f"{vid}: auto={len(auto)} manual={len(manual)}"
            except Exception as e:
                err_detail = f"{type(e).__name__}: {str(e)[:120]}"
                continue
        return False, err_detail if 'err_detail' in locals() else "all videos empty"

    r = _timed(call)
    r.name = label
    return r


def test_timedtext() -> TestResult:
    import urllib.parse, urllib.request, urllib.error

    def call():
        for vid in PROBE_VIDEOS:
            url = ("https://video.google.com/timedtext?"
                   + urllib.parse.urlencode({"v": vid, "lang": "en", "kind": "asr", "fmt": "srv3"}))
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    body = resp.read().decode("utf-8", "replace")
                if body.strip():
                    return True, f"{vid}: {len(body)} bytes"
            except urllib.error.HTTPError as e:
                err_detail = f"HTTP {e.code}"
                continue
            except Exception as e:
                err_detail = f"{type(e).__name__}"
                continue
        return False, err_detail if 'err_detail' in locals() else "all empty"

    r = _timed(call)
    r.name = "timedtext direct"
    return r


def test_gemini() -> TestResult:
    key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return TestResult(name="Gemini 1.5 Flash", ok=False, skip=True,
                          detail="GOOGLE_API_KEY not set")
    import urllib.parse, urllib.request, urllib.error

    def call():
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-2.5-flash:generateContent?key={urllib.parse.quote(key)}")
        req = urllib.request.Request(
            url, method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({
                "contents": [{"role": "user", "parts": [{"text": "한 글자만 답: Y"}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 4},
            }).encode(),
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
            cand = body.get("candidates", [{}])[0]
            text = cand.get("content", {}).get("parts", [{}])[0].get("text", "")
            return True, f"response: {text[:20]!r}"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            return False, f"HTTP {e.code}: {body}"

    r = _timed(call)
    r.name = "Gemini 1.5 Flash"
    return r


def test_anthropic() -> TestResult:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return TestResult(name="Anthropic Claude", ok=False, skip=True,
                          detail="ANTHROPIC_API_KEY not set")
    import urllib.request, urllib.error

    def call():
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            method="POST",
            headers={
                "x-api-key": key, "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=json.dumps({
                "model": "claude-haiku-4-5-20251001", "max_tokens": 4,
                "messages": [{"role": "user", "content": "한 글자만: Y"}],
            }).encode(),
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
            text = body.get("content", [{}])[0].get("text", "")
            return True, f"response: {text[:20]!r}"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:200]
            return False, f"HTTP {e.code}: {body}"

    r = _timed(call)
    r.name = "Anthropic Claude"
    return r


# ---------- orchestrator ----------

def run_all() -> dict:
    suite = [
        test_youtube_data_api,
        test_transcript_api,
        lambda: test_ytdlp(["ios", "tv_embedded"]),
        lambda: test_ytdlp(["android"]),
        lambda: test_ytdlp(["web", "web_creator"]),
        lambda: test_ytdlp(["mweb"]),
        test_timedtext,
        test_gemini,
        test_anthropic,
    ]
    results: list[TestResult] = []
    for fn in suite:
        results.append(fn())

    # Captions summary: at least one captions-fetching path must pass
    captions_tests = [r for r in results if r.name.startswith("youtube-transcript") or r.name.startswith("yt-dlp") or r.name == "timedtext direct"]
    captions_ok = any(r.ok for r in captions_tests if not r.skip)

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [asdict(r) for r in results],
        "captions_ok": captions_ok,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.ok),
            "failed": sum(1 for r in results if not r.ok and not r.skip),
            "skipped": sum(1 for r in results if r.skip),
        },
    }


def print_table(report: dict) -> None:
    print("=== Collector API Health Check ===")
    for r in report["results"]:
        if r["skip"]:
            mark = "[-]"
        elif r["ok"]:
            mark = "[✓]"
        else:
            mark = "[✗]"
        name = r["name"].ljust(30)
        dur  = f"({r['duration_ms']}ms)".ljust(10)
        print(f"{mark} {name} {dur} — {r['detail']}")
    s = report["summary"]
    print("-" * 60)
    print(f"Summary: {s['passed']}/{s['total']} passed, "
          f"{s['failed']} failed, {s['skipped']} skipped")
    if report["captions_ok"]:
        print("Captions: ✓ at least one fetcher works — pipeline CAN run")
    else:
        print("Captions: ✗ NO fetcher works — pipeline will fail at COLLECT")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector apitest")
    ap.add_argument("--out", default="docs/apitest.json",
                    help="write JSON report (default: docs/apitest.json)")
    ap.add_argument("--no-file", action="store_true",
                    help="stdout only, don't write file")
    ap.add_argument("--quiet", action="store_true", help="no stdout table")
    args = ap.parse_args(argv)

    report = run_all()
    if not args.quiet:
        print_table(report)
    if not args.no_file:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"\n→ wrote {out}")

    return 0 if report["captions_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
