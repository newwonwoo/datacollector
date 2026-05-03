"""Obsidian-compatible vault writer (Master_03 §2).

Writes one Markdown note per promoted record into `vault/strategies/`.
Also (re)generates `vault/README.md` as a Map-of-Content (MOC) index.

Notes follow Obsidian conventions:
- YAML frontmatter with tags/aliases
- > [!info] callouts
- #태그 inline tags
- PII-masked (via collector.pii.mask_payload)

Output lives in the repo under `vault/` so users can:
- Browse directly on GitHub
- Clone the repo and open in Obsidian
- Use Obsidian Git plugin to pull changes
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .pii import mask_payload


def render_note(payload: dict[str, Any]) -> str:
    p = mask_payload(payload)
    tags = p.get("tags") or []
    rules = p.get("rules") or []
    vid = p.get("video_id", "")
    ch = p.get("channel_id", "")
    yaml_tags = "[" + ", ".join(tags) + "]" if tags else "[]"

    lines: list[str] = [
        "---",
        f"source_key: {p.get('source_key')}",
        f"video_id: {vid}",
        f"title: \"{(p.get('title') or '').replace(chr(34), chr(39))}\"",
        f"channel: {ch}",
        f"published: {p.get('published_at', '')}",
        f"collected: {p.get('collected_at', '')}",
        f"confidence: {p.get('confidence', '')}",
        f"record_status: {p.get('record_status', '')}",
        f"schema_version: {p.get('schema_version', '')}",
        f"tags: {yaml_tags}",
        f"aliases: [\"{vid}\"]",
        "---",
        "",
        f"# {p.get('title', vid)}",
        "",
        "> [!info] 원본 메타",
        f"> - **Source**: [YouTube](https://www.youtube.com/watch?v={vid})",
        f"> - **Channel**: `{ch}`",
        f"> - **Published**: {p.get('published_at', '—')}",
        f"> - **Collected**: {p.get('collected_at', '—')}",
        f"> - **Confidence**: {p.get('confidence', '—')}",
        "",
        "## 요약",
        p.get("summary") or "(요약 없음)",
        "",
    ]

    # extract_generic_v2 sections — only render when non-empty so notes
    # stay clean for v1-shape records that lack these fields entirely.
    knowledge = p.get("knowledge") or []
    examples  = p.get("examples") or []
    claims    = p.get("claims") or []
    unclear   = p.get("unclear") or []
    if knowledge:
        lines += ["## 핵심 개념"]
        lines += [f"- {k}" for k in knowledge]
        lines += [""]
    if rules:
        lines += ["## 행동 지침"]
        lines += [f"{i}. {r}" for i, r in enumerate(rules, 1)]
        lines += [""]
    if examples:
        lines += ["## 사례"]
        lines += [f"- {e}" for e in examples]
        lines += [""]
    if claims:
        lines += ["## 화자의 주장"]
        lines += [f"- {c_}" for c_ in claims]
        lines += [""]
    if unclear:
        lines += ["## 명확하지 않은 부분"]
        lines += [f"- {u}" for u in unclear]
        lines += [""]
    notes_md = (p.get("notes_md") or "").strip()
    if notes_md:
        lines += ["## 상세 노트", notes_md, ""]

    # Back-compat: when none of the v2 substance fields are populated and
    # we don't even have a notes_md, surface the legacy '규칙' block so
    # records collected before the v2 schema still produce a usable note.
    if not (knowledge or rules or examples or claims or unclear or notes_md):
        lines += ["## 규칙", "(규칙 없음)", ""]

    ctype = (p.get("content_type") or "").strip().lower()
    llm_conf = (p.get("llm_confidence") or "").strip().lower()
    meta_bits = []
    if ctype:
        meta_bits.append(f"종류: `{ctype}`")
    if llm_conf:
        meta_bits.append(f"LLM 자평: `{llm_conf}`")
    if meta_bits:
        lines += ["> " + " · ".join(meta_bits), ""]

    lines += [
        "## 태그",
        " ".join(f"#{t}" for t in tags) if tags else "(없음)",
        "",
        "## 관련",
        "- [[strategies-index]]",
        f"- [[channel-{ch}]]" if ch else "",
        "",
    ]
    return "\n".join(l for l in lines if l is not None)


def write_note(payload: dict[str, Any], vault_root: Path) -> Path:
    sk = payload["source_key"]
    name = sk.replace(":", "__") + ".md"
    out_dir = Path(vault_root) / "strategies"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name
    out.write_text(render_note(payload), encoding="utf-8")
    return out


def _iter_notes(vault_root: Path) -> Iterable[dict[str, Any]]:
    """Re-parse written notes to build a MOC. Minimal — extract frontmatter."""
    strat = Path(vault_root) / "strategies"
    if not strat.exists():
        return
    for md in strat.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        try:
            end = text.index("\n---", 3)
        except ValueError:
            continue
        meta: dict[str, Any] = {"file": md.name}
        for line in text[3:end].splitlines():
            if ": " in line:
                k, _, v = line.partition(": ")
                meta[k.strip()] = v.strip().strip('"').strip("[]")
        yield meta


def regenerate_moc(vault_root: Path) -> Path:
    """Write vault/README.md (also acts as strategies-index)."""
    entries = list(_iter_notes(vault_root))
    entries.sort(key=lambda e: e.get("collected", ""), reverse=True)

    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        for t in [s.strip() for s in (e.get("tags") or "").split(",") if s.strip()]:
            by_tag[t].append(e)
        ch = e.get("channel", "")
        if ch:
            by_channel[ch].append(e)

    lines: list[str] = [
        "# Collector Vault",
        "",
        f"총 **{len(entries)}개** 노트. 자동 생성 — 직접 수정 금지.",
        "",
        "## 전체 목록 (최신순)",
    ]
    for e in entries[:200]:
        title = e.get("title", "")
        name = e.get("file", "").replace(".md", "")
        lines.append(f"- [[{name}]] — {title} · {e.get('confidence','')}")
    lines += ["", "## 태그별"]
    for tag, items in sorted(by_tag.items(), key=lambda x: -len(x[1])):
        lines.append(f"### #{tag} ({len(items)})")
        for e in items[:20]:
            name = e.get("file", "").replace(".md", "")
            lines.append(f"- [[{name}]]")
        lines.append("")
    lines += ["", "## 채널별"]
    for ch, items in sorted(by_channel.items(), key=lambda x: -len(x[1])):
        lines.append(f"- `{ch}` ({len(items)})")

    out = Path(vault_root) / "README.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Obsidian-friendly alias: strategies-index.md that redirects to README
    (Path(vault_root) / "strategies-index.md").write_text(
        "# strategies-index\n\n→ [[README]]\n", encoding="utf-8"
    )
    return out
