"""CLI: `collector alerts` — evaluate metrics + optionally open GitHub issues."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..alerts import Alert, emit_github_issue, emit_stdout, evaluate


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector alerts")
    ap.add_argument("--metrics", default="metrics/daily.jsonl")
    ap.add_argument("--emit", choices=["stdout", "github"], default="stdout")
    ap.add_argument("--owner", default=None)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--token-env", default="GITHUB_TOKEN")
    args = ap.parse_args(argv)

    dailies = _load_jsonl(Path(args.metrics))
    alerts = evaluate(dailies)

    if not alerts:
        print("no alerts")
        return 0

    if args.emit == "github":
        token = os.environ.get(args.token_env, "")
        if not (args.owner and args.repo and token):
            print("missing --owner/--repo/ or env token; falling back to stdout")
            args.emit = "stdout"

    for a in alerts:
        if args.emit == "stdout":
            emit_stdout(a)
        else:
            emit_github_issue(a, owner=args.owner, repo=args.repo,
                              token=os.environ[args.token_env])
    return 0 if args.emit == "stdout" else 0


if __name__ == "__main__":
    sys.exit(main())
