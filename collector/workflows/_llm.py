"""Shared helpers for workflow primitives.

The workflow modules (brainstorm/synthesize/report) share a tiny LLM
abstraction so they can all be retargeted to a different cheap model
in one place. Defaults to Gemini 2.5 Flash since the user already has
that key for the pipeline; falls back to Groq llama-3.1-8b-instant
when only a Groq key is present.

These are *workflow* calls (planning + summarisation), separate from
the *pipeline* extract calls in stages.stage_extract — keeping them
on the cheap tier guarantees a full brainstorm→research→synthesize
session stays well under any free-tier daily quota.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any


def call_workflow_llm(prompt: str, *, expect_json: bool = True, max_tokens_hint: int = 4000) -> Any:
    """Single LLM round-trip for workflow planning steps, with rollover.

    Tries every configured cheap-LLM key in order (Gemini → Groq → Anthropic).
    A 503/429/5xx/auth error on one provider falls through to the next so a
    transient outage doesn't kill the whole brainstorm/synthesize step.

    Returns parsed JSON (when `expect_json`) or raw text. Raises RuntimeError
    when no key is available, every provider failed, or the final output
    fails to parse and `expect_json` is True.
    """
    goog_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")
    anth_key = os.environ.get("ANTHROPIC_API_KEY")

    providers: list[tuple[str, Any]] = []
    if goog_key:
        providers.append(("gemini", lambda: _call_gemini(goog_key, prompt, expect_json=expect_json)))
    if groq_key:
        providers.append(("groq", lambda: _call_groq(groq_key, prompt, expect_json=expect_json)))
    if anth_key:
        providers.append(("anthropic", lambda: _call_anthropic(anth_key, prompt, expect_json=expect_json)))

    if not providers:
        raise RuntimeError(
            "no workflow LLM key set (GOOGLE_API_KEY / GROQ_API_KEY / ANTHROPIC_API_KEY)"
        )

    last_err: Exception | None = None
    text = None
    for name, call in providers:
        try:
            text = call()
            break
        except RuntimeError as e:
            last_err = e
            sys.stderr.write(f"[workflow_llm] {name} failed: {e}\n[workflow_llm] falling through to next provider\n")
            continue
    if text is None:
        raise RuntimeError(f"all workflow LLM providers failed; last: {last_err}")

    if not expect_json:
        return text
    try:
        return _parse_json_loose(text)
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"workflow LLM returned non-JSON: {e}: {text[:200]}")


def _call_gemini(key: str, prompt: str, *, expect_json: bool) -> str:
    """Cheapest free-tier path. Uses the existing Gemini adapter's HTTP."""
    from ..adapters._llm_http import llm_http
    import urllib.parse
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:generateContent?key=" + urllib.parse.quote(key)
    )
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3},
    }
    if expect_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    resp = llm_http(
        "POST", url,
        headers={"content-type": "application/json"},
        data=json.dumps(body).encode("utf-8"),
    )
    if resp["status"] != 200:
        raise RuntimeError(f"gemini HTTP {resp['status']}: {resp['body'][:200]}")
    payload = json.loads(resp["body"])
    return payload["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(key: str, prompt: str, *, expect_json: bool) -> str:
    from ..adapters._llm_http import llm_http
    body: dict[str, Any] = {
        "model": "llama-3.1-8b-instant",  # cheapest, plenty for planning text
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    if expect_json:
        body["response_format"] = {"type": "json_object"}
    resp = llm_http(
        "POST", "https://api.groq.com/openai/v1/chat/completions",
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
        data=json.dumps(body).encode("utf-8"),
    )
    if resp["status"] != 200:
        raise RuntimeError(f"groq HTTP {resp['status']}: {resp['body'][:200]}")
    payload = json.loads(resp["body"])
    return payload["choices"][0]["message"]["content"]


def _call_anthropic(key: str, prompt: str, *, expect_json: bool) -> str:
    from ..adapters._llm_http import llm_http
    body: dict[str, Any] = {
        "model": "claude-haiku-4-5-20251001",  # cheapest, fast
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }
    resp = llm_http(
        "POST", "https://api.anthropic.com/v1/messages",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        data=json.dumps(body).encode("utf-8"),
    )
    if resp["status"] != 200:
        raise RuntimeError(f"anthropic HTTP {resp['status']}: {resp['body'][:200]}")
    payload = json.loads(resp["body"])
    return payload["content"][0]["text"]


def _parse_json_loose(text: str) -> Any:
    """LLMs sometimes wrap JSON in code fences. Strip those before parsing."""
    s = text.strip()
    if s.startswith("```"):
        # ```json ... ``` or ``` ... ```
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return json.loads(s.strip())
