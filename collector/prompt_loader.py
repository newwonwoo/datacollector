"""Load prompt templates from `prompts/<name>.md`.

Template format:
- `## system`          — system prompt body until next `##`
- `## user_wrap`       — optional user-message wrap instructions
- `## reprompt_on_schema_fail` — reprompt-on-failure body (may contain
  `{original_transcript}` placeholder)

Unknown sections are ignored. Missing file falls back to a built-in
default so adapters never hard-fail when prompts/ is missing.
"""
from __future__ import annotations

import re
from pathlib import Path


_DEFAULT_SYSTEM = (
    "너는 한국어 유튜브 자막에서 영상의 핵심 지식을 JSON으로 추출한다. "
    "도메인은 영상 내용에 맞춘다 — 미리 가정하지 말 것. "
    "반드시 다음 스키마만 출력한다: "
    '{"summary": str, "rules": [str], "tags": [str]}. '
    "영상에 없는 도메인의 규칙을 끼워넣지 말 것. 다른 설명/마크다운/줄글 금지."
)
_DEFAULT_REPROMPT = (
    "직전 응답이 JSON 스키마를 위반했다. "
    "스키마에 엄격히 맞춰 다시 출력하라.\n\n{original_transcript}"
)


def _parse_sections(text: str) -> dict[str, str]:
    """Split into {section_name: body} by `## name` headings."""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S+)\s*$", line)
        if m:
            if current:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1)
            buf = []
        elif current is not None:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def load_prompt(
    name: str,
    *,
    prompts_root: Path = Path("prompts"),
) -> dict[str, str]:
    """Return {system, reprompt} with sensible defaults."""
    path = prompts_root / f"{name}.md"
    out = {"system": _DEFAULT_SYSTEM, "reprompt": _DEFAULT_REPROMPT}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    sections = _parse_sections(text)
    if sections.get("system"):
        out["system"] = sections["system"]
    if sections.get("reprompt_on_schema_fail"):
        out["reprompt"] = sections["reprompt_on_schema_fail"]
    return out
