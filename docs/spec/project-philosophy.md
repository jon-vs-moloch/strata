# Strata Project Philosophy

## Purpose

Strata exists to answer a practical question:

Can a modest local model become genuinely useful if the system around it supplies the rigor, memory, validation, and iterative structure that the model itself lacks?

The project assumes the answer is yes, but only if intelligence is treated as a systems problem rather than a single-model problem.

## Core Thesis

Strata is designed around two goals at once:

1. extract useful work from small local models
2. turn that refined capability into an agent that can do meaningful work on modest local hardware

The central idea is not "make the model smarter" in isolation. The central idea is:

- refine outputs through multi-step processes
- validate outputs against downstream reality
- avoid relying on the model to self-police
- move rigor, memory, routing, and evaluation into the surrounding system

In other words, the model is only one component in the loop. The system is where most of the intelligence is supposed to live.

## Design Commitments

Several design decisions in this repository follow directly from that thesis.

### 1. Small models should be able to participate

The architecture is intentionally biased toward workflows that a small model with limited context can still navigate.

That means:

- work is broken into smaller units
- context is externalized into storage, memory, and task structure
- intermediate artifacts are persisted instead of kept only in prompts
- evaluation criteria are explicit where possible

Repo structure is part of this design. It is easier for a constrained model to reason about a system that is decomposed into narrow modules with persistent state and visible interfaces than about a monolithic "magic agent."

### 2. Refinement beats one-shot generation

Strata assumes raw generations are not enough.

Useful outputs come from staged processing:

- framing
- decomposition
- implementation
- evaluation
- retry or reroute based on outcomes

The project treats generation as the beginning of work, not the end of it.

That same principle applies to inference configuration.

The mutable unit is not just "which model to call." The mutable unit is the full inference-shaping config for a lane, including:

- model selection
- prompt/preamble/profile
- context payload
- inference parameters
- output schema or tool contract
- in-pool escalation order

This matters because the thing that should be evaluated and promoted is often the mixed strategy, not a single model call in isolation. A fast-first configuration that escalates to a slower lane only when needed may outperform both "always fast" and "always slow" in end-to-end throughput and quality.

### 3. Desired outcomes should become eval targets

If we want a property from the system, we should turn it into something measurable instead of treating it as a hope or a vibe.

That applies to outcomes like:

- factual correctness
- useful decomposition
- tool-use reliability
- disclosure restraint
- latency or token efficiency
- self-improvement success

The project should prefer explicit evals, benchmarks, and telemetry for these properties over informal confidence that the prompt or architecture "probably" handles them.

### 4. Validation must come from outside the model

Models are allowed to propose. They are not trusted to declare themselves correct.

This is why the project emphasizes:

- validators
- downstream checks
- telemetry
- task and attempt separation
- fail-closed policies

Wherever possible, correctness should be established by the system and the data, not by model confidence.

### 5. Telemetry is part of the learning loop

Telemetry is not just for monitoring. It is an optimization substrate.

The point of storing structured outcomes is to answer questions like:

- which routing choices work for which task types
- which validators are catching real problems
- which decompositions help weak models succeed
- whether a system change improved the weak tier in practice
- whether a desired property actually improved after a change

The system should evolve from measured outcomes, not from intuition alone.

## Weak/Strong Separation

The strong/weak split is intentional and foundational.

- The `strong` tier exists to bootstrap progress, propose improvements, and explore higher-capability changes.
- The `weak` tier represents the constrained local model the system is ultimately trying to empower.

These are role boundaries, not permanent provider categories.

- by default, `strong` means the bootstrap/supervision lane
- by default, `weak` means the normal execution lane
- the current operational assumption remains `strong -> cloud-preferred` and `weak -> local-preferred`
- but either pool may eventually point at local or cloud inference depending on the active config

The important distinction is:

- in-pool escalation is normal strategy behavior
- cross-pool escalation is a separate policy boundary

By default, Strata should not silently escalate weak work into strong. If cross-pool escalation is introduced later, it should be explicit, telemetered, and policy-controlled.

This separation is not merely an implementation convenience. It encodes the developmental strategy of the project.

## Intended Bootstrap Sequence

The intended improvement loop is:

1. run a strong model inside the harness
2. let it propose or implement a system change
3. evaluate the weak model with that change in place
4. record telemetry about whether the weak model improved
5. adjust the system based on that telemetry
6. repeat

In day-to-day operation, this should not collapse into "every tier proposes everything all the time."

- The weak tier should keep doing normal system work, including user-facing tasks and bounded autonomous work inside the harness.
- The strong tier should primarily supervise the weak tier: diagnose failures, propose harness repairs, run targeted evaluations, and decide which mutations are worth promoting.
- Extra eval sampling is useful as telemetry, but should stay subordinate to the real goal of improving weak-tier behavior.

The target state is not just "the strong model can improve Strata."

The target state is that repeated system improvements eventually enable the weak model to make a meaningful improvement to the system by itself, such as:

- completing a broader class of tasks
- using tools more reliably
- decomposing work more effectively
- adding or refining a capability under evaluation

At that point, the system is no longer merely compensating for a weak model. It is teaching a weak model how to be useful.

## Why The Repository Looks Like This

The current structure reflects the need to keep reasoning local and explicit:

- `strata/orchestrator/` contains the control logic, because agent behavior should be inspectable and evolvable
- `strata/storage/` persists system state, because limited-context agents need durable external memory
- `strata/memory/` stores semantic recall, because useful context should be retrieved instead of re-explained
- `strata/models/` isolates provider and routing behavior, because model choice is part of the experiment
- `docs/spec/` captures durable design intent, because otherwise the "why" disappears behind implementation details

This structure is meant to help both humans and small models answer the same question:

What is this subsystem for, and how does it fit into the improvement loop?

The same principle now applies to communication. Replies, autonomous notices, and operator-facing recommendations should route through one explicit communication substrate rather than being scattered side effects across the codebase. That contract is documented in [communication-model.md](/Users/jon/Projects/strata/docs/spec/communication-model.md).

## Non-Goal

The project is not primarily trying to demonstrate that a single prompt can turn a small model into a great autonomous engineer.

It is trying to demonstrate that a disciplined harness can extract increasingly capable behavior from limited models through structure, validation, and iterative self-improvement.

## Practical Standard

When making design decisions in Strata, prefer the option that:

- reduces reliance on hidden prompt cleverness
- increases explicit state and inspectability
- makes failure measurable
- improves the odds that a small model with a small context window can still succeed
- helps the system learn from outcomes over time

That is "what we are doing here." The rest is implementation detail.

## Near-Medium-Term Direction

Two productization directions are now intentional parts of the project's trajectory.

### 1. Desktop Shell Without Architectural Lock-In

Strata should eventually become a real desktop application that can launch from the taskbar or menu bar, restore its own window, manage startup/update behavior, and feel like an installable program instead of only a localhost workflow.

That should be accomplished by wrapping the existing web UI and backend lifecycle, not by collapsing the project into a desktop-only architecture.

The design constraint is:

- preserve a backend/API boundary that still allows future web and mobile clients

In other words, desktop should be a shell around Strata, not a fork of Strata.

### 2. Strata-Managed Local Inference

Strata should eventually own more of the local inference lifecycle instead of assuming that LM Studio is always the external operator-managed bridge from model files to inference.

The intended level of ownership is operational rather than kernel-level:

- Strata should be able to download/select models, launch and supervise a local inference engine, health-check it, route requests to it, and recover when it fails
- Strata should prefer wrapping established engines such as MLX, vLLM-class runtimes, or promising successors if they prove faster and stable enough in practice
- Strata should avoid taking on the responsibility of implementing low-level inference itself unless that becomes strategically necessary

This keeps the project focused on orchestration, evaluation, and system intelligence while still reducing reliance on manual sidecar tools.
