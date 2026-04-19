"""Unified CLI dispatcher: `collector <subcmd> [args]`."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

SUBCOMMANDS = {
    "app": "collector.cli.app",
    "run": "collector.cli.run",
    "dashboard": "collector.cli.dashboard",
    "review": "collector.cli.review",
    "quota": "collector.cli.quota",
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
    mod = importlib.import_module(SUBCOMMANDS[cmd])
    return int(mod.main(rest) or 0)


if __name__ == "__main__":
    sys.exit(main())
