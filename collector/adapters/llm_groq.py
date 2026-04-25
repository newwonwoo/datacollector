"""Groq adapter — OpenAI-compatible chat-completions endpoint.

Why this exists alongside Gemini/Anthropic: free-tier Gemini quota
(1500 RPD per project) gets exhausted within a single afternoon when
chunked transcripts emit many calls. Groq's free tier offers a separate
RPD pool for the same kind of work, with very fast inference. Adding it
as a peer LLM lets the user toggle (or eventually fall back) without
reshaping the pipeline.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from ..prompt_loader import load_prompt
from ..services import MockError
from ._llm_http import llm_http as _default_http


SYSTEM_PROMPT = (
    "너는 한국어 유튜브 자막에서 영상의 핵심 지식을 JSON으로 추출한다. "
    "도메인은 자막 내용을 따르고, 영상에 없는 외부 지식·다른 도메인의 규칙을 끼워넣지 말 것. "
    "본문은 영상의 일부 청크일 수 있다 — 이 청크에서 직접 확인되는 내용만 추출하고, "
    "맥락이 부족한 내용은 unclear 에 남겨라. "
    "출력 스키마: {\"summary\": str, \"content_type\": str, \"knowledge\": [str], \"rules\": [str], "
    "\"examples\": [str], \"claims\": [str], \"unclear\": [str], \"tags\": [str], "
    "\"llm_confidence\": str, \"notes_md\": str}. "
    "출력은 유효한 JSON 한 개만, 다른 설명/줄글/코드펜스 금지."
)


# _default_http is provided by ._llm_http (curl_cffi-backed when available).


class GroqAdapter:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        prompt_version: str = "extract_generic_v2",
        http: Callable = _default_http,
    ):
        self.api_key = api_key
        self.model = model
        self.prompt_version = prompt_version
        self.http = http
        self._prompts = load_prompt(prompt_version)

    def extract(self, transcript: str, attempt: int) -> dict[str, Any]:
        if attempt == 0:
            user = transcript
        else:
            user = self._prompts["reprompt"].replace("{original_transcript}", transcript)
        body = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._prompts["system"]},
                {"role": "user", "content": user},
            ],
        }
        resp = self.http(
            "POST",
            self.URL,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self.api_key}",
            },
            data=json.dumps(body).encode("utf-8"),
        )
        if resp["status"] == 429:
            raise MockError("HTTP_429", resp["body"][:200])
        if 500 <= resp["status"] < 600:
            raise MockError("HTTP_5XX", resp["body"][:200])
        if resp["status"] != 200:
            raise MockError(f"LLM_HTTP_{resp['status']}", resp["body"][:200])

        payload = json.loads(resp["body"])
        try:
            text = payload["choices"][0]["message"]["content"]
            out = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", str(e))
        if not isinstance(out, dict) or "summary" not in out or "rules" not in out:
            raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", "missing keys")
        out.setdefault("tags", [])
        out.setdefault("notes_md", "")
        # Defensive: LLMs occasionally return notes_md/summary as a list of
        # strings (chunked output) or summary as a list. Coerce to string
        # so downstream `.strip()` / length checks don't blow up.
        return _normalize_schema(out)


_CONTENT_TYPES = {"concept", "howto", "case", "opinion", "ad", "chat", "mixed"}
_LLM_CONFIDENCES = {"high", "medium", "low"}


def _normalize_schema(out: dict) -> dict:
    """Coerce JSON schema fields to expected primitive types and ensure
    every field present (with sensible empty defaults).

    LLMs occasionally emit list-of-strings where we asked for a string,
    or vice-versa. We normalise rather than reject — a malformed-but-
    recoverable answer is more useful than a fail.

    Schema (extract_generic_v2): summary, content_type, knowledge, rules,
    examples, claims, unclear, tags, llm_confidence, notes_md.
    """
    def _to_str(v) -> str:
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return "\n\n".join(str(x) for x in v if x is not None)
        if v is None:
            return ""
        return str(v)

    def _to_list_of_str(v) -> list:
        if isinstance(v, list):
            return [str(x) for x in v if x is not None and str(x).strip()]
        if isinstance(v, str):
            return [v] if v.strip() else []
        return []

    def _to_enum(v, allowed: set, fallback: str) -> str:
        s = _to_str(v).strip().lower()
        return s if s in allowed else fallback

    out["summary"]        = _to_str(out.get("summary", ""))
    out["content_type"]   = _to_enum(out.get("content_type"), _CONTENT_TYPES, "mixed")
    out["knowledge"]      = _to_list_of_str(out.get("knowledge", []))
    out["rules"]          = _to_list_of_str(out.get("rules", []))
    out["examples"]       = _to_list_of_str(out.get("examples", []))
    out["claims"]         = _to_list_of_str(out.get("claims", []))
    out["unclear"]        = _to_list_of_str(out.get("unclear", []))
    out["tags"]           = _to_list_of_str(out.get("tags", []))
    out["llm_confidence"] = _to_enum(out.get("llm_confidence"), _LLM_CONFIDENCES, "medium")
    out["notes_md"]       = _to_str(out.get("notes_md", ""))
    return out
