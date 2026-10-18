# Surprise-First Feedback Prioritization

## Overview
Strata employs a "Surprise-First" feedback system to triage high-frequency signals from both users and internal agents. This system ensures that the most informative signals—those that violate system expectations—receive immediate attention, while routine feedback is logged for later batch review.

---

## 1. Core Logic: Surprise Scoring
The primary metric used for triage is the **Surprise Score (0.0 - 1.0)**. Surprise is calculated by measuring the mismatch between the "message kind" and the "observed reaction."

### Message Classification
- **Greeting**: Fast, low-stakes interactions (e.g., "Hello," "Hi").
- **Question**: Requests for information.
- **Substantive**: Detailed instructions or long-form content.
- **Answer**: Standard system responses.

### Surprising Mismatches
The system flags high surprise in cases like:
- **Negative Feedback on Greetings**: If a user reacts negatively to a simple greeting, the surprise score is very high (~0.88), as it suggests a fundamental user-model mismatch or deep dissatisfaction.
- **Confusion on Success**: If a user reacts with `confused` to a task that the system marked as successful, the surprise score is maximized (~0.95).

---

## 2. Alignment Risk
Alongside surprise, the system calculates an **Alignment Risk (0.0 - 1.0)** score. High alignment risk indicates that the system's internal model of the user’s intent or the codebase state is likely incorrect.

- **Urgent Priority**: Triggers when a signal carries high alignment risk (e.g., textual corrections or `confused` reactions). These signals typically set `should_interrupt: True`.
- **Review Soon**: Used for negative feedback on substantive work or explicit "importance" highlights.
- **Logged / Batch**: Standard positive feedback (`thumbs_up`, `heart`) on expected results is logged but does not interrupt state.

---

## 3. Feedback Triage Implementation
The logic is distributed across two primary modules:
- `strata/feedback/signals.py`: Durable storage and registration of signals in the `parameters` table.
- `strata/prioritization/feedback.py`: Classification logic that applies heuristics for surprise and alignment risk.

### Supported Reaction Types
- `thumbs_up` / `heart`: Positive preference signals.
- `thumbs_down`: Negative feedback (triage level depends on message kind).
- `confused`: High-priority surprise signal.
- `emphasis`: Salience indicator for agent knowledge integration.

---

## 4. Usage in the Loop
1. **Model Signal**: User or agent submits a `feedback_signal` tool call.
2. **Prioritization**: The signal is classified in real-time.
3. **Intervention**: If `priority == urgent`, the orchestrator may pause current work and request operator intervention.
4. **Learning**: Aggregated signals (reaction clusters) are used in the self-improvement loop to refine system behavior.
