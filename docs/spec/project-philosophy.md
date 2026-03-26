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

### 3. Validation must come from outside the model

Models are allowed to propose. They are not trusted to declare themselves correct.

This is why the project emphasizes:

- validators
- downstream checks
- telemetry
- task and attempt separation
- fail-closed policies

Wherever possible, correctness should be established by the system and the data, not by model confidence.

### 4. Telemetry is part of the learning loop

Telemetry is not just for monitoring. It is an optimization substrate.

The point of storing structured outcomes is to answer questions like:

- which routing choices work for which task types
- which validators are catching real problems
- which decompositions help weak models succeed
- whether a system change improved the weak tier in practice

The system should evolve from measured outcomes, not from intuition alone.

## Weak/Strong Separation

The strong/weak split is intentional and foundational.

- The `strong` tier exists to bootstrap progress, propose improvements, and explore higher-capability changes.
- The `weak` tier represents the constrained local model the system is ultimately trying to empower.

This separation is not merely an implementation convenience. It encodes the developmental strategy of the project.

## Intended Bootstrap Sequence

The intended improvement loop is:

1. run a strong model inside the harness
2. let it propose or implement a system change
3. evaluate the weak model with that change in place
4. record telemetry about whether the weak model improved
5. adjust the system based on that telemetry
6. repeat

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
