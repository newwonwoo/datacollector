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


SYSTEM_PROMPT = (
    "너는 한국어 유튜브 자막에서 영상의 핵심 지식을 JSON으로 추출한다. "
    "도메인은 영상 내용에 맞춘다 — 미리 가정하지 말 것. "
    "반드시 다음 스키마만 출력한다: {\"summary\": str, \"rules\": [str], \"tags\": [str], \"notes_md\": str}. "
    "notes_md 는 영상 전체 맥락을 마크다운 (## 소제목, 목록, 인용) 으로 상세 정리. "
    "영상에 없는 도메인의 규칙을 끼워넣지 말 것. 출력은 유효한 JSON 한 개만, 다른 설명/줄글/코드펜스 금지."
)


def _default_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}


class GroqAdapter:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
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
        return out
