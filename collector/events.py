"""Append-only event logger (events.jsonl)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .payload import utcnow_iso


class EventLogger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []

    def log(
        self,
        *,
        entity_type: str,
        entity_id: str,
        from_status: str | None,
        to_status: str,
        run_id: str,
        reason: str = "",
        metrics: dict[str, Any] | None = None,
        actor: str = "worker-test",
    ) -> dict[str, Any]:
        event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "run_id": run_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
            "metrics": metrics or {},
            "actor": actor,
            "recorded_at": utcnow_iso(),
        }
        self.events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def filter(self, **kw) -> list[dict[str, Any]]:
        out = self.events
        for k, v in kw.items():
            out = [e for e in out if e.get(k) == v]
        return out
