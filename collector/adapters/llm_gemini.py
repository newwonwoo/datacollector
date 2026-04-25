"""Google Gemini adapter (generativelanguage.googleapis.com)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..prompt_loader import load_prompt
from ..services import MockError
from ._llm_http import llm_http as _default_http

# Built-in fallback (kept for backward compat). Adapters prefer external
# prompts/extract_saju_v1.md when present.
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


# _default_http imported from ._llm_http (curl_cffi-backed when available).


class GeminiAdapter:
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        prompt_version: str = "extract_generic_v2",
        http: Callable = _default_http,
    ):
        self.api_key = api_key
        self.model = model
        self.prompt_version = prompt_version
        self.http = http
        self._prompts = load_prompt(prompt_version)
        # Gemini Flash supports 1M-token context; we cap at 100k chars
        # per call mostly because round-trip latency goes up sharply on
        # huge bodies and free-tier RPM (15) doesn't reward larger calls.
        self.max_chars_per_request = 100_000

    def extract(self, transcript: str, attempt: int) -> dict[str, Any]:
        url = f"{self.BASE}/{self.model}:generateContent?key={urllib.parse.quote(self.api_key)}"
        if attempt == 0:
            user = transcript
        else:
            user = self._prompts["reprompt"].replace("{original_transcript}", transcript)
        body = {
            "system_instruction": {"parts": [{"text": self._prompts["system"]}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        resp = self.http(
            "POST",
            url,
            headers={"content-type": "application/json"},
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
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            out = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", str(e))
        if not isinstance(out, dict) or "summary" not in out or "rules" not in out:
            raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", "missing keys")
        out.setdefault("tags", [])
        out.setdefault("notes_md", "")
        # Coerce against type drift so downstream stages don't crash.
        from .llm_groq import _normalize_schema
        return _normalize_schema(out)
