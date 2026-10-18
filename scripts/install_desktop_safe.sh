#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="$ROOT_DIR/src-tauri/target/release/bundle/macos/Strata.app"
INSTALL_DIR="/Applications"
TARGET_APP="$INSTALL_DIR/Strata.app"
BACKUP_APP="$INSTALL_DIR/Strata.previous.app"

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "Built app bundle not found at $APP_BUNDLE" >&2
  echo "Run 'npm run desktop:build' first." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"

if [[ -d "$BACKUP_APP" ]]; then
  rm -rf "$BACKUP_APP"
fi

if [[ -d "$TARGET_APP" ]]; then
  mv "$TARGET_APP" "$BACKUP_APP"
fi

cp -R "$APP_BUNDLE" "$TARGET_APP"

echo "Installed new desktop app to $TARGET_APP"
if [[ -d "$BACKUP_APP" ]]; then
  echo "Previous app preserved at $BACKUP_APP"
fi
