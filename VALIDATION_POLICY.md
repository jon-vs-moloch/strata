# STRATA VALIDATION POLICY

## Overview
Strata is a self-improving agentic system. To ensure safety and deterministic quality, it employs a **Fail-Closed** validation policy. This means any candidate modification that cannot be proven valid is automatically rejected.

---

## 1. Candidate Evaluation Policy
Every code candidate must pass the `EvaluationPipeline`.

### Mandatory Validators
Validators are **REQUIRED** for the following task types:
- `IMPL` (Implementation)
- `BUG_FIX`
- `REFACTOR`
- `improve_tooling`
- `feature`

If a task of these types lacks a declared validator, or if the validator fails, the candidate is **INVALID**.

### Boundary Confinement
- **Diff Size**: Candidates must stay within the `max_diff_size` (default 500 lines).
- **Target Confinement**: Candidates must only modify files explicitly listed in `target_files`. Modifying any other file renders the candidate **INVALID**.

### Available Validators
- `python_import_only`: Verifies the file can be imported without syntax errors.
- `python_pytest`: Executes a designated test file against the candidate in an isolated temporary workspace.
- `custom_script:<path>`: Executes a custom shell script for specialized validation.

---

## 2. Tool Promotion Policy
Tools in `strata/tools/*.experimental.py` are gated by the `ToolsPromotionPipeline`.

### Promotion Requirements
1. **Manifest**: A mandatory `strata/tools/manifests/<tool_name>.json` must exist.
2. **Validator**: The manifest must declare a validator that passes for the new code.
3. **Smoke Test**: A mandatory smoke test (e.g., `strata/tools/tests/test_foo_smoke.py`) must pass.

Failure to meet ANY of these requirements prevents promotion.

---

## 3. Metrics & Fitness Signals
The system records structured metrics for every evaluation and promotion event:
- `candidate_validity`: 1.0 for pass, 0.0 for fail.
- `validator_pass_rate`: Success/failure of the specific test gate.
- `tie_break_triggered`: Frequency of LLM intervention.
- `tool_promotion_success`: Rate of successful tool upgrades.

These signals are used by the orchestrator to optimize model routing and task decomposition.
