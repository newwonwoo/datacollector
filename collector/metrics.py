"""Daily aggregation of events + store → metrics/daily.jsonl.

Master_01 §8 fields:
  date, processed, failed, retry_wait, sync_failed,
  avg_runtime_sec, cost_usd, youtube_quota_used, llm_tokens_used
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _iter_events(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _iter_payloads(root: Path) -> Iterable[dict[str, Any]]:
    if not root.exists():
        return
    for p in Path(root).rglob("*.json"):
        try:
            yield json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue


def _date_of(iso: str) -> str:
    return iso[:10] if iso else ""


def aggregate_daily(
    events_path: Path,
    data_store_root: Path,
    *,
    dates: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate by UTC date. Returns list of daily records."""
    by_date_runtime: dict[str, list[float]] = defaultdict(list)
    by_date_run_status: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_date_record_status: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    run_start: dict[str, float] = {}
    run_end: dict[str, tuple[float, str]] = {}
    run_date: dict[str, str] = {}

    for evt in _iter_events(events_path):
        d = _date_of(evt.get("recorded_at", ""))
        if not d:
            continue
        et = evt.get("entity_type")
        if et == "run":
            run_id = evt.get("run_id") or evt.get("entity_id")
            t = _parse_iso_to_epoch(evt.get("recorded_at", ""))
            if evt.get("to_status") == "running":
                run_start[run_id] = t
                run_date[run_id] = d
            elif evt.get("to_status") in ("completed", "partially_completed", "failed"):
                run_end[run_id] = (t, evt["to_status"])
                by_date_run_status[d][evt["to_status"]] += 1
        elif et == "record" and evt.get("to_status"):
            by_date_record_status[d][evt["to_status"]] += 1

    # runtime per run
    for rid, (end_t, _status) in run_end.items():
        s = run_start.get(rid)
        if s is not None:
            by_date_runtime[run_date.get(rid, "")].append(end_t - s)

    # cost + tokens from payloads (bucket by collected_at date)
    by_date_cost: dict[str, float] = defaultdict(float)
    by_date_in_tok: dict[str, int] = defaultdict(int)
    by_date_out_tok: dict[str, int] = defaultdict(int)
    by_date_rules_total: dict[str, int] = defaultdict(int)
    by_date_rules_actionable: dict[str, int] = defaultdict(int)
    by_date_fail_codes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in _iter_payloads(data_store_root):
        d = _date_of(p.get("collected_at", ""))
        if not d:
            continue
        llm = p.get("llm_context") or {}
        by_date_cost[d] += float(llm.get("cost_usd", 0) or 0)
        by_date_in_tok[d] += int(llm.get("input_tokens", 0) or 0)
        by_date_out_tok[d] += int(llm.get("output_tokens", 0) or 0)
        # Actionable Rule 비율 (QA 지표 — 간단 휴리스틱)
        for r in (p.get("rules") or []):
            by_date_rules_total[d] += 1
            if _is_actionable_rule(r):
                by_date_rules_actionable[d] += 1
        # failure code distribution
        code = p.get("failure_reason_code")
        if code:
            by_date_fail_codes[d][code] += 1

    all_dates = set(by_date_run_status) | set(by_date_runtime) | set(by_date_record_status) | set(by_date_cost)
    if dates is not None:
        all_dates &= set(dates)

    out: list[dict[str, Any]] = []
    for d in sorted(all_dates):
        runs = by_date_run_status[d]
        records = by_date_record_status[d]
        rt = by_date_runtime[d]
        rules_total = by_date_rules_total[d]
        rules_actionable = by_date_rules_actionable[d]
        actionable_ratio = (rules_actionable / rules_total) if rules_total else 0.0
        out.append({
            "date": d,
            "processed": records.get("promoted", 0),
            "failed": runs.get("failed", 0),
            "retry_wait": records.get("collected", 0),  # approximate
            "sync_failed": records.get("invalid", 0),
            "promoted": records.get("promoted", 0),
            "invalid": records.get("invalid", 0),
            "runs_completed": runs.get("completed", 0),
            "runs_partial": runs.get("partially_completed", 0),
            "runs_failed": runs.get("failed", 0),
            "avg_runtime_sec": (sum(rt) / len(rt)) if rt else 0.0,
            "cost_usd": round(by_date_cost[d], 6),
            "llm_input_tokens": by_date_in_tok[d],
            "llm_output_tokens": by_date_out_tok[d],
            "youtube_quota_used": 0,  # not tracked yet; filled by quota.jsonl if integrated
            "rules_total": rules_total,
            "rules_actionable": rules_actionable,
            "actionable_rule_ratio": round(actionable_ratio, 4),
            "failure_codes": dict(by_date_fail_codes[d]),
        })
    return out


_ACTIONABLE_HINTS = (
    # Korean verb endings / actionable keywords in trading domain
    "매수", "매도", "진입", "익절", "손절", "청산", "분할",
    "컷", "돌파", "추격", "관망", "설정", "확인",
    "한다", "하라", "시킨다", "둔다", "잡는다",
)


def _is_actionable_rule(text: str) -> bool:
    """Simple heuristic: rule contains any of the actionable keywords.

    False positives OK; used as trend indicator, not gating.
    """
    if not text:
        return False
    return any(h in text for h in _ACTIONABLE_HINTS)


def write_daily(records: list[dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Overwrite strategy: keep a single source of truth file
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return out_path


def _parse_iso_to_epoch(iso: str) -> float:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0
