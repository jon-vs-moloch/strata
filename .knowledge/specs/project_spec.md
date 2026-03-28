# Project Spec

This file stores the current high-level vision for the active Strata project.

Canonical location: `.knowledge/specs/project_spec.md`

Current project intent:
- extract useful work from small local models by pushing rigor into the system rather than the model
- improve outputs through multi-step refinement, validation against downstream data, and explicit evaluation
- use a stronger tier to improve the harness until the weak tier can improve the system itself
- treat repo structure, modularity, and progressive disclosure as supports for small models with small context
- keep bootstrap progress measurable through evals, telemetry, promotion evidence, and explainable provenance

Normal operating mode:
- weak Strata handles normal user-facing work and bounded autonomous self-improvement activity inside the harness
- strong Strata acts as supervisor and diagnostician for the weak system: proposing harness mutations, running deliberate diagnostics, and interpreting telemetry
- the default bootstrap relationship is strong -> weak, not weak/strong symmetry
- evals should support diagnosis and promotion decisions, not dominate wall-clock activity when the system could be doing useful work

Canonical supporting references:
- `README.md`
- `docs/spec/project-philosophy.md`
- `docs/spec/codemap.md`

Operational guidance:
- when the system detects durable user intent, route it into the spec proposal workflow rather than mutating the spec casually
- when alignment work is triggered, the spec files above are the source of truth and should be cited explicitly
- if the spec is missing detail, prefer a bounded spec-hardening task over claiming the project vision is unknown
