"""PII masking (Appendix D §6 — renderer preprocessor).

Strict regex-based. Leaves markers so reviewers can audit.
"""
from __future__ import annotations

import re


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 이메일
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[이메일]"),
    # 한국 휴대폰 (01x-xxxx-xxxx / 01x xxxx xxxx / 01xxxxxxxxx)
    (re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b"), "[전화]"),
    # 한국 일반 전화 (02-xxx-xxxx, 0xx-xxx-xxxx)
    (re.compile(r"\b0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}\b"), "[전화]"),
    # 주민등록번호 유사 (YYMMDD-NNNNNNN)
    (re.compile(r"\b\d{6}[- ]?\d{7}\b"), "[주민번호]"),
    # 신용카드 유사 (4-4-4-4)
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "[카드번호]"),
    # IPv4
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
]


def mask(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def mask_payload(payload: dict) -> dict:
    """Return a copy of payload with user-visible text fields masked."""
    p = dict(payload)
    if "summary" in p:
        p["summary"] = mask(p.get("summary", ""))
    if "title" in p:
        p["title"] = mask(p.get("title", ""))
    if "rules" in p:
        p["rules"] = [mask(r) for r in (p.get("rules") or [])]
    # transcript stays raw (private) — not rendered to Markdown
    return p
