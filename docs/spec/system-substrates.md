# Strata System Substrates

This document is the architecture index for Strata's first-class system artifacts and subsystems.

It answers a simple question:

What are the named things in this system, what do they do, and where do they live?

This page is intentionally high-level. It is not a full implementation guide for every module. It is the map that helps humans and models orient before drilling into code.

## Naming

Strata uses proper nouns for first-class architectural artifacts and subsystems.

Examples:

- `Procedure`
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
- not yet fully learning and rewriting itself from successful branches

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
