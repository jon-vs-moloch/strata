# Workspace Context Management

## Overview
Strata treats model context as a scarce resource. It uses a specialized context management system to ensure that small-context local models are not overwhelmed by large files while maintaining access to critical persistent information across rounds (`context-pinning`).

---

## 1. Persistent Context Pinning
The system allows users or internal agents to "pin" specific workspace files into **persistent context**. Unlike standard retrieval, which may cycle in and out of cache:
- **Round-Level Continuity**: Pinned files stay in the model's system-block across all rounds of a session.
- **Budgeting**: The pinning system enforces a default budget (`DEFAULT_LOADED_CONTEXT_BUDGET_TOKENS = 3200`). This is meant for compact, high-value artifacts (standards, manifests, specific module interfaces).
- **Tool Logic**: The `load_context_file` and `unload_context_file` tools used by the chat interface interact directly with this system.

This should not remain purely automatic or implicit.

- Strata should support deliberate context management as an explicit operator/agent capability.
- The system should be able to retrieve the original source behind a compacted memory, open new context into the active working set, close stale context, merge related context items, and summarize context items deliberately rather than only through background policy.
- Automatic context management and deliberate context management should complement each other: background policy keeps context healthy, while explicit tools let the operator or the model reason about context as an object of work.

---

## 2. Context-Pressure Observability
To prevent "quiet degradation" where a model is technically within context limits but becoming less capable due to "token noise," Strata scans for context pressure.

### Startup Pressure Scan
On API startup, the system performs a non-recursive scan of the codebase to:
- **Estimate Tokens**: Line counts are ignored in favor of true token estimates using a standardized tokenizer profile.
- **Telemetry**: Records the "tax" of every document in a telemetry table, allowing the UI to warn the operator before loading files that might drown the model's reasonings.

---

## 3. Implementation Details
The context system is implemented in:
- `strata/context/loaded_files.py`: Management of the pinning registry and token budgeting.
- `strata/observability/context.py`: Token estimation logic and repository-wide context pressure scanning.

---

## 4. Operational Pacing
When context reaches dangerous levels (as measured by the `observability` layer), the orchestrator may adopt **Defensive Pacing**:
- **Truncation**: Snippets are reduced or omitted in favor of page slugs.
- **Progressive Disclosure**: Force-reads of specific pages instead of wide-context research loops.
- **Deeper Decomposition**: Automatically breaking the task into smaller subtasks to keep local context windows narrow and "clean."

## 5. Deliberate Context Operations

Context should eventually be searchable, inspectable, and directly operable from inside Strata.

Near-term capability direction:

- search existing context items and compacted memories
- reopen the original source behind a compacted or summarized context item
- load a new file/page/artifact into active context deliberately
- unload or demote a stale context item deliberately
- merge overlapping context items into a cleaner working bundle
- summarize or re-summarize a context item for budget reduction
- expose current active context budget, priorities, and pressure clearly enough that both the user and the model can make informed tradeoffs
