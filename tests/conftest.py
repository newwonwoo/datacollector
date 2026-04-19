"""Shared fixtures for the E2E suite."""
from __future__ import annotations

import uuid

import pytest

from collector.events import EventLogger
from collector.payload import new_payload
from collector.services import MockError, build_mock_services
from collector.store import JSONStore


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
