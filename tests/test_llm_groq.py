"""GroqAdapter — OpenAI-compatible /chat/completions wrapper."""
from __future__ import annotations

import json

import pytest

from collector.adapters.llm_groq import GroqAdapter
from collector.services import MockError


def _good_resp(payload: dict) -> dict:
    return {
        "status": 200,
        "body": json.dumps({
            "choices": [{"message": {"content": json.dumps(payload)}}]
        }),
    }


def test_groq_extract_success_with_4_field_schema():
    captured: dict = {}

    def fake_http(method, url, *, headers=None, data=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(data.decode())
        return _good_resp({
            "summary": "한 줄 요약",
            "rules": ["규칙1", "규칙2"],
            "tags": ["tag1"],
            "notes_md": "## 핵심\n- A\n- B",
        })

    adapter = GroqAdapter("gsk_FAKE_KEY", http=fake_http)
    out = adapter.extract("자막 본문", attempt=0)

    assert captured["url"].endswith("/openai/v1/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer gsk_FAKE_KEY"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    # System + user messages in OpenAI format
    msgs = captured["body"]["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "자막 본문"
    # Schema preserved through the adapter
    assert out["summary"] == "한 줄 요약"
    assert out["rules"] == ["규칙1", "규칙2"]
    assert out["tags"] == ["tag1"]
    assert out["notes_md"].startswith("## 핵심")


def test_groq_429_raises_http_429():
    def fake_http(*a, **kw):
        return {"status": 429, "body": '{"error":{"message":"rate"}}'}
    a = GroqAdapter("k", http=fake_http)
    with pytest.raises(MockError) as ei:
        a.extract("t", attempt=0)
    assert ei.value.code == "HTTP_429"


def test_groq_5xx_raises_http_5xx():
    def fake_http(*a, **kw):
        return {"status": 503, "body": "down"}
    a = GroqAdapter("k", http=fake_http)
    with pytest.raises(MockError) as ei:
        a.extract("t", attempt=0)
    assert ei.value.code == "HTTP_5XX"


def test_groq_4xx_raises_specific_code():
    def fake_http(*a, **kw):
        return {"status": 401, "body": "unauthorized"}
    a = GroqAdapter("k", http=fake_http)
    with pytest.raises(MockError) as ei:
        a.extract("t", attempt=0)
    assert ei.value.code == "LLM_HTTP_401"


def test_groq_schema_fail_when_content_not_json():
    def fake_http(*a, **kw):
        return {
            "status": 200,
            "body": json.dumps({"choices": [{"message": {"content": "this is not json"}}]}),
        }
    a = GroqAdapter("k", http=fake_http)
    with pytest.raises(MockError) as ei:
        a.extract("t", attempt=0)
    assert ei.value.code == "SEMANTIC_JSON_SCHEMA_FAIL"


def test_groq_schema_fail_when_required_keys_missing():
    def fake_http(*a, **kw):
        return _good_resp({"summary": "ok"})  # missing 'rules'
    a = GroqAdapter("k", http=fake_http)
    with pytest.raises(MockError) as ei:
        a.extract("t", attempt=0)
    assert ei.value.code == "SEMANTIC_JSON_SCHEMA_FAIL"


def test_groq_setdefaults_optional_fields():
    """If the LLM omits tags/notes_md but schema is otherwise valid, the
    adapter fills empty defaults so downstream stages don't KeyError."""
    def fake_http(*a, **kw):
        return _good_resp({"summary": "s", "rules": ["r"]})
    a = GroqAdapter("k", http=fake_http)
    out = a.extract("t", attempt=0)
    assert out["tags"] == []
    assert out["notes_md"] == ""


def test_groq_attempt_1_uses_reprompt(monkeypatch):
    seen_user: dict = {}

    def fake_http(method, url, *, headers=None, data=None):
        body = json.loads(data.decode())
        seen_user["content"] = body["messages"][1]["content"]
        return _good_resp({"summary": "ok", "rules": [], "tags": [], "notes_md": ""})

    a = GroqAdapter("k", http=fake_http)
    a.extract("ORIGINAL", attempt=1)
    # Reprompt template wraps the original transcript
    assert "ORIGINAL" in seen_user["content"]
    assert seen_user["content"] != "ORIGINAL"  # reprompt prefix added


def _adapter_names(services):
    return [a.__class__.__name__ for a in services.llm_extract.adapters]


def test_real_services_picks_groq_when_only_groq_set(monkeypatch):
    """Auto-pick fallback chain: YouTube + only GROQ_API_KEY → use Groq."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt_fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    from collector.cli.run import _real_services_or_none
    services = _real_services_or_none()
    assert services is not None
    assert _adapter_names(services) == ["GroqAdapter"]


def test_real_services_explicit_llm_choice_groq(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt_fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza_fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from collector.cli.run import _real_services_or_none
    services = _real_services_or_none(llm_choice="groq")
    # Explicit choice → exactly one adapter, no fallback.
    assert _adapter_names(services) == ["GroqAdapter"]

    services2 = _real_services_or_none(llm_choice="gemini")
    assert _adapter_names(services2) == ["GeminiAdapter"]


def test_real_services_auto_chain_orders_gemini_then_groq(monkeypatch):
    """Without --llm, auto-chain so a 429 on Gemini falls through to Groq."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt_fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza_fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COLLECTOR_LLM", raising=False)

    from collector.cli.run import _real_services_or_none
    services = _real_services_or_none()
    assert _adapter_names(services) == ["GeminiAdapter", "GroqAdapter"]


def test_llm_chain_falls_through_on_429(monkeypatch):
    """A 429 from the first adapter must hand the call to the second."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt_fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza_fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from collector.cli.run import _real_services_or_none
    services = _real_services_or_none()
    chain = services.llm_extract.adapters

    def gemini_raises(*a, **kw):
        raise MockError("HTTP_429", "quota")

    expected = {"summary": "ok", "rules": [], "tags": [], "notes_md": ""}

    def groq_returns(*a, **kw):
        return expected

    chain[0].extract = gemini_raises
    chain[1].extract = groq_returns

    out = services.llm_extract("transcript", 0)
    assert out == expected


def test_llm_chain_does_not_fallback_on_schema_fail(monkeypatch):
    """Schema/semantic errors stay on the same adapter — falling through
    on those would mask a malformed prompt or buggy adapter response."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt_fake")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza_fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from collector.cli.run import _real_services_or_none
    services = _real_services_or_none()
    chain = services.llm_extract.adapters

    def gemini_raises(*a, **kw):
        raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", "broken")

    groq_called = []

    def groq_returns(*a, **kw):
        groq_called.append(True)
        return {"summary": "ok", "rules": [], "tags": [], "notes_md": ""}

    chain[0].extract = gemini_raises
    chain[1].extract = groq_returns

    with pytest.raises(MockError) as ei:
        services.llm_extract("transcript", 0)
    assert ei.value.code == "SEMANTIC_JSON_SCHEMA_FAIL"
    assert groq_called == []  # never reached
