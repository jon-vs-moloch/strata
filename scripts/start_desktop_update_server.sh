#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${STRATA_DESKTOP_UPDATE_PORT:-8765}"
HOST="${STRATA_DESKTOP_UPDATE_HOST:-127.0.0.1}"
SERVE_DIR="${STRATA_DESKTOP_UPDATE_SERVE_DIR:-$ROOT_DIR/dist/desktop-updates}"
RUNTIME_DIR="$ROOT_DIR/strata/runtime"
PID_FILE="$RUNTIME_DIR/desktop-update-server.pid"
LOG_FILE="$RUNTIME_DIR/desktop-update-server.log"

mkdir -p "$RUNTIME_DIR" "$SERVE_DIR"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Desktop update server already listening on $HOST:$PORT"
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" >/dev/null 2>&1; then
    kill "$old_pid" >/dev/null 2>&1 || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3 || command -v python)"
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "No Python runtime available to start the desktop update server." >&2
  exit 1
fi

if command -v setsid >/dev/null 2>&1; then
  setsid "$PYTHON_BIN" -m http.server "$PORT" --bind "$HOST" --directory "$SERVE_DIR" >"$LOG_FILE" 2>&1 < /dev/null &
else
  nohup "$PYTHON_BIN" -m http.server "$PORT" --bind "$HOST" --directory "$SERVE_DIR" >"$LOG_FILE" 2>&1 &
fi
server_pid="$!"
echo "$server_pid" > "$PID_FILE"

sleep 1
if ! kill -0 "$server_pid" >/dev/null 2>&1; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Desktop update server is already listening at http://$HOST:$PORT/"
    exit 0
  fi
  echo "Failed to start desktop update server. Check $LOG_FILE" >&2
  exit 1
fi

echo "Desktop update server listening at http://$HOST:$PORT/"
