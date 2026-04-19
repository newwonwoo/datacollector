"""Google Gemini adapter (generativelanguage.googleapis.com)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..services import MockError

SYSTEM_PROMPT = (
    "너는 한국어 유튜브 자막에서 매매 전략의 summary, rules, tags를 JSON으로 추출한다. "
    "반드시 다음 스키마만 출력한다: {\"summary\": str, \"rules\": [str], \"tags\": [str]}. "
    "다른 설명/마크다운/줄글 금지."
)


def _default_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}


class GeminiAdapter:
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
        prompt_version: str = "v1",
        http: Callable = _default_http,
    ):
        self.api_key = api_key
        self.model = model
        self.prompt_version = prompt_version
        self.http = http

    def extract(self, transcript: str, attempt: int) -> dict[str, Any]:
        url = f"{self.BASE}/{self.model}:generateContent?key={urllib.parse.quote(self.api_key)}"
        user = transcript if attempt == 0 else (
            "직전 응답이 JSON 스키마를 위반했다. 스키마에 엄격히 맞춰 다시 출력하라.\n\n" + transcript
        )
        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
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
        return out
