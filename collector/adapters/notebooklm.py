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

import json
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
               *, timeout: float = DEFAULT_TIMEOUT_ADD,
               wait: bool = True) -> str:
    """Upload a local file as a source for the given notebook. Returns
    the source ID (or empty string if nlm doesn't print one).

    nlm CLI syntax (jacob-bd v0.6+):
        nlm source add NOTEBOOK --file PATH [--wait]
    The legacy `nlm source add NOTEBOOK PATH` form is rejected with
    'unexpected extra argument' — the file MUST come via --file."""
    args = ["source", "add", notebook_id, "--file", str(file_path)]
    if wait:
        args.append("--wait")
    out = _run(args, timeout)
    try:
        return _parse_id(out, label="source")
    except NotebookLMUnavailable:
        return ""  # source-id is informational; missing it is non-fatal


def query(notebook_id: str, prompt: str,
          *, timeout: float = DEFAULT_TIMEOUT_QUERY) -> str:
    """Send a prompt to the notebook and return the model's response
    text (markdown-ish, citations may be inline depending on nlm config)."""
    return _run(["notebook", "query", notebook_id, prompt], timeout).strip()


def list_sources(notebook_id: str, *, timeout: float = 60) -> list[dict]:
    """Return the notebook's current sources. nlm output shape may
    vary — try JSON first, fall back to a permissive line parser.

    On parse failure returns []. Caller decides whether that means
    'empty' or 'unsupported nlm version'."""
    out = _run(["source", "list", notebook_id], timeout)
    s = out.strip()
    if not s:
        return []
    if s.startswith("[") or s.startswith("{"):
        try:
            j = json.loads(s)
            if isinstance(j, list):
                return [x for x in j if isinstance(x, dict)]
            if isinstance(j, dict) and isinstance(j.get("sources"), list):
                return [x for x in j["sources"] if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    # Best-effort line parse: each non-empty line is one source whose
    # first whitespace-token looks like an id.
    rows: list[dict] = []
    for ln in s.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        tok = ln.split()[0] if ln.split() else ""
        if re.fullmatch(r"[\w\-]{6,}", tok):
            rows.append({"id": tok, "raw": ln})
    return rows


def remove_source(notebook_id: str, source_id: str,
                  *, timeout: float = 60) -> None:
    """Delete a source by id. nlm CLI: `nlm source delete SOURCE_ID --confirm`.
    notebook_id is accepted only for symmetry with add_source — the CLI
    doesn't actually need it here."""
    del notebook_id  # unused; kept for caller symmetry
    _run(["source", "delete", source_id, "--confirm"], timeout)


def replace_source(
    notebook_id: str, file_path: Path,
    *, timeout_list: float = 60,
    timeout_remove: float = 60,
    timeout_add: float = DEFAULT_TIMEOUT_ADD,
) -> str:
    """Drop ALL existing sources, then add `file_path` as the only one.

    Used by `collector watch` so each tick refreshes the notebook with
    the current vault snapshot — keeps the chat grounded in fresh data
    without piling up sources past the 50-per-notebook free-tier cap.

    On any subprocess failure, raises NotebookLMUnavailable so the
    caller can record the watch tick as failed."""
    try:
        existing = list_sources(notebook_id, timeout=timeout_list)
    except NotebookLMUnavailable:
        existing = []
    for src in existing:
        sid = src.get("id") or src.get("source_id")
        if not sid:
            continue
        try:
            remove_source(notebook_id, sid, timeout=timeout_remove)
        except NotebookLMUnavailable:
            # Ignore individual failures — the add below is what matters
            # and a stale leftover source won't break grounding.
            pass
    return add_source(notebook_id, file_path, timeout=timeout_add)


def replace_sources(
    notebook_id: str, file_paths: list[Path],
    *, timeout_list: float = 60,
    timeout_remove: float = 60,
    timeout_add: float = DEFAULT_TIMEOUT_ADD,
) -> list[str]:
    """Drop ALL existing sources, then add the given files (in order).

    Used when `collector watch` produces a chunked bundle ({label}_part01.md,
    _part02.md, …) — NotebookLM's free tier rejects single sources >500K
    words, so we split and upload N parts. Returns the list of source ids
    nlm assigned (best-effort; missing ids are returned as empty strings)."""
    try:
        existing = list_sources(notebook_id, timeout=timeout_list)
    except NotebookLMUnavailable:
        existing = []
    for src in existing:
        sid = src.get("id") or src.get("source_id")
        if not sid:
            continue
        try:
            remove_source(notebook_id, sid, timeout=timeout_remove)
        except NotebookLMUnavailable:
            pass
    added: list[str] = []
    for p in file_paths:
        try:
            added.append(add_source(notebook_id, p, timeout=timeout_add))
        except NotebookLMUnavailable as e:
            # Continue uploading the rest — partial coverage > none.
            import sys as _sys
            _sys.stderr.write(f"[nlm] add_source failed for {p.name}: {e}\n")
            added.append("")
    return added


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
