# Strata Codemap

This file is the fast path to understanding the repository.

The goal is not to list every file. The goal is to help humans and small-context models find the right module quickly, inspect the narrowest relevant surface first, and avoid loading unrelated code.

## Top Level

- `strata/api/`
  API assembly plus route groups.
- `strata/orchestrator/`
  Background worker, routing, evaluation, and task execution flow.
- `strata/storage/`
  SQLAlchemy models, repositories, and retention logic.
- `strata/models/`
  Model adapter, provider transport, and registry configuration.
- `strata/eval/`
  Benchmarks, structured evals, matrix runs, and queued eval jobs.
- `strata/observability/`
  Context-load attribution and context-pressure scans.
- `strata/knowledge/`
  Synthesized knowledge-page store and permissions-aware retrieval.
- `strata/specs/`
  Durable spec bootstrap and proposal governance.
- `strata_ui/`
  Dashboard frontend.
- `.knowledge/`
  Raw research archive and provenance artifacts.
- `docs/spec/kb/`
  Mirrored synthesized knowledge pages for human/model browsing.

## API Map

Start with [main.py](/Users/jon/Projects/strata/strata/api/main.py) only when you need app assembly or shared singletons. Most route logic now lives elsewhere.

- [main.py](/Users/jon/Projects/strata/strata/api/main.py)
  App assembly, lifespan, shared worker/model/storage wiring, eval-job queue helpers.
- [chat_task_admin.py](/Users/jon/Projects/strata/strata/api/chat_task_admin.py)
  Chat, sessions, task creation, and task intervention routes.
- [chat_runtime.py](/Users/jon/Projects/strata/strata/api/chat_runtime.py)
  Clarification handling, chat message assembly, and the synchronous tool loop.
- [chat_tools.py](/Users/jon/Projects/strata/strata/api/chat_tools.py)
  Built-in chat tool schemas and dynamic tool loading.
- [eval_admin.py](/Users/jon/Projects/strata/strata/api/eval_admin.py)
  Thin assembler for eval and experiment route groups.
- [eval_routes.py](/Users/jon/Projects/strata/strata/api/eval_routes.py)
  Benchmark, structured eval, matrix eval, sampled eval, telemetry/dashboard endpoints.
- [experiment_admin.py](/Users/jon/Projects/strata/strata/api/experiment_admin.py)
  Benchmark gates, full eval gates, promotion, bootstrap cycles, tool cycles, ignition detection.
- [experiment_runtime.py](/Users/jon/Projects/strata/strata/api/experiment_runtime.py)
  Shared experiment helpers: candidate generation, promotion, dashboard aggregation, eval-override signatures.
- [spec_admin.py](/Users/jon/Projects/strata/strata/api/spec_admin.py)
  Durable spec reads and proposal review endpoints.
- [knowledge_admin.py](/Users/jon/Projects/strata/strata/api/knowledge_admin.py)
  KB compaction, page CRUD, and knowledge update task creation.
- [retention_admin.py](/Users/jon/Projects/strata/strata/api/retention_admin.py)
  DB/storage retention inspection and maintenance.
- [runtime_admin.py](/Users/jon/Projects/strata/strata/api/runtime_admin.py)
  Model selection, settings, health, logs, reboot, promotion/rollback, worker controls, SSE, context telemetry.
- [hotreload.py](/Users/jon/Projects/strata/strata/api/hotreload.py)
  Experimental module promotion and rollback mechanics.

## Worker Map

- [background.py](/Users/jon/Projects/strata/strata/orchestrator/background.py)
  Main worker loop, queue consumption, and task lifecycle control.
- [evaluation.py](/Users/jon/Projects/strata/strata/orchestrator/evaluation.py)
  Task evaluation and completion logic.
- [research.py](/Users/jon/Projects/strata/strata/orchestrator/research.py)
  Research-task behavior.
- [implementation.py](/Users/jon/Projects/strata/strata/orchestrator/implementation.py)
  Implementation-task behavior.
- [eval_jobs.py](/Users/jon/Projects/strata/strata/orchestrator/eval_jobs.py)
  Orchestrator-side eval job helpers.
- `strata/orchestrator/worker/`
  Focused worker policies and telemetry.

Important worker submodules:

- [attempt_runner.py](/Users/jon/Projects/strata/strata/orchestrator/worker/attempt_runner.py)
  Attempt execution, including queued system/eval jobs.
- [telemetry.py](/Users/jon/Projects/strata/strata/orchestrator/worker/telemetry.py)
  Metrics aggregation and snapshots.
- [resolution_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/resolution_policy.py)
  Retry/block/abandon behavior.
- [idle_policy.py](/Users/jon/Projects/strata/strata/orchestrator/worker/idle_policy.py)
  Idle task generation and alignment policy.

## Eval Map

- [benchmark.py](/Users/jon/Projects/strata/strata/eval/benchmark.py)
  Freeform prompt benchmark path.
- [structured_eval.py](/Users/jon/Projects/strata/strata/eval/structured_eval.py)
  Dataset-style exact-match / MCQ eval path.
- [matrix.py](/Users/jon/Projects/strata/strata/eval/matrix.py)
  Cross-profile eval matrix runner.
- [harness_eval.py](/Users/jon/Projects/strata/strata/eval/harness_eval.py)
  Eval-only harness execution path and config.
- [job_runner.py](/Users/jon/Projects/strata/strata/eval/job_runner.py)
  Queued eval/system-job execution.
- `strata/eval/suites/`
  Built-in eval datasets.

## Knowledge Map

- [pages.py](/Users/jon/Projects/strata/strata/knowledge/pages.py)
  Knowledge page storage, mirrored page output, and retrieval entry points.
- [page_payloads.py](/Users/jon/Projects/strata/strata/knowledge/page_payloads.py)
  Pure page normalization, TOC/summary shaping, provenance compaction, and section splitting.
- [page_access.py](/Users/jon/Projects/strata/strata/knowledge/page_access.py)
  Audience-aware knowledge access policy and redaction logic.
- [compact_knowledge.py](/Users/jon/Projects/strata/scripts/compact_knowledge.py)
  Archive/compaction pass for raw `.knowledge/` research notes.

## Observability Map

- [context.py](/Users/jon/Projects/strata/strata/observability/context.py)
  Context-load attribution, large-artifact warnings, and startup file token-pressure scans.

## Storage Map

- [models.py](/Users/jon/Projects/strata/strata/storage/models.py)
  SQLAlchemy schema.
- [services/main.py](/Users/jon/Projects/strata/strata/storage/services/main.py)
  Storage manager assembly.
- [retention.py](/Users/jon/Projects/strata/strata/storage/retention.py)
  DB retention and compaction.
- `strata/storage/repositories/`
  Narrow repositories for parameters, messages, tasks, and related records.

## Spec Map

- [bootstrap.py](/Users/jon/Projects/strata/strata/specs/bootstrap.py)
  Durable spec bootstrap, proposal persistence, and resolution flow.
- [.knowledge/specs/global_spec.md](/Users/jon/Projects/strata/.knowledge/specs/global_spec.md)
  Global durable intent.
- [.knowledge/specs/project_spec.md](/Users/jon/Projects/strata/.knowledge/specs/project_spec.md)
  Project-specific durable intent.

## Frontend Map

- [App.jsx](/Users/jon/Projects/strata/strata_ui/src/App.jsx)
  Main dashboard shell.
- `strata_ui/src/components/`
  Task cards, sidebar, logo, and related UI pieces.

## Best Entry Points

For common tasks, start here:

- “Why does chat behave this way?”
  [chat_task_admin.py](/Users/jon/Projects/strata/strata/api/chat_task_admin.py)
- “How does the synchronous chat tool loop work?”
  [chat_runtime.py](/Users/jon/Projects/strata/strata/api/chat_runtime.py)
- “What tools can chat call?”
  [chat_tools.py](/Users/jon/Projects/strata/strata/api/chat_tools.py)
- “How does eval work?”
  [eval_routes.py](/Users/jon/Projects/strata/strata/api/eval_routes.py)
- “How does self-improvement/promotion work?”
  [experiment_admin.py](/Users/jon/Projects/strata/strata/api/experiment_admin.py)
- “How do queued eval jobs run?”
  [job_runner.py](/Users/jon/Projects/strata/strata/eval/job_runner.py)
- “How does knowledge retrieval enforce permissions?”
  [pages.py](/Users/jon/Projects/strata/strata/knowledge/pages.py)
- “What is inflating context and which files are too big?”
  [context.py](/Users/jon/Projects/strata/strata/observability/context.py)
- “How does long-term retention work?”
  [retention.py](/Users/jon/Projects/strata/strata/storage/retention.py)

## Remaining Large Files

These are still worth future decomposition:

- [chat_task_admin.py](/Users/jon/Projects/strata/strata/api/chat_task_admin.py)
  Much smaller now, but could still split session routes from task routes if needed.
- [retention.py](/Users/jon/Projects/strata/strata/storage/retention.py)
  Still worth splitting the message-archive helpers from the coordinator if we want an even smaller control file.
- [experiment_runner.py](/Users/jon/Projects/strata/strata/experimental/experiment_runner.py)
  Could still split promotion decisions from metric aggregation if we want even tighter seams.
