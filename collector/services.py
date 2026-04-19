"""Pluggable services + default mocks (YouTube, LLM, Git)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class MockError(Exception):
    """Raised by mocks to simulate infra errors. Carries a failure code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class Services:
    # Discover
    youtube_search: Callable[[dict], list[dict]] = field(
        default_factory=lambda: (lambda q: [])
    )
    # Collect: returns {"source": manual|asr|none, "text": str}
    youtube_captions: Callable[[str], dict] = field(
        default_factory=lambda: (lambda vid: {"source": "none", "text": ""})
    )
    # Check video still exists (for removal detection)
    youtube_video_alive: Callable[[str], bool] = field(
        default_factory=lambda: (lambda vid: True)
    )
    # Extract: returns dict with summary/rules/tags or raises MockError on schema fail
    llm_extract: Callable[[str, int], dict] = field(
        default_factory=lambda: (lambda text, attempt: {"summary": "", "rules": [], "tags": []})
    )
    # Optional semantic similarity (0-1). Default 0.8 = confirmed-likely.
    semantic_similarity: Callable[[str, str], float] = field(
        default_factory=lambda: (lambda src, summary: 0.8)
    )
    # Git sync (raises MockError on failure)
    git_sync: Callable[[dict], None] = field(
        default_factory=lambda: (lambda payload: None)
    )


def build_mock_services(
    *,
    search_results: list[dict] | None = None,
    captions_map: dict[str, dict] | None = None,
    alive_map: dict[str, bool] | None = None,
    llm_script: list[Any] | None = None,
    similarity: float = 0.8,
    git_script: list[Any] | None = None,
) -> Services:
    """Build Services where each callable follows a scripted behaviour.

    - `llm_script` is a list applied across attempts. Elements: dict (success), MockError (raise).
    - `git_script` similarly; on each call pop index until exhausted, then use last.
    """
    search_results = search_results or []
    captions_map = captions_map or {}
    alive_map = alive_map or {}
    llm_script = list(llm_script or [{"summary": "좋은 요약", "rules": ["규칙1"], "tags": ["t"]}])
    git_script = list(git_script or [None])

    llm_state = {"i": 0}
    git_state = {"i": 0}

    def yt_search(q: dict) -> list[dict]:
        return list(search_results)

    def yt_captions(vid: str) -> dict:
        out = captions_map.get(vid)
        if isinstance(out, MockError):
            raise out
        return out or {"source": "none", "text": ""}

    def yt_alive(vid: str) -> bool:
        return alive_map.get(vid, True)

    def llm(text: str, attempt: int) -> dict:
        i = min(llm_state["i"], len(llm_script) - 1)
        llm_state["i"] += 1
        step = llm_script[i]
        if isinstance(step, MockError):
            raise step
        return step

    def sim(src: str, summary: str) -> float:
        return similarity

    def git(payload: dict) -> None:
        i = min(git_state["i"], len(git_script) - 1)
        git_state["i"] += 1
        step = git_script[i]
        if isinstance(step, MockError):
            raise step
        return None

    return Services(
        youtube_search=yt_search,
        youtube_captions=yt_captions,
        youtube_video_alive=yt_alive,
        llm_extract=llm,
        semantic_similarity=sim,
        git_sync=git,
    )
