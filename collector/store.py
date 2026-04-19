"""In-memory + optional JSON-file store with Rule A/B/C dedup + archive."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JSONStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else None
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
        # Promotion / normal path stays in ACTIVE
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

    def mark_removed(self, source_key: str) -> None:
        rec = self.active.get(source_key) or self.archived.get(source_key)
        if rec:
            rec["archive_state"] = "REMOVED"

    def send_to_dlq(self, payload: dict[str, Any], code: str) -> None:
        self.dlq.append({"code": code, "payload": payload})

    def _flush(self, payload: dict[str, Any]) -> None:
        if not self.root:
            return
        yyyymm = payload.get("collected_at", "")[:7].replace("-", "") or "unknown"
        p = self.root / yyyymm
        p.mkdir(parents=True, exist_ok=True)
        (p / f"{payload['source_key'].replace(':', '__')}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
