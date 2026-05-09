"""Tests for the watch registry + CLI + /api/watches endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from collector import watch_registry as reg


# -------- registry --------

def test_registry_upsert_creates_entry(tmp_path: Path):
    p = tmp_path / "w.json"
    reg.upsert("사주", path=p, keywords=["사주 결혼"], modes=["monetize"],
               interval="daily")
    items = reg.load(p)
    assert "사주" in items
    e = items["사주"]
    assert e["keywords"] == ["사주 결혼"]
    assert e["interval"] == "daily"
    assert e["paused"] is False
    assert e.get("created_at")


def test_registry_upsert_updates_existing(tmp_path):
    p = tmp_path / "w.json"
    reg.upsert("X", path=p, keywords=["k1"], interval="daily")
    reg.upsert("X", path=p, keywords=["k1", "k2"])
    items = reg.load(p)
    assert items["X"]["keywords"] == ["k1", "k2"]
    assert items["X"]["interval"] == "daily"  # preserved


def test_registry_remove(tmp_path):
    p = tmp_path / "w.json"
    reg.upsert("X", path=p, keywords=["k"])
    assert reg.remove("X", p) is True
    assert reg.remove("X", p) is False  # idempotent
    assert reg.load(p) == {}


def test_registry_record_run_appends_history(tmp_path):
    p = tmp_path / "w.json"
    reg.upsert("X", path=p, keywords=["k"])
    for i in range(3):
        reg.record_run("X", path=p, new_videos=i, promoted=i // 2,
                       status="completed", notebook_id="nb-1")
    items = reg.load(p)
    e = items["X"]
    assert len(e["history"]) == 3
    assert e["last_diff"]["new_videos"] == 2
    assert e["notebook_id"] == "nb-1"


def test_registry_record_run_caps_history(tmp_path):
    p = tmp_path / "w.json"
    reg.upsert("X", path=p, keywords=["k"])
    for i in range(25):
        reg.record_run("X", path=p, new_videos=i)
    e = reg.load(p)["X"]
    assert len(e["history"]) == 20  # trimmed
    assert e["history"][-1]["new_videos"] == 24
    assert e["history"][0]["new_videos"] == 5


# -------- watch CLI --------

def test_watch_register_then_list(tmp_path, capsys):
    from collector.cli.watch import main
    rc = main([
        "register", "--domain", "사주",
        "--keywords", "사주 결혼,사주 직업", "--modes", "monetize,summary",
        "--interval", "daily",
        "--registry", str(tmp_path / "w.json"),
    ])
    assert rc == 0
    rc = main(["list", "--registry", str(tmp_path / "w.json")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "사주" in out
    assert "daily" in out


def test_watch_run_once_calls_research_batch(tmp_path, monkeypatch):
    from collector.cli import watch as w
    from collector.cli.watch import main

    captured = {}

    def fake_research(keywords, **kw):
        captured["keywords"] = list(keywords)
        return [{"query": k, "promoted": 1} for k in keywords]

    monkeypatch.setattr("collector.cli.watch.research_batch", fake_research)

    # Pre-register
    reg.upsert("X", path=tmp_path / "w.json",
               keywords=["k1", "k2"], modes=[], interval="manual")

    rc = main([
        "run", "--domain", "X", "--once",
        "--no-notebook",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(tmp_path / "out"),
        "--registry", str(tmp_path / "w.json"),
    ])
    assert rc == 0
    assert captured["keywords"] == ["k1", "k2"]
    e = reg.load(tmp_path / "w.json")["X"]
    assert e["last_status"] == "completed"


def test_watch_run_unregistered_returns_error(tmp_path):
    from collector.cli.watch import main
    rc = main([
        "run", "--domain", "NOPE", "--once", "--no-notebook",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(tmp_path / "out"),
        "--registry", str(tmp_path / "w.json"),
    ])
    assert rc == 2


def test_watch_run_paused_skips_tick(tmp_path, monkeypatch):
    from collector.cli.watch import main
    called = {"n": 0}

    def fake_research(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr("collector.cli.watch.research_batch", fake_research)
    reg.upsert("X", path=tmp_path / "w.json",
               keywords=["k"], modes=[], interval="daily", paused=True)
    rc = main([
        "run", "--domain", "X", "--once", "--no-notebook",
        "--data-store", str(tmp_path / "ds"),
        "--logs", str(tmp_path / "lg"),
        "--out-dir", str(tmp_path / "out"),
        "--registry", str(tmp_path / "w.json"),
    ])
    assert rc == 0
    assert called["n"] == 0  # paused → no work


# -------- vault snapshot diff --------

def test_vault_snapshot_and_diff(tmp_path):
    from collector.cli.watch import _vault_snapshot, _diff
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "v1.json").write_text(json.dumps({
        "source_key": "youtube:V1", "video_id": "V1",
        "record_status": "promoted",
    }), encoding="utf-8")
    snap1 = _vault_snapshot(ds)
    assert snap1 == {"youtube:V1": "promoted"}
    # Add another file
    (ds / "v2.json").write_text(json.dumps({
        "source_key": "youtube:V2", "video_id": "V2",
        "record_status": "invalid",
    }), encoding="utf-8")
    snap2 = _vault_snapshot(ds)
    diff = _diff(snap1, snap2)
    assert diff == {"new_videos": 1, "promoted": 0, "invalid": 1}
