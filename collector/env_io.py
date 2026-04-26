"""Read/merge/write `.env` files while preserving comments and unrelated keys.

Used by the local web app (`collector app`) to let users paste API keys via
the browser UI and persist them to disk. See GOTCHAS G-14.

Invariants:
- Existing keys are overwritten in place (same line, no reorder).
- Keys not previously present are appended at the bottom.
- Comment lines (`#`) and blank lines are preserved verbatim.
- Values containing whitespace, `#`, quotes, or newlines are double-quoted.
- File is always written as UTF-8 with LF endings and a trailing newline.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_NEED_QUOTE = re.compile(r"[\s#\"'\\]")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        inner = v[1:-1]
        if v[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return v


def _escape(v: str) -> str:
    if v == "" or _NEED_QUOTE.search(v):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return v


def _parse(text: str) -> list[tuple[str | None, str]]:
    """Return list of (key_or_None, original_line). Key=None for comments/blank."""
    out: list[tuple[str | None, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            out.append((None, raw))
            continue
        m = _LINE_RE.match(raw)
        if not m:
            out.append((None, raw))
        else:
            out.append((m.group(1), raw))
    return out


def read_env(path: Path) -> dict[str, str]:
    """Parse `.env` at `path` and return a dict of key → value.

    Returns an empty dict if the file does not exist or cannot be read.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for key, line in _parse(text):
        if key is None:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        out[key] = _unquote(m.group(2))
    return out


def merge_env(path: Path, updates: dict[str, str]) -> None:
    """Merge `updates` into the `.env` file at `path`.

    - Existing keys are replaced in place (comments/other keys untouched).
    - Keys not previously present are appended at the bottom.
    - Creates the file (and parent dirs) if missing.
    - Does nothing if `updates` is empty.
    """
    if not updates:
        return
    p = Path(path)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    entries = _parse(existing)

    seen: set[str] = set()
    out_lines: list[str] = []
    for key, raw in entries:
        if key is not None and key in updates:
            out_lines.append(f"{key}={_escape(updates[key])}")
            seen.add(key)
        else:
            out_lines.append(raw)

    tail: list[str] = []
    for k in updates:
        if k not in seen:
            tail.append(f"{k}={_escape(updates[k])}")
    if tail:
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("")
        out_lines.extend(tail)

    p.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(out_lines).rstrip("\n") + "\n"
    p.write_text(body, encoding="utf-8", newline="\n")


def apply_to_environ(updates: dict[str, str]) -> None:
    """Copy updates into `os.environ` (non-destructive for other keys)."""
    for k, v in updates.items():
        if v:
            os.environ[k] = v


def has_keys(env: dict[str, str], names: Iterable[str]) -> bool:
    """True iff every name in `names` has a non-empty, non-placeholder value."""
    for n in names:
        v = env.get(n, "") or os.environ.get(n, "")
        if not v or _looks_placeholder(v):
            return False
    return True


def _looks_placeholder(v: str) -> bool:
    s = v.strip()
    if not s:
        return True
    # Reject Korean placeholder in .env.example: "여기에_키_붙여넣기"
    if "여기에" in s:
        return True
    if s.lower() in {"your_key_here", "paste_here", "xxx", "placeholder"}:
        return True
    return False
