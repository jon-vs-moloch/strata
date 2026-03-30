# Desktop Distribution

## Goal

Keep the desktop shell in lockstep with the Strata runtime and UI during fast iteration, without making operators manually rebuild or reinstall for every meaningful change.

## Current State

- The desktop app is a thin Tauri shell around the local Strata frontend and backend.
- It currently bundles the built frontend from `strata_ui/dist`.
- It does not currently configure the Tauri updater plugin.
- It does not currently publish signed updater artifacts or channel manifests.
- Therefore, a packaged desktop install will drift behind the repo until it is manually rebuilt and reinstalled.

## Desired State

- An `alpha` channel exists for rapid internal iteration.
- The desktop shell can check for updates on startup and on demand.
- Alpha builds publish signed updater artifacts and a channel manifest.
- The UI can surface:
  - current desktop version
  - available update version
  - download/install progress
  - restart-required state
- Later, a `stable` channel can be layered on top of the same mechanism.

## Recommended Architecture

### Channels

- `alpha`
  - fast-moving internal builds
  - low ceremony
  - intended to track `main` closely
- `stable`
  - slower, intentional promotion target
  - fed by tested alpha builds or tagged releases

### Update Transport

Use Tauri v2 updater with signed artifacts and a static channel manifest first.

Why this first:
- simplest operational model
- easy to host on static storage / GitHub Releases / object storage
- no custom dynamic update service required on day one

### Channel Manifest Shape

Per Tauri updater requirements, each channel should expose a manifest describing the latest signed build artifacts for each supported target.

Initial target support:
- `darwin-aarch64`

Later:
- `darwin-x86_64`
- `windows-x86_64`
- `linux-x86_64`

## Concrete Implementation Plan

### Phase 1: Enable updater plumbing

1. Add the Tauri updater plugin to the desktop shell.
2. Configure `tauri.conf.json` with:
   - updater endpoints
   - updater public key
   - updater artifact generation
3. Add frontend wiring for:
   - check for updates
   - install update
   - progress reporting

### Phase 2: Add release artifacts

1. Generate and securely store the updater signing keypair.
2. Add a release script that:
   - bumps the desktop version
   - builds signed updater artifacts
   - generates channel manifest JSON
   - uploads artifacts and manifest to the selected host
3. Add an `alpha` publication path keyed off:
   - manual release command, or
   - CI on `main`

### Current Build Commands

- `npm run desktop:build`
  - regular packaged desktop build
  - no updater manifest generation required
- `npm run desktop:build:alpha`
  - signed alpha channel build
  - requires:
    - `STRATA_DESKTOP_UPDATE_ENDPOINT`
    - `STRATA_DESKTOP_UPDATE_PUBKEY`
    - `TAURI_SIGNING_PRIVATE_KEY`
- `npm run desktop:build:stable`
  - same flow for the stable channel

### Phase 3: UI/operator ergonomics

1. Show the desktop version in settings/about.
2. Expose current update channel.
3. Add:
   - `Check for updates`
   - `Download and install`
   - `Restart to finish update`
4. Show updater status in the operator surfaces so “am I stale?” is answerable immediately.

### Phase 4: Stable channel

1. Add a second manifest endpoint for `stable`.
2. Promote alpha builds into stable intentionally.
3. Allow desktop channel selection in settings.

## Operational Notes

- Tauri updater requires signed artifacts. This is not optional.
- The signing private key must be kept out of the repo and injected at build/release time.
- The public key is safe to embed in config.
- The simplest first host is a static endpoint serving:
  - updater bundles
  - signatures
  - channel manifest JSON

## Recommended First Host

Prefer a static host with simple URLs:
- GitHub Releases
- or object storage / CDN bucket

The system does not need a custom release server yet.

## Developer Workflow Recommendation

During active local development:
- `desktop:dev` remains the fastest feedback loop

For internal packaged testing:
- publish to `alpha`
- desktop installs track that channel
- use the in-app updater panel to check, install, and restart cleanly after an update

## Future Extension

- Allow runtime channel overrides for canary builds.
- Attach release notes generated from commit history or explicit operator notes.
- Feed updater status into task/operator telemetry so the system can reason about UI/runtime skew.
