# Strata's Role in the Unified Product

## Context

Strata and Astra are merging. This document describes Strata's role in that unified direction.

For the full vision and integration plan, see:
- [UNIFIED_VISION.md](/Users/jon/Projects/UNIFIED_VISION.md) — philosophy and stack model
- [INTEGRATION.md](/Users/jon/Projects/INTEGRATION.md) — practical integration plan

## Strata's Position in the Stack

Strata is the **upstream** half of the product stack. It bridges the gap from "I have a model file and a processor" to "I have reliable agency."

Astra is the **downstream** half. It takes reliable agency and turns it into a product that proactively solves problems for the user.

Both projects converge at the **agency layer** — an agent that proactively does work. The merged product uses Astra's agency model (Prime / Assistants / Workers) since it has the richer delegation architecture. Strata's pipeline plugs into that model as an execution option.

## What Strata Contributes

Strata is a quality-amplification pipeline. It takes weak local inference and produces strong output through:

- **Task decomposition** — breaking problems into pieces a small model can handle
- **Multi-attempt execution** — branching, retry, rollback instead of one-shot failure
- **External validation** — correctness established by the system, not by model confidence
- **Knowledge synthesis** — accumulated context with provenance, compensating for limited context windows
- **Context management** — token budgets, pressure tracking, compaction
- **Eval and bootstrap** — measuring whether the pipeline is improving
- **Procedures** — durable workflows that compound capability
- **Observability** — attempt artifacts, telemetry, lineage tracking

## What Strata Is Not

Strata is not:

- **The product.** Astra is the product. Strata is an internal pipeline.
- **A provider that returns tokens.** Strata returns structured work products: validated answers, tool improvements, procedures, knowledge pages, eval results, code artifacts.
- **The only inference path.** SOTA cloud models can handle queries directly. Strata's pipeline is for cases where we want to use weak/local models and still get reliable output.
- **The core runtime.** Astra's agency layer (Prime, Assistants, scheduling, routing) is the core runtime. Strata sits underneath it.

## How Strata Integrates

Strata exposes two surfaces to Astra:

1. **Inference amplifier** — for queries where Astra wants a validated answer from a local model. The pipeline decomposes, validates, retries, and returns a high-confidence result.

2. **Work engine** — for tasks where Astra wants durable system improvements. The pipeline produces typed artifacts (tools, procedures, knowledge, eval results) that Astra consumes.

The exchange is bidirectional: Astra feeds Strata task definitions, context, tools, and capability grants. Strata feeds Astra validated results and durable artifacts.

## Migration Path

Strata's code migrates into `astra/pipeline/`:

| Current Location | Destination |
|---|---|
| `strata/orchestrator/` | `astra/pipeline/engine.py`, `tasks.py` |
| `strata/eval/` | `astra/pipeline/eval/` |
| `strata/knowledge/` | `astra/pipeline/knowledge.py` |
| `strata/context/` | `astra/pipeline/context.py` |
| `strata/procedures/` | `astra/pipeline/procedures.py` |
| `strata/observability/` | `astra/pipeline/observability.py` |
| `strata/storage/retention*` | `astra/pipeline/retention.py` |
| `strata/experimental/` | `astra/pipeline/eval/bootstrap.py` |

Strata's agency-layer code (background worker, API surface, UI) does not migrate — Astra's equivalents are more mature.

## The Endgame

The pipeline progressively earns trust by demonstrating quality across expanding task classes. As it proves reliable, more queries route through it instead of SOTA cloud. Eventually, the local model + pipeline becomes capable enough to serve as Astra's primary agent.

This transition is gradual and evidence-based, driven by the eval harness. There is no single switchover.

## What This Means for Strata Development

Current Strata development should focus on:

1. **Pipeline depth** — making the decompose → validate → retry loop more effective
2. **Eval coverage** — expanding the set of measurable task classes
3. **Knowledge and procedure accumulation** — the flywheel that makes the pipeline stronger over time
4. **Clean interfaces** — preparing modules for extraction into `astra/pipeline/`

Strata development should *not* focus on:

- Product UX (that's Astra's job)
- Multi-provider routing (that's Astra's job)
- Desktop/CLI distribution (that's Astra's job)
- Security model (that's Astra's job)
- Building another agency layer (Astra's wins)

## Relationship to Existing Strata Concepts

| Strata Concept | In Merged Product |
|---|---|
| Agent tier | Not directly mapped. Closest to an Astra Assistant running through the Strata pipeline |
| Trainer tier | Not a tier. The trainer function (improving the pipeline) is performed by Astra's Prime or a dedicated subagent |
| Background worker | Replaced by Astra's Assistant/Worker delegation model |
| Bootstrap loop | Preserved wholesale in `astra/pipeline/eval/` |
| Eval harness | Preserved wholesale. Becomes the quality backbone |
| Knowledge wiki | Preserved. Complements Astra's memory layer |
| Communication model | Ideas absorbed into Astra's comms. Code not ported |
| Model registry | Concepts (pools, transport policy) absorbed into Astra's provider registry |
| Desktop shell | Dropped. Astra owns desktop |
| React UI | Dropped. Astra owns UI |
