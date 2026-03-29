#!/usr/bin/env bash
set -euo pipefail

HOST="127.0.0.1"
PORT="5174"
URL="http://${HOST}:${PORT}"

if command -v curl >/dev/null 2>&1; then
  if curl --silent --fail --max-time 1 "${URL}" >/dev/null 2>&1; then
    echo "Reusing existing Strata frontend dev server at ${URL}"
    exit 0
  fi
fi

exec npm run dev --prefix strata_ui -- --host "${HOST}" --port "${PORT}" --strictPort
