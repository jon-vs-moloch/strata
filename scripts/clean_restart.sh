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
RESTART_PROFILE="${RESTART_PROFILE:-onboarding_stress}"
API_CMD=(./venv/bin/python -m uvicorn strata.api.main:app --host 127.0.0.1 --port 8000)

case "$RESTART_PROFILE" in
  resume)
    default_start_supervisor=1
    default_supervisor_mode="continuous"
    default_reset_runtime_db=0
    default_prune_agent_scratch=0
    default_reset_user_profile=0
    default_reset_project_knowledge=0
    default_reset_world_knowledge=0
    default_reset_system_knowledge=0
    ;;
  fresh_runtime)
    default_start_supervisor=1
    default_supervisor_mode="continuous"
    default_reset_runtime_db=1
    default_prune_agent_scratch=0
    default_reset_user_profile=0
    default_reset_project_knowledge=0
    default_reset_world_knowledge=0
    default_reset_system_knowledge=0
    ;;
  cold_boot)
    default_start_supervisor=1
    default_supervisor_mode="continuous"
    default_reset_runtime_db=1
    default_prune_agent_scratch=1
    default_reset_user_profile=1
    default_reset_project_knowledge=1
    default_reset_world_knowledge=1
    default_reset_system_knowledge=1
    ;;
  onboarding_stress|*)
    default_start_supervisor=1
    default_supervisor_mode="continuous"
    default_reset_runtime_db=1
    default_prune_agent_scratch=1
    default_reset_user_profile=0
    default_reset_project_knowledge=0
    default_reset_world_knowledge=0
    default_reset_system_knowledge=0
    ;;
esac

START_BOOTSTRAP_SUPERVISOR="${START_BOOTSTRAP_SUPERVISOR:-$default_start_supervisor}"
SUPERVISOR_MODE="${SUPERVISOR_MODE:-$default_supervisor_mode}"
RESET_RUNTIME_DB="${RESET_RUNTIME_DB:-$default_reset_runtime_db}"
PRUNE_AGENT_SCRATCH="${PRUNE_AGENT_SCRATCH:-$default_prune_agent_scratch}"
RESET_USER_PROFILE="${RESET_USER_PROFILE:-$default_reset_user_profile}"
RESET_PROJECT_KNOWLEDGE="${RESET_PROJECT_KNOWLEDGE:-$default_reset_project_knowledge}"
RESET_WORLD_KNOWLEDGE="${RESET_WORLD_KNOWLEDGE:-$default_reset_world_knowledge}"
RESET_SYSTEM_KNOWLEDGE="${RESET_SYSTEM_KNOWLEDGE:-$default_reset_system_knowledge}"
SUPERVISOR_CMD=(env PYTHONPATH=. SUPERVISOR_MODE="$SUPERVISOR_MODE" ./venv/bin/python scripts/bootstrap_supervisor.py)

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

reset_knowledge_surfaces() {
  local removed=0
  mkdir -p .knowledge/specs

  if [ "$PRUNE_AGENT_SCRATCH" = "1" ]; then
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      rm -f "$path"
      removed=$((removed + 1))
    done < <(find .knowledge -maxdepth 1 -type f -name 'wip_research_*.md' | sort)
  fi

  if [ "$RESET_USER_PROFILE" = "1" ]; then
    rm -f .knowledge/user-profile.md
    removed=$((removed + 1))
  fi

  if [ "$RESET_PROJECT_KNOWLEDGE" = "1" ]; then
    rm -f .knowledge/specs/project_spec.md
    removed=$((removed + 1))
  fi

  if [ "$RESET_WORLD_KNOWLEDGE" = "1" ]; then
    rm -f .knowledge/specs/global_spec.md
    removed=$((removed + 1))
  fi

  if [ "$RESET_SYSTEM_KNOWLEDGE" = "1" ]; then
    rm -f .knowledge/specs/constitution.md
    rm -f .knowledge/specs/investigation-patterns.md
    rm -f .knowledge/model_performance_intel.md
    removed=$((removed + 3))
  fi

  if [ "$RESET_PROJECT_KNOWLEDGE" = "1" ] || [ "$RESET_WORLD_KNOWLEDGE" = "1" ] || [ "$RESET_SYSTEM_KNOWLEDGE" = "1" ]; then
    rm -f .knowledge/provenance_index.json
  fi

  echo "Knowledge reset profile applied (${RESTART_PROFILE}); scratch/user/project/world/system = ${PRUNE_AGENT_SCRATCH}/${RESET_USER_PROFILE}/${RESET_PROJECT_KNOWLEDGE}/${RESET_WORLD_KNOWLEDGE}/${RESET_SYSTEM_KNOWLEDGE}"
}

start_api() {
  echo "Starting API..."
  if command -v setsid >/dev/null 2>&1; then
    setsid "${API_CMD[@]}" </dev/null > strata/runtime/api.log 2>&1 &
  else
    nohup "${API_CMD[@]}" </dev/null > strata/runtime/api.log 2>&1 &
  fi
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
  if command -v setsid >/dev/null 2>&1; then
    setsid "${SUPERVISOR_CMD[@]}" </dev/null > strata/runtime/bootstrap_supervisor.log 2>&1 &
  else
    nohup "${SUPERVISOR_CMD[@]}" </dev/null > strata/runtime/bootstrap_supervisor.log 2>&1 &
  fi
}

stop_matching_processes "uvicorn strata.api.main:app"
stop_matching_processes "Python strata/api/main.py"
stop_matching_processes "python.*strata.api.main"
stop_matching_processes "scripts/bootstrap_supervisor.py"

echo "Restart profile: ${RESTART_PROFILE}"
archive_and_reset_runtime_db
reset_knowledge_surfaces
start_api
wait_for_api
start_supervisor

echo "Clean restart complete."
echo "  API log: strata/runtime/api.log"
if [ "$START_BOOTSTRAP_SUPERVISOR" = "1" ]; then
  echo "  Supervisor log: strata/runtime/bootstrap_supervisor.log"
fi
