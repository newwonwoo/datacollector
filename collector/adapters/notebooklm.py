"""Subprocess wrapper around the `nlm` CLI (notebooklm-mcp-cli).

This is a *thin* driver. We expose three primitives the workflow needs
(create / add_source / query) and surface a single recoverable error
(`NotebookLMUnavailable`) so callers can fall back to cheap-LLM specs
when the CLI is missing, the cookies expired, or Google's UI broke.

We deliberately do NOT install the `nlm` package automatically. If it's
not on PATH, raise — the user is expected to run `collector setup-notebooklm`
once (which prints the install/login instructions).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

NLM_BIN = "nlm"
DEFAULT_TIMEOUT_CREATE = 60
DEFAULT_TIMEOUT_ADD = 180
DEFAULT_TIMEOUT_QUERY = 300


class NotebookLMUnavailable(RuntimeError):
    """Raised whenever the nlm CLI is missing, exits non-zero, or its
    output cannot be parsed. The workflow catches this and falls back."""


def is_available() -> bool:
    """Cheap probe — used by `_cmd_full` to decide whether to attempt
    NotebookLM at all."""
    return shutil.which(NLM_BIN) is not None


def _run(args: list[str], timeout: float) -> str:
    if not is_available():
        raise NotebookLMUnavailable(
            "`nlm` CLI not found on PATH. Install with "
            "`pip install notebooklm-mcp-cli` then run `nlm login` once."
        )
    try:
        result = subprocess.run(
            [NLM_BIN, *args],
            capture_output=True, text=True, timeout=timeout, check=False,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as e:
        raise NotebookLMUnavailable(f"nlm not found: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise NotebookLMUnavailable(
            f"nlm {' '.join(args[:2])} timed out after {timeout}s"
        ) from e
    if result.returncode != 0:
        raise NotebookLMUnavailable(
            f"nlm {' '.join(args[:2])} failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def create_notebook(title: str, *, timeout: float = DEFAULT_TIMEOUT_CREATE) -> str:
    """Create a new NotebookLM notebook. Returns its ID (opaque string).
    The actual nlm output format varies a bit by version, so we accept
    a few shapes (JSON-ish, key=value, or just an ID on stdout)."""
    out = _run(["notebook", "create", title], timeout)
    return _parse_id(out, label="notebook")


def add_source(notebook_id: str, file_path: Path,
               *, timeout: float = DEFAULT_TIMEOUT_ADD) -> str:
    """Upload a local file as a source for the given notebook. Returns
    the source ID (or empty string if nlm doesn't print one)."""
    out = _run(["source", "add", notebook_id, str(file_path)], timeout)
    try:
        return _parse_id(out, label="source")
    except NotebookLMUnavailable:
        return ""  # source-id is informational; missing it is non-fatal


def query(notebook_id: str, prompt: str,
          *, timeout: float = DEFAULT_TIMEOUT_QUERY) -> str:
    """Send a prompt to the notebook and return the model's response
    text (markdown-ish, citations may be inline depending on nlm config)."""
    return _run(["notebook", "query", notebook_id, prompt], timeout).strip()


_ID_PATTERNS = [
    re.compile(r'"id"\s*:\s*"([\w\-]{6,})"'),
    re.compile(r'\bid\s*[:=]\s*([\w\-]{6,})\b', re.IGNORECASE),
    re.compile(r'/notebook/([\w\-]{6,})'),
    re.compile(r'\b([0-9a-f]{8}-[0-9a-f\-]{20,})\b'),  # uuid-ish
]


def _parse_id(text: str, *, label: str) -> str:
    text = (text or "").strip()
    for pat in _ID_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1)
    # Last resort: a single bare token on the last non-empty line.
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[\w\-]{6,}", line):
            return line
        break
    raise NotebookLMUnavailable(
        f"could not parse {label} id from nlm output: {text[:200]!r}"
    )
