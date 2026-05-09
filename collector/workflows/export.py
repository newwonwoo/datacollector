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


def export_raw_bundle(
    *,
    data_store_root: Path = Path("data_store"),
    out_dir: Path = Path("exports"),
    channel_id: str | None = None,
    only_promoted: bool = True,
    label: str = "",
    limit: int | None = None,
    max_chars_per_record: int | None = None,
) -> Path:
    """Bundle RAW transcripts (not extracted bullets) into one .md ready
    for NotebookLM upload. NotebookLM does its own analysis — we should
    feed it the source material, not our pre-chewed bullet points.

    `limit`: keep only the N most-recent records (by collected_at desc).
    None = all. Use a small N (e.g. 30) when uploading to NotebookLM —
    free tier rejects sources beyond ~500K words.
    `max_chars_per_record`: per-transcript truncation as a second
    safety net for ridiculously long videos. None = full transcript."""
    records: list[dict[str, Any]] = []
    for rec in _iter_payloads(data_store_root):
        if only_promoted and rec.get("record_status") != "promoted":
            continue
        if channel_id and rec.get("channel_id") != channel_id:
            continue
        records.append(rec)
    records.sort(key=lambda r: (r.get("collected_at") or ""), reverse=True)
    if limit is not None and limit > 0:
        records = records[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    parts = ["notebook_raw", ts]
    if label:
        parts.append(label.replace(" ", "_"))
    if channel_id:
        parts.append(f"ch_{channel_id[:10]}")
    name = "_".join(parts) + ".md"
    out = out_dir / name

    total_chars = 0
    lines: list[str] = [
        f"# Collector Raw Bundle — {len(records)}건",
        f"_export_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}_",
        "",
    ]
    for r in records:
        sk = r.get("source_key", "?")
        title = r.get("title") or sk
        vid = r.get("video_id", "")
        ch = r.get("channel_id", "")
        transcript = (r.get("transcript") or "").strip()
        if max_chars_per_record and len(transcript) > max_chars_per_record:
            transcript = transcript[:max_chars_per_record] + "\n\n[…truncated…]"
        total_chars += len(transcript)
        lines += [
            f"## {title}",
            f"- video: https://www.youtube.com/watch?v={vid}",
            f"- channel: `{ch}`",
            f"- collected: {r.get('collected_at', '')}",
            "",
        ]
        if transcript:
            lines += ["```", transcript, "```", ""]
        else:
            lines += ["_(no transcript captured)_", ""]
        lines += ["---", ""]

    lines.insert(2, f"_total_transcript_chars: {total_chars}_")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


import re as _re_export


def _sanitize_label(s: str) -> str:
    """File-safe label — ASCII/Korean/digits only, hyphens for the rest."""
    cleaned = _re_export.sub(r"[^\w가-힣\-]+", "_", (s or "").strip())
    return cleaned.strip("_-") or "bundle"


def _render_chunk_md(records: list[dict[str, Any]], *,
                     label: str, part: int, total_parts: int) -> str:
    lines = [
        f"# {label} — part {part}/{total_parts} ({len(records)}건)",
        f"_export_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}_",
        "",
    ]
    for r in records:
        title = r.get("title") or r.get("source_key", "?")
        vid = r.get("video_id", "")
        ch = r.get("channel_id", "")
        transcript = (r.get("transcript") or "").strip()
        lines += [
            f"## {title}",
            f"- video: https://www.youtube.com/watch?v={vid}",
            f"- channel: `{ch}`",
            f"- collected: {r.get('collected_at', '')}",
            "",
        ]
        if transcript:
            lines += ["```", transcript, "```", ""]
        else:
            lines += ["_(no transcript captured)_", ""]
        lines += ["---", ""]
    return "\n".join(lines)


def export_raw_bundles(
    *,
    data_store_root: Path = Path("data_store"),
    out_dir: Path = Path("exports"),
    label: str = "bundle",
    only_promoted: bool = True,
    channel_id: str | None = None,
    max_chars_per_bundle: int = 300_000,
    max_parts: int = 50,
    max_chars_per_record: int = 30_000,
) -> list[Path]:
    """Split raw transcripts into multiple bundles for NotebookLM upload.

    NotebookLM free tier: 50 sources/notebook, ~500K words/source. We
    target ~300K chars per chunk (≈ 200K words, safe margin) and stop
    at 50 parts. Files are written as `{label}_part01.md`, `_part02.md`
    so they're easily identifiable in the NotebookLM source list.

    Per-record transcripts longer than `max_chars_per_record` are
    truncated with a `[…truncated…]` marker.

    Returns the list of written paths (most-recent records first)."""
    records: list[dict[str, Any]] = []
    for rec in _iter_payloads(data_store_root):
        if only_promoted and rec.get("record_status") != "promoted":
            continue
        if channel_id and rec.get("channel_id") != channel_id:
            continue
        records.append(rec)
    records.sort(key=lambda r: (r.get("collected_at") or ""), reverse=True)

    # Truncate per-record up front so the chunk-fit math is honest.
    for r in records:
        t = (r.get("transcript") or "").strip()
        if max_chars_per_record and len(t) > max_chars_per_record:
            r["transcript"] = t[:max_chars_per_record] + "\n\n[…truncated…]"

    # Greedy fit: walk records, push to current chunk if it fits, else
    # start a new one. Stop after `max_parts` chunks (drop the rest).
    chunks: list[list[dict[str, Any]]] = [[]]
    cur = 0
    for r in records:
        rec_size = (len(r.get("transcript") or "")
                    + len(r.get("title") or "")
                    + 200)  # constant overhead per record
        if cur + rec_size > max_chars_per_bundle and chunks[-1]:
            if len(chunks) >= max_parts:
                break
            chunks.append([])
            cur = 0
        chunks[-1].append(r)
        cur += rec_size

    chunks = [c for c in chunks if c]
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = _sanitize_label(label)
    paths: list[Path] = []
    for i, chunk in enumerate(chunks, 1):
        name = f"{safe_label}_part{i:02d}.md"
        path = out_dir / name
        path.write_text(
            _render_chunk_md(chunk, label=label, part=i, total_parts=len(chunks)),
            encoding="utf-8",
        )
        paths.append(path)
    return paths
