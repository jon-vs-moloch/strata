# Strata Product Roadmap

This document turns current product direction into staged implementation work.

It is intentionally practical rather than aspirational. The goal is to preserve Strata's core architecture while moving it from a local orchestration prototype toward a real installable product.

## Guiding Constraints

- Keep the FastAPI backend as the durable system core.
- Keep the React UI as a client of that backend rather than letting platform wrappers absorb product logic.
- Treat desktop, web, and future mobile clients as shells over shared services and APIs.
- Keep the bundled frontend shell minimal and trustworthy; prefer validated runtime modules/plugins for fast-changing product surfaces.
- Prefer operational ownership of local inference over low-level inference implementation.
- Do not let packaging convenience erase the weak/strong bootstrap architecture.

## Workstreams

There are two active roadmap workstreams:

1. Desktop shell
2. Strata-managed local inference

They can progress in parallel, but desktop shell should stay architecturally thin while inference ownership grows behind the existing model/provider layer.

## Workstream 1: Desktop Shell

### Phase 1. Shell Bootstrap

Goal: run Strata as an installable desktop wrapper around the current local web app.

Scope:

- add a desktop process that launches or reuses the local Python backend
- open the existing web UI in a native window
- support tray/taskbar presence, window restore, and single-app launch behavior
- keep UI and backend endpoints unchanged

Exit criteria:

- Strata can be launched without manually starting shell scripts
- the backend lifecycle is owned by the desktop wrapper
- the same backend can still be used by browser-based development

### Phase 2. Desktop Product Basics

Goal: make the shell feel like a real app instead of a dev wrapper.

Scope:

- packaged builds and installers
- signed binaries and app identity
- startup-on-login support
- log viewing and crash recovery surfaces
- desktop notifications
- environment and model-health onboarding
- a stable safe-mode shell that remains usable even if higher-level runtime modules fail to load

Exit criteria:

- non-technical startup flow works on a clean machine
- common startup failures are diagnosable from the app
- updating or restarting the app does not require terminal use
- the packaged app can fall back to bundled safe-mode views when runtime modules or plugins are invalid

### Phase 3. Multi-Shell Discipline

Goal: preserve future web/mobile optionality while desktop evolves.

Scope:

- capability adapters for notifications, file open/save, and lifecycle events
- API contracts that remain shell-agnostic
- removal of browser-only assumptions from UI flows where desktop behavior differs
- optional remote/backend deployment path for web/mobile clients
- versioned extension contracts for runtime modules, tool panels, telemetry cards, and plugin-provided views

Exit criteria:

- desktop-specific behavior is isolated behind wrappers
- the web UI remains first-class
- future mobile work can target stable backend surfaces instead of reverse-engineering desktop behavior
- communication routing remains shell-agnostic, so chat replies, in-app notices, and future OS notifications all share the same underlying decision model
- plugin/module surfaces are interchangeable by default because they target shared contracts owned by the stable shell

## Workstream 2: Strata-Managed Local Inference

### Phase 1. Runtime Abstraction

Goal: separate "Strata uses a local model" from "LM Studio is manually running."

Scope:

- formalize pool config as the mutable inference unit rather than treating a single model choice as the whole strategy
- formalize local runtime types in the model registry and provider config
- distinguish runtime management from request transport
- investigate whether supported providers/runtimes expose a configurable reasoning or non-reasoning mode, and treat that as another mutable inference-config element rather than a hardcoded model assumption
- preserve room for fast-response profiles such as immediate acknowledgements that can be emitted before a slower reasoning pass when the endpoint/runtime actually supports that distinction
- add health/status reporting for local inference runtimes
- define a runtime adapter contract for engines Strata may supervise
- distinguish in-pool escalation from cross-pool escalation in both policy and telemetry

Exit criteria:

- LM Studio becomes one runtime adapter among several possible adapters
- the system can describe which runtime is expected, configured, healthy, or degraded
- the system can represent a fast-first local strategy such as `4b -> 4b slow profile -> 9b` without treating that as a cross-pool escalation

## Cross-Cutting Communication Surface

Communication routing is now also an intentional product surface.

The durable contract lives in [communication-model.md](/Users/jon/Projects/strata/docs/spec/communication-model.md).

This matters to both workstreams:

- desktop/web/mobile shells need a shared notion of replies, notices, and recommendations
- multi-runtime/multi-lane Strata needs explicit provenance and session routing instead of ad hoc message writes

Near-term implementation standard:

- non-user-authored messages should flow through the shared communication decision/routing/delivery layer
- emitters should provide rich metadata such as source kind, tags, topic summary, and urgency
- session metadata should be maintained as routing substrate, not only UI state
- the right-rail task pane should stay an at-a-glance surface; a dedicated full `Tasks` view should eventually own deep task inspection, failure forensics, attempt history, and archive navigation

## Cross-Cutting Observability and Self-Evaluation Follow-Up

The recent observability hardening work established the core substrate:

- append-only observability/event tables for hot telemetry paths
- typed durable sidecar artifacts for autopsies and plan reviews
- buffered write lanes for isolated observability writes
- operator-facing inspection endpoints for attempt observability artifacts
- compact attempt-intelligence summaries injected into recovery and plan-review prompts

Follow-up work remains and should stay on the roadmap:

- expose compact attempt-intelligence summaries to more agent-facing tools beyond recovery prompts
- add lineage-level rollups/materialized summaries so the UI and agents can read branch pressure cheaply
- make trainer/self-review jobs consume observability artifacts and attempt intelligence more explicitly
- continue shaping raw observability into ergonomic summary surfaces that weak models can use without reconstructing state by hand
- keep sharpening lane/runtime visibility so labels like `stalled`, `queued`, and `children in progress` always resolve to an explicit reason such as transport wait, database contention, missing progress heartbeat, or child-task handoff
- keep refining `Procedure` execution so successful decompositions and reusable partial progress can fold back into durable `Procedure` artifacts instead of staying one-off runtime branches
- treat mutable config fields as a first-class evolutionary search surface so bootstrap can explore deterministic config mutations before falling back to prompt or code mutation
- add explicit inference-throttle postures (`hard` and `greedy`) and provider-limit probing so the system can distinguish strict ceilings from adaptive best-effort operation
- add operator-comfort sensing and policy loops for local inference, including comfort-oriented targets such as fan-noise avoidance, memory-pressure avoidance, and other "not annoying" runtime constraints
- split overloaded task `session_id` semantics into distinct concepts such as `workstream_id`, `source_session_id`, and lane ownership so task provenance, chat affinity, and execution routing stop sharing one field
- add a signed desktop `alpha` updater/distribution channel so packaged desktop installs stay in lockstep with rapid internal iteration without manual rebuild/reinstall churn
- harden the desktop launcher path so detached startup is as reliable and observable as foreground startup, with clear logs and safe recovery when the backend fails to stay bound to its port
- if SQLite continues to interfere with core task/attempt control flow after hot-path write-shape hardening, graduate runtime state to Postgres rather than continuing to accumulate lock workarounds

### Phase 2. Managed Engine Supervision

Goal: let Strata launch and monitor a local inference engine itself.

Scope:

- process management for one initial engine family
- model-path/config registration
- health checks, restarts, and timeout handling
- operator-visible runtime telemetry
- safe fallback when local runtime is unavailable

Candidate engines:

- MLX-backed local serving on Apple Silicon
- vLLM-class servers where hardware/OS support makes sense
- Strata-native inference/runtime ownership once that unlocks finetuning, adaptation, and evaluation surfaces that are awkward through external sidecar tools
- other mature runtimes only if they offer a meaningful operational advantage

Exit criteria:

- the user can start Strata and have Strata bring up its own local inference service
- runtime failures become observable and recoverable from inside Strata

### Phase 3. Runtime UX and Distribution

Goal: make local inference management feel productized instead of experimental.

Scope:

- model download/install flows
- runtime selection in the UI
- resource checks and warnings
- persistent runtime preferences
- benchmarking and recommendation surfaces

Exit criteria:

- a user can pick, install, and run a local runtime from Strata-managed flows
- runtime switching is operationally safe and visible

### Phase 4. Native Adaptation Surfaces

Goal: make Strata-owned inference worthwhile by exposing training and adaptation loops that are hard to express through external sidecar tools alone.

Scope:

- finetuning-oriented runtime ownership where it materially improves product capability
- lightweight adaptation surfaces such as QLoRA-style mutation/fine-tune workflows
- evaluation and promotion loops that can compare base and adapted model variants
- provenance tracking for model mutations produced inside Strata

Exit criteria:

- Strata-native inference is not just another runtime adapter, but a meaningful substrate for controlled adaptation and self-improvement

## Current Implementation Status

The repository now has the first Phase 1 desktop scaffold:

- Tauri-based desktop bootstrap under `src-tauri/`
- desktop shell starts or reuses the Python API
- desktop window wraps the existing UI instead of forking it
- packaging-oriented structure is in place without committing the product to Electron-specific conventions

This is intentionally only the first shell step. Packaging polish, tray/menu behavior, updates, signing, and polished onboarding remain future work.

For inference ownership, no engine supervision has been implemented yet. The next meaningful step is to define a local runtime adapter model inside `strata/models/` and make LM Studio one concrete adapter instead of the implicit default.
