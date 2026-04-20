"""Kill Switch: env COLLECTOR_PAUSED=1 honored across every stage boundary."""
from __future__ import annotations

import os


PAUSE_ENV = "COLLECTOR_PAUSED"


def is_paused() -> bool:
    """True when operator has set the kill switch.

    Honored values: "1", "true", "yes" (case-insensitive).
    """
    v = os.environ.get(PAUSE_ENV, "").strip().lower()
    return v in ("1", "true", "yes", "on")


class KillSwitchTriggered(Exception):
    """Raised by pipeline/stages when the kill switch is set mid-run."""

    def __init__(self, where: str):
        super().__init__(f"kill switch triggered at {where}")
        self.where = where
