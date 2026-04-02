# Strata Bug Tracker

This document is the durable tracker for active bugs, regressions, and truthfulness gaps.

It exists so bugs stop living only in thread history or operator memory. The tracker should be treated as live system state:

- current bugs should be recorded here with enough detail to reproduce or observe them
- resolved bugs should either be removed or moved into a historical section once the fix is verified
- trainer, audit, and alignment work may read this file as a real source of "what should be improved next"

## Triage Model

Priority is primarily determined by:

1. mission execution risk
2. operator truthfulness / interpretability risk
3. product polish / ergonomics risk

Suggested severity labels:

- `P0`: system cannot reliably run or data/control flow is unsafe
- `P1`: core mission flow works poorly or operator is debugging in the dark
- `P2`: important UX or operability defect, but work can continue
- `P3`: polish, ergonomics, or lower-value follow-up

## Active Bugs

### `P0` Detached launcher drops API/worker after startup

- Status: active
- Area: desktop/runtime lifecycle
- Symptoms:
  - `/Users/jon/Projects/strata/scripts/clean_restart.sh` reports success
  - API health may briefly pass during startup, then `http://127.0.0.1:8000` becomes unreachable
  - detached `uvicorn`, worker, and supervisor processes do not remain alive in this environment
- Current workaround:
  - run API and worker in the known-good foreground path
- Why it matters:
  - makes desktop/runtime restart behavior unreliable
  - blocks clean observation of post-restart telemetry

### `P1` Trainer `Bootstrap Cycle` can stall before or during eval gate

- Status: active
- Area: trainer runtime / eval pipeline
- Symptoms:
  - lane remains on `Bootstrap Cycle`
  - work may appear stalled for long periods
  - recent instrumentation suggests some stalls happen before the inner `run_full_eval_gate(...)` checkpoints fire
- Recent improvement:
  - finer eval-gate progress checkpoints now exist, but more observation is needed to localize the remaining stall site
- Why it matters:
  - trainer is not yet reliably cashing out into real system improvements

### `P1` Agent still churns through recovery/decomposition loops on leaf research work

- Status: active
- Area: agent execution quality
- Symptoms:
  - research leaf reads one file, then boundary-fails into decomposition
  - queue depth can grow significantly under repeated recovery-plan churn
  - branch quality depends heavily on decomposition specificity
- Recent improvement:
  - recovery-path `NoneType` crash in resolution/decomposition has been fixed
  - per-file inspect leaves and deterministic smoke fast paths are in place
- Remaining problem:
  - too many leaves still fail to cash out directly into success, question, or durable attention item

### `P1` Lane/task state can render inconsistently depending on selected scope

- Status: active
- Area: UI truthfulness
- Symptoms:
  - lane cards can show different task/progress states depending on which scope is selected
  - this has been made worse historically by stale in-memory lane activity after exceptions
- Recent improvement:
  - lane snapshot reconciliation against durable task/attempt state is safer now
- Remaining problem:
  - UI derivation still is not fully scope-independent and truthful in all cases

### `P1` Desktop updater path is still not trustworthy

- Status: active
- Area: desktop updates
- Symptoms:
  - Settings may say `current on this channel` while newer local alpha artifacts exist
  - UI-side manifest probe can fail with `Channel manifest check failed in the UI: Network Error`
  - installed desktop build/version identity is still confusing in practice
- Recent improvement:
  - local alpha publishes now auto-bump monotonic patch versions
  - manifest probing is more truthful than before
- Remaining problem:
  - end-to-end update pickup is still not dependable

### `P2` Runtime settings persistence is not trustworthy enough

- Status: active
- Area: settings / parameters persistence
- Symptoms:
  - UI can optimistically reflect settings changes that do not durably persist
  - `Quiet / Turbo` throttle control is not yet trustworthy because persisted global settings snap back
- Why it matters:
  - operator controls can appear to work while runtime truth remains unchanged

### `P2` Version identity is still confusing across shell/build/update flows

- Status: active
- Area: desktop shell identity
- Symptoms:
  - upper-left app version can diverge from channel/build expectations
  - manual rebuild/install may pick up new code while still presenting as `0.1.0`
- Why it matters:
  - makes update/debug state harder to reason about

### `P2` Settings UI model/pool management is too restrictive

- Status: active
- Area: Settings UI / Model Registry
- Symptoms:
  - UI does not allow adding multiple models to a single pool (pools are currently restricted to a single model selection in the UI, even if the backend supports lists).
  - UI does not allow binding a specific pool to a specific agent role (e.g., cannot explicitly bind a custom "strong pool" to the `Trainer` agent).
- Proposed Direction:
  - Implement a provider/model registry where individual endpoints can be saved to a list.
  - Allow pools to be populated by selecting one or more models from this saved list.
  - Enable explicit binding between pools and agent roles (Agent/Trainer).
- Why it matters:
  - Prevents the operator from configuring robust fallback or specialty routing within a pool via the UI.
  - Hardcodes the relationship between pools and roles, making it difficult to experiment with different capability tiers for the Agent vs. Trainer without code changes.

### `P3` Chat composer button spacing is asymmetric

- Status: active
- Area: chat composer polish
- Symptoms:
  - the horizontal spacing between the composer shell edge and the left `Attach` button does not match the spacing on the right side around `Send`
  - the input bar looks subtly off-center even when the underlying layout is otherwise healthy
- Known reproduction or observation path:
  - open chat and inspect the bottom composer in the desktop shell
  - compare left shell padding near the paperclip button with the right shell padding near the send button
- Why it matters:
  - this is a small bug, but it makes a high-frequency surface feel less intentional than the rest of the app

## Recently Resolved

### Recovery-path `NoneType` crash on child validator constraints

- Status: resolved
- Area: agent recovery flow
- Fix:
  - recovery subtasks now always get dict-backed constraints before validator flags are written
- Files:
  - [/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py)

### Stale lane attempt IDs were poisoning runtime snapshots after some exception paths

- Status: partially resolved
- Area: worker status truthfulness
- Fix:
  - runtime snapshots now reconcile active attempt IDs against durable attempts more safely
- Files:
  - [/Users/jon/Projects/strata/strata/orchestrator/background.py](/Users/jon/Projects/strata/strata/orchestrator/background.py)
- Note:
  - this improved truthfulness, but did not fully solve the scope-dependent UI rendering bug

## Tracking Guidance

When adding a new bug, include:

- severity
- symptoms
- known reproduction or observation path
- current workaround if one exists
- why it matters

When resolving a bug, do not just delete context if it would help future diagnosis. Prefer moving it to `Recently Resolved` once the fix has been verified in runtime behavior or focused tests.
