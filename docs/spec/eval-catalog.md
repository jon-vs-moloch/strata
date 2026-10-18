# Eval Catalog

This catalog lists the eval suites currently checked into Strata's
structured-eval lane. These suites are meant to support apples-to-apples,
no-context comparisons between:

- raw model
- harness without tools/web
- harness with optional tools/web profiles

The current bundled suite is:

- `bootstrap_mcq_v1`
  Source: written for Strata (questions about its own operating model)
  Format: multiple choice
  Intended use: checks that a model or harness understands Strata's bootstrap
  loop, quiet mode, secondary ignition and promotion rules

This snapshot bundles no items from external benchmark datasets. To add a
suite, drop a JSONL file into `strata/eval/suites/` with one case per line
(`id`, `prompt`, `expected_answer`, `grading`); the eval catalog endpoint lists
whatever is there.

Small suites let Strata accumulate eval signal quickly without saturating
local hardware. They are not substitutes for full benchmark runs, but they are
useful for:

- trend tracking over time
- weak vs strong comparisons
- raw vs harness comparisons
- no-context sanity checks

If we want a system property, we should measure it. Standard benchmark suites
would help ground that measurement in recognizable external tasks instead of
only Strata-native evals.
