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


def _videos_list_resp(ids: list[str], duration: str = "PT10M0S") -> dict:
    body = {"items": [{"id": vid, "contentDetails": {"duration": duration}} for vid in ids]}
    return {"status": 200, "body": json.dumps(body)}


def _route(method, url, search_pages, video_pages_box):
    if "youtube/v3/search" in url:
        return search_pages.pop(0)
    if "youtube/v3/videos" in url:
        if video_pages_box:
            return video_pages_box.pop(0)
        return {"status": 200, "body": json.dumps({"items": []})}
    raise AssertionError(f"unexpected url {url}")


def test_search_paginates_when_count_exceeds_page_size():
    search_pages = [
        _mk_resp([{"id": f"vid_{i}"} for i in range(50)], next_page_token="page2"),
        _mk_resp([{"id": f"vid_{i}"} for i in range(50, 100)], next_page_token=None),
    ]
    videos_pages = [
        _videos_list_resp([f"vid_{i}" for i in range(50)]),
        _videos_list_resp([f"vid_{i}" for i in range(50, 100)]),
    ]
    search_calls = []
    def fake_http(method, url, **kw):
        if "youtube/v3/search" in url:
            search_calls.append(url)
        return _route(method, url, search_pages, videos_pages)
    yt = YouTubeAdapter("KEY", http=fake_http)
    results = yt.search({"topic": "단타", "max_results": 100})
    assert len(results) == 100
    assert len(search_calls) == 2
    assert "pageToken=page2" in search_calls[1]


def test_search_respects_max_results_cap():
    search_pages = [
        _mk_resp([{"id": f"vid_{i}"} for i in range(50)], next_page_token="page2"),
        _mk_resp([{"id": f"vid_{i}"} for i in range(50, 100)], next_page_token=None),
    ]
    videos_pages = [
        _videos_list_resp([f"vid_{i}" for i in range(50)]),
        _videos_list_resp([f"vid_{i}" for i in range(50, 60)]),
    ]
    def fake_http(method, url, **kw):
        return _route(method, url, search_pages, videos_pages)
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.search({"topic": "x", "max_results": 60})
    assert len(out) == 60


def test_search_stops_when_no_next_page():
    search_pages = [_mk_resp([{"id": f"v{i}"} for i in range(20)], next_page_token=None)]
    videos_pages = [_videos_list_resp([f"v{i}" for i in range(20)])]
    def fake_http(method, url, **kw):
        return _route(method, url, search_pages, videos_pages)
    yt = YouTubeAdapter("KEY", http=fake_http)
    out = yt.search({"topic": "x", "max_results": 100})
    assert len(out) == 20


def test_search_default_max_results_25():
    search_calls = {"n": 0}
    search_pages = [_mk_resp([{"id": f"v{i}"} for i in range(25)], next_page_token=None)]
    videos_pages = [_videos_list_resp([f"v{i}" for i in range(25)])]
    def fake_http(method, url, **kw):
        if "youtube/v3/search" in url:
            search_calls["n"] += 1
            assert "maxResults=25" in url
        return _route(method, url, search_pages, videos_pages)
    yt = YouTubeAdapter("KEY", http=fake_http)
    yt.search({"topic": "x"})  # no max_results → default 25
    assert search_calls["n"] == 1
