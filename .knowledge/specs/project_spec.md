# Project Spec

This file stores the current high-level vision for the active Strata project.

Canonical location: `.knowledge/specs/project_spec.md`

Current project intent:
- extract useful work from small local models by pushing rigor into the system rather than the model
- improve outputs through multi-step refinement, validation against downstream data, and explicit evaluation
- use a stronger tier to improve the harness until the weak tier can improve the system itself
- treat repo structure, modularity, and progressive disclosure as supports for small models with small context
- keep bootstrap progress measurable through evals, telemetry, promotion evidence, and explainable provenance
- treat deterministic preprocessing as part of the product, not just an optimization; inference should receive already-structured, evidence-rich tasks whenever possible

Normal operating mode:
- weak Strata handles normal user-facing work and bounded autonomous self-improvement activity inside the harness
- weak Strata should also author candidate mutations during normal bootstrap operation so the system can evaluate and improve that capability directly
- strong Strata acts as supervisor and diagnostician for the weak system: proposing harness mutations, running deliberate diagnostics, interpreting telemetry, and steering weak-side mutation quality
- the default bootstrap relationship is supervisory strong -> weak, with both tiers allowed to propose bounded mutations
- evals should support diagnosis and promotion decisions, not dominate wall-clock activity when the system could be doing useful work

Canonical supporting references:
- `README.md`
- `docs/spec/project-philosophy.md`
- `docs/spec/codemap.md`

Operational guidance:
- when the system detects durable user intent, route it into the spec proposal workflow rather than mutating the spec casually
- when alignment work is triggered, the spec files above are the source of truth and should be cited explicitly
- if the spec is missing detail, prefer a bounded spec-hardening task over claiming the project vision is unknown
- when duplicate detection, routing, or mutation selection is ambiguous, prefer a hybrid pipeline: deterministic preflight first, then inference over the reduced ambiguity set
