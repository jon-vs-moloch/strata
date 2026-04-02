# Task + Attempt Ontology

Minimal recursive work model for agentic systems.

Naming note:

- capitalized architecture terms refer to named first-class system artifacts or subsystems
- lowercase terms refer to the generic activity or concept

Examples:

- `Procedure` != procedure
- `Verifier` != verifier
- `Audit` != audit

## 1. Purpose

Define a simple, consistent model for representing work in a recursive system where:

- all meaningful objectives are `tasks`
- all variance-bearing execution happens through `attempts`
- deterministic fallout is still recorded, but is not confused with retryable learning
- durable reusable workflow artifacts can capture successful decompositions as first-class `Procedure`s
- success or failure is evaluated at explicit boundaries
- parent tasks can route around failing children
- root tasks cannot be bypassed

## 2. Core Model

### 2.1 Three layers

The system consists of:

- `task`
  A persistent unit of work in the task graph.
- `attempt`
  One variance-bearing invocation against a task.
- `execution record`
  A record of one concrete invocation or step, whether deterministic or non-deterministic.
- `Procedure`
  A durable, reusable, mutable workflow artifact that can instantiate tasks and preserve how a class of work should be done.
- `Kit`
  A durable, reusable bundle artifact that groups multiple first-class artifacts such as `Procedure`s, tools, evals, knowledge artifacts, policies, or other `Kit`s into one named unit.

### 2.2 Key principles

- Tasks persist. Attempts end.
- Procedures persist across many tasks and can be refined as the system learns better decompositions.
- nontrivial work should always be understood as executing a `Procedure`, even when that Procedure is still draft and being discovered live
- Kits persist across many tasks too, but as composition/bundling artifacts rather than execution artifacts; they package reusable capabilities that should travel and evolve together
- A task should be oneshottable at its own abstraction level.
- If progress requires a second semantically different non-deterministic step, the task was underspecified and should decompose.
- Deterministic work may surround an attempt, but does not become a new attempt just by taking time.
- Long-running work, deterministic or non-deterministic, must emit progress telemetry so the operator can see forward motion.
- Deterministic handoff between tasks matters. If a parent attempt already gathered useful evidence, child tasks should inherit an explicit deterministic handoff rather than spending a new variance-bearing attempt re-acquiring the same state.
- DAG structure determines handoff shape. Serial edges may hand deterministic state directly to the next node; parallel edges must merge through parent-owned branch state.
- Tool calls terminate a step. If a model emits a tool call, deterministic tool execution belongs to that same attempt, but any later model interpretation of the tool result must happen in a new explicit step or child task.

## 3. Task

### 3.1 Definition

A task represents one bounded objective that should plausibly be completable by one variance-bearing shot at the current model/tool capability level.

Tasks are:

- persistent
- recursive
- composable
- replaceable if they have a parent
- invalid if they secretly require multiple progressive stages like inspect, then patch, then validate

If work naturally breaks into progressive stages, those stages are not multiple attempts at one task. They are separate subtasks in a decomposition.

### 3.2 Task fields

```json
{
  "task_id": "uuid",
  "parent_task_id": "uuid | null",
  "title": "string",
  "description": "string",
  "state": "pending | working | blocked | pushed | complete | abandoned | cancelled",
  "priority": 0.0,
  "success_criteria": {},
  "active_child_ids": ["uuid"],
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### 3.3 Task states

`pending`

Task exists but has not yet been attempted.

`working`

Task is actively being pursued, either directly or via children.

`blocked`

Task cannot proceed until a dependency is resolved.

`pushed`

Task is acting as a coordination node rather than a runnable leaf.

For decomposed work, this should be read operationally as:

- the parent is now a coordination node
- the children own execution
- the parent should not be re-run as an ordinary leaf task while it still has live children
- if the child plan fails, reactivating the parent should be deliberate rather than accidental

`complete`

Task achieved its success criteria.

`abandoned`

Task will no longer be pursued.

`cancelled`

Task was invalidated before meaningful work began.

### 3.4 Parent rule

- `parent_task_id == null` -> root task
- `parent_task_id != null` -> child task

This is the only structural distinction that matters.

## 4. Attempt

### 4.1 Definition

An attempt is one variance-bearing execution instance of a task.

In practice, this usually means one non-deterministic model or sampler invocation, plus its immediate deterministic fallout before the next non-deterministic invocation.

Attempts are:

- ephemeral
- repeatable in principle, but meaningfully comparable only when variance exists
- the only place where stochastic success or failure occurs

### 4.2 Attempt boundary rule

One attempt must map to one non-deterministic invocation.

If another non-deterministic invocation is needed, that is not “attempt 2 of the same progressive stage” by default. It is evidence that:

- the task should decompose, or
- the next thing to do is a different task entirely

This means a task that really needs:

1. inspect and summarize
2. patch
3. validate

should decompose into three tasks rather than accumulate three progressive attempts under one task.

### 4.5 Procedures and decomposition

A successful decomposition pattern should not remain trapped inside one branch forever.

When the system repeatedly learns that a class of work decomposes in a stable way, that reusable process should be promoted into a `Procedure` artifact:

- `procedure`
  generic English description of a process
- `Procedure`
  a named system artifact with identity, instructions, checklist structure, success criteria, and eventually reusable subtask structure

In other words:

- tasks are the live work instances
- attempts are the variance-bearing shots on those tasks
- Procedures are the durable memory of how similar work should be structured in the future

### 4.6 Procedure lifecycle

Procedures should have an explicit lifecycle:

- `draft`
  work-in-progress Procedure inferred from live execution or decomposition; not yet proven
- `tested`
  Procedure has completed successfully at least once and is now eligible for reuse as a known-good baseline
- `vetted`
  intentionally curated or promoted Procedure trusted for default use
- `retired`
  lineage-visible but no longer preferred by default

Draft Procedures matter because they preserve partial progress:

- interrupted or restarted work can resume from a known proto-workflow
- live search leaves behind reusable structure instead of disappearing into task lineage
- successful draft Procedures can promote into `tested`, then continue evolving through lineage, variants, and evaluation

### 4.3 Attempt fields

```json
{
  "attempt_id": "uuid",
  "task_id": "uuid",
  "started_at": "timestamp",
  "ended_at": "timestamp",
  "outcome": "succeeded | failed | cancelled | superseded",
  "reason": "string",
  "evidence": [],
  "artifacts": {},
  "resolution": "reattempt | decompose | internal_replan | abandon_to_parent",
  "plan_review": {
    "plan_health": "healthy | uncertain | degraded | invalid",
    "recommendation": "continue | reattempt | decompose | internal_replan | abandon_to_parent",
    "confidence": 0.0,
    "rationale": "string"
  }
}
```

### 4.4 Attempt outcomes

`succeeded`

The attempt achieved the task’s success criteria.

`failed`

The attempt did not achieve the task’s success criteria.

`cancelled`

The attempt was stopped before meaningful completion.

`superseded`

The attempt was overtaken by a newer attempt or branch.

## 5. Execution Record

### 5.1 Definition

## 6. DAG Handoff Rules

### 6.1 Serial edges

For serial work, deterministic state may hand forward directly to the next dependency-ready node.

That means:

- child N completes
- its deterministic handback is recorded
- child N+1 inherits that handback as part of starting context

This is the normal shape for chains like:

1. inspect
2. decide
3. cash out

### 6.2 Parallel edges

For parallel work, siblings should not mutate each other's live context directly.

Instead:

- each child writes a deterministic handback into parent-owned branch state
- the parent acts as the merge point
- later serial steps or replans consume the merged branch state

### 6.3 Replanning

If a child triggers replanning, the coordination node should inspect:

- completed child outputs
- still-running or pending child status
- failure autopsies
- open questions and unresolved attention items

Replanning should happen from the coordination node with the whole branch picture available, not by sibling-to-sibling improvisation.

An execution record is the broader operational trace of one concrete invocation or step.

Examples:

- a non-deterministic model call
- a temperature-0 model call
- a tool invocation
- a deterministic handoff artifact derived from a prior tool result
- deterministic context assembly
- patch application
- validation
- file download

Not every execution record is an attempt.

### 5.2 Why keep it separate

This distinction lets the system:

- preserve full operational traceability
- keep deterministic and non-deterministic work visible in the UI
- avoid treating deterministic reruns as meaningful retries
- reason about learning over attempts rather than over all raw steps

Suggested fields:

```json
{
  "execution_record_id": "uuid",
  "task_id": "uuid",
  "attempt_id": "uuid | null",
  "kind": "model_call | tool_call | validation | download | context_load | other",
  "is_variance_bearing": true,
  "determinism_class": "nondeterministic | deterministic | mixed",
  "started_at": "timestamp",
  "ended_at": "timestamp",
  "progress": {
    "current": 10,
    "total": 100,
    "unit": "files"
  }
}
```

⸻

5. Failure Resolution

5.1 Required rule

If an attempt outcome is failed, it must include a resolution.

⸻

5.2 Resolution types

reattempt

Try again with the same task.

Use when:
	•	execution error
	•	stochastic failure
	•	minor fix needed

⸻

decompose

Split the task into child tasks.

Use when:
	•	task is too large or complex
	•	success requires multiple steps

Effect:
	•	create child tasks
	•	keep current task working

⸻

internal_replan

Generate alternative approaches within the same task.

Use when:
	•	strategy is wrong but task is still correct
	•	alternative methods exist without changing parent structure

Effect:
	•	replace or add child tasks under the same task

⸻

abandon_to_parent

Stop pursuing this task; let the parent solve the goal another way.

Use when:
	•	this approach is no longer viable
	•	constraints invalidate this branch
	•	better sibling strategies exist

Effect:
	•	mark this task abandoned
	•	parent may activate or generate alternative child tasks

⸻

5.3 Root constraint

If:

task.parent_task_id == null

Then:

resolution != abandon_to_parent

Root tasks cannot be bypassed—only:
	•	reattempted
	•	decomposed
	•	internally replanned
	•	or explicitly abandoned as a whole goal

⸻

6. Core Execution Loop

6.1 On task execution

For a given task:
	1.	Select or generate attempt
	2.	Execute attempt
	3.	Record outcome
	4.	If outcome == succeeded:
	•	mark task complete
	5.	If outcome == failed:
	•	classify resolution
	•	apply resolution

⸻

6.2 Resolution handling

reattempt
	•	create new attempt
	•	keep task working

decompose
	•	create child tasks
	•	set task working
	•	schedule children

internal_replan
	•	generate alternative children or structure
	•	mark previous approach superseded if needed
	•	keep task working

abandon_to_parent
	•	set task abandoned
	•	notify parent for alternative routing

⸻

7. Parent Behavior

7.1 Parent does not fail when child fails

A parent task remains working if:
	•	any child is still viable or active
	•	new children can be generated

⸻

7.2 Parent routing rule

If a child task becomes abandoned, the parent may:
	•	activate another existing child
	•	generate a new child task
	•	reattempt another branch
	•	decompose further

⸻

7.3 Parent completion rule

A parent task is complete when:
	•	its success criteria are satisfied
	•	regardless of how many child branches were abandoned

⸻

8. Example

Scenario

Goal:
	•	Go to school

⸻

Representation

Go to school [working]
├─ Catch bus [abandoned]
│  └─ Attempt #1 → failed
│     reason: arrived after 8
│     resolution: abandon_to_parent
│
└─ Walk [working]
   ├─ Walk Ave A [working]
   └─ Walk 2nd Street [pending]


⸻

Interpretation
	•	Failure occurred at attempt level
	•	Bus strategy was abandoned
	•	Goal persists
	•	Alternative strategy activated

⸻

9. Metrics

9.1 Track separately

Task-level
	•	completion rate
	•	abandonment rate

Attempt-level
	•	success rate
	•	failure rate

Resolution distribution
	•	% reattempt
	•	% decompose
	•	% internal_replan
	•	% abandon_to_parent

⸻

9.2 Important rule

Do not treat:

attempt failure == task failure


⸻

10. UI Semantics

10.1 Required phrasing

The system must support status like:
	•	“Still working on goal”
	•	“Previous approach abandoned”
	•	“Trying alternative method”
	•	“Task decomposed into subtasks”

⸻

10.2 Avoid
	•	“Task failed” (for non-root tasks still being pursued indirectly)

⸻

11. Invariants
	1.	Every attempt belongs to exactly one task
	2.	Tasks may have multiple attempts
	3.	Only attempts can succeed or fail
	4.	Failed attempts must include a resolution
	5.	Tasks may remain working across multiple failed attempts
	6.	Parent tasks are not failed by child failure
	7.	Root tasks cannot use abandon_to_parent
	8.	Abandoned tasks do not automatically imply parent abandonment
	9.	Every attempt must include a plan review to assess the continued viability of the broader task strategy.

⸻

12. Minimal Ruleset (for system ingestion)
	1.	Everything is a Task.
	2.	Execution happens via Attempts.
	3.	Only Attempts succeed or fail.
	4.	Failed Attempts must include a resolution:
	•	reattempt
	•	decompose
	•	internal_replan
	•	abandon_to_parent
	5.	Tasks with parents may be abandoned in favor of sibling solutions.
	6.	Root Tasks cannot be abandoned_to_parent.
	7.	Parent Tasks remain working while alternative branches exist.
	8.	Metrics must separate task outcomes from attempt outcomes.

⸻

13. Handoff Block for Antigravity

Implement a minimal task system using two core objects: Task and Attempt.

Core model:
- Everything is a Task.
- Execution occurs through Attempts.
- Only Attempts succeed or fail.

Task:
- Persistent node with:
  - task_id
  - parent_task_id (nullable)
  - state (pending, working, blocked, pushed, complete, abandoned, cancelled)
  - success criteria
  - active children

Attempt:
- Ephemeral execution with:
  - attempt_id
  - task_id
  - outcome (succeeded, failed, cancelled, superseded)
  - reason
  - evidence
  - artifacts
  - resolution

Critical rule:
- If outcome == failed, resolution is required.

Allowed resolutions:
- reattempt
- decompose
- internal_replan
- abandon_to_parent

Behavior:
- Tasks persist across failed attempts.
- Parent tasks are not failed when a child fails.
- Child tasks may be abandoned to allow parent to pursue alternatives.
- Root tasks (no parent) cannot use abandon_to_parent.

System behavior:
- On failed attempt, choose a resolution and apply it.
- On success, mark task complete.
- On abandonment, mark task abandoned and allow parent to route around it.

Metrics:
- Track task completion separately from attempt success/failure.
- Track resolution distribution.

UI:
- Must express branch abandonment and continued work on parent goals.
- Avoid phrasing that implies global failure when only a branch failed.


⸻

Final compression

Tasks define the search space.
Attempts explore it.
Failure reshapes the search.
