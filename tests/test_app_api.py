"""Smoke tests for /api/config and /api/run on the local app server."""
from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from collector.cli.api_handler import make_handler, reset_run_state_for_tests


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LocalServer:
    def __init__(self, handler_cls) -> None:
        self.port = _pick_free_port()
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("127.0.0.1", self.port), handler_cls)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        # give the thread a moment to start listening
        for _ in range(20):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", self.port)) == 0:
                    break
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"


@pytest.fixture
def layout(tmp_path: Path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.html").write_text("<h1>root</h1>", encoding="utf-8")
    (docs / "status.json").write_text("{}", encoding="utf-8")
    dashboard = tmp_path / "index" / "dashboard.html"
    dashboard.parent.mkdir(parents=True)
    dashboard.write_text("<h1>dash</h1>", encoding="utf-8")
    env_path = tmp_path / ".env"
    data_store = tmp_path / "data_store"
    logs = tmp_path / "logs"
    reset_run_state_for_tests()
    return {
        "root": tmp_path,
        "docs": docs,
        "dashboard": dashboard,
        "env": env_path,
        "data_store": data_store,
        "logs": logs,
    }


@pytest.fixture
def server(layout):
    h = make_handler(
        project_root=layout["root"],
        docs_dir=layout["docs"],
        dashboard_html=layout["dashboard"],
        env_path=layout["env"],
        data_store=layout["data_store"],
        logs_root=layout["logs"],
    )
    with _LocalServer(h) as srv:
        yield srv


def _get(url: str) -> tuple[int, dict, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode("utf-8", "replace")


def _post(url: str, payload: dict | None) -> tuple[int, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def test_root_serves_index_html(server):
    status, _h, body = _get(server.url("/"))
    assert status == 200
    assert "<h1>root</h1>" in body


def test_dashboard_html_served(server):
    status, _h, body = _get(server.url("/dashboard.html"))
    assert status == 200
    assert "<h1>dash</h1>" in body


def test_directory_traversal_is_blocked(server):
    status, _h, _b = _get(server.url("/../etc/passwd"))
    assert status == 404


def test_api_config_get_reports_no_keys_initially(server):
    status, _h, body = _get(server.url("/api/config"))
    assert status == 200
    obj = json.loads(body)
    assert obj["has_youtube"] is False
    assert obj["has_gemini"] is False
    # Never leaks actual values
    assert "YOUTUBE_API_KEY" not in body
    assert "AIza" not in body


def test_api_config_post_writes_env_file(server, layout):
    status, body = _post(server.url("/api/config"), {
        "youtube": "AIzaSyFakeYoutubeKey01",
        "google": "AIzaSyFakeGeminiKey02",
    })
    assert status == 200, body
    env_text = layout["env"].read_text(encoding="utf-8")
    assert "YOUTUBE_API_KEY=AIzaSyFakeYoutubeKey01" in env_text
    assert "GOOGLE_API_KEY=AIzaSyFakeGeminiKey02" in env_text
    # And GET now reports both present
    _s, _h, b = _get(server.url("/api/config"))
    obj = json.loads(b)
    assert obj["has_youtube"] and obj["has_gemini"]


def test_api_config_post_empty_body_rejected(server):
    status, body = _post(server.url("/api/config"), {})
    assert status == 400
    assert "no keys" in body


def test_api_run_requires_query(server):
    status, body = _post(server.url("/api/run"), {})
    assert status == 400
    assert "query" in body


def test_api_run_mock_pipeline_smoke(server, layout, monkeypatch):
    """POST /api/run runs the scripted mock pipeline (no API keys needed)."""
    # Ensure no real adapters are attempted
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status, body = _post(server.url("/api/run"), {"query": "단타", "count": 2})
    assert status == 202, body
    obj = json.loads(body)
    assert obj["ok"] and obj["run_id"].startswith("api_")

    # Poll status until terminal (max ~10s)
    final = None
    for _ in range(50):
        _, _h, b = _get(server.url("/api/run/status"))
        final = json.loads(b)
        if final["status"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    assert final is not None
    assert final["status"] == "completed", final
    assert final["summary"]["query"] == "단타"


def test_api_run_rejects_when_already_running(server, layout, monkeypatch):
    # Force the state to look like a run is in progress.
    from collector.cli.api_handler import _RUN_LOCK, _RUN_STATE
    with _RUN_LOCK:
        _RUN_STATE.update({"status": "running", "run_id": "api_test"})
    try:
        status, body = _post(server.url("/api/run"), {"query": "x"})
        assert status == 409
        assert "already" in body
    finally:
        reset_run_state_for_tests()


def test_static_files_from_project_root(server, layout):
    # Arbitrary file inside the sandbox
    (layout["root"] / "vault").mkdir()
    (layout["root"] / "vault" / "hello.md").write_text("hi", encoding="utf-8")
    status, _h, body = _get(server.url("/vault/hello.md"))
    assert status == 200
    assert body == "hi"
