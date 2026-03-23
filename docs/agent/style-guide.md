# Agent-Friendly Repository Style Guide

For weak-model coding agents, retrieval, self-reflection, and safe mutation

## 1. Purpose

This style guide exists to make the repository easier for both humans and small-model agents to:
- understand
- navigate
- retrieve relevant context from
- modify safely
- summarize accurately
- benchmark and reflect on

This is not just a cleanliness guide. It is a cognitive-efficiency guide.

**The core principle is:**
Code should be organized so that a weak model can usually understand what a component does without needing to read all of its implementation.

## 2. Design goals

The repository should optimize for:
- small-context comprehension
- precise retrieval
- modular mutation
- semantic legibility
- explicit contracts
- bounded side effects
- easy benchmarking
- easy summarization
- machine-readable metadata

The repository should avoid:
- giant monolithic files
- hidden behavior
- scripts with embedded business logic
- vague function names
- implicit data flow
- undocumented side effects
- retrieval-hostile structure

## 3. General philosophy

### 3.1 Interfaces first, implementation second
The most important information about a component is:
- what it does
- what it expects
- what it returns
- what it mutates
- what it depends on
- what invariants it preserves

A model should usually be able to answer those questions from metadata, headers, docstrings, or sidecar summaries before opening the full code.

### 3.2 Small, addressable units beat giant clever blobs
Small models reason better over:
- many small named components
- with explicit contracts
- and clean composition

than over:
- large clever functions
- overloaded modules
- dense orchestration logic
- context-dependent side effects

### 3.3 Scripts are entrypoints, not homes for logic
Scripts should wire together reusable logic, not contain it.

### 3.4 Every meaningful unit should have a semantic handle
Every public module, class, and function should expose a compact summary of what it is for.

## 4. Repository structure rules

### 4.1 Scripts must be orchestration-only
Scripts may:
- parse arguments
- load config
- instantiate services
- call library functions
- print/log status
- handle top-level control flow

Scripts should not:
- define substantive business logic
- contain long utility functions
- embed parsing/transformation logic
- become dumping grounds for one-off helpers

If a script contains logic that would matter to another agent or task, that logic belongs in a library module.

### 4.2 Library code should live in stable modules
Reusable functionality should live in modules with narrow responsibilities.

Preferred:
- `src/parsing/normalize.py`
- `src/eval/benchmark.py`
- `src/planning/decompose.py`

Avoid:
- `misc.py`
- `helpers.py`
- `stuff.py`
- `utils.py` that collects unrelated logic

If `utils.py` exists, it must be domain-scoped, such as `json_utils.py`, `path_utils.py`.

### 4.3 Modules must have clear ownership
Each module should answer:
- what belongs here
- what does not belong here

If that cannot be stated in 1–3 lines, the module is probably too vague.

## 5. File-size and function-size policy

These are not moral laws. They are context-budget protections.

### 5.1 File size
Recommended:
- ideal: under 300 lines
- warning: above 400 lines
- strong review threshold: above 600 lines

Exceptions are allowed, but require justification.

### 5.2 Function size
Recommended:
- ideal: under 30 lines
- warning: above 50 lines
- strong review threshold: above 80 lines

Exceptions are allowed when:
- the function is structurally simple
- the logic is linear and readable
- splitting would harm comprehension

### 5.3 Class size
Classes should be reviewed if:
- they own too many responsibilities
- they require the reader to understand too much hidden state
- their public API cannot be summarized concisely

## 6. Naming rules

### 6.1 Names should expose intention
Prefer names that tell the reader:
- what is being transformed
- how
- toward what result

Good: `normalize_fallback_parse`, `rank_candidate_plans`, `summarize_failure_cluster`
Bad: `process_data`, `handle_case`, `do_task`, `run_step`

### 6.2 Avoid ambiguous verbs
Avoid names like `handle`, `manage`, `process`, `deal_with`, `fix_stuff`.

### 6.3 Use domain terms consistently
If the repo uses a concept like `candidate`, `synthesis`, `judgment`, `promotion`, `memory summary`, then use those exact terms consistently in code, docs, prompts, logs, and schemas.

## 7. Module contract headers

Every nontrivial module should begin with a compact contract header.

Example:
```python
"""
@module parser.normalize
@purpose Normalize parser outputs into canonical internal schema.
@owns fallback normalization, key normalization, schema stabilization
@does_not_own IO, benchmarking, model prompting
@key_exports normalize_fallback_parse, normalize_keys
@upstream parser.fallback, parser.raw
@downstream eval.parse_valid_rate, parser.pipeline
@side_effects none
"""
```

## 8. Public symbol semantic headers

Every public function/class should have a compact semantic header.

### 8.1 Function template
```python
def normalize_fallback_parse(raw: str) -> dict:
    """
    @summary Normalize malformed fallback parser output into canonical dict form.
    @inputs raw: fallback parser output string
    @returns canonical dict with stable schema
    @side_effects none
    @raises ValueError on unrecoverable malformed structure
    @depends json_repair, normalize_keys
    @invariants never returns None; always returns dict with keys 'value' and 'errors'
    """
```

### 8.2 Class template
```python
class CandidateRanker:
    """
    @summary Rank candidate plans or patches using rubric-based scoring.
    @inputs candidate records, scoring rubric, optional historical priors
    @outputs ranked candidate ids with score breakdowns
    @side_effects may write ranking logs
    @depends scoring.compute_borda, validation.filter_invalid_candidates
    @invariants does not mutate candidate content
    """
```

## 9. Side-effect policy

Small models are bad at reasoning about hidden mutation.

### 9.1 Side effects must be explicit
If a function writes files, changes global state, mutates passed objects, starts subprocesses, makes network calls, or writes DB rows, that must be stated in its semantic header.

### 9.2 Prefer pure functions where reasonable
Pure or mostly-pure functions are easier to retrieve, test, mutate, summarize, and compose.

## 10. Dependency flow rules

Make dependencies obvious and favor acyclic relationships.

## 11. Agent-friendly metadata layer

The repository should maintain a machine-readable symbol index (e.g. `symbols.jsonl`, `modules.jsonl`).

## 12. Summary freshness policy

Metadata must not silently rot. When code changes for a symbol, mark its metadata as stale.
