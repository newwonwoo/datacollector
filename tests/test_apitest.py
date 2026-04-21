"""Unit tests for apitest CLI — mock external calls."""
from __future__ import annotations

import json
import sys
from types import ModuleType

import pytest

from collector.cli import apitest_cli


def test_timed_catches_exception():
    def boom():
        raise RuntimeError("kapow")
    r = apitest_cli._timed(boom)
    assert r.ok is False
    assert "RuntimeError" in r.detail
    assert "kapow" in r.detail


def test_youtube_data_api_skipped_without_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    r = apitest_cli.test_youtube_data_api()
    assert r.skip is True
    assert "not set" in r.detail.lower()


def test_transcript_api_skipped_if_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", None)
    r = apitest_cli.test_transcript_api()
    assert r.skip is True
    assert "not installed" in r.detail.lower()


def test_ytdlp_skipped_if_not_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    r = apitest_cli.test_ytdlp(["ios"])
    assert r.skip is True


def test_ytdlp_success_path(monkeypatch):
    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, url, download=False):
            return {"automatic_captions": {"ko": [{"url": "x"}]}, "subtitles": {}}
    fake = ModuleType("yt_dlp"); fake.YoutubeDL = FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    r = apitest_cli.test_ytdlp(["ios"])
    assert r.ok is True
    assert "auto=1" in r.detail


def test_gemini_skipped_without_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = apitest_cli.test_gemini()
    assert r.skip is True


def test_anthropic_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = apitest_cli.test_anthropic()
    assert r.skip is True


def test_run_all_returns_report_shape(monkeypatch):
    # All tests skip (no env vars + no libs available)
    for var in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", None)
    monkeypatch.setitem(sys.modules, "yt_dlp", None)

    # Block timedtext too (urllib raises in sandbox) — still expected structure
    report = apitest_cli.run_all()
    assert "timestamp" in report
    assert "results" in report
    assert isinstance(report["results"], list)
    assert "captions_ok" in report
    assert "summary" in report
    s = report["summary"]
    assert s["total"] == len(report["results"])
    assert s["passed"] + s["failed"] + s["skipped"] == s["total"]


def test_main_writes_file(tmp_path, monkeypatch, capsys):
    # Make all tests quickly skip to avoid network
    for var in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", None)
    monkeypatch.setitem(sys.modules, "yt_dlp", None)

    out = tmp_path / "apitest.json"
    rc = apitest_cli.main(["--out", str(out), "--quiet"])
    # No captions path works → exit 1 by contract
    assert rc == 1
    assert out.exists()
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["captions_ok"] is False


def test_main_quiet_still_runs(tmp_path, monkeypatch, capsys):
    for var in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", None)
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    rc = apitest_cli.main(["--no-file", "--quiet"])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert out == ""  # quiet
