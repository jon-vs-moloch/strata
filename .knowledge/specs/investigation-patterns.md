# Investigation Patterns

This file stores durable failure-investigation lessons so future audits can start from named patterns instead of raw incident debris.

Canonical location: `.knowledge/specs/investigation-patterns.md`

Current durable guidance:
- ask "what failed first?" before focusing on the loudest downstream symptom; supervision churn and queue noise are often secondary failures
- when a bad claim originates before attempt execution, investigate the generation surface itself; missing verification hooks upstream of task execution are real failures, not just missing retries downstream
- preserve task semantics through recovery; if recovery replaces a bounded task with a generic shell like `Error Recover` or `Research manually`, treat that as a fresh failure, not progress
- when a task is fundamentally about clarification, confirmation, or operator preference, surface explicit pending questions rather than spending autonomous iterations on broad research
- if a question begins as non-blocking but current capabilities cannot resolve it, promote it to blocking; if new tooling or procedure unlocks autonomous progress, demote it back to non-blocking
- supervision should cash out into intervention; a trace review that only records `review_unavailable` or vague concern without changing routing, prompting, or user communication is incomplete recovery
- blocked-task supervision must require new evidence before rerunning; repeated reviews over unchanged evidence are telemetry churn, not learning
- prefer bounded replacement plans over recursive recovery placeholders; if a branch cannot produce a concrete plan, escalate or abandon explicitly instead of inventing semantic-free work
- compile findings during the investigation itself so later runs, audits, and self-evaluations can start from the current best model of the failure
- treat mutable config fields as a first-class evolutionary search surface; deterministic config mutation and eval search should be exhausted before asking the model to invent prompt or code mutations
- when the same tool fails repeatedly in the same lane/task context, treat that as a reusable diagnosis; record it structurally and circuit-break the tool instead of allowing another blind retry loop
