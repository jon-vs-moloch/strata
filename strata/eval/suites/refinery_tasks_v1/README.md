# refinery_tasks_v1

Sixteen small but fiddly Python coding tasks (semantic-version ordering, glob
matching, Roman numerals, word wrap, an expression evaluator, CSV records,
business days, topological order, bowling, poker hands, JSON Pointer, ISO
weeks, a ledger, a text table, run-length coding). They exist to test
Strata's thesis on real multi-step work with a deterministic check: can the
refinery get a small local model to working code that it does not reach in
one raw call?

Each task is graded **only** by its pytest file. No LLM judge is involved.

## Layout

- `tasks.jsonl`: one task per line with `id`, `title`, `target_file`,
  `test_file`, `function` and `description`. The description is the complete
  spec.
- `tests/test_<id>.py`: the grader. It loads the module at `target_file`
  (relative to the current directory) or at `$REFINERY_TARGET`.

Reference solutions are deliberately **not** in the repository, because the
system under test can read the repository during research. They were checked
against every test before the bench ran, and are kept with the benchmark
record.

## Running

`scripts/refinery_tasks/` holds the drivers:

- `raw_pilot.py` sends each task to the model once.
- `run_pipeline.py` submits each task to a running Strata API and worker as
  an IMPL task.

Both use the same prompt text, the same code extraction and the same pytest
grader. See `docs/results.md` for the measured runs.
