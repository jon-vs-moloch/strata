# Project Spec

This file stores the current high-level vision for the active Strata project.

Canonical location: `.knowledge/specs/project_spec.md`

Current project intent:
- extract useful work from small local models by pushing rigor into the system rather than the model
- improve outputs through multi-step refinement, validation against downstream data, and explicit evaluation
- use the trainer tier to improve the harness until the agent tier can improve the system itself
- treat repo structure, modularity, and progressive disclosure as supports for small models with small context
- keep bootstrap progress measurable through evals, telemetry, promotion evidence, and explainable provenance
- treat deterministic preprocessing as part of the product, not just an optimization; inference should receive already-structured, evidence-rich tasks whenever possible

Normal operating mode:
- agent Strata handles normal user-facing work and bounded autonomous self-improvement activity inside the harness
- agent Strata should also author candidate mutations during normal bootstrap operation so the system can evaluate and improve that capability directly
- trainer Strata acts as supervisor and diagnostician for the agent system: proposing harness mutations, running deliberate diagnostics, interpreting telemetry, and steering agent-side mutation quality
- the default bootstrap relationship is supervisory trainer -> agent, with both tiers allowed to propose bounded mutations
- evals should support diagnosis and promotion decisions, not dominate wall-clock activity when the system could be doing useful work
- trainer supervision should treat verifier findings and deterministic contradictions as first-class evidence; repeated verifier failures without correction indicate a system-level supervision gap, not merely a task that needs more retries
- verification policy should be shared across tiers and anneal from measured error rate rather than from hardcoded role-specific trust

Canonical supporting references:
- `README.md`
- `docs/spec/project-philosophy.md`
- `docs/spec/codemap.md`

Operational guidance:
- when the system detects durable user intent, route it into the spec proposal workflow rather than mutating the spec casually
- when alignment work is triggered, the spec files above are the source of truth and should be cited explicitly
- if the spec is missing detail, prefer a bounded spec-hardening task over claiming the project vision is unknown
- when duplicate detection, routing, or mutation selection is ambiguous, prefer a hybrid pipeline: deterministic preflight first, then inference over the reduced ambiguity set
- user-chat feedback should be gathered as durable evidence and eventually distilled into maintained user knowledge, agent knowledge, project intent, and other reviewable state rather than remaining an isolated UI-side signal
- prioritization should be surprise-sensitive: expected successes and expected failures usually warrant less attention than outcomes that violate the system's current expectations, because unexpected outcomes are often the strongest evidence that the model of the user, task, or harness is incomplete
- reflection should be implemented as self-audit rather than a parallel mechanism; the same audit pipeline should be able to inspect external task traces, internal process traces, attention signals, prior audit artifacts, and other reviewable sequences
- surprising signals should themselves remain auditable so the system can ask not only "what happened?" but also "was it right to be surprised by this?" and recalibrate its own attention policy
- prefer evolving existing pipelines into shared primitives instead of creating adjacent special-purpose systems; a new subsystem should justify itself by becoming reusable across multiple Strata surfaces
