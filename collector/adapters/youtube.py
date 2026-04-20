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
        q = query.get("topic", "")
        excludes = " ".join(f"-{w}" for w in query.get("exclude_terms", []))
        params = {
            "key": self.api_key,
            "part": "snippet",
            "type": "video",
            "maxResults": "25",
            "q": f"{q} {excludes}".strip(),
            "relevanceLanguage": "ko",
        }
        url = f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}"
        resp = self.http("GET", url)
        self._raise_for(resp)
        body = json.loads(resp["body"])
        return [
            {
                "video_id": it["id"]["videoId"],
                "channel_id": it["snippet"].get("channelId", ""),
                "title": it["snippet"].get("title", ""),
                "published_at": it["snippet"].get("publishedAt", ""),
            }
            for it in body.get("items", [])
            if it.get("id", {}).get("videoId")
        ]

    def captions(self, video_id: str) -> dict[str, Any]:
        # 1st path: timedtext (no auth, no quota cost).
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
        # 2nd path (fallback): yt-dlp subprocess if binary is on PATH.
        # Opt-in via COLLECTOR_YT_DLP=1 so CI doesn't spend time on it.
        import os, shutil
        if os.environ.get("COLLECTOR_YT_DLP") == "1" and shutil.which("yt-dlp"):
            try:
                return self._captions_via_ytdlp(video_id)
            except Exception:
                pass
        return {"source": "none", "text": ""}

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
