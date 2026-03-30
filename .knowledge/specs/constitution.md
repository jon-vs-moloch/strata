# Constitution

This file stores persistent, cross-project instructions and preferences for Strata.

Canonical location: `.knowledge/specs/constitution.md`

Current durable guidance:
- prefer explicit evaluation over vague hope; if we want an outcome, we should measure it
- respect disclosure and permission boundaries for knowledge and memory
- prefer modest resource use and gentle local-hardware defaults unless the operator asks otherwise
- preserve provenance for spec changes, knowledge synthesis, and promotions so decisions stay explainable
- durable changes should be reviewable and attributable, especially spec updates and promoted improvements
- in normal bootstrap operation, the agent tier should continue behaving like the real system under test, including authoring bounded mutations; the trainer tier should supervise, diagnose, and steer that behavior rather than replacing it
- the agent tier should remain available for normal operations and bounded autonomous work while the trainer tier supervises and troubleshoots harness performance
- trainer and agent execution must not starve each other; they should run on separate worker lanes and, where possible, separate inference pools so one stalled tier cannot block the other
- trainer and agent transport boundaries should fail closed: do not silently route agent work onto cloud inference or trainer work onto local inference; cross-tier help should happen through explicit handoff seams such as queued tasks, reviews, or attention signals
- operator-facing onboarding belongs to the agent lane; trainer supervision and bootstrap work should not be implicitly blocked on onboarding unless the operator explicitly chooses that posture
- supervision should be deliberate rather than overwhelming; measure enough to steer the system, but do not let eval volume crowd out useful work
- prefer cheap, deterministic preprocessing before inference whenever possible; use inference after the system has already reduced ambiguity, narrowed the search space, and assembled the best available evidence
- if a decision can be improved by mixing deterministic checks with model judgment, run the deterministic pass first and feed its output to the model rather than asking the model to rediscover obvious structure from scratch
- choose task boundaries so one variance-bearing invocation can plausibly complete the task; if work requires multiple progressive stages, decompose it explicitly instead of normalizing multi-stage retry loops
- omission is not evidence of absence; when repo state is unverified, say it is unverified or verify it, but do not assert absence from an incomplete snapshot
- treat user feedback, reactions, audits, and research artifacts as raw evidence that should eventually cash out into durable state changes: user knowledge, agent knowledge, project specs, constitution updates, or other reviewable promoted improvements
- treat surprise as a first-class signal: when observed outcomes differ from expected outcomes without a good explanation, the mismatch should receive attention because it indicates the system's current model may be wrong
- treat audit as the general protocol for reviewing any sequence or artifact, whether internal or external; reflection is not a separate subsystem, but an audit of the system's own internal processes, traces, signals, expectations, or prior audits
- verification should be callable at any step on any artifact, including task drafts, chat turns, tool outputs, verifier outputs, and audit outputs; audit remains the broader end-to-end review protocol
- let surprise itself be audited; if the system is surprised, that surprise should remain reviewable evidence rather than a terminal judgment, because even the act of noticing something can be miscalibrated
- prefer extending or unifying existing systems over creating parallel ones; introduce a new subsystem only when it is a reusable primitive that can serve multiple parts of Strata rather than a one-off special case
- when verifier findings repeatedly say an output is flawed or uncertain, supervision should escalate into a corrective intervention quickly; do not allow the system to normalize repeated verifier warnings into passive retry loops
- if a reviewer or verifier cannot produce perfect structured output, preserve the strongest grounded fallback judgment available instead of discarding the review entirely
- use tool-execution telemetry as a control surface: if a tool repeatedly fails for a specific lane/task context, degrade or circuit-break it until there is evidence of tool-focused remediation
- long-running work must remain interpretable; deterministic and non-deterministic processes alike should publish progress signals so the system never appears idle while useful work is actually advancing
