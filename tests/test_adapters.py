"""Contract tests for real service adapters. All HTTP is injected as fakes."""
from __future__ import annotations

import json

import pytest

from collector.adapters.youtube import YouTubeAdapter
from collector.adapters.llm_anthropic import AnthropicAdapter
from collector.adapters.llm_gemini import GeminiAdapter
from collector.adapters.git_sync import GitSyncAdapter
from collector.services import MockError


# ----- YouTube -----

def test_youtube_search_parses_items():
    def fake_http(method, url, **kw):
        assert method == "GET" and "youtube/v3/search" in url
        return {"status": 200, "body": json.dumps({"items": [
            {"id": {"videoId": "abc"}, "snippet": {"channelId": "ch", "title": "t", "publishedAt": "2026-04-19T00:00:00Z"}},
            {"id": {}, "snippet": {}},  # filtered out
        ]})}
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.search({"topic": "단타", "exclude_terms": ["코인"]})
    assert [o["video_id"] for o in out] == ["abc"]


def test_youtube_captions_falls_back_to_asr():
    calls = []
    def fake_http(method, url, **kw):
        calls.append(url)
        # Fail manual, succeed asr
        if "kind=asr" in url:
            return {"status": 200, "body": "<transcript>hi</transcript>"}
        return {"status": 404, "body": ""}
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.captions("vid")
    assert out["source"] == "asr"
    assert "transcript" in out["text"]


def test_youtube_429_raises_mock_error():
    def fake_http(*a, **kw):
        return {"status": 429, "body": "rate"}
    yt = YouTubeAdapter("KEY", http=fake_http)
    with pytest.raises(MockError) as ei:
        yt.search({"topic": "x"})
    assert ei.value.code == "HTTP_429"


def test_youtube_video_alive_returns_false_on_410():
    def fake_http(*a, **kw):
        return {"status": 410, "body": ""}
    yt = YouTubeAdapter("KEY", http=fake_http)
    assert yt.video_alive("vid") is False


# ----- Anthropic -----

def test_anthropic_extract_success():
    captured = {}
    def fake_http(method, url, *, headers=None, data=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(data.decode())
        response = {
            "content": [{"type": "text", "text": json.dumps({"summary": "요약", "rules": ["r1"], "tags": ["t"]})}]
        }
        return {"status": 200, "body": json.dumps(response)}
    a = AnthropicAdapter("KEY", http=fake_http)
    out = a.extract("자막", attempt=0)
    assert out["summary"] == "요약" and out["rules"] == ["r1"]
    assert captured["headers"]["x-api-key"] == "KEY"


def test_anthropic_reprompt_marks_attempt():
    captured = {"msgs": []}
    def fake_http(method, url, *, headers=None, data=None):
        body = json.loads(data.decode())
        captured["msgs"].append(body["messages"][0]["content"])
        out = {"content": [{"type": "text", "text": json.dumps({"summary": "s", "rules": ["r"], "tags": []})}]}
        return {"status": 200, "body": json.dumps(out)}
    a = AnthropicAdapter("KEY", http=fake_http)
    a.extract("원문", attempt=1)
    assert "직전 응답" in captured["msgs"][0]


def test_anthropic_bad_json_raises():
    def fake_http(*a, **kw):
        return {"status": 200, "body": json.dumps({"content": [{"text": "not json"}]})}
    a = AnthropicAdapter("KEY", http=fake_http)
    with pytest.raises(MockError) as ei:
        a.extract("x", 0)
    assert ei.value.code == "SEMANTIC_JSON_SCHEMA_FAIL"


# ----- Gemini -----

def test_gemini_extract_success():
    def fake_http(method, url, *, headers=None, data=None):
        assert "generateContent" in url
        response = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({"summary": "요약", "rules": ["r"], "tags": ["t"]})}]}}]
        }
        return {"status": 200, "body": json.dumps(response)}
    g = GeminiAdapter("KEY", http=fake_http)
    out = g.extract("자막", 0)
    assert out["summary"] == "요약"


def test_gemini_500_maps_http_5xx():
    def fake_http(*a, **kw):
        return {"status": 503, "body": "unavailable"}
    g = GeminiAdapter("KEY", http=fake_http)
    with pytest.raises(MockError) as ei:
        g.extract("x", 0)
    assert ei.value.code == "HTTP_5XX"


# ----- Git Sync -----

def test_git_sync_runs_expected_commands(tmp_path):
    commands: list[list[str]] = []
    def fake_run(cmd, *, cwd=None, env=None, check=True):
        commands.append(cmd)
        # Make clone create a .git dir so subsequent branches are taken correctly.
        if cmd[:2] == ["git", "clone"]:
            target = cmd[-1]
            from pathlib import Path
            Path(target).mkdir(parents=True, exist_ok=True)
            (Path(target) / ".git").mkdir(parents=True, exist_ok=True)
        return {"code": 0, "stdout": "", "stderr": ""}

    def fake_signer(msg, pem):
        return b"sig"

    g = GitSyncAdapter(
        app_id="1", installation_id="2", private_key_pem="PEM",
        repo="owner/repo", branch="data/main", work_root=tmp_path,
        run=fake_run, signer=fake_signer,
    )
    # Bypass installation token http
    g._installation_token = lambda: "ghs_token"
    payload = {
        "source_key": "youtube:VID", "video_id": "VID", "title": "t",
        "summary": "s", "rules": ["r"], "tags": ["t"],
        "payload_version": 1, "confidence": "confirmed",
        "schema_version": "10.0.0", "collected_at": "", "published_at": "",
    }
    g.sync(payload)
    verbs = [c[3] if len(c) > 3 else c[0] for c in commands]
    # Expect commit and push present
    assert any(c[:3] == ["git", "clone", "--branch"] for c in commands)
    assert any("commit" in c for c in commands)
    assert any("push" in c for c in commands)


def test_git_sync_conflict_raises():
    def fake_run(cmd, *, cwd=None, env=None, check=True):
        if cmd[:2] == ["git", "clone"]:
            from pathlib import Path
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            (Path(cmd[-1]) / ".git").mkdir(parents=True, exist_ok=True)
            return {"code": 0, "stdout": "", "stderr": ""}
        if "push" in cmd:
            raise MockError("GIT_CONFLICT", "rejected")
        return {"code": 0, "stdout": "", "stderr": ""}

    g = GitSyncAdapter(
        app_id="1", installation_id="2", private_key_pem="PEM",
        repo="owner/repo", run=fake_run, signer=lambda m, p: b"s",
        work_root="/tmp/test-git-conflict",
    )
    g._installation_token = lambda: "ghs_token"
    with pytest.raises(MockError) as ei:
        g.sync({
            "source_key": "youtube:VID", "video_id": "VID", "title": "", "summary": "",
            "rules": [], "tags": [], "payload_version": 1, "confidence": "",
            "schema_version": "10.0.0", "collected_at": "", "published_at": "",
        })
    assert ei.value.code == "GIT_CONFLICT"
