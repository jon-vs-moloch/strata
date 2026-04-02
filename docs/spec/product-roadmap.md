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
- operator-facing work surfaces should show all active work, including verification, audits, reviews, and other child work; there should be no invisible work
- non-blocking operator observations and UX pain points should feed durable alignment/backlog surfaces so the system can consume them as real work instead of leaving them stranded in thread history

## Cross-Cutting Operator Work Surfaces

The next UI/product iteration should make runtime work legible without asking the user to perform housekeeping.

Near-term product backlog:

- redesign the `Tasks` surface around present work, queued work, recently completed work, legacy work, and archived work
- keep task progress visible even when a task is expanded and keep lane progress visible even when another scope is focused
- move throttle controls off the global header and onto the relevant agent/lane surfaces, because throttle posture should be scoped to the workstream it affects rather than presented as an app-global switch
- show task provenance explicitly: why the task exists, who/what spawned it, and what caused retries, decomposition, review, or verification
- show "why this happened" and "under what authority" directly in operator work surfaces, not only raw timestamps or parent ids
- render attempts as expandable context -> result narratives rather than only status chips, while still keeping concise attempt metadata visible when collapsed
- nest verification, audits, reviews, and other spawned child work under the task or attempt that created it
- let operators invoke internal machinery from task/attempt surfaces, including things like verification, audit, retry, decomposition, pause, resume, or other bounded control actions where policy allows
- add a scoped chronological `History` view that acts as a queryable event log for both operators and the system
- make `History` a true provenance ledger: every consequential action should record authority, cause, derived-from links, and governing spec/user references
- add a first-class operator `Workbench` surface (working title; effectively a universal debugger) that can step through any Strata process end-to-end, pause at arbitrary nodes, inspect exact inputs/context/tool results, regenerate outputs from a chosen node, branch from modified context, and run downstream consequences side-by-side
- add a first-class `Tools` view
- add a first-class `Procedures` view so durable workflows are visible, inspectable, and eventually editable/promotable
- add a first-class `Kits` surface for bundled artifact groups such as tool packs, procedure bundles, eval suites, or higher-level capability bundles
- treat all of these work surfaces as interactive operator tooling rather than passive dashboards; `History`, `Tasks`, `Procedures`, `Tools`, and `Knowledge` should all be able to grow bounded edit/control actions over time
- extend that same interactive rule to the `Workbench`: it should not only replay or inspect flows, but also let the operator substitute tools, models, context, and branching decisions, then observe how downstream execution changes
- replace raw structured metadata blobs with purpose-built displays wherever the structure is known
- age completed work automatically from recent -> legacy -> archived without requiring user cleanup
- summarize and archive stale message/task clutter automatically rather than expecting user housekeeping
- reduce the right-rail operational telemetry panel to a lightweight status summary once the task pane itself communicates task state well; verbose telemetry should move to dedicated inspection surfaces instead of dominating the default task view
- eventually support capability/profile gating across shells, including simplified user modes, power-user/developer modes, and stricter enterprise-managed visibility/control profiles
- maintain a first-class bug tracker so runtime defects and truthfulness gaps become durable system work rather than ephemeral thread-local observations; see [bug-tracker.md](/Users/jon/Projects/strata/docs/spec/bug-tracker.md)
- make reflection arbitrarily deep: operator surfaces should eventually drill all the way down into Strata's own source, so tools, Procedures, Knowledge artifacts, runtime policies, and even the UI itself can be inspected and edited from inside Strata

## Cross-Cutting Chat Latency

Chat should feel responsive even when the real work is multi-step or long-running.

Near-term product backlog:

- emit a fast conversational acknowledgement before long-running work begins, so the user immediately knows the system accepted the request and what it is about to do
- treat non-thinking responses as `instant` responses throughout the system vocabulary and UI
- add an explicit fast routing decision for `instant` vs thinking responses, with the option to skip the router entirely when a flow is already known to be safely `instant`
- move `instant`/thinking selection out of the chat composer; chat should route automatically unless a future debugging/control surface explicitly says otherwise
- treat `instant`/thinking as a workbench/runtime configuration control for bounded processes, so operators can deliberately short-circuit reasoning on procedures or flows where non-thinking execution is sufficient
- build a first-class "decide to think or not" routing module and treat explicit `instant` overrides as policy inputs to that module rather than as chat-global UI state
- narrate tool use conversationally in user-facing chat when the model is performing a lookup, inspection, or other multi-step process
- emit periodic progress updates for longer chat work so silence is never confused with idleness
- push long-running or backgroundable chat work onto the background worker instead of holding the foreground request open unnecessarily
- let latency policy apply outside chat too, so internal routing, verification, review, and other system flows can choose `instant` vs thinking behavior deliberately rather than implicitly
- add first-class voice I/O so the same communication layer can accept microphone input, route speech-to-text into sessions, and optionally deliver spoken output without inventing a second interaction model
- treat voice as an operator/debugging surface too: support push-to-talk, transcript provenance, and eventually ambient-audio-aware comfort loops where fan noise or room noise can inform local throttle posture when the user has explicitly enabled that sensing

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
- add incident-time capability snapshots or version pointers for reusable tools/processes so audit can compare “broken then” vs “looks fine now”
- add append-only lifecycle logging for edits, redactions, compactions, opens, closes, and other history mutations so log mutation never becomes silent provenance loss
- keep sharpening lane/runtime visibility so labels like `stalled`, `queued`, and `children in progress` always resolve to an explicit reason such as transport wait, database contention, missing progress heartbeat, or child-task handoff
- keep refining `Procedure` execution so successful decompositions and reusable partial progress can fold back into durable `Procedure` artifacts instead of staying one-off runtime branches
- add a durable `Kit` substrate so the system can package coordinated artifact groups, including nested Kits, instead of treating multi-artifact capability bundles as ad hoc conventions
- treat mutable config fields as a first-class evolutionary search surface so bootstrap can explore deterministic config mutations before falling back to prompt or code mutation
- add explicit inference-throttle postures (`hard` and `greedy`) and provider-limit probing so the system can distinguish strict ceilings from adaptive best-effort operation
- add operator-comfort sensing and policy loops for local inference, including comfort-oriented targets such as fan-noise avoidance, memory-pressure avoidance, and other "not annoying" runtime constraints
- add pluggable direct sensor adapters for operator-comfort telemetry, including lightweight helper-command or native-helper paths that can report fan RPM and temperatures when the platform permits it
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
- aggressive prompt caching: manage KV cache reuse across requests with shared prefixes (system prompts, tools, common context)
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
