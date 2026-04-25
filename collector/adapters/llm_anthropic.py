"""Anthropic Claude adapter for summary/rules/tags extraction."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from ..prompt_loader import load_prompt
from ..services import MockError


SYSTEM_PROMPT = (
    "너는 한국어 유튜브 자막에서 영상의 핵심 지식을 JSON으로 추출한다. "
    "도메인은 영상 내용에 맞춘다 — 미리 가정하지 말 것. "
    "반드시 다음 스키마만 출력한다: {\"summary\": str, \"rules\": [str], \"tags\": [str]}. "
    "영상에 없는 도메인의 규칙을 끼워넣지 말 것. 다른 설명/마크다운/줄글 금지."
)


def _default_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}


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
        return out
