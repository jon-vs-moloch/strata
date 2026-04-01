#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CHANNEL="${1:-${STRATA_DESKTOP_UPDATE_CHANNEL:-alpha}}"
RUNTIME_DIR="$ROOT_DIR/strata/runtime"
KEY_DIR="$RUNTIME_DIR/updater_keys"
PRIVATE_KEY_PATH="$KEY_DIR/local-alpha.key"
PUBLIC_KEY_PATH="$KEY_DIR/local-alpha.key.pub"
PASSWORD_PATH="$KEY_DIR/local-alpha.password"
HOST="${STRATA_DESKTOP_UPDATE_HOST:-127.0.0.1}"
PORT="${STRATA_DESKTOP_UPDATE_PORT:-8765}"

bash "$ROOT_DIR/scripts/setup_local_desktop_updates.sh"

if [[ ! -f "$PRIVATE_KEY_PATH" || ! -f "$PUBLIC_KEY_PATH" || ! -f "$PASSWORD_PATH" ]]; then
  echo "Local updater keypair is missing after setup." >&2
  exit 1
fi

export TAURI_SIGNING_PRIVATE_KEY="$(cat "$PRIVATE_KEY_PATH")"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat "$PASSWORD_PATH")"
export STRATA_DESKTOP_UPDATE_PUBKEY="$(tr -d '\n' < "$PUBLIC_KEY_PATH")"
export STRATA_DESKTOP_UPDATE_CHANNEL="$CHANNEL"
export STRATA_DESKTOP_UPDATE_ENDPOINT="http://$HOST:$PORT/{channel}/latest.json"
export STRATA_DESKTOP_AUTO_BUMP_VERSION="${STRATA_DESKTOP_AUTO_BUMP_VERSION:-1}"

bash "$ROOT_DIR/scripts/build_desktop_channel.sh" "$CHANNEL"

echo "Published local desktop update for channel '$CHANNEL'."
echo "Installed desktop builds on that channel can now check and install from:"
echo "  http://$HOST:$PORT/$CHANNEL/latest.json"
