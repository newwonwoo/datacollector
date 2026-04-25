"""Tests for Obsidian vault writer (G-11 fix)."""
from __future__ import annotations

from pathlib import Path

from collector.events import EventLogger
from collector.payload import new_payload
from collector.pipeline import run_pipeline
from collector.services import build_mock_services
from collector.store import JSONStore
from collector.vault import regenerate_moc, render_note, write_note


_LONG_SUMMARY = (
    "단타 매매 전략 요약입니다. 장중 고점 돌파 시 분할 진입, 손절 직전 저점, "
    "익절 분할로 실행한다는 것이 핵심 흐름."
)


def _fixture():
    p = new_payload(video_id="VAULT001", run_id="r1",
                    channel_id="UCxyz", title="단타 전략",
                    published_at="2026-04-19T00:00:00Z")
    p["collected_at"] = "2026-04-20T00:00:00Z"
    p["summary"] = _LONG_SUMMARY
    p["rules"] = ["고점 돌파 시 분할 진입", "손절 직전 저점"]
    p["tags"] = ["단타", "돌파매매"]
    p["confidence"] = "confirmed"
    p["record_status"] = "promoted"
    return p


def test_render_note_contains_frontmatter_and_sections():
    md = render_note(_fixture())
    assert md.startswith("---")
    assert "source_key: youtube:VAULT001" in md
    assert "tags: [단타, 돌파매매]" in md
    assert "## 요약" in md
    assert "## 규칙" in md
    assert "[YouTube](https://www.youtube.com/watch?v=VAULT001)" in md
    assert "[[strategies-index]]" in md


def test_render_note_masks_pii():
    p = _fixture()
    p["summary"] = "문의 alice@example.com 010-1234-5678"
    md = render_note(p)
    assert "alice@example.com" not in md
    assert "010-1234-5678" not in md


def test_write_note_creates_markdown_file(tmp_path):
    out = write_note(_fixture(), tmp_path)
    assert out.exists()
    assert out.parent.name == "strategies"
    assert out.name == "youtube__VAULT001.md"


def test_regenerate_moc_builds_readme(tmp_path):
    write_note(_fixture(), tmp_path)
    # second note
    p2 = _fixture()
    p2["source_key"] = "youtube:VAULT002"
    p2["video_id"] = "VAULT002"
    p2["title"] = "두 번째 노트"
    write_note(p2, tmp_path)
    readme = regenerate_moc(tmp_path)
    assert readme.exists()
    body = readme.read_text(encoding="utf-8")
    assert "총 **2개** 노트" in body
    assert "[[youtube__VAULT001]]" in body
    assert "[[youtube__VAULT002]]" in body


def test_pipeline_writes_vault_on_promote(tmp_path):
    vault = tmp_path / "vault"
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    services = build_mock_services(
        captions_map={"VIDVAULT001": {"source": "manual", "text": "매매 전략 본문"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["r1", "r2"], "tags": ["단타"]}],
        similarity=0.8,
    )
    p = new_payload(video_id="VIDVAULT001", run_id="rv", title="Vault 통합 테스트")
    run_pipeline(p, services, store, logger, use_lock=False, vault_root=vault)
    assert (vault / "strategies" / "youtube__VIDVAULT001.md").exists()
    assert (vault / "README.md").exists()
    assert "r1" in (vault / "strategies" / "youtube__VIDVAULT001.md").read_text(encoding="utf-8")


def test_pipeline_skips_vault_when_disabled(tmp_path):
    vault = tmp_path / "vault"
    store = JSONStore(root=tmp_path / "ds")
    logger = EventLogger()
    services = build_mock_services(
        captions_map={"NOVAULT0001": {"source": "manual", "text": "t"}},
        llm_script=[{"summary": _LONG_SUMMARY, "rules": ["r"], "tags": ["t"]}],
        similarity=0.8,
    )
    p = new_payload(video_id="NOVAULT0001", run_id="rv2")
    run_pipeline(p, services, store, logger, use_lock=False, vault_root=None)
    assert not vault.exists()


def test_render_note_includes_notes_md_when_present():
    """When the LLM emits a detailed markdown note, the vault file should
    surface it under '## 상세 노트' so users browsing Obsidian see the
    full knowledge-library content, not just summary + rules."""
    from collector.payload import new_payload
    from collector.vault import render_note

    p = new_payload(video_id="VIDNOTE0001", run_id="rn", title="자세한 노트")
    p["summary"] = "한 줄 요약"
    p["rules"] = ["규칙 하나"]
    p["tags"] = ["tag1"]
    p["notes_md"] = "## 핵심\n- 항목 A\n- 항목 B\n\n## 인용\n> 화자의 말"

    out = render_note(p)
    assert "## 상세 노트" in out
    assert "## 핵심" in out
    assert "항목 A" in out
    assert "화자의 말" in out


def test_render_note_omits_notes_section_when_empty():
    from collector.payload import new_payload
    from collector.vault import render_note

    p = new_payload(video_id="VIDNOTE0002", run_id="rn")
    p["summary"] = "요약"
    p["notes_md"] = ""
    out = render_note(p)
    assert "## 상세 노트" not in out


def test_reduce_outputs_combines_notes_md():
    """When a long transcript is chunked, each chunk's notes_md must end
    up in the final reduced output — otherwise the knowledge-library
    intent gets thrown away on long videos."""
    from collector.chunking import reduce_outputs

    chunk_outs = [
        {"summary": "s1", "rules": ["r1"], "tags": ["t1"],
         "notes_md": "## 1부\n첫 번째 청크"},
        {"summary": "s2", "rules": ["r2"], "tags": ["t2"],
         "notes_md": "## 2부\n두 번째 청크"},
    ]
    reduced = reduce_outputs(chunk_outs)
    assert "1부" in reduced["notes_md"]
    assert "2부" in reduced["notes_md"]
