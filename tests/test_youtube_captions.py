"""Tests for YouTubeAdapter.captions() — multi-path fallback."""
from __future__ import annotations

import sys
from types import ModuleType

import pytest

from collector.adapters.youtube import YouTubeAdapter


def test_captions_primary_yt_transcript_api(monkeypatch):
    """When youtube_transcript_api is available and returns manual Korean,
    use it immediately."""
    class FakeTranscript:
        language_code = "ko"
        is_generated = False
        def fetch(self):
            return [{"text": "안녕"}, {"text": "세계"}]
    class FakeApi:
        def list(self, vid):
            return [FakeTranscript()]

    fake_mod = ModuleType("youtube_transcript_api")
    fake_mod.YouTubeTranscriptApi = FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_mod)

    # http should NOT be called
    def fake_http(*a, **kw):
        pytest.fail("http should not be called when transcript-api succeeds")
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.captions("vid_x")
    assert out["source"] == "manual"
    assert "안녕" in out["text"]


def test_captions_transcript_api_prefers_manual_over_asr(monkeypatch):
    class Manual:
        language_code = "ko"; is_generated = False
        def fetch(self): return [{"text": "수동"}]
    class Auto:
        language_code = "ko"; is_generated = True
        def fetch(self): return [{"text": "자동"}]
    class FakeApi:
        def list(self, vid):
            return [Auto(), Manual()]
    fake_mod = ModuleType("youtube_transcript_api")
    fake_mod.YouTubeTranscriptApi = FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_mod)

    yt = YouTubeAdapter("KEY", http=lambda *a, **kw: {"status": 404, "body": ""})
    out = yt.captions("vid")
    assert out["source"] == "manual"
    assert "수동" in out["text"]


def test_captions_falls_back_when_all_paths_fail(monkeypatch):
    # Break youtube_transcript_api
    class BrokenApi:
        def list(self, vid):
            raise RuntimeError("broken")
    fake_mod = ModuleType("youtube_transcript_api")
    fake_mod.YouTubeTranscriptApi = BrokenApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_mod)

    # yt_dlp not installed (simulate ImportError)
    monkeypatch.setitem(sys.modules, "yt_dlp", None)

    # timedtext always 404
    yt = YouTubeAdapter("KEY", http=lambda *a, **kw: {"status": 404, "body": ""})
    out = yt.captions("vid")
    assert out["source"] == "none"
    assert out["text"] == ""
    assert "error" in out  # details are now captured for UX


def test_captions_ytdlp_lib_fallback(monkeypatch):
    # transcript-api absent
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", None)

    # Fake yt_dlp returning a manual Korean sub URL
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=False):
            return {
                "subtitles": {"ko": [{"url": "https://subs/ko.vtt"}]},
                "automatic_captions": {},
            }
    fake = ModuleType("yt_dlp")
    fake.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)

    def fake_http(method, url, **kw):
        return {"status": 200, "body": "WEBVTT\n\n00:00\n한글자막"}
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.captions("vid")
    assert out["source"] == "manual"
    assert "한글자막" in out["text"]


def test_captions_to_plain_text_strips_json3():
    """Raw json3 from YouTube must be flattened into clean text — otherwise
    the LLM gets multi-KB of JSON structure and burns Gemini quota fast (G-16)."""
    from collector.adapters.youtube import _captions_to_plain_text

    json3 = (
        '{"wireMagic":"pb3","events":['
        '{"segs":[{"utf8":"안녕"},{"utf8":" 세계"}]},'
        '{"segs":[{"utf8":"\\n"}]},'
        '{"segs":[{"utf8":"두 번째"},{"utf8":" 줄"}]}'
        "]}"
    )
    out = _captions_to_plain_text(json3, "json3")
    assert "안녕 세계" in out
    assert "두 번째 줄" in out
    assert "wireMagic" not in out
    assert "segs" not in out


def test_captions_to_plain_text_strips_vtt():
    from collector.adapters.youtube import _captions_to_plain_text
    vtt = (
        "WEBVTT\n\n"
        "1\n00:00:01.000 --> 00:00:02.000\n첫 줄\n\n"
        "2\n00:00:02.500 --> 00:00:03.500\n<c>두 번째</c>\n"
    )
    out = _captions_to_plain_text(vtt, "vtt")
    assert "첫 줄" in out
    assert "두 번째" in out
    assert "WEBVTT" not in out
    assert "-->" not in out


def test_captions_ytdlp_lib_prefers_json3_and_returns_plain_text(monkeypatch):
    """If multiple track formats are returned, pick json3 and decode it."""
    import sys
    from types import ModuleType

    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=False):
            return {
                "subtitles": {},
                "automatic_captions": {"ko": [
                    {"ext": "vtt",   "url": "https://subs/ko.vtt"},
                    {"ext": "json3", "url": "https://subs/ko.json3"},
                ]},
            }
    fake = ModuleType("yt_dlp")
    fake.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)

    json3_body = (
        '{"events":[{"segs":[{"utf8":"clean"},{"utf8":" text"}]}]}'
    )

    def fake_http(method, url, **kw):
        # The adapter should pick json3 first.
        assert url.endswith(".json3"), f"adapter fetched {url}, expected json3 first"
        return {"status": 200, "body": json3_body}

    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.captions("vid")
    assert out["source"] == "asr"
    assert out["text"] == "clean text"
