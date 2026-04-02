# Strata System Substrates

This document is the architecture index for Strata's first-class system artifacts and subsystems.

It answers a simple question:

What are the named things in this system, what do they do, and where do they live?

This page is intentionally high-level. It is not a full implementation guide for every module. It is the map that helps humans and models orient before drilling into code.

System direction:

- as much of Strata as possible should eventually be made from first-class Strata substrates rather than bespoke hidden machinery
- the target is "Strata all the way down": Procedures, tools, Knowledge, Kits, policies, audits, verifiers, and other reusable artifacts should increasingly explain and implement the system's own internals

## Naming

Strata uses proper nouns for first-class architectural artifacts and subsystems.

Examples:

- `Procedure`
- `Kit`
- `Verifier`
- `Audit`

Lowercase terms still refer to the ordinary generic activity:

- a procedure
- verification
- an audit

Use capitalized names when referring to the system object rather than the general concept.

## Substrate Map

### `Procedure`

Purpose:

- represent a durable, reusable, mutable workflow artifact
- capture how a class of work should be decomposed and verified
- preserve useful process structure so the system does not have to rediscover it every time

Owns:

- reusable workflow identity
- checklist structure
- lifecycle state such as `draft`, `tested`, `vetted`, or `retired`
- lineage and variant identity for workflow evolution
- tool-selection guidance, including one-or-more tools a Procedure expects or permits
- optional tool-binding rules such as "recommended Procedure for this tool" or "mandatory Procedure for this tool"
- startup/onboarding seeding
- eventual promotion target for successful repeated decompositions

Does not own:

- one-off runtime task execution
- attempt-level stochastic behavior

Primary surfaces:

- [/Users/jon/Projects/strata/strata/procedures/registry.py](/Users/jon/Projects/strata/strata/procedures/registry.py)
- [/Users/jon/Projects/strata/docs/spec/task-attempt-ontology.md](/Users/jon/Projects/strata/docs/spec/task-attempt-ontology.md)

Current status:

- real and active
- still biased toward seeding top-level tasks plus checklist structure
- now supports explicit draft Procedures for novel live work, plus promotion from `draft` to `tested` after a successful run
- not yet fully learning and rewriting itself from successful branches
- still wants a dedicated operator-facing `Procedures` surface so durable workflows are visible, inspectable, and eventually steerable
- still needs explicit cycle-detection and loop-governance for Procedure <-> tool relationships so circular recommended/mandatory flows remain visible and bounded rather than implicit

### `Kit`

Purpose:

- represent a durable bundle of related system artifacts that should travel, evolve, and be inspected together
- let the system package coordinated tools, Procedures, evals, knowledge, policies, or other reusable assets as one named unit
- support recursive composition so larger bundles can be built from smaller bundles

Owns:

- bundle identity and human-facing label
- membership over other first-class artifacts such as `Procedure`, tool, eval, knowledge artifact, or other `Kit`
- versioning, lineage, and variant identity for bundled artifact families
- bundle-level metadata such as purpose, compatibility, and promotion state

Does not own:

- execution of the artifacts it contains
- the internal implementation details of member artifacts

Primary surfaces:

- [/Users/jon/Projects/strata/.knowledge/specs/project_spec.md](/Users/jon/Projects/strata/.knowledge/specs/project_spec.md)
- [/Users/jon/Projects/strata/docs/spec/product-roadmap.md](/Users/jon/Projects/strata/docs/spec/product-roadmap.md)
- [/Users/jon/Projects/strata/docs/spec/bug-tracker.md](/Users/jon/Projects/strata/docs/spec/bug-tracker.md)

Current status:

- specified as a first-class substrate
- intended working name is `Kit`
- should support recursive nesting (`Kit` containing other `Kit`s)
- not yet implemented as a durable registry, runtime surface, or operator-facing view

### `Verifier`

Purpose:

- evaluate whether a step, artifact, or output is good and correct
- provide bounded skepticism at arbitrary points in the process
- convert verification findings into durable evidence, review prompts, and attention signals

Owns:

- general verification invocation over arbitrary artifacts
- verification summaries and recommended actions
- verifier-origin attention signals

Does not own:

- full-sequence judgment over an entire branch
- policy decisions about final promotion or abandonment

Primary surfaces:

- [/Users/jon/Projects/strata/strata/experimental/verifier.py](/Users/jon/Projects/strata/strata/experimental/verifier.py)
- [/Users/jon/Projects/strata/.knowledge/specs/project_spec.md](/Users/jon/Projects/strata/.knowledge/specs/project_spec.md)

Current status:

- conceptually strong and now callable at arbitrary steps
- still needs a cleaner dedicated implementation note covering invocation patterns, outputs, and known failure modes
- should converge toward a normal `Procedure`-backed system capability rather than a permanently special hardcoded sidecar
- should be allowed to request `Audit` directly when a verification outcome is severe enough that the branch needs immediate diagnosis

### Machinery Health and Repair

Purpose:

- treat reusable system machinery such as `Verifier`, `Audit`, decomposition policy, and repair flows as first-class capabilities with health
- let repeated machinery failures degrade the capability instead of silently poisoning task-level evidence
- route degraded machinery into bounded repair work the same way repeated tool failures already do

Owns:

- degradation state for reusable internal processes
- circuit-breaking or caution signals when a process is unhealthy
- repair-task queueing for degraded system machinery

Does not own:

- the full implementation of every process it monitors
- permanent special-case logic for each subsystem

Primary surfaces:

- [/Users/jon/Projects/strata/strata/orchestrator/tool_health.py](/Users/jon/Projects/strata/strata/orchestrator/tool_health.py)
- [/Users/jon/Projects/strata/strata/orchestrator/background.py](/Users/jon/Projects/strata/strata/orchestrator/background.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py)

Current status:

- tool health is real and already supports `healthy`, `degraded`, and `broken`
- process degradation is now beginning to appear for verification machinery
- the architecture still needs unification so internal machinery is represented as normal `Procedure` or tool artifacts instead of ad hoc runtime branches
- repeated failures should be treated as an obvious repair trigger across both tools and higher-level reusable processes
- degradation should eventually become sticky and version-aware: a later audit should be able to inspect the incident-time state of a tool/process and explicitly decide whether to re-green it or blame the earlier review
- this substrate should converge toward normal first-class artifacts with operator-visible controls, not hidden flags buried inside runtime logic

### `Audit`

Purpose:

- review a sequence, artifact, branch, or system behavior in order to diagnose what happened and whether it was good
- serve as the general review protocol rather than a one-off “reflection” side mechanism

Owns:

- review of traces and branch behavior
- interpretive diagnosis of outcomes
- a reusable protocol for reviewing system behavior, including verifier outputs and prior audits

Does not own:

- primary execution of the task being audited
- low-level validation of a single artifact in isolation

Primary surfaces:

- [/Users/jon/Projects/strata/strata/experimental/trace_review.py](/Users/jon/Projects/strata/strata/experimental/trace_review.py)
- [/Users/jon/Projects/strata/strata/eval/job_runner.py](/Users/jon/Projects/strata/strata/eval/job_runner.py)
- [/Users/jon/Projects/strata/docs/spec/project-philosophy.md](/Users/jon/Projects/strata/docs/spec/project-philosophy.md)

Current status:

- philosophically defined and partially embodied through trace review
- still missing a dedicated implementation spec that cleanly distinguishes `Audit` from `Verifier`
- should ultimately be built from ordinary Strata primitives: a proper `Procedure`, explicit tools, explicit provenance, and explicit repair/re-green outputs rather than a forever-special hardcoded branch

### Trace Review

Purpose:

- perform concrete review work over task traces, session traces, blocked branches, and supervision events
- generate interpretable artifacts for trainer review, agent recovery, and operator inspection

Owns:

- task/session trace summaries
- review artifacts and followups
- attention signals from trace review outcomes

Primary surfaces:

- [/Users/jon/Projects/strata/strata/experimental/trace_review.py](/Users/jon/Projects/strata/strata/experimental/trace_review.py)
- [/Users/jon/Projects/strata/strata/eval/job_runner.py](/Users/jon/Projects/strata/strata/eval/job_runner.py)
- [/Users/jon/Projects/strata/strata/api/runtime_admin.py](/Users/jon/Projects/strata/strata/api/runtime_admin.py)

Current status:

- real and active
- increasingly central to supervision and recovery
- should likely become the main implementation surface for much of `Audit`
- should eventually either become the first concrete `Audit` Procedure implementation or sit beneath one as a clearly-scoped tool/capability rather than remaining ambiguous proto-machinery

### Communication Lanes

Purpose:

- let Strata operate across multiple incoming and outgoing communication channels while preserving one coherent system model
- keep routing, provenance, safety, and operator control consistent whether the surface is chat, email, Slack, Discord, voice, or another lane

Owns:

- lane identity and routing metadata
- ingress/egress safety posture
- provenance and authority on communications
- future anti-injection and sandboxing posture for untrusted inbound content

Does not own:

- the product logic of every individual client integration
- UI-specific rendering details for each shell

Primary surfaces:

- [/Users/jon/Projects/strata/docs/spec/communication-model.md](/Users/jon/Projects/strata/docs/spec/communication-model.md)
- [/Users/jon/Projects/strata/docs/spec/product-roadmap.md](/Users/jon/Projects/strata/docs/spec/product-roadmap.md)

Current status:

- partially real for chat
- voice is on the roadmap
- email/Slack/Discord and similar lanes are still roadmap-level work
- should become a first-class safety surface because incoming content cannot be trusted blindly

### Operator Work Surfaces

Purpose:

- expose meaningful system capabilities to the operator by default
- keep all of Strata's machinery inspectable and steerable without requiring that manual involvement for ordinary use

Owns:

- visibility of tasks, Procedures, tools, Knowledge, History, Workbench, and similar control surfaces
- bounded operator actions over those artifacts
- the principle that capability generally implies UI access unless deliberately withheld

Does not own:

- autonomy policy itself
- backend execution of the capabilities it exposes

Primary surfaces:

- [/Users/jon/Projects/strata/docs/spec/product-roadmap.md](/Users/jon/Projects/strata/docs/spec/product-roadmap.md)
- [/Users/jon/Projects/strata/.knowledge/specs/project_spec.md](/Users/jon/Projects/strata/.knowledge/specs/project_spec.md)

Current status:

- partially real
- still too viewer-heavy and too sparse for the full machine
- should keep evolving toward "the user can operate every subsystem from the UI, but never has to"

### Attempt / Task Execution

Purpose:

- run bounded units of work
- preserve one variance-bearing invocation per attempt
- turn failures into explicit continuation decisions

Owns:

- task execution lifecycle
- attempt lifecycle
- decomposition fallback and recovery routing
- plan-review and resolution application

Primary surfaces:

- [/Users/jon/Projects/strata/strata/orchestrator/background.py](/Users/jon/Projects/strata/strata/orchestrator/background.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py](/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py)
- [/Users/jon/Projects/strata/docs/spec/task-attempt-ontology.md](/Users/jon/Projects/strata/docs/spec/task-attempt-ontology.md)

Current status:

- heavily evolved today
- now much closer to the intended one-attempt / one-variance-bearing-invocation model
- DAG-aware execution semantics are becoming explicit:
  - serial child chains hand forward deterministic evidence
  - parallel children write into parent-owned merge state
  - coordination nodes replan from branch-wide child status rather than isolated sibling guesses

### Bootstrap / Self-Improvement

Purpose:

- mutate and evaluate harness behavior
- let trainer and agent propose bounded changes
- turn eval outcomes into promotion decisions

Owns:

- bootstrap cycles
- eval job execution
- experiment runner flow
- promotion surfaces for harness changes

Primary surfaces:

- [/Users/jon/Projects/strata/strata/eval/job_runner.py](/Users/jon/Projects/strata/strata/eval/job_runner.py)
- [/Users/jon/Projects/strata/strata/api/experiment_admin.py](/Users/jon/Projects/strata/strata/api/experiment_admin.py)
- [/Users/jon/Projects/strata/strata/experimental/experiment_runner.py](/Users/jon/Projects/strata/strata/experimental/experiment_runner.py)
- [/Users/jon/Projects/strata/docs/spec/self-improvement-substrate.md](/Users/jon/Projects/strata/docs/spec/self-improvement-substrate.md)

Current status:

- real and productive
- still too opaque in runtime visibility
- still occasionally confused by runtime/storage instability

### Tool Health

Purpose:

- track which tools are working, degraded, or broken for which scope
- prevent repeated bad tool calls from spamming a known-failing path

Owns:

- scope-aware tool health telemetry
- circuit breaking by tool, lane, and task shape

Primary surfaces:

- [/Users/jon/Projects/strata/strata/orchestrator/tool_health.py](/Users/jon/Projects/strata/strata/orchestrator/tool_health.py)
- [/Users/jon/Projects/strata/strata/storage/models.py](/Users/jon/Projects/strata/strata/storage/models.py)

Current status:

- implemented and useful
- still wants better operator-facing surfaces

### Observability

Purpose:

- make runtime behavior interpretable to operators, trainers, and eventually the agent itself
- preserve durable evidence about attempts, context pressure, provider behavior, and branch health

Owns:

- context-load telemetry
- host telemetry
- provider snapshots
- sidecar attempt observability artifacts
- buffered observability writes

Primary surfaces:

- [/Users/jon/Projects/strata/strata/observability/context.py](/Users/jon/Projects/strata/strata/observability/context.py)
- [/Users/jon/Projects/strata/strata/observability/host.py](/Users/jon/Projects/strata/strata/observability/host.py)
- [/Users/jon/Projects/strata/strata/observability/writer.py](/Users/jon/Projects/strata/strata/observability/writer.py)
- [/Users/jon/Projects/strata/strata/experimental/trace_review.py](/Users/jon/Projects/strata/strata/experimental/trace_review.py)

Current status:

- much stronger than before
- now a core learning substrate rather than just logging
- still wants richer operator-native presentations so structured traces, usage metadata, and child-work relationships are rendered as legible UI rather than raw blobs

### History

Purpose:

- provide a chronological event log of what the system did, when, and why
- support operator inspection, system querying, and later summarization/archive flows
- preserve authority and provenance so every consequential action is attributable and reviewable

Owns:

- append-only chronological runtime history views
- event-level provenance and timestamp ordering
- authority chain for events, including user-origin, spec-origin, audit-origin, and system-policy-origin actions
- lifecycle events for edits, redactions, opens, closes, and compactions
- history summaries and archive rollups

Does not own:

- live task execution state itself
- long-term semantic synthesis on its own

Primary surfaces:

- [/Users/jon/Projects/strata/strata/observability/writer.py](/Users/jon/Projects/strata/strata/observability/writer.py)
- [/Users/jon/Projects/strata/strata/api/runtime_admin.py](/Users/jon/Projects/strata/strata/api/runtime_admin.py)
- [/Users/jon/Projects/strata/docs/spec/product-roadmap.md](/Users/jon/Projects/strata/docs/spec/product-roadmap.md)

Current status:

- partially present in logs, attempt artifacts, and observability tables
- now beginning to surface as a first-class operator/system `History` view
- still wants richer drilldown, query, archive, and action affordances
- provenance is still under-specified relative to the system's needs; history should grow into a true ledger of what happened, why, and under what authority

### Workbench

Purpose:

- provide a universal debugger-like operator surface for any Strata process
- let the operator step through execution node-by-node with real inputs, outputs, context, and handoff behavior visible
- support branching, replay, regeneration, and substitution of tools/models/context from arbitrary intermediate nodes

Owns:

- replay and warm-up of process state up to a selected node
- node-level input/output inspection
- editable branch context and re-run surfaces
- alternate-path comparison across tool/model/context variants
- drilldown into verification, audit, and other child processes as first-class subflows

Does not own:

- the canonical source of runtime truth itself
- policy decisions about what an operator profile is allowed to edit or invoke

Primary surfaces:

- [/Users/jon/Projects/strata/docs/spec/product-roadmap.md](/Users/jon/Projects/strata/docs/spec/product-roadmap.md)
- [/Users/jon/Projects/strata/.knowledge/specs/project_spec.md](/Users/jon/Projects/strata/.knowledge/specs/project_spec.md)

Current status:

- not implemented yet
- now a first-class architectural target rather than an ad hoc debugging wish
- should evolve into the deepest reflection surface in the system, eventually allowing inspection and bounded editing of Strata's own tools, Procedures, runtime behavior, source, and UI

### `Context Pressure`

Purpose:

- make prompt/context occupancy visible and governable instead of invisible prompt glue
- preserve high-value context while allowing low-value or stale context to compact away deterministically
- support explicit handoff of useful evidence between parent and child tasks

Owns:

- persistent pinned-context registry
- context priority and age metadata
- deterministic compaction and unload behavior
- context-pressure notices surfaced into prompts and operator views
- parent-to-child execution handoff payloads for decomposed work

Does not own:

- semantic memory retrieval as a whole
- long-term knowledge synthesis
- arbitrary retention of every artifact forever

Primary surfaces:

- [/Users/jon/Projects/strata/strata/context/loaded_files.py](/Users/jon/Projects/strata/strata/context/loaded_files.py)
- [/Users/jon/Projects/strata/strata/api/chat_tools.py](/Users/jon/Projects/strata/strata/api/chat_tools.py)
- [/Users/jon/Projects/strata/strata/api/chat_tool_executor.py](/Users/jon/Projects/strata/strata/api/chat_tool_executor.py)
- [/Users/jon/Projects/strata/strata/api/chat_runtime.py](/Users/jon/Projects/strata/strata/api/chat_runtime.py)
- [/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py](/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py)
- [/Users/jon/Projects/strata/strata/orchestrator/research.py](/Users/jon/Projects/strata/strata/orchestrator/research.py)

Current status:

- newly first-class and increasingly foundational
- now supports explicit priority, deterministic compaction, and prompt-visible pressure summaries
- still wants a fuller policy layer for when the system should compact automatically versus merely advising the model/operator

### Knowledge

Purpose:

- preserve compacted, durable, provenance-aware synthesized knowledge
- make useful learned state reusable across future work

Owns:

- knowledge pages
- mirrored knowledge outputs
- access policy for knowledge retrieval

Primary surfaces:

- [/Users/jon/Projects/strata/strata/knowledge/pages.py](/Users/jon/Projects/strata/strata/knowledge/pages.py)
- [/Users/jon/Projects/strata/strata/knowledge/page_payloads.py](/Users/jon/Projects/strata/strata/knowledge/page_payloads.py)

Current status:

- present and useful
- still wants stronger explicit linkage from runtime learning into durable page updates

### Lane Model

Purpose:

- separate the operator-facing agent lane from the supervisory trainer lane
- preserve independent execution, telemetry, and policy context across those lanes

Owns:

- lane-local status
- lane-local runtime interpretation
- lane-to-pool routing discipline

Primary surfaces:

- [/Users/jon/Projects/strata/strata/orchestrator/background.py](/Users/jon/Projects/strata/strata/orchestrator/background.py)
- [/Users/jon/Projects/strata/strata/core/lanes.py](/Users/jon/Projects/strata/strata/core/lanes.py)
- [/Users/jon/Projects/strata/docs/spec/project-philosophy.md](/Users/jon/Projects/strata/docs/spec/project-philosophy.md)

Current status:

- architecturally correct
- still wants clearer progress semantics and less runtime ambiguity under failure

### Storage Runtime

Purpose:

- persist tasks, attempts, messages, observability, and control settings
- act as the durable state substrate for the system

Owns:

- SQLAlchemy schema
- repositories
- retention
- current runtime DB

Primary surfaces:

- [/Users/jon/Projects/strata/strata/storage/models.py](/Users/jon/Projects/strata/strata/storage/models.py)
- [/Users/jon/Projects/strata/strata/storage/services/main.py](/Users/jon/Projects/strata/strata/storage/services/main.py)
- [/Users/jon/Projects/strata/strata/storage/repositories/](/Users/jon/Projects/strata/strata/storage/repositories)

Current status:

- functionally central
- still the biggest operational risk because SQLite contention continues to interfere with core control flow

### Desktop Shell

Purpose:

- provide a trustworthy operator shell around the backend and web UI
- own backend lifecycle, updates, and desktop-specific affordances without swallowing product logic

Owns:

- backend startup and reuse policy
- updater shell
- quit/restart/install surfaces
- desktop-only lifecycle hooks

Primary surfaces:

- [/Users/jon/Projects/strata/src-tauri/src/main.rs](/Users/jon/Projects/strata/src-tauri/src/main.rs)
- [/Users/jon/Projects/strata/docs/spec/desktop-distribution.md](/Users/jon/Projects/strata/docs/spec/desktop-distribution.md)

Current status:

- real and now usable
- still wants explicit close/adopt/background-mode policy and cleaner process housekeeping semantics
- still wants capability/profile-aware presentation so the same shell can serve simple chat-first use, deeper operator/developer use, and future managed/enterprise restrictions

## Current Gaps

The main missing documentation is no longer philosophy. It is implementation-oriented subsystem clarity.

The strongest remaining docs gaps are:

- a dedicated `Verifier` implementation note
- a dedicated `Audit` implementation note
- a lifecycle document for desktop process ownership, adoption, close behavior, and background mode
- a storage/runtime migration note for SQLite -> Postgres if the current lock pressure persists

## Best Companion Documents

Read this file with:

- [/Users/jon/Projects/strata/docs/spec/project-philosophy.md](/Users/jon/Projects/strata/docs/spec/project-philosophy.md)
- [/Users/jon/Projects/strata/docs/spec/task-attempt-ontology.md](/Users/jon/Projects/strata/docs/spec/task-attempt-ontology.md)
- [/Users/jon/Projects/strata/docs/spec/codemap.md](/Users/jon/Projects/strata/docs/spec/codemap.md)
- [/Users/jon/Projects/strata/docs/spec/product-roadmap.md](/Users/jon/Projects/strata/docs/spec/product-roadmap.md)
