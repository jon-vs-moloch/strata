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

## Local Alpha Workflow

For local packaged iteration on one machine, Strata should support a repo-local alpha channel.

Recommended flow:

1. Run `npm run desktop:update:setup:local`
   - generates a local signing keypair if needed
   - writes a repo-local desktop updater config
   - starts a tiny localhost server for updater artifacts
2. Install the desktop app once with the normal packaged flow
3. Publish later changes with `npm run desktop:update:publish:local`
   - builds a signed alpha update
   - auto-bumps the packaged desktop version to the next patch release, for example `0.1.1`, then `0.1.2`
   - writes it into the local channel directory
   - makes it available to the already-installed desktop shell
4. Pick the update up from inside the app
   - bring the desktop app to the foreground or open `Settings`
   - let the updater auto-check, or click `Check for updates`
   - click `Install update`
   - restart the app when prompted

Important:
- the installed app must already be on an updater-capable build; the very first install is still manual
 - every publish intended for the updater path must advance the desktop version; the local alpha publish path should do this automatically with a fresh prerelease version
- during active alpha-branch iteration, any desktop-visible change that should reach the packaged app should be followed by a local alpha publish rather than relying on manual rebuild/reinstall

This does not replace `desktop:dev` for fast UI work, but it should remove the need to manually reinstall a packaged app for every internal alpha delta.

## Future Extension

- Allow runtime channel overrides for canary builds.
- Attach release notes generated from commit history or explicit operator notes.
- Feed updater status into task/operator telemetry so the system can reason about UI/runtime skew.
