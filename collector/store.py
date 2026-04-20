"""In-memory + optional JSON-file store with Rule A/B/C dedup, archive, DLQ.

Write durability:
- _flush writes via tempfile + os.replace (POSIX atomic) to avoid
  half-written JSON on crash (Master_01 §7.2).
- DLQ entries persisted to `dlq/<code>/<YYYYMMDD>/<source_key>.json` so
  process restarts don't lose the queue (Master_03 §4).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=str(path.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class JSONStore:
    def __init__(self, root: Path | None = None, *, dlq_root: Path | None = None):
        self.root = Path(root) if root else None
        self.dlq_root = Path(dlq_root) if dlq_root else (self.root.parent / "dlq" if self.root else None)
        # ACTIVE and ARCHIVED records, both keyed by source_key
        self.active: dict[str, dict[str, Any]] = {}
        self.archived: dict[str, dict[str, Any]] = {}
        self.dlq: list[dict[str, Any]] = []

    def dedup_rule(self, source_key: str, transcript_hash: str) -> str:
        """Return 'A' (new), 'B' (changed), or 'C' (duplicate).

        Looks in both ACTIVE and ARCHIVED (Master_03 §5).
        """
        existing = self.active.get(source_key) or self.archived.get(source_key)
        if existing is None:
            return "A"
        if existing.get("transcript_hash") == transcript_hash:
            return "C"
        return "B"

    def get(self, source_key: str) -> dict[str, Any] | None:
        return self.active.get(source_key) or self.archived.get(source_key)

    def upsert(self, payload: dict[str, Any]) -> None:
        sk = payload["source_key"]
        if payload.get("archive_state") == "ARCHIVED":
            self.archived[sk] = payload
            self.active.pop(sk, None)
        else:
            self.active[sk] = payload
        self._flush(payload)

    def archive(self, source_key: str) -> None:
        rec = self.active.pop(source_key, None)
        if rec:
            rec["archive_state"] = "ARCHIVED"
            self.archived[source_key] = rec
            self._flush(rec)

    def mark_removed(self, source_key: str) -> None:
        rec = self.active.get(source_key) or self.archived.get(source_key)
        if rec:
            rec["archive_state"] = "REMOVED"
            self._flush(rec)

    def send_to_dlq(self, payload: dict[str, Any], code: str) -> None:
        entry = {"code": code, "payload": payload}
        self.dlq.append(entry)
        if self.dlq_root is not None:
            yyyymmdd = datetime.now(timezone.utc).strftime("%Y%m%d")
            path = self.dlq_root / code / yyyymmdd / f"{payload['source_key'].replace(':', '__')}.json"
            _atomic_write_json(path, entry)

    def _flush(self, payload: dict[str, Any]) -> None:
        if not self.root:
            return
        yyyymm = payload.get("collected_at", "")[:7].replace("-", "") or "unknown"
        path = self.root / yyyymm / f"{payload['source_key'].replace(':', '__')}.json"
        _atomic_write_json(path, payload)
