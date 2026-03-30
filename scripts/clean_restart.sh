#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

mkdir -p strata/runtime strata/runtime/archive

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/admin/test}"
START_BOOTSTRAP_SUPERVISOR="${START_BOOTSTRAP_SUPERVISOR:-1}"
SUPERVISOR_MODE="${SUPERVISOR_MODE:-continuous}"
RESET_RUNTIME_DB="${RESET_RUNTIME_DB:-1}"
API_CMD=(./venv/bin/python -m uvicorn strata.api.main:app --host 127.0.0.1 --port 8000)

stop_matching_processes() {
  local pattern="$1"
  local pids=()
  while IFS= read -r pid; do
    [ -n "$pid" ] && pids+=("$pid")
  done < <(pgrep -f "$pattern" || true)
  if [ "${#pids[@]}" -eq 0 ]; then
    return
  fi
  echo "Stopping processes matching: $pattern"
  kill "${pids[@]}" >/dev/null 2>&1 || true
  sleep 1
  kill -9 "${pids[@]}" >/dev/null 2>&1 || true
}

archive_and_reset_runtime_db() {
  if [ "$RESET_RUNTIME_DB" != "1" ]; then
    return
  fi
  local ts
  ts="$(date +%Y%m%d_%H%M%S)"
  if [ -f strata/runtime/strata.db ]; then
    cp strata/runtime/strata.db "strata/runtime/archive/strata_clean_restart_${ts}.db"
  fi
  rm -f strata/runtime/strata.db strata/runtime/strata.db-shm strata/runtime/strata.db-wal
  echo "Runtime DB reset complete (${ts})"
}

start_api() {
  echo "Starting API..."
  nohup "${API_CMD[@]}" > strata/runtime/api.log 2>&1 &
}

wait_for_api() {
  echo "Waiting for API health..."
  for _ in $(seq 1 45); do
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
  if [ "$START_BOOTSTRAP_SUPERVISOR" != "1" ]; then
    echo "Bootstrap supervisor disabled for this launch"
    return
  fi
  echo "Starting bootstrap supervisor in ${SUPERVISOR_MODE} mode..."
  nohup env PYTHONPATH=. SUPERVISOR_MODE="$SUPERVISOR_MODE" ./venv/bin/python scripts/bootstrap_supervisor.py \
    > strata/runtime/bootstrap_supervisor.log 2>&1 &
}

stop_matching_processes "uvicorn strata.api.main:app"
stop_matching_processes "Python strata/api/main.py"
stop_matching_processes "python.*strata.api.main"
stop_matching_processes "scripts/bootstrap_supervisor.py"

archive_and_reset_runtime_db
start_api
wait_for_api
start_supervisor

echo "Clean restart complete."
echo "  API log: strata/runtime/api.log"
if [ "$START_BOOTSTRAP_SUPERVISOR" = "1" ]; then
  echo "  Supervisor log: strata/runtime/bootstrap_supervisor.log"
fi
