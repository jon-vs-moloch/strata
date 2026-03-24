# Strata

Strata is a powerful agentic task orchestration platform designed for managing complex, recursive agent swarms.

## Features

- **Recursive Task Hierarchies:** Break down complex goals into nested subtasks and execution attempts.
- **Temporal Grouping:** Automatically organizes tasks into **Past**, **Present**, and **Future** strata for clear focus.
- **FastAPI Backend:** High-performance API with SQLite WAL support for reliable concurrent access.
- **React + Framer Motion UI:** A premium, dynamic dashboard for real-time swarm visualization.

## Getting Started

1.  **Backend:**
    ```bash
    PYTHONPATH=. ./venv/bin/python strata/api/main.py
    ```
2.  **Orchestrator:**
    ```bash
    PYTHONPATH=. ./venv/bin/python strata/orchestrator/background.py
    ```
3.  **Frontend:**
    ```bash
    npm run dev --prefix strata_ui
    ```

Navigate to `http://localhost:5173` to view the Strata dashboard.
