"""Circuit breaker for external services (Appendix C §5).

Rules (from the design):
- youtube_api : 5분 내 HTTP_429  3회 → 10분 차단
- llm_api    : 5분 내 LLM_TIMEOUT 3회 → 15분 차단
- git_sync   : 10분 내 GIT_AUTH_FAIL 2회 → 30분 차단

State persisted to `state/breakers.json` so the breaker survives process
restarts. File is atomic-written via temp+rename.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Policy:
    window_sec: int
    threshold: int
    cooldown_sec: int


POLICIES: dict[str, dict[str, Policy]] = {
    "youtube_api": {"HTTP_429": Policy(300, 3, 600)},
    "llm_api":     {"LLM_TIMEOUT": Policy(300, 3, 900)},
    "git_sync":    {"GIT_AUTH_FAIL": Policy(600, 2, 1800)},
}


class CircuitOpen(Exception):
    def __init__(self, service: str, until: float):
        super().__init__(f"circuit open: {service} until {until}")
        self.service = service
        self.until = until


def _atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".brk.", dir=str(path.parent), suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def check(service: str, *, root: Path = Path("state")) -> None:
    """Raise CircuitOpen if the breaker for `service` is currently open."""
    state = _read(root / "breakers.json").get(service) or {}
    until = float(state.get("open_until", 0))
    if until > time.time():
        raise CircuitOpen(service, until)


def record_failure(service: str, code: str, *, root: Path = Path("state"), now: float | None = None) -> bool:
    """Record a failure. Returns True if this failure tripped the breaker."""
    policy = POLICIES.get(service, {}).get(code)
    if policy is None:
        return False
    now = now or time.time()
    path = root / "breakers.json"
    state = _read(path)
    svc = state.setdefault(service, {"failures": [], "open_until": 0})
    # drop failures outside the window
    svc["failures"] = [t for t in svc["failures"] if now - t <= policy.window_sec] + [now]
    tripped = False
    if len(svc["failures"]) >= policy.threshold:
        svc["open_until"] = now + policy.cooldown_sec
        svc["failures"] = []
        tripped = True
    _atomic_write(path, state)
    return tripped


def record_success(service: str, *, root: Path = Path("state")) -> None:
    """Reset the window on a successful call."""
    path = root / "breakers.json"
    state = _read(path)
    if service in state:
        state[service]["failures"] = []
        _atomic_write(path, state)


def open_until(service: str, *, root: Path = Path("state")) -> float:
    return float((_read(root / "breakers.json").get(service) or {}).get("open_until", 0))
