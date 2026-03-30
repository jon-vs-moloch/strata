#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CHANNEL="${1:-${STRATA_DESKTOP_UPDATE_CHANNEL:-alpha}}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/dist/desktop-updates/$CHANNEL}"
BASE_CONFIG="$ROOT_DIR/src-tauri/tauri.conf.json"
TMP_CONFIG="$(mktemp "${TMPDIR:-/tmp}/strata-tauri-updater.XXXXXX.json")"
trap 'rm -f "$TMP_CONFIG"' EXIT

if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
  echo "TAURI_SIGNING_PRIVATE_KEY must be set to build signed updater artifacts." >&2
  exit 1
fi

if [[ -z "${STRATA_DESKTOP_UPDATE_PUBKEY:-}" ]]; then
  echo "STRATA_DESKTOP_UPDATE_PUBKEY must be set to build updater-enabled channel artifacts." >&2
  exit 1
fi

if [[ -z "${STRATA_DESKTOP_UPDATE_ENDPOINT:-}" ]]; then
  echo "STRATA_DESKTOP_UPDATE_ENDPOINT must be set to the channel manifest endpoint template." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

export STRATA_DESKTOP_UPDATE_CHANNEL="$CHANNEL"
export STRATA_DESKTOP_UPDATE_ENDPOINT="${STRATA_DESKTOP_UPDATE_ENDPOINT//\{channel\}/$CHANNEL}"

echo "Building Strata desktop for channel: $CHANNEL"
./venv/bin/python - <<'PY' "$BASE_CONFIG" "$TMP_CONFIG"
import json, os, sys
from pathlib import Path

base_path = Path(sys.argv[1])
tmp_path = Path(sys.argv[2])
config = json.loads(base_path.read_text())
config.setdefault("bundle", {})["createUpdaterArtifacts"] = True
config.setdefault("plugins", {})["updater"] = {
    "pubkey": os.environ["STRATA_DESKTOP_UPDATE_PUBKEY"],
    "endpoints": [os.environ["STRATA_DESKTOP_UPDATE_ENDPOINT"]],
}
tmp_path.write_text(json.dumps(config, indent=2))
PY

npx tauri build --config "$TMP_CONFIG"

BUNDLE_DIR="$ROOT_DIR/src-tauri/target/release/bundle"
if [[ ! -d "$BUNDLE_DIR" ]]; then
  echo "Expected Tauri bundle directory at $BUNDLE_DIR" >&2
  exit 1
fi

echo "Collecting updater artifacts into $OUTPUT_DIR"
find "$BUNDLE_DIR" \
  \( -name "latest.json" -o -name "*.sig" -o -name "*.tar.gz" -o -name "*.zip" -o -name "*.dmg" -o -name "*.app.tar.gz" \) \
  -type f \
  -exec cp "{}" "$OUTPUT_DIR/" \;

if [[ ! -f "$OUTPUT_DIR/latest.json" ]]; then
  echo "No latest.json updater manifest was produced. Check the Tauri updater configuration and signing environment." >&2
  exit 1
fi

echo "Desktop channel artifacts ready:"
find "$OUTPUT_DIR" -maxdepth 1 -type f | sort
