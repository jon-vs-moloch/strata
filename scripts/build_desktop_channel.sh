#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CHANNEL="${1:-${STRATA_DESKTOP_UPDATE_CHANNEL:-alpha}}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/dist/desktop-updates/$CHANNEL}"
BASE_CONFIG="$ROOT_DIR/src-tauri/tauri.conf.json"
BASE_CARGO_TOML="$ROOT_DIR/src-tauri/Cargo.toml"
TMP_CONFIG_BASE="$(mktemp "${TMPDIR:-/tmp}/strata-tauri-updater.XXXXXX")"
TMP_CONFIG="${TMP_CONFIG_BASE}.json"
mv "$TMP_CONFIG_BASE" "$TMP_CONFIG"
ORIGINAL_CARGO_TOML=""
DESKTOP_VERSION=""

cleanup() {
  rm -f "$TMP_CONFIG"
  if [[ -n "$ORIGINAL_CARGO_TOML" ]]; then
    printf '%s' "$ORIGINAL_CARGO_TOML" > "$BASE_CARGO_TOML"
  fi
}

trap cleanup EXIT

if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" && -z "${TAURI_SIGNING_PRIVATE_KEY_PATH:-}" ]]; then
  echo "TAURI_SIGNING_PRIVATE_KEY or TAURI_SIGNING_PRIVATE_KEY_PATH must be set to build signed updater artifacts." >&2
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

DESKTOP_VERSION="$(./venv/bin/python - <<'PY' "$BASE_CONFIG" "$OUTPUT_DIR/latest.json" "${STRATA_DESKTOP_AUTO_BUMP_VERSION:-0}"
import json, sys
from datetime import datetime, timezone
from pathlib import Path
import re

base_config = json.loads(Path(sys.argv[1]).read_text())
latest_manifest_path = Path(sys.argv[2])
auto_bump = str(sys.argv[3]).strip() not in {"", "0", "false", "False", "no", "No"}
base_version = str(base_config.get("version") or "0.1.0").strip() or "0.1.0"

if not auto_bump:
    print(base_version)
    raise SystemExit(0)

def parse_triplet(value: str):
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value or "")
    if not match:
        return None
    return tuple(map(int, match.groups()))

base_triplet = parse_triplet(base_version)
current = base_triplet if base_triplet is not None else (0, 1, 0)

if latest_manifest_path.exists():
    try:
        latest = json.loads(latest_manifest_path.read_text())
        latest_triplet = parse_triplet(str(latest.get("version") or ""))
        if latest_triplet and latest_triplet >= current:
            current = latest_triplet
    except Exception:
        pass

major, minor, patch = current
print(f"{major}.{minor}.{patch + 1}")
PY
)"

export STRATA_DESKTOP_BUILD_VERSION="$DESKTOP_VERSION"

ORIGINAL_CARGO_TOML="$(cat "$BASE_CARGO_TOML")"
./venv/bin/python - <<'PY' "$BASE_CARGO_TOML" "$DESKTOP_VERSION"
import re, sys
from pathlib import Path

path = Path(sys.argv[1])
version = sys.argv[2]
text = path.read_text()
updated, count = re.subn(r'(?m)^version = "[^"]+"$', f'version = "{version}"', text, count=1)
if count != 1:
    raise SystemExit("Could not rewrite desktop Cargo.toml version")
path.write_text(updated)
PY

echo "Building Strata desktop for channel: $CHANNEL"
./venv/bin/python - <<'PY' "$BASE_CONFIG" "$TMP_CONFIG" "$DESKTOP_VERSION"
import json, os, sys
from pathlib import Path

base_path = Path(sys.argv[1])
tmp_path = Path(sys.argv[2])
version = sys.argv[3]
config = json.loads(base_path.read_text())
config["version"] = version
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

UPDATE_ARTIFACT="$(find "$OUTPUT_DIR" -maxdepth 1 -type f \( -name "*.app.tar.gz" -o -name "*.tar.gz" -o -name "*.zip" \) ! -name "latest.json" | sort | tail -n 1)"
if [[ -z "$UPDATE_ARTIFACT" ]]; then
  echo "No updater archive was produced, so latest.json could not be generated." >&2
  exit 1
fi
if [[ ! -f "$UPDATE_ARTIFACT.sig" ]]; then
  echo "Updater archive signature missing for $UPDATE_ARTIFACT" >&2
  exit 1
fi
./venv/bin/python - <<'PY' "$BASE_CONFIG" "$OUTPUT_DIR/latest.json" "$UPDATE_ARTIFACT" "$UPDATE_ARTIFACT.sig" "${STRATA_DESKTOP_UPDATE_ENDPOINT}" "${STRATA_DESKTOP_BUILD_VERSION}"
import json, platform, sys
from datetime import datetime, timezone
from pathlib import Path

base_config = json.loads(Path(sys.argv[1]).read_text())
output_path = Path(sys.argv[2])
artifact_path = Path(sys.argv[3])
signature_path = Path(sys.argv[4])
endpoint = sys.argv[5].rstrip("/")
version = sys.argv[6].strip() or str(base_config.get("version") or "0.1.0")
base_url = endpoint[:-len("/latest.json")] if endpoint.endswith("/latest.json") else endpoint

system = platform.system().lower()
arch = platform.machine().lower()
system_map = {"darwin": "darwin", "linux": "linux", "windows": "windows"}
arch_map = {
    "arm64": "aarch64",
    "aarch64": "aarch64",
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "i386": "i686",
    "i686": "i686",
    "armv7l": "armv7",
}
target = f"{system_map.get(system, system)}-{arch_map.get(arch, arch)}"
manifest = {
    "version": version,
    "notes": f"Local {base_config.get('productName', 'Strata')} alpha build",
    "pub_date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "platforms": {
        target: {
            "signature": signature_path.read_text().strip(),
            "url": f"{base_url}/{artifact_path.name}",
        }
    },
}
output_path.write_text(json.dumps(manifest, indent=2))
PY

echo "Desktop channel artifacts ready:"
find "$OUTPUT_DIR" -maxdepth 1 -type f | sort
