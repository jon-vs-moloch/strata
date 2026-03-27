# Eval Catalog

This catalog lists the standard benchmark suites currently checked into Strata's
structured-eval lane. These suites are meant to support apples-to-apples,
no-context comparisons between:

- raw model
- harness without tools/web
- harness with optional tools/web profiles

The current standard suites are:

- `mmlu_mini_v1`
  Source family: MMLU / `cais/mmlu`
  Format: multiple choice
  Intended use: broad knowledge and reasoning sanity checks

- `arc_challenge_mini_v1`
  Source family: ARC-Challenge / `allenai/ai2_arc`
  Format: multiple choice
  Intended use: grade-school science reasoning

- `hellaswag_mini_v1`
  Source family: HellaSwag / `Rowan/hellaswag`
  Format: multiple choice
  Intended use: commonsense continuation and plausibility

- `boolq_mini_v1`
  Source family: BoolQ / `google/boolq`
  Format: passage-grounded yes/no, rendered as multiple choice
  Intended use: reading comprehension under no-context conditions

These mini suites are intentionally small so Strata can accumulate eval signal
quickly without saturating local hardware. They are not substitutes for full
benchmark runs, but they are useful for:

- trend tracking over time
- weak vs strong comparisons
- raw vs harness comparisons
- no-context sanity checks

If we want a system property, we should measure it. Standard benchmark suites
help ground that measurement in recognizable external tasks instead of only
Strata-native evals.
