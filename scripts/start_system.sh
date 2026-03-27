#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

mkdir -p strata/runtime
HEALTH_URL="http://127.0.0.1:8000/admin/health"

start_api() {
  if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "API already listening on :8000"
    return
  fi
  echo "Starting API..."
  nohup ./venv/bin/python -m uvicorn strata.api.main:app --host 0.0.0.0 --port 8000 \
    > strata/runtime/api.log 2>&1 &
}

wait_for_api() {
  echo "Waiting for API health..."
  for _ in $(seq 1 30); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      echo "API is healthy"
      return 0
    fi
    sleep 1
  done
  echo "API failed to become healthy in time" >&2
  return 1
}

start_supervisor() {
  if pgrep -f "scripts/bootstrap_supervisor.py" >/dev/null 2>&1; then
    echo "Bootstrap supervisor already running"
    return
  fi
  echo "Starting bootstrap supervisor..."
  nohup env PYTHONPATH=. ./venv/bin/python scripts/bootstrap_supervisor.py \
    > strata/runtime/bootstrap_supervisor.log 2>&1 &
}

start_api
wait_for_api
start_supervisor

echo "System launch requested. Logs:"
echo "  API: strata/runtime/api.log"
echo "  Supervisor: strata/runtime/bootstrap_supervisor.log"
