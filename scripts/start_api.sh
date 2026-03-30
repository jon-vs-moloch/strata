#!/usr/bin/env bash
set -euo pipefail

if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

PYTHONPATH=. ./venv/bin/python -m uvicorn strata.api.main:app --host 127.0.0.1 --port 8000
