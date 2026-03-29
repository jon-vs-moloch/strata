# Fresh Start

Use a fresh start when the runtime has accumulated stale sessions, orphaned task state, or pre-architecture-change churn that you do not want to carry forward.

What it does:
- Pauses the worker globally.
- Cancels the active task, if one is running.
- Drains queued task IDs from the in-memory worker backlog.
- Recreates the database from scratch.
- Restores persisted operator settings.
- Leaves the worker paused so the next run starts from a deliberate operator decision.

Operator flow:
1. Open Settings.
2. In Danger Zone, click `Fresh Start`.
3. Confirm the action.
4. Wait for the completion state in the UI.
5. Resume the worker when you are ready to begin a new supervised run.

What is cleared:
- Tasks
- Attempts
- Sessions and messages
- Database-backed telemetry/history
- Frontend archived-task cache

What is preserved:
- Global operator settings such as testing mode and replay-on-startup preferences
- Spec files and repository code
- Non-database assets such as knowledge/spec source files on disk

Recommended use:
- Before an overnight telemetry run after major architectural changes
- After debugging sessions that created misleading historical state
- Before handing the app to another operator who should start from a clean slate
