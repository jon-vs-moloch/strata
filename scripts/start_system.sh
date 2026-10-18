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
HEALTH_URL="http://127.0.0.1:8000/admin/test"
START_BOOTSTRAP_SUPERVISOR="${START_BOOTSTRAP_SUPERVISOR:-1}"
SUPERVISOR_MODE="${SUPERVISOR_MODE:-continuous}"
API_PATTERN="uvicorn strata.api.main:app --host 127.0.0.1 --port 8000"
WORKER_PATTERN="scripts/worker_daemon.py"
API_CMD=(env PYTHONUNBUFFERED=1 STRATA_API_EMBED_WORKER=0 ./venv/bin/python -m uvicorn strata.api.main:app --host 127.0.0.1 --port 8000)
WORKER_CMD=(env PYTHONUNBUFFERED=1 PYTHONPATH=. ./venv/bin/python scripts/worker_daemon.py)
SUPERVISOR_CMD=(env PYTHONUNBUFFERED=1 PYTHONPATH=. SUPERVISOR_MODE="$SUPERVISOR_MODE" ./venv/bin/python scripts/bootstrap_supervisor.py)

cleanup_stale_api_processes() {
  local api_pids=()
  while IFS= read -r pid; do
    [ -n "$pid" ] && api_pids+=("$pid")
  done < <(pgrep -f "$API_PATTERN" || true)
  if [ "${#api_pids[@]}" -eq 0 ]; then
    return
  fi
  echo "Stopping API processes: ${api_pids[*]}"
  kill "${api_pids[@]}" >/dev/null 2>&1 || true
  sleep 1
  kill -9 "${api_pids[@]}" >/dev/null 2>&1 || true
}

cleanup_stale_worker_processes() {
  local worker_pids=()
  while IFS= read -r pid; do
    [ -n "$pid" ] && worker_pids+=("$pid")
  done < <(pgrep -f "$WORKER_PATTERN" || true)
  if [ "${#worker_pids[@]}" -eq 0 ]; then
    return
  fi
  echo "Stopping worker processes: ${worker_pids[*]}"
  kill "${worker_pids[@]}" >/dev/null 2>&1 || true
  sleep 1
  kill -9 "${worker_pids[@]}" >/dev/null 2>&1 || true
}

launch_detached() {
  local logfile="$1"
  shift
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" </dev/null > "$logfile" 2>&1 &
  else
    nohup "$@" </dev/null > "$logfile" 2>&1 &
  fi
}

start_api() {
  cleanup_stale_api_processes
  if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "API already listening on :8000"
    return
  fi
  echo "Starting API..."
  launch_detached strata/runtime/api.log "${API_CMD[@]}"
}

start_worker() {
  cleanup_stale_worker_processes
  echo "Starting worker daemon..."
  launch_detached strata/runtime/worker.log "${WORKER_CMD[@]}"
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
  echo "Starting bootstrap supervisor in ${SUPERVISOR_MODE} mode..."
  launch_detached strata/runtime/bootstrap_supervisor.log "${SUPERVISOR_CMD[@]}"
}

start_api
wait_for_api
start_worker

if [ "$START_BOOTSTRAP_SUPERVISOR" = "1" ]; then
  start_supervisor
else
  echo "Bootstrap supervisor disabled for this launch (set START_BOOTSTRAP_SUPERVISOR=1 to enable)"
fi

echo "System launch requested. Logs:"
echo "  API: strata/runtime/api.log"
echo "  Worker: strata/runtime/worker.log"
if [ "$START_BOOTSTRAP_SUPERVISOR" = "1" ]; then
  echo "  Supervisor: strata/runtime/bootstrap_supervisor.log"
fi
