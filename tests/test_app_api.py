"""Smoke tests for /api/config and /api/run on the local app server."""
from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.parse
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


def test_api_run_requires_query_or_channel(server):
    """At least one of (query, channel) must be supplied."""
    status, body = _post(server.url("/api/run"), {})
    assert status == 400
    assert "검색어" in body or "채널" in body


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

    # Poll status until terminal. The conftest patches `time.sleep` to
    # a no-op (for stage_package backoff), so we can't rely on
    # time.sleep here for pacing — use a wall-clock deadline + busy
    # wait yielding to other threads via time.monotonic.
    import time as _t
    deadline = _t.monotonic() + 10.0
    final = None
    while _t.monotonic() < deadline:
        _, _h, b = _get(server.url("/api/run/status"))
        final = json.loads(b)
        if final["status"] in ("completed", "failed"):
            break
        # Tiny CPU-yield without depending on sleep — round-trip HTTP
        # already gives the worker thread a chance to advance.
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


def test_status_refresh_uses_app_paths(tmp_path, monkeypatch):
    """After a run, status_cli must be called with the app's data/log paths."""
    from collector.cli import api_handler

    recorded: dict = {}

    def fake_status_main(argv):
        recorded["argv"] = list(argv)
        return 0

    monkeypatch.setattr(
        "collector.cli.status_cli.main", fake_status_main, raising=True
    )

    def fake_run_query(*_a, **_k):
        return {"query": "x", "mode": "mock", "run_id": "run_fake",
                "candidates": 0, "promoted": 0, "inferred": 0,
                "unverified": 0, "invalid": 0, "failed_reasons": [],
                "events": 0, "per_video": []}

    monkeypatch.setattr("collector.cli.run.run_query", fake_run_query, raising=True)

    data = tmp_path / "custom_ds"
    logs = tmp_path / "custom_logs"
    docs = tmp_path / "custom_docs"
    docs.mkdir()

    api_handler.reset_run_state_for_tests()
    api_handler._run_worker(
        query="x", count=1, llm_choice=None,
        data_store=data, logs_root=logs, docs_dir=docs,
    )

    argv = recorded.get("argv") or []
    assert "--data-store" in argv and str(data) in argv
    assert "--events" in argv
    assert str(logs / "events.jsonl") in argv
    assert "--out" in argv and str(docs / "status.json") in argv


def test_static_files_from_project_root(server, layout):
    # Arbitrary file inside the sandbox
    (layout["root"] / "vault").mkdir()
    (layout["root"] / "vault" / "hello.md").write_text("hi", encoding="utf-8")
    status, _h, body = _get(server.url("/vault/hello.md"))
    assert status == 200
    assert body == "hi"


def test_workflow_post_validates_domain(server):
    status, body = _post(server.url("/api/workflow"), {})
    assert status == 400
    assert "domain" in body


def test_workflow_post_rejects_unknown_mode(server):
    status, body = _post(server.url("/api/workflow"),
                         {"domain": "x", "modes": ["not_a_mode"]})
    assert status == 400
    assert "unknown mode" in body or "not_a_mode" in body


def test_workflow_post_starts_run(server, layout, monkeypatch):
    """POST /api/workflow returns 202, runs the worker (we patch it),
    and exposes the run_id via /api/workflow/status."""
    import collector.cli.api_handler as ah

    started = threading.Event()
    captured = {}

    def fake_workflow_main(argv):
        captured["argv"] = list(argv)
        started.set()
        return 0  # success

    monkeypatch.setattr("collector.cli.workflow.main", fake_workflow_main)

    status, body = _post(server.url("/api/workflow"),
                         {"domain": "사주", "count": 2,
                          "modes": ["monetize", "summary"]})
    assert status == 202, body
    j = json.loads(body)
    assert j["ok"] is True
    assert j["domain"] == "사주"
    assert j["modes"] == ["monetize", "summary"]
    rid = j["run_id"]
    assert rid.startswith("wf_")

    assert started.wait(timeout=5)

    # Worker has set the state to completed eventually.
    deadline = time.time() + 5
    final = None
    while time.time() < deadline:
        _, _h, body2 = _get(server.url("/api/workflow/status"))
        snap = json.loads(body2)
        if snap["status"] in ("completed", "failed"):
            final = snap
            break
        time.sleep(0.05)
    assert final is not None and final["status"] == "completed"
    # CLI args were assembled correctly.
    argv = captured["argv"]
    assert argv[0] == "full"
    assert "--domain" in argv and "사주" in argv
    assert "--modes" in argv
    assert "monetize,summary" in argv


def test_workflow_post_409_when_already_running(server, monkeypatch):
    """Second POST while one is in flight gets 409."""
    barrier = threading.Event()

    def slow_workflow_main(argv):
        barrier.wait(timeout=5)
        return 0

    monkeypatch.setattr("collector.cli.workflow.main", slow_workflow_main)

    s1, _ = _post(server.url("/api/workflow"), {"domain": "x"})
    assert s1 == 202
    s2, body2 = _post(server.url("/api/workflow"), {"domain": "x"})
    assert s2 == 409
    assert "already" in body2
    barrier.set()


def test_workflow_files_lists_artifacts(server, layout, monkeypatch):
    """After a run, /api/workflow/files lists the produced artifacts."""
    project_root = layout["root"]

    def fake_workflow_main(argv):
        # Simulate the workflow writing artifacts into out_dir.
        idx = argv.index("--out-dir")
        out_dir = Path(argv[idx + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "step4_spec_0.md").write_text("# spec", encoding="utf-8")
        (out_dir / "spec_NB_monetize.md").write_text("# nb", encoding="utf-8")
        return 0

    monkeypatch.setattr("collector.cli.workflow.main", fake_workflow_main)
    status, body = _post(server.url("/api/workflow"),
                         {"domain": "x", "modes": ["monetize"]})
    assert status == 202

    deadline = time.time() + 5
    while time.time() < deadline:
        _, _h, b = _get(server.url("/api/workflow/status"))
        if json.loads(b)["status"] == "completed":
            break
        time.sleep(0.05)

    _, _h, files_body = _get(server.url("/api/workflow/files"))
    obj = json.loads(files_body)
    names = sorted(f["name"] for f in obj["files"])
    assert "spec_NB_monetize.md" in names
    assert "step4_spec_0.md" in names


def test_resolve_channel_passes_uc_id_through(server, monkeypatch):
    """A bare UC… id requires no API call — must round-trip directly."""
    status, _h, body = _get(server.url(
        "/api/resolve_channel?q=" + "UC1234567890ABCDEFGHIJKL"
    ))
    assert status == 200
    obj = json.loads(body)
    assert obj["ok"] is True
    assert obj["channel_id"] == "UC1234567890ABCDEFGHIJKL"


def test_resolve_channel_resolves_handle_via_youtube_adapter(server, monkeypatch):
    """An @handle should be routed to YouTubeAdapter.resolve_channel."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "FAKE")

    captured = {}

    def fake_resolve(self, raw):
        captured["raw"] = raw
        return "UCFAKEFAKEFAKEFAKEFAKE12"

    monkeypatch.setattr(
        "collector.adapters.youtube.YouTubeAdapter.resolve_channel",
        fake_resolve,
    )

    status, _h, body = _get(server.url(
        "/api/resolve_channel?q=" + urllib.parse.quote("@somehandle")
    ))
    assert status == 200, body
    obj = json.loads(body)
    assert obj["ok"] is True
    assert obj["channel_id"] == "UCFAKEFAKEFAKEFAKEFAKE12"
    assert "@somehandle" in (captured.get("raw") or "")


def test_resolve_channel_rejects_unknown_input(server, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "FAKE")
    monkeypatch.setattr(
        "collector.adapters.youtube.YouTubeAdapter.resolve_channel",
        lambda self, raw: None,
    )
    status, _h, body = _get(server.url(
        "/api/resolve_channel?q=" + urllib.parse.quote("not-a-channel")
    ))
    # 200 with ok:false — reserved 502 for transport / adapter errors.
    assert status == 200
    obj = json.loads(body)
    assert obj["ok"] is False
    assert obj["channel_id"] is None


def test_run_post_resolves_channel_url(server, layout, monkeypatch):
    """POST /api/run with a channel URL should resolve to UC… server-side
    and pass it on as target_channel_id."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "FAKE")
    monkeypatch.setattr(
        "collector.adapters.youtube.YouTubeAdapter.resolve_channel",
        lambda self, raw: "UCRESOLVEDFROMURL12345A",
    )

    captured: dict = {}
    started = threading.Event()

    def fake_run_query(*a, **kw):
        captured.update(kw)
        started.set()
        return {"requested_count": kw.get("count", 0), "promoted": 0,
                "processed": 0, "candidates": 0, "skipped_duplicates": 0,
                "per_video": []}

    monkeypatch.setattr("collector.cli.run.run_query", fake_run_query)

    status, body = _post(server.url("/api/run"), {
        "query": "x",
        "count": 1,
        "channel_id": "https://www.youtube.com/@someone",
    })
    assert status == 202, body
    assert started.wait(timeout=5)
    assert captured.get("target_channel_id") == "UCRESOLVEDFROMURL12345A"


def test_resolve_channel_handles_korean_handle(server, monkeypatch):
    """@슈카월드 should reach the YouTube adapter — earlier ASCII-only
    regex silently dropped Korean handles."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "FAKE")
    captured = {}

    def fake_resolve(self, raw):
        captured["raw"] = raw
        return "UCSHUKAWORLD0000000000A1"

    monkeypatch.setattr(
        "collector.adapters.youtube.YouTubeAdapter.resolve_channel",
        fake_resolve,
    )
    status, _h, body = _get(server.url(
        "/api/resolve_channel?q=" + urllib.parse.quote("@슈카월드")
    ))
    assert status == 200, body
    obj = json.loads(body)
    assert obj["ok"] is True
    assert obj["channel_id"] == "UCSHUKAWORLD0000000000A1"
    assert "슈카월드" in (captured.get("raw") or "")


def test_run_post_allows_empty_query_when_channel_set(server, monkeypatch):
    """Channel-only run: no keyword needed — pipeline pulls the
    channel's latest videos, dedup'd against vault as usual."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "FAKE")
    monkeypatch.setattr(
        "collector.adapters.youtube.YouTubeAdapter.resolve_channel",
        lambda self, raw: "UCABCDEFGHIJKLMNOPQRSTUV",
    )

    captured = {}
    started = threading.Event()

    def fake_run_query(query, **kw):
        captured["query"] = query
        captured["target_channel_id"] = kw.get("target_channel_id")
        started.set()
        return {"requested_count": kw.get("count", 0), "promoted": 0,
                "processed": 0, "candidates": 0, "skipped_duplicates": 0,
                "per_video": []}

    monkeypatch.setattr("collector.cli.run.run_query", fake_run_query)

    status, body = _post(server.url("/api/run"), {
        "query": "",  # blank — allowed because channel is set
        "count": 5,
        "channel_id": "https://www.youtube.com/@somechan",
    })
    assert status == 202, body
    assert started.wait(timeout=5)
    assert captured.get("query") == ""
    assert captured.get("target_channel_id") == "UCABCDEFGHIJKLMNOPQRSTUV"


def test_run_post_400_when_neither_query_nor_channel(server):
    s, body = _post(server.url("/api/run"), {"query": "", "count": 5})
    assert s == 400
    assert "검색어" in body or "채널" in body


def test_youtube_search_skips_livestreams(monkeypatch):
    """liveBroadcastContent != 'none' results have no captions yet —
    they'd waste yt-dlp calls and produce 100% YT_NO_TRANSCRIPT runs.
    The adapter must drop them at the search step."""
    from collector.adapters.youtube import YouTubeAdapter
    payload = {
        "items": [
            {"id": {"videoId": "VID_LIVE"}, "snippet": {
                "channelId": "UCx", "title": "Live", "publishedAt": "2026-05-05T00:00:00Z",
                "liveBroadcastContent": "live"}},
            {"id": {"videoId": "VID_UPCOMING"}, "snippet": {
                "channelId": "UCx", "title": "Premiere", "publishedAt": "2026-05-05T00:00:00Z",
                "liveBroadcastContent": "upcoming"}},
            {"id": {"videoId": "VID_OK"}, "snippet": {
                "channelId": "UCx", "title": "Normal", "publishedAt": "2026-05-05T00:00:00Z",
                "liveBroadcastContent": "none"}},
            {"id": {"videoId": "VID_LEGACY"}, "snippet": {
                "channelId": "UCx", "title": "No field", "publishedAt": "2026-05-05T00:00:00Z"}},
        ]
    }
    yt = YouTubeAdapter("KEY", http=lambda *a, **k:
                        {"status": 200, "headers": {}, "body": json.dumps(payload)})
    out = yt.search({"topic": "x", "exclude_terms": [], "max_results": 10})
    ids = sorted(c["video_id"] for c in out)
    # Only non-live items should remain. Missing field defaults to 'none' too.
    assert ids == ["VID_LEGACY", "VID_OK"]


def test_enrich_stats_populates_duration_sec(monkeypatch):
    """contentDetails.duration is parsed into duration_sec so callers
    can drop Shorts upfront."""
    from collector.adapters.youtube import YouTubeAdapter

    def fake_http(method, url, **kw):
        if "videos" in url:
            return {"status": 200, "headers": {}, "body": json.dumps({
                "items": [
                    {"id": "VID_SHORT", "statistics": {"viewCount": "10"},
                     "contentDetails": {"duration": "PT45S"}},
                    {"id": "VID_LONG", "statistics": {"viewCount": "100"},
                     "contentDetails": {"duration": "PT12M30S"}},
                ]
            })}
        return {"status": 200, "headers": {}, "body": json.dumps({"items": []})}

    yt = YouTubeAdapter("KEY", http=fake_http)
    cands = [{"video_id": "VID_SHORT", "channel_id": "UC1"},
             {"video_id": "VID_LONG", "channel_id": "UC1"}]
    yt.enrich_stats(cands)
    by_id = {c["video_id"]: c for c in cands}
    assert by_id["VID_SHORT"]["duration_sec"] == 45
    assert by_id["VID_LONG"]["duration_sec"] == 750  # 12m30s


def test_parse_iso8601_duration():
    from collector.adapters.youtube import YouTubeAdapter
    f = YouTubeAdapter._parse_iso8601_duration
    assert f("PT45S") == 45
    assert f("PT3M") == 180
    assert f("PT1H2M3S") == 3723
    assert f("") == 0
    assert f("garbage") == 0


def test_youtube_search_omits_q_param_when_channel_only(monkeypatch):
    """Channel-only browse: drop `q` so the API returns the channel's
    actual recent uploads (not noise from an empty query) and order=date."""
    from collector.adapters.youtube import YouTubeAdapter

    seen_params: list[dict] = []

    def fake_http(method, url):
        from urllib.parse import urlparse, parse_qs
        seen_params.append({k: v[0] for k, v in parse_qs(urlparse(url).query).items()})
        return {"status": 200, "headers": {}, "body": json.dumps({"items": []})}

    yt = YouTubeAdapter(api_key="K", http=fake_http)
    yt.search({
        "topic": "",
        "exclude_terms": [],
        "max_results": 5,
        "target_channel_id": "UCxxx",
    })
    assert seen_params, "no API call made"
    p = seen_params[0]
    assert "q" not in p, f"q should be omitted: {p}"
    assert p.get("channelId") == "UCxxx"
    assert p.get("order") == "date"


def test_run_post_400_when_channel_unresolvable(server, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "FAKE")
    monkeypatch.setattr(
        "collector.adapters.youtube.YouTubeAdapter.resolve_channel",
        lambda self, raw: None,
    )
    status, body = _post(server.url("/api/run"),
                         {"query": "x", "channel_id": "garbage"})
    assert status == 400
    assert "인식" in body or "channel" in body


def test_api_events_returns_latest_run_by_default(server, layout):
    """When run_id is omitted, /api/events should return only the
    most-recently-started run's events. Older runs must not leak in
    (or the cohort replay would mix runs)."""
    events_path = layout["logs"] / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("\n".join([
        json.dumps({"run_id": "OLD", "entity_type": "run",
                    "to_status": "running", "recorded_at": "2026-01-01T00:00:00Z"}),
        json.dumps({"run_id": "OLD", "entity_type": "stage",
                    "entity_id": "v1:collect", "to_status": "completed",
                    "recorded_at": "2026-01-01T00:00:01Z"}),
        json.dumps({"run_id": "NEW", "entity_type": "run",
                    "to_status": "running", "recorded_at": "2026-05-05T00:00:00Z"}),
        json.dumps({"run_id": "NEW", "entity_type": "stage",
                    "entity_id": "v2:collect", "to_status": "started",
                    "recorded_at": "2026-05-05T00:00:01Z"}),
    ]) + "\n", encoding="utf-8")

    status, _h, body = _get(server.url("/api/events"))
    assert status == 200
    obj = json.loads(body)
    assert obj["run_id"] == "NEW"
    assert all(e["run_id"] == "NEW" for e in obj["events"])
    assert any(e.get("entity_id") == "v2:collect" for e in obj["events"])


def test_api_events_filters_by_run_id(server, layout):
    events_path = layout["logs"] / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("\n".join([
        json.dumps({"run_id": "A", "entity_type": "run",
                    "to_status": "running", "recorded_at": "2026-01-01T00:00:00Z"}),
        json.dumps({"run_id": "A", "entity_type": "stage",
                    "entity_id": "vA:collect", "to_status": "completed",
                    "recorded_at": "2026-01-01T00:00:01Z"}),
        json.dumps({"run_id": "B", "entity_type": "run",
                    "to_status": "running", "recorded_at": "2026-02-01T00:00:00Z"}),
    ]) + "\n", encoding="utf-8")

    status, _h, body = _get(server.url("/api/events?run_id=A"))
    obj = json.loads(body)
    assert obj["run_id"] == "A"
    assert all(e["run_id"] == "A" for e in obj["events"])


def test_api_events_returns_empty_when_no_events(server):
    status, _h, body = _get(server.url("/api/events"))
    assert status == 200
    obj = json.loads(body)
    assert obj["events"] == []


def test_api_records_returns_local_data_store(server, layout):
    """Local-mode dashboard reads records via /api/records, not GitHub."""
    ds = layout["data_store"]
    (ds / "202604").mkdir(parents=True)
    rec = {
        "source_key": "youtube:LOCAL00001",
        "video_id": "LOCAL00001",
        "title": "로컬 테스트",
        "record_status": "promoted",
        "confidence": "confirmed",
        "transcript": "이건 응답에서 제거되어야 함",
        "summary": "요약",
        "rules": ["r1"],
        "tags": ["t1"],
        "llm_context": {"cost_usd": 0.0001},
        "collected_at": "2026-04-25T12:00:00Z",
    }
    (ds / "202604" / "youtube__LOCAL00001.json").write_text(
        json.dumps(rec, ensure_ascii=False), encoding="utf-8"
    )
    status, _h, body = _get(server.url("/api/records?limit=10"))
    assert status == 200
    obj = json.loads(body)
    assert obj["total_files"] == 1
    assert len(obj["records"]) == 1
    r = obj["records"][0]
    # transcript stripped (could be huge); other fields preserved
    assert "transcript" not in r
    assert r["source_key"] == "youtube:LOCAL00001"
    assert r["record_status"] == "promoted"


def test_run_status_exposes_requested_count_and_channel(server, monkeypatch):
    """Dashboard relies on /api/run/status reporting the requested
    count + channel_id mid-run so it can render the funnel banner
    (요청 N · 발견 M · ...) even before the run finishes."""
    barrier = threading.Event()
    captured = {}

    def slow_run_query(query, **kw):
        captured["count"] = kw.get("count")
        captured["target_channel_id"] = kw.get("target_channel_id")
        barrier.wait(timeout=5)
        return {"requested_count": kw.get("count"), "promoted": 0}

    monkeypatch.setattr("collector.cli.run.run_query", slow_run_query)
    s, _ = _post(server.url("/api/run"),
                 {"query": "단타", "count": 7,
                  "channel_id": "UCabcDEFghi12345678JKLmn"})  # 24 chars, real shape
    assert s == 202

    # While the run is in flight, the status snapshot should already
    # carry the user-supplied denominator + channel.
    deadline = time.time() + 3
    snap = None
    while time.time() < deadline:
        _, _h, body = _get(server.url("/api/run/status"))
        snap = json.loads(body)
        if snap.get("status") == "running":
            break
        time.sleep(0.02)
    assert snap is not None and snap["status"] == "running"
    assert snap["requested_count"] == 7
    assert snap["channel_id"] == "UCabcDEFghi12345678JKLmn"
    barrier.set()


def test_youtube_search_threads_channel_id(monkeypatch):
    """Channel-restricted search must pass `channelId` through to the
    YouTube Data API so results are scoped to that channel only."""
    from collector.adapters.youtube import YouTubeAdapter

    seen_params: list[dict] = []

    def fake_http(method, url):
        from urllib.parse import urlparse, parse_qs
        seen_params.append({k: v[0] for k, v in parse_qs(urlparse(url).query).items()})
        return {"status": 200, "headers": {}, "body": json.dumps({"items": []})}

    yt = YouTubeAdapter(api_key="K", http=fake_http)
    yt.search({"topic": "단타", "max_results": 3,
               "target_channel_id": "UCxyz98765432"})
    assert seen_params, "search() did not call http"
    assert seen_params[0].get("channelId") == "UCxyz98765432"

    yt.search({"topic": "단타", "max_results": 3})
    assert "channelId" not in seen_params[1]
