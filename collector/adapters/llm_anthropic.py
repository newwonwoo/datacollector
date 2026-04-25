"""Anthropic Claude adapter for summary/rules/tags extraction."""
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
    "도메인은 영상 내용에 맞춘다 — 미리 가정하지 말 것. "
    "반드시 다음 스키마만 출력한다: {\"summary\": str, \"rules\": [str], \"tags\": [str], \"notes_md\": str}. "
    "notes_md 는 영상 전체 맥락을 마크다운 (## 소제목, 목록, 인용) 으로 상세 정리. "
    "영상에 없는 도메인의 규칙을 끼워넣지 말 것. 출력은 유효한 JSON 한 개만, 다른 설명/줄글/코드펜스 금지."
)


# _default_http imported from ._llm_http (curl_cffi-backed when available).


class AnthropicAdapter:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        prompt_version: str = "extract_generic_v1",
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
            "max_tokens": 1024,
            "temperature": 0.2,
            "system": self._prompts["system"],
            "messages": [{"role": "user", "content": user}],
        }
        resp = self.http(
            "POST",
            self.URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
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
        text = "".join(block.get("text", "") for block in payload.get("content", []))
        try:
            out = json.loads(text)
        except json.JSONDecodeError as e:
            raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", f"{e}: {text[:200]}")
        if not isinstance(out, dict) or "summary" not in out or "rules" not in out:
            raise MockError("SEMANTIC_JSON_SCHEMA_FAIL", f"missing keys")
        out.setdefault("tags", [])
        out.setdefault("notes_md", "")
        from .llm_groq import _normalize_schema
        return _normalize_schema(out)
