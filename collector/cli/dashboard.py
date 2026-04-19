"""SQLite sidecar + single-file HTML dashboard."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from html import escape
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    source_key TEXT PRIMARY KEY,
    video_id TEXT,
    title TEXT,
    record_status TEXT,
    archive_state TEXT,
    confidence TEXT,
    reviewer TEXT,
    transcript_hash TEXT,
    payload_version INTEGER,
    failure_reason_code TEXT,
    cost_usd REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    collected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(record_status);
CREATE INDEX IF NOT EXISTS idx_records_archive ON records(archive_state);
CREATE INDEX IF NOT EXISTS idx_records_code ON records(failure_reason_code);
"""


def _iter_payloads(data_store: Path) -> Iterable[dict[str, Any]]:
    for p in Path(data_store).rglob("*.json"):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue


def build_index(data_store: Path, db_path: Path) -> int:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.execute("DELETE FROM records")
        rows = []
        for p in _iter_payloads(data_store):
            llm = p.get("llm_context") or {}
            rows.append((
                p["source_key"], p.get("video_id", ""), p.get("title", ""),
                p.get("record_status", ""), p.get("archive_state", ""),
                p.get("confidence", ""), p.get("reviewer", ""),
                p.get("transcript_hash", ""),
                int(p.get("payload_version", 1)),
                p.get("failure_reason_code"),
                float(llm.get("cost_usd", 0.0)),
                int(llm.get("input_tokens", 0)),
                int(llm.get("output_tokens", 0)),
                p.get("collected_at", ""),
            ))
        con.executemany(
            "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
        con.commit()
        return len(rows)
    finally:
        con.close()


def _query(con: sqlite3.Connection, sql: str) -> list[tuple]:
    return list(con.execute(sql).fetchall())


def build_dashboard(db_path: Path, out_html: Path) -> Path:
    con = sqlite3.connect(db_path)
    try:
        totals = _query(con, "SELECT COUNT(*), COALESCE(SUM(cost_usd),0) FROM records")[0]
        by_status = _query(con, "SELECT record_status, COUNT(*) FROM records GROUP BY record_status ORDER BY 2 DESC")
        by_archive = _query(con, "SELECT archive_state, COUNT(*) FROM records GROUP BY archive_state")
        by_fail = _query(con, "SELECT COALESCE(failure_reason_code,'(none)'), COUNT(*) FROM records GROUP BY failure_reason_code ORDER BY 2 DESC")
        recent = _query(con, "SELECT collected_at, source_key, record_status, confidence FROM records ORDER BY collected_at DESC LIMIT 20")
    finally:
        con.close()

    def table(rows, headers):
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>"
            for r in rows
        )
        head = "".join(f"<th>{escape(h)}</th>" for h in headers)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Collector Dashboard</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;margin:24px;color:#222}}
h1{{margin-top:0}} h2{{margin-top:28px}}
table{{border-collapse:collapse;margin-top:8px}}
td,th{{border:1px solid #ddd;padding:6px 10px;font-size:14px}}
th{{background:#f5f5f5;text-align:left}}
.kpi{{display:flex;gap:16px;margin:12px 0}}
.kpi div{{background:#f0f7ff;border:1px solid #cfe;padding:12px 16px;border-radius:8px}}
</style></head><body>
<h1>Collector Dashboard</h1>
<div class="kpi">
  <div><b>{totals[0]}</b><br>total records</div>
  <div><b>${totals[1]:.4f}</b><br>llm cost</div>
</div>
<h2>By record_status</h2>{table(by_status, ["status","count"])}
<h2>By archive_state</h2>{table(by_archive, ["archive","count"])}
<h2>By failure_reason_code</h2>{table(by_fail, ["code","count"])}
<h2>Recent 20</h2>{table(recent, ["collected_at","source_key","record_status","confidence"])}
</body></html>"""
    Path(out_html).parent.mkdir(parents=True, exist_ok=True)
    Path(out_html).write_text(html, encoding="utf-8")
    return Path(out_html)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector-dashboard")
    ap.add_argument("--data-store", default="data_store")
    ap.add_argument("--db", default="index/collector.sqlite")
    ap.add_argument("--html", default="index/dashboard.html")
    args = ap.parse_args(argv)
    n = build_index(Path(args.data_store), Path(args.db))
    out = build_dashboard(Path(args.db), Path(args.html))
    print(f"indexed {n} records → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
