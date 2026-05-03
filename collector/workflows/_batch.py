"""Step 2 — execute many run_query calls with bounded concurrency.

WARP / Cloudflare detection auto-drops concurrency to 1 (treats those
IPs like cloud, where YouTube blocks fast). Residential default is 3.
"""
from __future__ import annotations

import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable


_CLOUDFLARE_PREFIXES = (
    # Coarse but covers WARP egress and most CF datacenter ranges.
    "104.16.", "104.17.", "104.18.", "104.19.", "104.20.", "104.21.",
    "104.22.", "104.23.", "104.24.", "104.25.", "104.26.", "104.27.",
    "104.28.", "104.29.", "104.30.", "104.31.",
    "162.158.", "162.159.",
    "172.64.", "172.65.", "172.66.", "172.67.", "172.68.", "172.69.",
    "172.70.", "172.71.",
)


def _detect_warp(timeout: float = 2.0) -> bool:
    """Best-effort guess. Returns True when our public IP looks like a
    Cloudflare egress (= WARP / generic CF tunnel). On failure, False
    so we don't over-throttle a normal residential network."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://ifconfig.me/ip", timeout=timeout) as r:
            ip = r.read().decode("utf-8").strip()
        return any(ip.startswith(p) for p in _CLOUDFLARE_PREFIXES)
    except Exception:  # noqa: BLE001
        return False


def _default_concurrency() -> int:
    return 1 if _detect_warp() else 3


def research_batch(
    keywords: list[str],
    *,
    count_per_keyword: int = 10,
    max_concurrency: int | None = None,
    min_views: int = 0,
    min_subscribers: int = 0,
    data_store_root: Path = Path("data_store"),
    logs_root: Path = Path("logs"),
    progress_cb: Callable[[str, dict], None] | None = None,
) -> list[dict[str, Any]]:
    """Run `collector.cli.run.run_query` for every keyword. Returns a
    list of summary dicts in the order keywords completed.

    `max_concurrency=None` enables WARP-aware auto-detect (1 if WARP,
    3 otherwise). Pass an int to force a specific level.

    `progress_cb(keyword, summary)` if provided is called after each
    keyword completes (worker thread context).
    """
    from ..cli.run import run_query

    if not keywords:
        return []

    if max_concurrency is None:
        max_concurrency = _default_concurrency()
    max_concurrency = max(1, min(max_concurrency, 5))

    sys.stderr.write(
        f"[workflow] research_batch: {len(keywords)} keywords × "
        f"{count_per_keyword} videos, concurrency={max_concurrency}\n"
    )

    results: list[dict[str, Any]] = []

    def _one(kw: str, stagger: float) -> dict[str, Any]:
        if stagger:
            time.sleep(stagger)
        try:
            return run_query(
                kw,
                count=count_per_keyword,
                data_store_root=data_store_root,
                logs_root=logs_root,
                min_views=min_views,
                min_subscribers=min_subscribers,
            )
        except Exception as e:  # noqa: BLE001
            return {"query": kw, "error": str(e), "candidates": 0,
                    "processed": 0, "promoted": 0}

    # Stagger the very first wave so workers don't simultaneously hit
    # YouTube on second 0.
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {}
        for i, kw in enumerate(keywords):
            stagger = 0.0 if i < max_concurrency else 0.0
            # Spread the first `max_concurrency` worker starts across 0–2s
            stagger = (i % max_concurrency) * (2.0 / max_concurrency) if i < max_concurrency else 0.0
            futures[pool.submit(_one, kw, stagger)] = kw

        for fut in as_completed(futures):
            kw = futures[fut]
            summary = fut.result()
            results.append(summary)
            if progress_cb is not None:
                try:
                    progress_cb(kw, summary)
                except Exception:  # noqa: BLE001
                    pass

    return results
