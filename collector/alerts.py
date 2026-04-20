"""Alert evaluator + GitHub Issue emitter (Master_03 §7).

Inputs a daily metrics stream (jsonl) and emits alerts when thresholds trip.
Emission is pluggable: default uses GitHub Issues via `gh` CLI or urllib.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


@dataclass
class Alert:
    code: str
    severity: str       # info | warning | critical
    title: str
    body: str


# ---------- Threshold evaluators ----------

def _ratio(num: int, den: int) -> float:
    return (num / den) if den > 0 else 0.0


def evaluate(
    dailies: list[dict[str, Any]],
    *,
    failed_ratio_threshold: float = 0.10,
    failed_consecutive_days: int = 3,
    sync_failed_cumulative: int = 20,
    runtime_multiplier: float = 2.0,
) -> list[Alert]:
    alerts: list[Alert] = []
    if not dailies:
        return alerts
    dailies = sorted(dailies, key=lambda r: r.get("date", ""))

    # 1) FAILED ratio > 10% for `failed_consecutive_days` consecutive days
    bad_streak = 0
    for row in dailies[-failed_consecutive_days - 1:]:
        total = row.get("runs_completed", 0) + row.get("runs_partial", 0) + row.get("runs_failed", 0)
        r = _ratio(row.get("runs_failed", 0), total)
        bad_streak = bad_streak + 1 if r > failed_ratio_threshold else 0
    if bad_streak >= failed_consecutive_days:
        alerts.append(Alert(
            code="FAILED_RATIO_HIGH",
            severity="critical",
            title=f"FAILED 비율 연속 {failed_consecutive_days}일 > {int(failed_ratio_threshold*100)}%",
            body=json.dumps(dailies[-failed_consecutive_days:], ensure_ascii=False, indent=2),
        ))

    # 2) SYNC_FAILED cumulative across recent window
    sync_cum = sum(r.get("sync_failed", 0) for r in dailies[-7:])
    if sync_cum >= sync_failed_cumulative:
        alerts.append(Alert(
            code="SYNC_FAILED_CUMULATIVE",
            severity="warning",
            title=f"SYNC_FAILED 7일 누적 {sync_cum} ≥ {sync_failed_cumulative}",
            body=f"Git/파일시스템 점검 필요. 최근 7일: {sync_cum} 건",
        ))

    # 3) avg_runtime_sec 급증 (N일 평균 대비 >= multiplier)
    recent = [r.get("avg_runtime_sec", 0) for r in dailies[-7:] if r.get("avg_runtime_sec", 0) > 0]
    today = dailies[-1].get("avg_runtime_sec", 0)
    if len(recent) >= 3 and today > 0:
        baseline = sum(recent[:-1]) / max(1, len(recent) - 1)
        if baseline > 0 and today >= runtime_multiplier * baseline:
            alerts.append(Alert(
                code="RUNTIME_SPIKE",
                severity="warning",
                title=f"평균 처리 시간 급증 ({today:.1f}s vs baseline {baseline:.1f}s)",
                body="성능 점검 필요",
            ))

    return alerts


# ---------- Emitters ----------

def emit_github_issue(
    alert: Alert,
    *,
    owner: str,
    repo: str,
    token: str,
    http: Callable | None = None,
) -> dict[str, Any]:
    """Create a GitHub Issue for the alert. Returns API response dict."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    body = json.dumps({
        "title": f"[{alert.severity}] {alert.title}",
        "body": f"**{alert.code}**\n\n{alert.body}",
        "labels": [f"alert:{alert.code}", alert.severity],
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "collector-alerts",
    }
    if http is not None:
        return http("POST", url, headers=headers, data=body)
    # default urllib
    req = urllib.request.Request(url, method="POST", headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}


def emit_stdout(alert: Alert) -> None:
    print(f"[{alert.severity.upper()}] {alert.code}: {alert.title}")
    print(alert.body)
    print("---")


def emit_slack(
    alert: Alert,
    *,
    webhook_url: str,
    http: Callable | None = None,
) -> dict[str, Any]:
    """Post the alert to a Slack incoming webhook URL.

    webhook_url is typically sourced from env SLACK_ALERT_URL.
    """
    color = {"critical": "#dc2626", "warning": "#d97706", "info": "#2563eb"}.get(alert.severity, "#6b7280")
    body = json.dumps({
        "attachments": [{
            "color": color,
            "title": f"[{alert.severity.upper()}] {alert.title}",
            "text": alert.body,
            "fields": [{"title": "code", "value": alert.code, "short": True}],
        }],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if http is not None:
        return http("POST", webhook_url, headers=headers, data=body)
    req = urllib.request.Request(webhook_url, method="POST", headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": e.read().decode("utf-8", "replace")}
