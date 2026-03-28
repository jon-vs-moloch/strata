# Strata

Strata is an agent orchestration prototype with three main pieces:

- A FastAPI backend that exposes task, message, admin, and streaming endpoints.
- A background worker that routes tasks through research, decomposition, and implementation flows.
- A React/Vite dashboard in `strata_ui/` for chat, task visibility, and admin controls.

The project philosophy and bootstrap strategy are documented in [project-philosophy.md](/Users/jon/Projects/strata/docs/spec/project-philosophy.md). That document is the best explanation of what Strata is trying to accomplish and why the repository is structured the way it is.
The repository structure itself is mapped in [codemap.md](/Users/jon/Projects/strata/docs/spec/codemap.md), which is the fastest way to find the right module without loading the entire codebase.

Strata now also tracks context pressure explicitly: every time the harness loads specs, session history, semantic memory, eval context files, or synthesized knowledge into model context, it records estimated token cost. On startup it also scans source/docs files for oversized artifacts using token estimates rather than line counts, so context-heavy files can be warned on before they quietly become a small-model tax.

## Why This Exists

Strata is built around a simple thesis:

- small local models can be made more useful if the system supplies rigor, memory, decomposition, and validation
- the goal is not just to assist those models, but to turn that refined capability into an agent that can do useful work on modest local resources

This is why the codebase emphasizes explicit task structure, external state, evaluation, and telemetry. The project is trying to move intelligence out of hidden prompt tricks and into the surrounding system.

## Repository Layout

- `strata/api/`: FastAPI app and hot-reload/promotion endpoints.
- `strata/orchestrator/`: background worker and task execution pipeline.
- `strata/storage/`: SQLAlchemy models, repositories, and storage service.
- `strata/memory/`: semantic memory backed by ChromaDB.
- `strata/models/`: model adapter, provider, and registry logic.
- `strata_ui/`: active frontend for the dashboard.
- `docs/`: design notes and agent-specific documentation.
- `.knowledge/`: raw generated research artifacts and provenance archive.
- `docs/spec/kb/`: synthesized knowledge pages mirrored for human and model browsing.

## Runtime Architecture

1. Start the API.
2. API startup initializes the database schema and starts the `BackgroundWorker`.
3. The UI talks to the API over REST and subscribes to `/events` for server-sent events.
4. Background tasks are persisted in SQLite and executed asynchronously by the worker.

By default the backend stores relational state in `strata/runtime/strata.db` and semantic memory in `memory/vector_db/`.
The API startup path also bootstraps `.knowledge/specs/global_spec.md` and `.knowledge/specs/project_spec.md` so alignment tasks always have stable spec files to inspect.

## Weak/Strong Bootstrap Loop

The `weak` and `strong` model tiers are intentional. They support the project’s improvement loop:

1. use a strong model inside the harness to propose or implement a change
2. evaluate the weak model with that change
3. inspect telemetry and downstream results
4. refine the system
5. repeat until the weak tier can make meaningful improvements itself

That separation is a core part of the design, not just a configuration detail.

## Requirements

The backend now includes a minimal [requirements.txt](/Users/jon/Projects/strata/requirements.txt) for reproducible setup:

```bash
./venv/bin/pip install -r requirements.txt
```

The current manifest covers the main runtime and test dependencies used by the repository:

- `fastapi`
- `uvicorn`
- `sqlalchemy`
- `pydantic`
- `httpx`
- `chromadb`
- `pytest`

The UI dependencies are managed in `strata_ui/package.json`.

## Getting Started

### Backend

Run the API from the repository root:

```bash
PYTHONPATH=. ./venv/bin/python strata/api/main.py
```

The API listens on `http://localhost:8000`.

### Worker

The background worker is created and started by the API lifespan hook. There is not a separate worker process to launch for the current architecture.

Important startup constraint:

- The worker performs a preflight model check on startup.
- The weak/local model tier is currently mandatory.
- If the local model endpoint is not reachable, the API startup will fail.

### Frontend

Run the active UI:

```bash
npm run dev --prefix strata_ui
```

Open `http://localhost:5173`.

Running `npm run dev` from the repository root now delegates to `strata_ui/`, which is the single active frontend. The root Vite scaffold is legacy residue and should not be treated as a separate product surface.

## Model Configuration

The default registry is defined in [strata/models/registry.py](/Users/jon/Projects/strata/strata/models/registry.py):

- `strong`: cloud model via OpenRouter, using `OPENROUTER_API_KEY`
- `weak`: local model via LM Studio at `http://127.0.0.1:1234/v1/chat/completions`

The UI also exposes registry and settings controls through the admin panel.

Each model endpoint can also carry pacing controls so the harness can respect cloud rate limits or be gentler on local hardware:

- `requests_per_minute`
- `max_concurrency`
- `min_interval_ms`

These are enforced in the provider transport layer, so they apply regardless of which orchestrator path ends up calling the model.

The admin API also exposes bootstrap-oriented registry presets, including Cerebras `zai-glm-4.7`, Google-hosted `gemma-3-27b-it`, and `openrouter/free`.

If you are using one of the cloud presets, these are the direct key pages:

- Cerebras: [cloud.cerebras.ai](https://cloud.cerebras.ai/)
- Google AI Studio: [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- OpenRouter: [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys)

## Main API Surface

Key endpoints implemented in [strata/api/main.py](/Users/jon/Projects/strata/strata/api/main.py):

- `GET /tasks`
- `GET /messages`
- `GET /sessions`
- `POST /chat`
- `POST /tasks`
- `POST /tasks/{task_id}/intervene`
- `GET /events`
- `GET /admin/specs`
- `GET /admin/spec_proposals`
- `GET /admin/spec_proposals/{proposal_id}`
- `POST /admin/spec_proposals`
- `POST /admin/spec_proposals/{proposal_id}/resolve`
- `GET /admin/storage/retention`
- `POST /admin/storage/retention/run`
- `GET/POST /admin/settings`
- `GET/POST /admin/registry`
- `GET /admin/health`

There are additional admin endpoints for reboot, promotion, rollback, worker control, and database reset.

The backend now applies conservative DB retention on startup and exposes its last compaction summary through `/admin/storage/retention`. The current defaults keep recent raw state lossless, compact old metrics into aggregate rollups, archive older chat history per session, trim old terminal attempt tails, and shrink stale experiment reports instead of letting raw traces grow forever.

Context-load telemetry is available at `/admin/context/telemetry`, and `/admin/context/scan` reruns the startup file-token pressure scan on demand.

## Evaluation Loop

Strata now has two eval paths for the weak-tier bootstrap loop:

- freeform benchmark prompts, judged by the strong tier
- structured eval suites for dataset-style exact-match or multiple-choice checks

The main eval endpoints are:

- `POST /admin/benchmark/run`
- `POST /admin/evals/run`
- `POST /admin/evals/matrix`
- `GET /admin/evals/jobs`
- `POST /admin/experiments/benchmark`
- `POST /admin/experiments/full_eval`
- `GET /admin/experiments/compare`
- `GET /admin/experiments/report`
- `GET/POST /admin/evals/config`
- `POST /admin/experiments/promote`
- `POST /admin/experiments/bootstrap_cycle`
- `GET /admin/experiments/secondary_ignition`
- `GET /admin/experiments/history`
- `POST /admin/experiments/tool_cycle`
- `GET /admin/knowledge/pages`
- `GET /admin/knowledge/pages/{slug}/metadata`
- `GET /admin/knowledge/pages/{slug}`
- `GET /admin/knowledge/pages/{slug}/section`
- `POST /admin/knowledge/pages`
- `POST /admin/knowledge/update`
- `POST /admin/knowledge/compact`

`/admin/experiments/full_eval` persists an exact sampled report for a candidate change, including the underlying benchmark and structured-eval runs. That gives the harness a concrete promotion record to inspect later instead of relying only on blended historical metric averages.

Experiment reports can also carry task associations, so if a report was generated by or spawned work for a specific task, the report stays easy to find from that task and retention keeps task-linked active reports hot.

The eval harness prompt/context is now configurable through `/admin/evals/config`, so a strong tier can propose prompt/context changes, evaluate them as a candidate, and promote the winning configuration through `/admin/experiments/promote` without requiring a code edit for each iteration.

`/admin/evals/matrix` runs a standard structured suite across weak/strong and direct/scaffolded variants, returning per-question answers plus aggregate accuracy, latency, and token counts. This is the path toward a simple `run_eval(eval)` style operator surface.

The heavier eval endpoints also support queued execution with `queue=true`, which creates a `JUDGE` task and returns immediately. `GET /admin/evals/jobs` exposes the queued eval/system-job lane, including current state, system job payloads, and compacted result summaries.

`/admin/experiments/bootstrap_cycle` can now ask both the weak and strong tiers to propose small eval-harness changes in parallel, evaluate them with provenance, and auto-promote any winner into the shared active harness configuration. Promotions now require repeated sample wins by default instead of a single pass. `GET /admin/experiments/history` exposes recent experiment reports and promotion readiness, while `GET /admin/experiments/secondary_ignition` reports whether a weak-originated promoted change has produced a measurable weak-tier gain.

For a first bounded code-change lane, `/admin/experiments/tool_cycle` lets a proposer tier generate a dynamic tool under `strata/tools/`, run it through the existing tool promotion pipeline, and persist the outcome as a provenance-tagged experiment report.

## Knowledge System

Strata now treats raw note accumulation and current synthesized knowledge as separate layers:

- raw archive: `.knowledge/`
- synthesized page store: parameter-backed pages mirrored to `docs/spec/kb/`
- provenance map: `.knowledge/provenance_index.json`
- current-facing wiki index: `docs/spec/current_knowledge_base.md`

`POST /admin/knowledge/compact` is the bridge between those layers. It now seeds the page store from durable specs/docs, mirrors the resulting pages into `docs/spec/kb/`, writes a current wiki index, and preserves per-page provenance in `.knowledge/provenance_index.json`. The intended steady state is that research and operators cite the synthesized knowledge pages first, and older raw note dumps can be aged out once their content has been integrated into the wiki.

The compaction pass also emits maintenance signals. Each page carries freshness and duplicate-candidate metadata, and the wiki now includes a `knowledge-maintenance-report` page so operators can see where pages may need merge, refresh, or review work instead of letting the knowledge base quietly fork.

Knowledge pages also carry first-class scope and disclosure metadata so the system can decide what it may use or reveal:

- `domain`: `system`, `agent`, `user`, `contacts`, `project`, or `world`
- `visibility_policy`: how broadly the page may be disclosed
- `disclosure_rules`: quoting/summarization/personalization/tool-use rules
- optional scope fields like `project_id`, `scope_id`, and `owner_id`

The intended access pattern is progressive disclosure:

1. list pages or fetch metadata first
2. read a section only if needed
3. read the full page only when necessary
4. queue `update_knowledge` work when a page is missing, stale, inaccurate, or thin

This keeps the knowledge layer friendlier to both humans and small-context models.

Knowledge reads are now audience-aware. The page store distinguishes at least `user`, `agent`, `tool`, and `operator` reads, so internal agent memory can remain available for reasoning without automatically becoming user-visible or tool-exportable.

## Current Caveats

These are the main current constraints:

- Provider presets and free-tier availability are operational assumptions and may change over time.
- The UI build currently emits a large-chunk warning, though the build succeeds.
