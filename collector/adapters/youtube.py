"""YouTube Data API v3 + timedtext captions adapter."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..services import MockError


def _default_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return {"status": resp.status, "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}


class YouTubeAdapter:
    SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
    VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
    TIMEDTEXT_URL = "https://video.google.com/timedtext"

    def __init__(self, api_key: str, http: Callable = _default_http):
        self.api_key = api_key
        self.http = http

    # ---------- Services interface ----------

    def search(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Search with pagination. query['max_results'] caps the result count
        (default 25). Fetches up to ceil(max_results/50) pages."""
        q = query.get("topic", "")
        excludes = " ".join(f"-{w}" for w in query.get("exclude_terms", []))
        max_results = int(query.get("max_results", 25))
        results: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(results) < max_results:
            batch = min(50, max_results - len(results))
            params = {
                "key": self.api_key,
                "part": "snippet",
                "type": "video",
                "maxResults": str(batch),
                "q": f"{q} {excludes}".strip(),
                "relevanceLanguage": "ko",
            }
            if page_token:
                params["pageToken"] = page_token
            url = f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}"
            resp = self.http("GET", url)
            self._raise_for(resp)
            body = json.loads(resp["body"])
            items = body.get("items", [])
            for it in items:
                if not it.get("id", {}).get("videoId"):
                    continue
                results.append({
                    "video_id": it["id"]["videoId"],
                    "channel_id": it["snippet"].get("channelId", ""),
                    "title": it["snippet"].get("title", ""),
                    "published_at": it["snippet"].get("publishedAt", ""),
                })
            page_token = body.get("nextPageToken")
            if not page_token or not items:
                break
        return results[:max_results]

    def captions(self, video_id: str) -> dict[str, Any]:
        """Multi-path captions fetch with per-path error capture.

        yt-dlp is primary because it handles YouTube's anti-scraping better
        (full User-Agent, cookies, Innertube). youtube-transcript-api is
        secondary (gets blocked with 403 more often). timedtext is last resort.
        """
        errors: list[str] = []

        # 1st: yt-dlp Python library
        res = self._captions_via_ytdlp_lib(video_id)
        if res["source"] != "none":
            return res
        if res.get("error"):
            errors.append(f"ytdlp:{res['error']}")

        # 2nd: youtube-transcript-api
        res = self._captions_via_yt_transcript(video_id)
        if res["source"] != "none":
            return res
        if res.get("error"):
            errors.append(f"ytapi:{res['error']}")

        # 3rd: timedtext direct
        for lang in ("ko", "en"):
            for kind in ("", "asr"):
                params = {"v": video_id, "lang": lang, "fmt": "srv3"}
                if kind:
                    params["kind"] = kind
                url = f"{self.TIMEDTEXT_URL}?{urllib.parse.urlencode(params)}"
                resp = self.http("GET", url)
                if resp["status"] == 200 and resp["body"].strip():
                    return {
                        "source": "asr" if kind == "asr" else "manual",
                        "text": resp["body"],
                    }
        errors.append("timedtext:all-empty")
        return {"source": "none", "text": "", "error": " | ".join(errors)}

    def _captions_via_yt_transcript(self, video_id: str) -> dict[str, Any]:
        """youtube-transcript-api 1.x — uses list()/fetch() (not list_transcripts)."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError:
            return {"source": "none", "text": "", "error": "not_installed"}
        api = YouTubeTranscriptApi()
        try:
            tr_list = api.list(video_id)
        except Exception as e:
            return {"source": "none", "text": "", "error": type(e).__name__}

        priority = [
            (lambda t: t.language_code == "ko" and not t.is_generated, "manual"),
            (lambda t: t.language_code == "ko" and t.is_generated,     "asr"),
            (lambda t: t.language_code == "en" and not t.is_generated, "manual"),
            (lambda t: t.language_code == "en" and t.is_generated,     "asr"),
        ]
        first_err = ""
        for pred, kind in priority:
            for tr in tr_list:
                try:
                    if pred(tr):
                        fetched = tr.fetch()
                        # v1.x: FetchedTranscript with .snippets; legacy: list of dicts
                        if hasattr(fetched, "snippets"):
                            text = " ".join(s.text for s in fetched.snippets)
                        else:
                            text = " ".join(s.get("text", "") for s in fetched)
                        if text.strip():
                            return {"source": kind, "text": text}
                except Exception as e:  # noqa: BLE001
                    first_err = first_err or type(e).__name__
                    continue
        return {"source": "none", "text": "", "error": first_err or "no_matching_lang"}

    def _captions_via_ytdlp_lib(self, video_id: str) -> dict[str, Any]:
        """Primary captions fetcher via yt_dlp Python library.

        Tries multiple YouTube player clients in order — YouTube blocks
        some clients based on IP (especially GitHub Actions). Optional
        cookies file via env COLLECTOR_YT_COOKIES_FILE (path to cookies.txt
        exported from a real browser).
        """
        import os
        try:
            from yt_dlp import YoutubeDL  # type: ignore
        except ImportError:
            return {"source": "none", "text": "", "error": "not_installed"}

        cookies = os.environ.get("COLLECTOR_YT_COOKIES_FILE", "")

        # Try multiple player clients — newer yt-dlp supports many fallbacks
        client_sets = [
            ["ios", "tv_embedded"],
            ["android"],
            ["web", "web_creator"],
            ["mweb"],
        ]
        last_err = ""
        for clients in client_sets:
            opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["ko", "en"],
                "quiet": True,
                "no_warnings": True,
                "extractor_args": {"youtube": {"player_client": clients}},
            }
            if cookies and os.path.exists(cookies):
                opts["cookiefile"] = cookies

            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(
                        f"https://www.youtube.com/watch?v={video_id}", download=False
                    )
            except Exception as e:  # noqa: BLE001
                last_err = f"{','.join(clients)}:{type(e).__name__}"
                continue

            # Look for any caption tracks
            for source_name, key in (("manual", "subtitles"), ("asr", "automatic_captions")):
                tracks_by_lang = (info or {}).get(key) or {}
                for lang in ("ko", "en"):
                    tracks = tracks_by_lang.get(lang) or []
                    for t in tracks:
                        url = t.get("url")
                        if not url:
                            continue
                        resp = self.http("GET", url)
                        if resp["status"] == 200 and resp["body"].strip():
                            return {"source": source_name, "text": resp["body"]}
            # info ok but no tracks — try next client
            last_err = f"{','.join(clients)}:no_tracks"
        return {"source": "none", "text": "", "error": last_err or "all_clients_failed"}

    def _captions_via_ytdlp(self, video_id: str) -> dict[str, Any]:
        """Last-resort caption extraction via yt-dlp. Requires binary on PATH."""
        import subprocess, tempfile, os
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            cmd = [
                "yt-dlp", "--skip-download",
                "--write-subs", "--write-auto-subs",
                "--sub-langs", "ko,en",
                "--sub-format", "srv3",
                "-o", str(Path(td) / "%(id)s.%(ext)s"),
                f"https://www.youtube.com/watch?v={video_id}",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                return {"source": "none", "text": ""}
            # Prefer manual over ASR
            for kind, suffix in (("manual", ".ko.srv3"), ("manual", ".en.srv3"),
                                 ("asr", ".ko.srv3"), ("asr", ".en.srv3")):
                for p in Path(td).glob(f"{video_id}*{suffix}"):
                    text = p.read_text(encoding="utf-8", errors="replace")
                    if text.strip():
                        return {"source": kind, "text": text}
        return {"source": "none", "text": ""}

    def video_alive(self, video_id: str) -> bool:
        params = {"key": self.api_key, "part": "id", "id": video_id}
        url = f"{self.VIDEOS_URL}?{urllib.parse.urlencode(params)}"
        resp = self.http("GET", url)
        if resp["status"] in (403, 410):
            return False
        if resp["status"] != 200:
            self._raise_for(resp)
        body = json.loads(resp["body"])
        return bool(body.get("items"))

    # ---------- helpers ----------

    @staticmethod
    def _raise_for(resp: dict) -> None:
        s = resp["status"]
        if s == 200:
            return
        if s == 429:
            raise MockError("HTTP_429", resp["body"][:200])
        if 500 <= s < 600:
            raise MockError("HTTP_5XX", f"{s}: {resp['body'][:200]}")
        if s in (403, 410):
            raise MockError("YT_VIDEO_REMOVED", resp["body"][:200])
        raise MockError(f"HTTP_{s}", resp["body"][:200])
