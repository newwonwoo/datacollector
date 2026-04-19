#!/usr/bin/env bash
# Collector 원클릭 실행 (macOS/Linux).
# 사용: ./run.sh [--watch 5] [--port 8765] [...]
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
exec "$PY" -m collector app "$@"
