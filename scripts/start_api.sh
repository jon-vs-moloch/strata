#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

WORKER_PID=""
API_PID=""

cleanup() {
  if [ -n "${API_PID:-}" ]; then
    kill "${API_PID}" >/dev/null 2>&1 || true
  fi
  if [ -n "${WORKER_PID:-}" ]; then
    kill "${WORKER_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

if ! pgrep -f "scripts/worker_daemon.py" >/dev/null 2>&1; then
  PYTHONPATH=. ./venv/bin/python scripts/worker_daemon.py &
  WORKER_PID="$!"
fi

PYTHONPATH=. STRATA_API_EMBED_WORKER=0 ./venv/bin/python -m uvicorn strata.api.main:app --host 127.0.0.1 --port 8000 &
API_PID="$!"
wait "$API_PID"
