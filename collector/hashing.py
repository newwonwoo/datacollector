"""Transcript hash with v10 normalization (Master_01 §3)."""
from __future__ import annotations

import hashlib
import re
import unicodedata

_TIMECODE_RE = re.compile(
    r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?"
)
_INDEX_LINE_RE = re.compile(r"^\d+\s*$", re.MULTILINE)
_WS_RE = re.compile(r"\s+")


def normalize_transcript(text: str) -> str:
    text = _TIMECODE_RE.sub(" ", text)
    text = _INDEX_LINE_RE.sub(" ", text)
    text = text.replace("WEBVTT", " ")
    text = unicodedata.normalize("NFC", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def transcript_hash(text: str) -> str:
    norm = normalize_transcript(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()
