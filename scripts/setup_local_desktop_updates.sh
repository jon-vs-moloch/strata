#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUNTIME_DIR="$ROOT_DIR/strata/runtime"
KEY_DIR="$RUNTIME_DIR/updater_keys"
PRIVATE_KEY_PATH="$KEY_DIR/local-alpha.key"
PUBLIC_KEY_PATH="$KEY_DIR/local-alpha.key.pub"
PASSWORD_PATH="$KEY_DIR/local-alpha.password"
CONFIG_PATH="$RUNTIME_DIR/desktop-updater.json"
HOST="${STRATA_DESKTOP_UPDATE_HOST:-127.0.0.1}"
PORT="${STRATA_DESKTOP_UPDATE_PORT:-8765}"
CHANNEL="${STRATA_DESKTOP_UPDATE_CHANNEL:-alpha}"
ENDPOINT_TEMPLATE="http://$HOST:$PORT/{channel}/latest.json"
LOCAL_UPDATER_PASSWORD="${STRATA_DESKTOP_UPDATE_PASSWORD:-strata-local-alpha}"

mkdir -p "$KEY_DIR"

if [[ ! -f "$PRIVATE_KEY_PATH" || ! -f "$PUBLIC_KEY_PATH" || ! -f "$PASSWORD_PATH" ]]; then
  echo "Generating local desktop updater signing keypair..."
  rm -f "$PRIVATE_KEY_PATH" "$PUBLIC_KEY_PATH" "$PASSWORD_PATH"
  npx tauri signer generate -w "$PRIVATE_KEY_PATH" --ci -f -p "$LOCAL_UPDATER_PASSWORD"
  printf '%s' "$LOCAL_UPDATER_PASSWORD" > "$PASSWORD_PATH"
fi

PUBKEY="$(tr -d '\n' < "$PUBLIC_KEY_PATH")"

cat > "$CONFIG_PATH" <<EOF
{
  "channel": "$CHANNEL",
  "endpoint": "$ENDPOINT_TEMPLATE",
  "pubkey": "$PUBKEY"
}
EOF

bash "$ROOT_DIR/scripts/start_desktop_update_server.sh"

echo "Local desktop updater configured."
echo "Config: $CONFIG_PATH"
echo "Public key: $PUBLIC_KEY_PATH"
echo "Endpoint template: $ENDPOINT_TEMPLATE"
