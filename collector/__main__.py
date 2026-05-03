"""Unified CLI dispatcher: `collector <subcmd> [args]`."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

SUBCOMMANDS: dict[str, tuple[str, str]] = {
    "app": ("collector.cli.app", "main"),
    "run": ("collector.cli.run", "main"),
    "dashboard": ("collector.cli.dashboard", "main"),
    "review": ("collector.cli.review", "main"),
    "quota": ("collector.cli.quota", "main"),
    "metrics": ("collector.cli.metrics_cli", "main_metrics"),
    "traces": ("collector.cli.metrics_cli", "main_traces"),
    "alerts": ("collector.cli.alerts_cli", "main"),
    "aggregate": ("collector.cli.extras_cli", "main_aggregate"),
    "archive": ("collector.cli.extras_cli", "main_archive"),
    "replay": ("collector.cli.replay_cli", "main"),
    "status": ("collector.cli.status_cli", "main"),
    "apitest": ("collector.cli.apitest_cli", "main"),
    "workflow": ("collector.cli.workflow", "main"),
    "mcp": ("collector.cli.mcp_server", "main"),
}


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from .env (cwd or repo root) into os.environ.

    Existing env vars take precedence. Lines starting with `#` are ignored.
    No external dependency.
    """
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for p in candidates:
        if not p.exists():
            continue
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except OSError:
            continue
        break

USAGE = """\
collector — YouTube data collector (v10)

Usage:
  collector app         원클릭 런처 (대시보드 빌드 + 로컬 서버 + 브라우저 오픈)
  collector run         검색어로 파이프라인 실행 (--query "단타매매전략")
  collector dashboard   SQLite 인덱스 + HTML 리포트 생성만
  collector review      review_queue/* 대화형 리뷰
  collector quota       runner-minute / 쿼터 / LLM 비용 점검
  collector metrics     events.jsonl + data_store → metrics/daily.jsonl 집계
  collector traces      events.jsonl → logs/traces.jsonl per-run 타임라인
  collector alerts      daily metrics 평가 + GitHub Issue 발행 (옵션)
  collector aggregate   다영상 집계 (--tags 단타,돌파) → aggregates/*.json
  collector archive     지난 분기 기록을 archive/YYYY_QN/ 으로 이동
  collector replay      DLQ 재처리 (dlq/* → pipeline 재실행)
  collector status      운영 상태 JSON 스냅샷 (dashboard용)
  collector apitest     YouTube/LLM API 연결 진단 (captions 경로별 개별 호출)
  collector workflow    한 줄 워크플로 (brainstorm·research-batch·synthesize·export·full)
  collector mcp         MCP 서버 모드 (Claude Desktop / Cursor / AntiGravity 등에서 자율 호출)

각 서브커맨드에 --help 를 붙이면 세부 옵션을 볼 수 있다.
"""


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in SUBCOMMANDS:
        print(f"unknown subcommand: {cmd}\n\n{USAGE}", file=sys.stderr)
        return 2
    modpath, fnname = SUBCOMMANDS[cmd]
    mod = importlib.import_module(modpath)
    fn = getattr(mod, fnname)
    return int(fn(rest) or 0)


if __name__ == "__main__":
    sys.exit(main())
