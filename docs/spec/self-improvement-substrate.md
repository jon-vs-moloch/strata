# Self-Improvement Substrate (Experiments & Variants)

## Overview
Strata is designed to autonomously optimize its own prompts, tools, and routing. This is accomplished via a "Self-Improvement Substrate" that supports running parallel experiments and promoting winning candidates.

---

## 1. Variant Management
The system treats every core process (e.g., `implementation`, `research`) as a series of "variants." This moves configuration out of static code and into a dynamic experiment registry.

### Variant Pools
- **Exploit Pool**: Proven variants used as the standard system baseline.
- **Explore Pool**: High-potential candidates being evaluated against the baseline.
- **Variant ID Assignment**: Every task records which variant was used, enabling accurate metric attribution.

---

## 2. The Experiment Loop
The orchestrator supports a continuous loop of system evaluation:
1. **Candidate Proposal**: A strong model proposes a change (e.g., a better implementation prompt).
2. **Matrix Evaluation**: The new candidate is run against standard `benchmarks` and `structured-evals`.
3. **Trace Review**: System traces are automatically reviewed for "drift" or "degradation" relative to the baseline.
4. **Promotion**: If the candidate consistently outperforms the baseline, its `is_active` status is updated, and it is promoted to the global system prompt.

---

## 3. Implementation Modules
This logic is found within the `strata/experimental/` directory:
- `variants.py`: Management of the variants and their process-level execution.
- `experiment_runner.py`: The core engine for matrix runs and candidate evaluation.
- `promotion_policy.py`: Rules for when a candidate is "fit" enough for promotion.
- `trace_review.py`: Automated review of system execution logs for performance auditing.

---

## 4. Key Metrics
Self-improvement is driven by measured outcomes:
- **Candidate Validity**: 1.0 for passing all validators, 0.0 for failure.
- **Secondary Ignition**: Whether a promoted change has produced a measurable gain in the weak tier's performance.
- **Success Rate Delta**: The improvement (or regression) relative to the baseline.

---

## 5. Tool Promotion
Custom tools (`strata/tools/`) follow a similar gated pipeline:
1. Created as `*.experimental.py`.
2. Passed through the `ToolsPromotionPipeline`.
3. Promoted to `*.py` and added to the live chat tool registry only after passing mandatory syntax, import, contract, and smoke tests.
