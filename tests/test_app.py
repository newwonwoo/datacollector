"""Smoke tests for the app launcher (collector.cli.app)."""
from __future__ import annotations

import json
import socket
import urllib.request
from pathlib import Path

from collector.cli.app import _pick_port, prepare_dashboard


def _seed(ds: Path) -> None:
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "a.json").write_text(json.dumps({
        "source_key": "youtube:A", "video_id": "A", "title": "t",
        "record_status": "promoted", "archive_state": "ACTIVE",
        "confidence": "confirmed", "reviewer": "auto",
        "transcript_hash": "h", "payload_version": 1,
        "failure_reason_code": None,
        "llm_context": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001},
        "collected_at": "2026-04-19T00:00:00Z",
    }), encoding="utf-8")


def test_prepare_dashboard_creates_artifacts(tmp_path):
    ds = tmp_path / "data_store"
    _seed(ds)
    db = tmp_path / "idx.sqlite"
    html = tmp_path / "out.html"
    n, out = prepare_dashboard(ds, db, html)
    assert n == 1
    assert out == html
    assert html.exists() and "total records" in html.read_text(encoding="utf-8")


def test_prepare_dashboard_creates_data_store_if_missing(tmp_path):
    ds = tmp_path / "fresh"
    db = tmp_path / "idx.sqlite"
    html = tmp_path / "out.html"
    n, _ = prepare_dashboard(ds, db, html)
    assert n == 0
    assert ds.exists()


def test_pick_port_returns_free_port():
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        _, used = blocker.getsockname()
        port = _pick_port(used, attempts=5)
        assert port != used
        assert port > used
    finally:
        blocker.close()


def test_entrypoint_dispatcher_help(capsys):
    from collector.__main__ import main as entry
    rc = entry([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "collector" in out and "dashboard" in out


def test_entrypoint_unknown_subcmd(capsys):
    from collector.__main__ import main as entry
    rc = entry(["definitely-not-a-command"])
    assert rc == 2
