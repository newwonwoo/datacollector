"""Pagination test for YouTubeAdapter.search — verify >25 results work."""
from __future__ import annotations

import json

from collector.adapters.youtube import YouTubeAdapter


def _mk_resp(videos: list[dict], next_page_token: str | None = None) -> dict:
    body = {"items": [], "nextPageToken": next_page_token}
    for v in videos:
        body["items"].append({
            "id": {"videoId": v["id"]},
            "snippet": {
                "channelId": v.get("channel", "CH"),
                "title": v.get("title", ""),
                "publishedAt": "2026-04-20T00:00:00Z",
            },
        })
    return {"status": 200, "body": json.dumps(body)}


def test_search_paginates_when_count_exceeds_page_size():
    pages = [
        _mk_resp([{"id": f"vid_{i}"} for i in range(50)], next_page_token="page2"),
        _mk_resp([{"id": f"vid_{i}"} for i in range(50, 100)], next_page_token=None),
    ]
    calls = []
    def fake_http(method, url, **kw):
        calls.append(url)
        return pages.pop(0)
    yt = YouTubeAdapter("KEY", http=fake_http)
    results = yt.search({"topic": "단타", "max_results": 100})
    assert len(results) == 100
    assert len(calls) == 2
    assert "pageToken=page2" in calls[1]


def test_search_respects_max_results_cap():
    pages = [
        _mk_resp([{"id": f"vid_{i}"} for i in range(50)], next_page_token="page2"),
        _mk_resp([{"id": f"vid_{i}"} for i in range(50, 100)], next_page_token=None),
    ]
    def fake_http(method, url, **kw):
        return pages.pop(0)
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.search({"topic": "x", "max_results": 60})
    assert len(out) == 60


def test_search_stops_when_no_next_page():
    only_page = _mk_resp([{"id": f"v{i}"} for i in range(20)], next_page_token=None)
    def fake_http(method, url, **kw):
        return only_page
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.search({"topic": "x", "max_results": 100})
    assert len(out) == 20


def test_search_default_max_results_25():
    called = {"n": 0}
    def fake_http(method, url, **kw):
        called["n"] += 1
        assert "maxResults=25" in url
        return _mk_resp([{"id": f"v{i}"} for i in range(25)], next_page_token=None)
    yt = YouTubeAdapter("KEY", http=fake_http)
    yt.search({"topic": "x"})  # no max_results → default 25
    assert called["n"] == 1
