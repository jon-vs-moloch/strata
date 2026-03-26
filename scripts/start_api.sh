#!/usr/bin/env bash
set -euo pipefail

if [ -f .env.local ]; then
  set -a
  . ./.env.local
  set +a
fi

PYTHONPATH=. ./venv/bin/python strata/api/main.py
