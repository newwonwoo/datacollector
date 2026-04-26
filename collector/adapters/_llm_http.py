"""HTTP utility for LLM adapter calls — TLS-fingerprint aware.

Cloudflare-fronted APIs (Groq, parts of Anthropic, sometimes Gemini)
fingerprint the TLS handshake with JA3 and reject vanilla Python
urllib calls — verified by reproducing the user's HTTP_403 / Cloudflare
1010 with `python -m urllib` and showing the same key works fine
through `curl`. When `curl_cffi` is installed (we already require it
for yt-dlp YouTube cookies in G-15) we route LLM calls through its
Chrome impersonation; otherwise we degrade to urllib.
"""
from __future__ import annotations

import urllib.error
import urllib.request


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _urllib_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    h = {"User-Agent": _BROWSER_UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, method=method, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}


def _curl_cffi_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    """curl_cffi-backed call with chrome impersonation (JA3 + headers).
    Returns the same {status, body} shape as the urllib variant.
    """
    from curl_cffi import requests as cffi_requests  # type: ignore

    h = {"User-Agent": _BROWSER_UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    try:
        resp = cffi_requests.request(
            method=method,
            url=url,
            headers=h,
            data=data,
            timeout=30,
            impersonate="chrome",
        )
        return {"status": resp.status_code, "body": resp.text}
    except Exception as e:  # noqa: BLE001
        # Surface as a synthetic 599 so the caller's MockError mapping fires.
        return {"status": 599, "body": f"curl_cffi error: {e}"}


def llm_http(method: str, url: str, *, headers: dict | None = None, data: bytes | None = None) -> dict:
    """Pick the best available transport. Lazily probe curl_cffi so
    importing this module doesn't require it."""
    try:
        import curl_cffi  # noqa: F401  type: ignore
        return _curl_cffi_http(method, url, headers=headers, data=data)
    except ImportError:
        return _urllib_http(method, url, headers=headers, data=data)
