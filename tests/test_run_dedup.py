"""run_query — pre-COLLECT smart-dedup filter.

Successfully processed records (promoted / reviewed_*) are skipped.
Records left in mid-pipeline failure states (invalid, collected, etc.)
are retried — the typical cause was a transient infra error
(LLM_HTTP_404 from a deprecated model, IP throttle), so silently
ignoring them forever surprises the user.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector.cli import run as run_mod
from collector.payload import new_payload


@pytest.fixture(autouse=True)
def _force_mock_mode(monkeypatch):
    """Other tests (test_app_api) leak fake API keys into os.environ via
    POST /api/config. Strip them so run_query() falls back to scripted mock."""
    for k in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _seed(ds: Path, video_id: str, *, status: str, archive_state: str = "ACTIVE") -> None:
    """Write a payload JSON to data_store as if a prior run had produced it."""
    p = new_payload(video_id=video_id, run_id="run_seed", channel_id="CH",
                    title=f"seeded {video_id}", source_query="단타")
    p["record_status"] = status
    p["archive_state"] = archive_state
    yyyymm = p["collected_at"][:7].replace("-", "")
    path = ds / yyyymm / f"{p['source_key'].replace(':', '__')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")


def _scripted_video_id(query: str, idx: int) -> str:
    """Mirror collector.cli.run._scripted_candidates so tests know what
    video_ids the mock will surface for a given query."""
    import hashlib
    h = hashlib.sha256(query.encode()).hexdigest()
    return f"Q{h[:6].upper()}{idx:02d}"


def test_skips_only_success_states(tmp_path: Path):
    ds = tmp_path / "ds"

    # Mock will produce video_ids {Q...00, Q...01, Q...02}. Seed them with
    # different prior statuses so we can assert the smart-dedup behaviour
    # in one shot.
    promoted_id = _scripted_video_id("단타", 0)
    inferred_id = _scripted_video_id("단타", 1)
    invalid_id  = _scripted_video_id("단타", 2)
    _seed(ds, promoted_id, status="promoted")
    _seed(ds, inferred_id, status="reviewed_inferred")
    _seed(ds, invalid_id, status="invalid")

    summary = run_mod.run_query(
        "단타", count=3, data_store_root=ds, logs_root=tmp_path / "logs"
    )
    # 2 success → skip, 1 invalid → retry
    assert summary["skipped_duplicates"] == 2
    assert summary["processed"] == 1
    # The retried one is the invalid one
    assert summary["per_video"][0]["video_id"] == invalid_id


def test_retries_collected_and_extracted(tmp_path: Path):
    """Mid-pipeline statuses count as failure → retry."""
    ds = tmp_path / "ds"
    _seed(ds, _scripted_video_id("단타", 0), status="collected")
    _seed(ds, _scripted_video_id("단타", 1), status="extracted")

    summary = run_mod.run_query(
        "단타", count=2, data_store_root=ds, logs_root=tmp_path / "logs"
    )
    assert summary["skipped_duplicates"] == 0
    assert summary["processed"] == 2


def test_no_seed_processes_all_new(tmp_path: Path):
    summary = run_mod.run_query(
        "단타", count=3, data_store_root=tmp_path / "ds", logs_root=tmp_path / "logs"
    )
    assert summary["skipped_duplicates"] == 0
    assert summary["processed"] == 3
    assert summary["requested_count"] == 3


def test_summary_includes_requested_count(tmp_path: Path):
    summary = run_mod.run_query(
        "단타", count=7, data_store_root=tmp_path / "ds", logs_root=tmp_path / "logs"
    )
    assert summary["requested_count"] == 7


def test_archived_records_also_skipped(tmp_path: Path):
    ds = tmp_path / "ds"
    # An ARCHIVED + promoted record from a prior run — still skip.
    _seed(ds, _scripted_video_id("단타", 0), status="promoted", archive_state="ARCHIVED")
    summary = run_mod.run_query(
        "단타", count=1, data_store_root=ds, logs_root=tmp_path / "logs"
    )
    assert summary["skipped_duplicates"] == 1
    assert summary["processed"] == 0
