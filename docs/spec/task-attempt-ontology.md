
Task + Attempt Ontology Spec

Minimal recursive work model for agentic systems

1. Purpose

Define a simple, consistent model for representing work in a recursive system where:
	•	all work units are Tasks
	•	all execution happens through Attempts
	•	failure is not terminal—it produces structured next actions
	•	parent tasks can route around failing children
	•	root tasks cannot be bypassed

⸻

2. Core Model

2.1 Two primitives only

The system consists of:

Task

A persistent unit of work (node in a tree/graph)

Attempt

A concrete execution of a task

⸻

2.2 Key principle

Tasks persist. Attempts succeed or fail.

⸻

3. Task

3.1 Definition

A Task represents an objective that may be pursued through multiple attempts and/or subtasks.

Tasks are:
	•	persistent
	•	recursive
	•	composable
	•	replaceable (if they have a parent)

⸻

3.2 Task fields

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


⸻

3.3 Task states

pending

Task exists but has not yet been attempted.

working

Task is actively being pursued, either directly or via children.

blocked

Task cannot proceed until a dependency is resolved.

pushed

Task is deprioritized in favor of higher-priority work.

complete

Task achieved its success criteria.

abandoned

Task will no longer be pursued.

cancelled

Task was invalidated before meaningful work began.

⸻

3.4 Parent rule

parent_task_id == null → root task
parent_task_id != null → child task

This is the only structural distinction that matters.

⸻

4. Attempt

4.1 Definition

An Attempt is a single execution instance of a task.

Attempts are:
	•	ephemeral
	•	repeatable
	•	the only place where success or failure occurs

⸻

4.2 Attempt fields

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


⸻

4.3 Attempt outcomes

succeeded

The attempt achieved the task’s success criteria.

failed

The attempt did not achieve the task’s success criteria.

cancelled

The attempt was stopped before meaningful completion.

superseded

The attempt was overtaken by a newer attempt or branch.

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