# Strata UI

This directory contains the active Strata dashboard built with React and Vite.

## Purpose

The UI is the operator surface for:

- chat with the orchestrator
- task and attempt visibility
- worker/admin controls
- model registry and settings management

The backend it expects lives at `http://localhost:8000` unless changed in the app.

## Development

From the repository root:

```bash
npm run dev --prefix strata_ui
```

Build the UI:

```bash
npm run build --prefix strata_ui
```

Lint the UI:

```bash
npm run lint --prefix strata_ui
```

## Important Notes

- This is the active frontend. The root-level `src/` Vite scaffold is separate and does not appear to be the primary dashboard described by the backend and top-level README.
- The UI depends on backend endpoints in [strata/api/main.py](/Users/jon/Projects/strata/strata/api/main.py), especially `/chat`, `/tasks`, `/events`, and the `/admin/*` routes.
- Parts of the settings surface currently depend on backend model/admin endpoints that are out of sync with `ModelAdapter`, so some controls may fail until those endpoints are repaired.

## Current Verification Status

During review on March 25, 2026:

- `npm run lint --prefix strata_ui` failed with 20 issues.
- The failures are mostly unused imports/state plus at least one React hooks rule violation in [App.jsx](/Users/jon/Projects/strata/strata_ui/src/App.jsx).

If you want this UI to be the source of truth, the next cleanup pass should focus on:

1. removing unused imports and dead state
2. fixing the `useEffect`/state update lint violation
3. reconciling the settings screen with the backend endpoints it calls
