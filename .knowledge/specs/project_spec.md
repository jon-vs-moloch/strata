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
- the agent's primary job is: help the user; improve yourself
- the agent should primarily rely on self-audit, verification, and user communication rather than assuming trainer rescue
- agent Strata should also author candidate mutations during normal bootstrap operation so the system can evaluate and improve that capability directly
- trainer Strata acts as supervisor and diagnostician for the agent system: proposing harness mutations, running deliberate diagnostics, interpreting telemetry, and steering agent-side mutation quality
- the trainer's primary job is: improve the agent and yourself; observe, diagnose, and improve the agent's ability to improve itself
- trainer supervision should be proactive observation of agent traces, outputs, and failure patterns, not a hidden assumption that the agent can always escalate upward successfully
- the default bootstrap relationship is supervisory trainer -> agent, with both tiers allowed to propose bounded mutations
- trainer and agent lanes should execute independently and avoid shared bottlenecks; a busy or stalled trainer loop must not prevent agent work from running, and vice versa
- trainer and agent should use distinct inference pools by default; cross-tier assistance should be expressed as explicit task/review handoffs rather than silent cross-pool execution
- onboarding is an agent-facing startup procedure; trainer bootstrap and supervision work should continue unless explicitly paused, rather than waiting on operator-facing onboarding steps that do not belong to the trainer role
- evals should support diagnosis and promotion decisions, not dominate wall-clock activity when the system could be doing useful work
- trainer supervision should treat verifier findings and deterministic contradictions as first-class evidence; repeated verifier failures without correction indicate a system-level supervision gap, not merely a task that needs more retries
- verification policy should be shared across tiers and anneal from measured error rate rather than from hardcoded role-specific trust
- the product shell should stay minimal and trustworthy: bundled code should provide continuity and safe-mode fallback, while higher-level UI and tool surfaces should increasingly be delivered through validated runtime modules or plugins
- task boundaries should be chosen so one variance-bearing invocation can plausibly complete the task; if work naturally requires inspect, then patch, then validate, those are separate subtasks rather than multiple progressive attempts at one task
- because decomposition quality now carries more of the intelligence, durable procedures should be treated as a primary substrate for reusable process knowledge, recovery logic, and compounding behavioral improvement
- formal terminology matters here: a generic `procedure` is ordinary language, while a `Procedure` is a durable system artifact representing a reusable and mutable workflow that can be rerun, refined, and eventually learned from prior successful decompositions
- this proper-noun convention applies across the architecture: `Procedure`, `Verifier`, `Audit`, and similar capitalized terms name first-class system artifacts or subsystems, while lowercase terms refer to the ordinary generic activity
- partial success should not be discarded; useful decompositions, clarified subgoals, successful recoveries, and reusable intermediate structure should be captured into durable artifacts such as Procedures, knowledge, or policy updates
- failures should metabolize into durable improvements too; repeated failure modes, blocked branches, verifier findings, and recovery dead ends should cash out into Procedures, tool health, policy changes, or other persistent system adaptations
- notifying the trainer is not, by itself, a recovery. When an autonomous branch fails to decompose or plan cleanly, the system should continue pursuing bounded self-recovery unless the branch is truly blocked on required external input or permission
- failures should always produce an explicit "what's next" decision. The system should never treat a failed attempt as the end of the line without choosing a concrete continuation path such as decomposition, replanning, remediation, escalation, or other bounded follow-on work

Canonical supporting references:
- `README.md`
- `docs/spec/project-philosophy.md`
- `docs/spec/codemap.md`
- `docs/spec/system-substrates.md`
- `.knowledge/specs/investigation-patterns.md`
- `docs/spec/task-attempt-ontology.md`

Operational guidance:
- when the system detects durable user intent, route it into the spec proposal workflow rather than mutating the spec casually
- when alignment work is triggered, the spec files above are the source of truth and should be cited explicitly
- seed operator onboarding before agent-side idle alignment; if onboarding is still active or incomplete, prefer progressing or clarifying onboarding over inventing freeform alignment work
- if the spec is missing detail, prefer a bounded spec-hardening task over claiming the project vision is unknown
- when repository facts are uncertain, prefer verification or explicit uncertainty over asserting absence from a partial snapshot
- when duplicate detection, routing, or mutation selection is ambiguous, prefer a hybrid pipeline: deterministic preflight first, then inference over the reduced ambiguity set
- user-chat feedback should be gathered as durable evidence and eventually distilled into maintained user knowledge, agent knowledge, project intent, and other reviewable state rather than remaining an isolated UI-side signal
- user escalation should support both `blocking` and `non_blocking` modes, and the system should be able to promote or demote between them as capabilities change
- prioritization should be surprise-sensitive: expected successes and expected failures usually warrant less attention than outcomes that violate the system's current expectations, because unexpected outcomes are often the strongest evidence that the model of the user, task, or harness is incomplete
- reflection should be implemented as self-audit rather than a parallel mechanism; the same audit pipeline should be able to inspect external task traces, internal process traces, attention signals, prior audit artifacts, and other reviewable sequences
- verification should be a fully general callable process over arbitrary steps and artifacts, not a post-attempt-only hook; audits may invoke verification, and verification outputs should themselves remain auditable
- one attempt should correspond to one variance-bearing invocation plus bounded deterministic fallout before the next invocation; if another semantically different invocation is needed, prefer decomposition over treating it as just another progressive retry
- long-running work, deterministic or non-deterministic, should emit explicit progress telemetry so the operator can distinguish healthy forward motion from true idleness or wedged execution
- runtime surfaces should expose live attempt-step state in real time so the operator can see whether a lane is routing, generating, executing a tool, validating, reviewing, or truly idle
- the agent should be allowed to discover and execute a decomposition needed to complete a Procedure, and once that decomposition proves stable, the resulting process should be eligible to fold back into the Procedure artifact itself
- tool telemetry should support scope-aware circuit breakers so the system can learn "this tool is broken for this lane doing this class of work" and stop spamming the same failing call until remediation is underway
- prompt/context budget should be treated as an explicitly managed resource: pinned context should carry priority, context pressure should be surfaced into prompts and operator views, and low-value or stale context should be compactable deterministically before it silently crowds out better evidence
- useful tool results should normally be handed forward into child work deterministically; the system should not force a new attempt to spend its only move re-acquiring evidence the parent already gathered
- DAG shape matters for deterministic handoff:
  - serial chains may hand branch state directly to the next dependency-ready node
  - parallel children must merge upward through parent-owned branch state
  - replanning should inspect the full active child set, not only the last failing child
- surprising signals should themselves remain auditable so the system can ask not only "what happened?" but also "was it right to be surprised by this?" and recalibrate its own attention policy
- prefer evolving existing pipelines into shared primitives instead of creating adjacent special-purpose systems; a new subsystem should justify itself by becoming reusable across multiple Strata surfaces
- plugin and module interfaces should be explicit and versioned so interchangeable product surfaces are normal behavior, not bespoke glue code
- deterministic mutation search over mutable config fields should be treated as part of the system's evolutionary hardware; search that space deliberately before escalating to prompt or code mutation
- inference throttling should support at least two explicit postures: `hard` ("do not exceed this limit") and `greedy` ("push up to the best currently believed safe/provider-friendly limit while probing carefully to improve that estimate")
- local-resource policy should optimize for operator comfort rather than raw throughput alone; for local inference, the default target should be "not annoying" under current conditions, with ambiguous cases resolved in favor of quieter/lighter operation unless the operator has explicitly opted in to more aggressive behavior
- the system should treat comfort constraints such as fan noise, memory pressure, and similar resource-side effects as measurable control surfaces, not merely informal preferences
