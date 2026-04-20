"""Shared fixtures for the E2E suite."""
from __future__ import annotations

import os
import uuid

import pytest

from collector.events import EventLogger
from collector.payload import new_payload
from collector.services import MockError, build_mock_services
from collector.store import JSONStore


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Skip real sleep inside stage_package exp-backoff so tests stay sub-second."""
    import collector.stages
    monkeypatch.setattr(collector.stages.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _no_kill_switch(monkeypatch):
    """Ensure COLLECTOR_PAUSED isn't leaked from the host env into tests."""
    monkeypatch.delenv("COLLECTOR_PAUSED", raising=False)


@pytest.fixture(autouse=True)
def _isolated_locks(monkeypatch, tmp_path):
    """Use a per-test lock directory so lockfiles don't collide across tests."""
    import collector.locks
    import collector.pipeline
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    orig = collector.pipeline.acquire
    def _acquire(source_key, *, root=lock_dir, owner=None):
        return orig(source_key, root=root, owner=owner)
    monkeypatch.setattr(collector.pipeline, "acquire", _acquire)


@pytest.fixture(autouse=True)
def _isolated_vault(monkeypatch, tmp_path):
    """Route run_pipeline's default vault_root into tmp_path so test runs
    don't litter the repo root with vault/ artifacts (G-11 follow-up)."""
    import collector.pipeline
    vault_dir = tmp_path / "default_vault"
    orig = collector.pipeline.run_pipeline
    def _run(payload, services, store, logger, *, fast_track=False, use_lock=True, vault_root=None):
        if vault_root is None:
            vault_root = vault_dir
        return orig(payload, services, store, logger,
                    fast_track=fast_track, use_lock=use_lock, vault_root=vault_root)
    monkeypatch.setattr(collector.pipeline, "run_pipeline", _run)


@pytest.fixture
def logger():
    return EventLogger()


@pytest.fixture
def store():
    return JSONStore()


def make_payload(video_id: str = "TEST0000001", **overrides):
    run_id = overrides.pop("run_id", f"run_{uuid.uuid4().hex[:8]}")
    p = new_payload(video_id=video_id, run_id=run_id, **overrides)
    return p


@pytest.fixture
def make_payload_fn():
    return make_payload
