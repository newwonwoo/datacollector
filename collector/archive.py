"""Archive by quarter (Master_03 §3).

Moves promoted records whose `collected_at` falls in a past quarter from
`data_store/YYYYMM/` to `archive/YYYY_QN/YYYYMM/`. Flips archive_state to
ARCHIVED (still discoverable by dedup which checks ACTIVE + ARCHIVED).
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Iterable


def quarter_of(month: int) -> int:
    return (month - 1) // 3 + 1


def month_in_quarter(month: int, quarter: int) -> bool:
    start = (quarter - 1) * 3 + 1
    return start <= month <= start + 2


def archive_quarter(
    data_store_root: Path,
    archive_root: Path,
    *,
    year: int,
    quarter: int,
) -> list[Path]:
    """Move files whose `collected_at` yyyy-mm is inside (year, quarter).

    Returns list of target paths actually moved.
    """
    moved: list[Path] = []
    if not data_store_root.exists():
        return moved
    for src in Path(data_store_root).rglob("*.json"):
        try:
            rec = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            continue
        col = rec.get("collected_at", "")
        if not col or len(col) < 7:
            continue
        try:
            y, m = int(col[:4]), int(col[5:7])
        except ValueError:
            continue
        if y != year or not month_in_quarter(m, quarter):
            continue
        # Move + flip archive_state
        target_dir = Path(archive_root) / f"{year}_Q{quarter}" / f"{y:04d}{m:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / src.name
        rec["archive_state"] = "ARCHIVED"
        target.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        src.unlink(missing_ok=True)
        moved.append(target)
    return moved


def archive_quarter_markdown(
    vault_root: Path,
    archive_root: Path,
    *,
    year: int,
    quarter: int,
) -> list[Path]:
    """Move vault/strategies/*.md whose frontmatter `collected` falls in
    the target (year, quarter) to `archive_root/<year>_Q<quarter>/`.

    README.md is regenerated after the move (caller's responsibility).
    Returns list of target paths.
    """
    import re

    moved: list[Path] = []
    strat = Path(vault_root) / "strategies"
    if not strat.exists():
        return moved

    for src in strat.glob("*.md"):
        text = src.read_text(encoding="utf-8")
        # Extract collected: YYYY-MM-DD... from frontmatter
        m = re.search(r"^collected:\s*(\S+)", text, flags=re.MULTILINE)
        if not m:
            continue
        iso = m.group(1)
        if len(iso) < 7:
            continue
        try:
            y, mo = int(iso[:4]), int(iso[5:7])
        except ValueError:
            continue
        if y != year or not month_in_quarter(mo, quarter):
            continue
        target_dir = Path(archive_root) / f"{year}_Q{quarter}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / src.name
        target.write_text(text, encoding="utf-8")
        src.unlink(missing_ok=True)
        moved.append(target)
    return moved


def current_year_quarter(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    return today.year, quarter_of(today.month)


def previous_quarter(today: date | None = None) -> tuple[int, int]:
    y, q = current_year_quarter(today)
    return (y - 1, 4) if q == 1 else (y, q - 1)
