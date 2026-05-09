"""Drive NotebookLM to produce purpose-fit specs from raw transcripts.

Flow per `generate_specs(domain, bundle_path, modes, out_dir)`:
  1. Create one NotebookLM notebook for this run.
  2. Upload the raw bundle once as a source.
  3. For each requested mode, send the corresponding hardcoded prompt
     and save the response as `{out_dir}/spec_NB_{mode}.md`.

If the `nlm` CLI is missing, the cookies expired, or any subprocess
fails, `NotebookLMUnavailable` propagates so `_cmd_full` can fall back
to the cheap-LLM design_spec without losing earlier outputs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..adapters import notebooklm
from ..adapters.notebooklm import NotebookLMUnavailable

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

MODES: dict[str, str] = {
    "monetize": "nb_monetize.md",
    "textbook": "nb_textbook.md",
    "summary": "nb_summary.md",
    "presentation": "nb_presentation.md",
}


def available_modes() -> list[str]:
    return list(MODES)


def load_prompt(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose from {list(MODES)}")
    p = PROMPTS_DIR / MODES[mode]
    return p.read_text(encoding="utf-8")


def parse_modes(spec: str) -> list[str]:
    """Comma-separated → list[str], with validation. Empty → []."""
    if not spec:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in spec.split(","):
        m = raw.strip().lower()
        if not m or m in seen:
            continue
        if m not in MODES:
            raise ValueError(
                f"unknown mode {m!r}; choose from {list(MODES)}"
            )
        out.append(m)
        seen.add(m)
    return out


def generate_specs(
    domain: str,
    bundle_path: Path,
    modes: Iterable[str],
    out_dir: Path,
    *,
    notebook_title: str | None = None,
) -> dict[str, Path]:
    """Returns {mode: written_path}. Notebook is created+uploaded once,
    then each mode adds a prompt and saves the response."""
    modes = list(modes)
    for m in modes:
        if m not in MODES:
            raise ValueError(f"unknown mode {m!r}; choose from {list(MODES)}")
    if not modes:
        return {}
    if not bundle_path.exists():
        raise FileNotFoundError(f"bundle not found: {bundle_path}")

    title = notebook_title or (
        f"{domain}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"
    )

    nb_id = notebooklm.create_notebook(title)
    notebooklm.add_source(nb_id, bundle_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for mode in modes:
        prompt = load_prompt(mode)
        answer = notebooklm.query(nb_id, prompt)
        out = out_dir / f"spec_NB_{mode}.md"
        header = (
            f"<!-- mode: {mode} · notebook: {nb_id} · "
            f"generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} -->\n\n"
        )
        out.write_text(header + answer + "\n", encoding="utf-8")
        written[mode] = out
    return written


__all__ = [
    "MODES", "available_modes", "load_prompt", "parse_modes",
    "generate_specs", "NotebookLMUnavailable",
]
