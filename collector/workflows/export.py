"""Step 4 — bundle vault notes into a single .md file ready for
NotebookLM (or any other knowledge tool) one-click upload.

No LLM call here — just file IO + frontmatter. Filters let the user
narrow the bundle down to the channel/topic they actually want to feed
to NotebookLM, instead of dumping the whole vault every time.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _iter_payloads(data_store: Path) -> Iterable[dict[str, Any]]:
    if not data_store.exists():
        return
    for p in data_store.rglob("*.json"):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue


def _matches(rec: dict[str, Any], *, channel_id: str | None, content_type: str | None,
             tag: str | None, only_promoted: bool) -> bool:
    if only_promoted and rec.get("record_status") != "promoted":
        return False
    if channel_id and rec.get("channel_id") != channel_id:
        return False
    if content_type and (rec.get("content_type") or "").lower() != content_type.lower():
        return False
    if tag and tag not in (rec.get("tags") or []):
        return False
    return True


def export_notebook(
    *,
    data_store_root: Path = Path("data_store"),
    out_dir: Path = Path("exports"),
    channel_id: str | None = None,
    content_type: str | None = None,
    tag: str | None = None,
    only_promoted: bool = True,
    label: str = "",
) -> Path:
    """Write a single combined Markdown file under `out_dir/` and return
    its path. Each matched record becomes one section with the same
    structure that vault notes use, but joined into one document so
    NotebookLM ingests it as a single source.
    """
    records: list[dict[str, Any]] = []
    for rec in _iter_payloads(data_store_root):
        if _matches(rec, channel_id=channel_id, content_type=content_type,
                    tag=tag, only_promoted=only_promoted):
            records.append(rec)

    records.sort(key=lambda r: (r.get("collected_at") or ""), reverse=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    parts = ["notebook", ts]
    if label:
        parts.append(label.replace(" ", "_"))
    if channel_id:
        parts.append(f"ch_{channel_id[:10]}")
    if content_type:
        parts.append(content_type)
    if tag:
        parts.append(f"tag_{tag}")
    name = "_".join(parts) + ".md"
    out = out_dir / name

    lines: list[str] = []
    # Top-level frontmatter
    lines += [
        "---",
        f"export_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"record_count: {len(records)}",
        f"channel_id: {channel_id or '*'}",
        f"content_type: {content_type or '*'}",
        f"tag: {tag or '*'}",
        f"only_promoted: {str(only_promoted).lower()}",
        "---",
        "",
        f"# Collector Notebook Export — {len(records)}건",
        "",
    ]

    for r in records:
        sk = r.get("source_key", "?")
        title = r.get("title") or sk
        vid = r.get("video_id", "")
        lines += [
            f"## {title}",
            "",
            f"- source: [YouTube](https://www.youtube.com/watch?v={vid})",
            f"- source_key: `{sk}`",
            f"- channel: `{r.get('channel_id', '')}`",
            f"- collected: {r.get('collected_at', '')}",
            f"- content_type: `{r.get('content_type', '')}` · "
            f"confidence: `{r.get('confidence', '')}` · "
            f"llm_confidence: `{r.get('llm_confidence', '')}`",
            "",
        ]
        summary = (r.get("summary") or "").strip()
        if summary:
            lines += ["### 요약", summary, ""]
        for label_, key in (
            ("핵심 개념", "knowledge"),
            ("행동 지침", "rules"),
            ("사례", "examples"),
            ("화자의 주장", "claims"),
            ("불명확", "unclear"),
        ):
            items = r.get(key) or []
            if items:
                lines += [f"### {label_}"]
                for it in items:
                    lines.append(f"- {it}")
                lines.append("")
        notes_md = (r.get("notes_md") or "").strip()
        if notes_md:
            lines += ["### 상세 노트", notes_md, ""]
        tags = r.get("tags") or []
        if tags:
            lines += ["### 태그", " ".join(f"#{t}" for t in tags), ""]
        lines.append("---")
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out
