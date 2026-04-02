# Project Spec

This file stores the current high-level vision for the active Strata project.

Canonical location: `.knowledge/specs/project_spec.md`

Current project intent:
- extract useful work from small local models by pushing rigor into the system rather than the model
- improve outputs through multi-step refinement, validation against downstream data, and explicit evaluation
- use the trainer tier to improve the harness until the agent tier can improve the system itself
- treat repo structure, modularity, and progressive disclosure as supports for small models with small context
- keep bootstrap progress measurable through evals, telemetry, promotion evidence, and explainable provenance
- all durable system actions should carry provenance that explains authority, causal chain, and governing policy, not only outcome
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
- severe verifier findings should be able to trigger `Audit` directly rather than waiting for passive review aggregation when immediate branch diagnosis is warranted
- reusable capability health should be sticky: once a tool or process is marked degraded by verification or audit, it should stay degraded until explicit re-greening evidence is recorded
- the product shell should stay minimal and trustworthy: bundled code should provide continuity and safe-mode fallback, while higher-level UI and tool surfaces should increasingly be delivered through validated runtime modules or plugins
- task boundaries should be chosen so one variance-bearing invocation can plausibly complete the task; if work naturally requires inspect, then patch, then validate, those are separate subtasks rather than multiple progressive attempts at one task
- because decomposition quality now carries more of the intelligence, durable procedures should be treated as a primary substrate for reusable process knowledge, recovery logic, and compounding behavioral improvement
- formal terminology matters here: a generic `procedure` is ordinary language, while a `Procedure` is a durable system artifact representing a reusable and mutable workflow that can be rerun, refined, and eventually learned from prior successful decompositions
- a `Kit` is a durable bundle artifact that packages multiple other first-class artifacts together; a Kit may include Procedures, tools, evals, knowledge artifacts, policies, or other Kits
- the system should treat nontrivial work as always executing a `Procedure`; if no vetted Procedure exists yet, live execution should mint or refresh a draft Procedure rather than pretending the workflow is structureless
- this proper-noun convention applies across the architecture: `Procedure`, `Kit`, `Verifier`, `Audit`, and similar capitalized terms name first-class system artifacts or subsystems, while lowercase terms refer to the ordinary generic activity
- partial success should not be discarded; useful decompositions, clarified subgoals, successful recoveries, and reusable intermediate structure should be captured into durable artifacts such as Procedures, knowledge, or policy updates
- failures should metabolize into durable improvements too; repeated failure modes, blocked branches, verifier findings, and recovery dead ends should cash out into Procedures, tool health, policy changes, or other persistent system adaptations
- repeated failures of reusable machinery, whether a tool or a higher-level process, should degrade that capability and route toward explicit repair of the owning artifact
- durable capability history should include enough versioning or snapshots to let later audits ask “was this actually broken at the time of incident?” rather than inferring from a later repaired state
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
- every nontrivial system decision should be explainable in terms of either direct user instruction or spec-derived policy; “the system decided” is not an acceptable terminal explanation
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
- user-facing chat should acknowledge long-running work immediately in conversational language before the slow work starts, so latency never looks like silence or dropped input
- the durable term for non-thinking responses is `instant`; latency-sensitive routing should explicitly choose between `instant` and thinking responses instead of treating all replies as the same kind of completion
- when chat work requires multiple tools or longer processing, the system should narrate what it is doing and periodically report progress rather than going dark
- long-running chat work should prefer the background worker when that preserves responsiveness without losing correctness
- runtime surfaces should not hide spawned work; verification, audits, reviews, and other child processes are part of the visible task tree and should remain operator-visible by default
- operator-facing work surfaces should allow bounded interaction with internal machinery; when policy permits, the user should be able to direct things like verification, audit, retry, pause, resume, or decomposition from the relevant task/attempt surface instead of those controls being hidden
- the agent should be allowed to discover and execute a decomposition needed to complete a Procedure, and once that decomposition proves stable, the resulting process should be eligible to fold back into the Procedure artifact itself
- Procedure lifecycle should be explicit: `draft` means work-in-progress and not yet proven, `tested` means the workflow completed successfully at least once, and `vetted` means intentionally trusted or promoted; after `tested`, Procedures and even their steps should be allowed to evolve through lineage, variants, and evaluation
- `Kit` lifecycle should parallel other mutable artifacts: bundled members may begin as draft compositions, become tested once the bundle works as intended at least once, and later be vetted/promoted, variant-compared, or retired
- Kits should be recursively composable so higher-level capability bundles can include lower-level Kits without flattening away structure
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
- the system should not depend on the user for routine housekeeping; stale tasks, stale channels, and other clutter should summarize, compact, age, and archive automatically unless the user explicitly intervenes
- operator visibility and control should be profile-aware over time: Strata should be able to support simple chat-only operation, power-user/developer operation, and stricter managed/enterprise profiles without changing the underlying system model
- bugs and regressions should live in a durable tracker rather than only in thread history; active runtime defects, truthfulness gaps, and operator pain points should be recorded in [bug-tracker.md](/Users/jon/Projects/strata/docs/spec/bug-tracker.md) so trainer/alignment work can consume them as real backlog
- operator-facing surfaces such as `History`, `Tasks`, `Procedures`, `Tools`, and `Knowledge` should not remain read-only viewers by default; they should evolve toward bounded interaction and editing surfaces where the operator can inspect, modify, queue, verify, audit, or otherwise act on the underlying artifacts
- `History` should evolve toward a true append-only event ledger with lifecycle provenance for reads, writes, edits, redactions, compactions, opens, closes, and other consequential interactions
- Strata should eventually expose a first-class operator `Workbench` surface: a universal debugger-like environment where any Strata process can be replayed, stepped, paused, branched, regenerated from intermediate context, and re-run under different tools, models, or inputs
- Workbench semantics should preserve real process shape rather than flatten it into logs: the operator should be able to inspect exact per-node inputs, outputs, handoff behavior, downstream consumers, and alternate branches, including verification and audit subflows
- reflection resolution should be arbitrarily high: Strata should be able to inspect and edit its own tools, Procedures, Knowledge artifacts, runtime policies, source code, and UI from within Strata itself, subject to whatever bounded controls/policy gates are active for the current operator profile
