# Strata workbench

The operator UI for Strata: React 19 + Vite, no CSS framework.

```bash
npm install
npm run dev        # talks to the API at http://127.0.0.1:8000 (override with VITE_STRATA_API)
npm run demo       # replays recorded sample data; no backend needed
npm run build      # production build; build:demo for a static demo build
npm run lint
```

## Layout

| Path | Purpose |
|---|---|
| `src/App.jsx` | Shell, top-bar lane controls, chat, polling and SSE wiring |
| `src/views/OverviewView.jsx` | The overview: refinery line, lanes, improvement loop, verifier, context cost, evals, transport |
| `src/views/NonChatContent.jsx` | Tasks, history, knowledge, procedures, workbench and dashboard views |
| `src/views/SettingsView.jsx` | Model registry, routing and runtime settings |
| `src/components/Chrome.jsx` | Strata mark, live/demo switch, theme toggle, demo notices, API-offline banner |
| `src/lib/runtime.js` | API base URL, demo-mode and theme preferences |
| `src/demo/` | Demo transport (`transport.js`) and the recorded sample (`recorded.json`) |
| `src/styles/tokens.css` | Design tokens: palette for both themes, type scale, spacing, radii |

## Design system

All colour comes from `src/styles/tokens.css`. Neutrals are warm greys ("limestone" light, "basalt" dark). Each hue family has a base for marks and fills and a `-soft` variant for text on tinted surfaces. Lane identity is fixed: **trainer = copper**, **agent = teal**, system accent = indigo. Those three were checked for lightness, chroma and colour-vision separation against both theme surfaces.

Components use tokens or `color-mix()` of tokens, never raw colour literals, so the light and dark themes stay in step. The theme follows the system preference until the viewer picks one with the toggle in the top bar.

Type: Inter for UI text, Space Grotesk for headings and figures, JetBrains Mono for ids and numbers. All three are bundled with `@fontsource`, so the UI works offline.

## Demo mode

Demo mode is on with `npm run demo`, `?demo=1`, or **View demo** in the top bar (remembered per browser; `?demo=0` or **Go live** turns it off). It swaps the HTTP layer, not the components:

- GET requests are answered from `src/demo/recorded.json`, real API responses recorded against a seeded sample database by `scripts/build_demo_fixtures.py`. Timestamps are shifted so the run looks current.
- Worker lane status is a small scripted loop, because live worker state is in memory and can't be recorded.
- Writes are never sent. Chat and pause/resume update a local copy so the controls respond; anything else shows a "Demo mode" notice.

The top bar and the overview label all of this as **Demo data**.

## Known gaps

`npm run lint` reports problems that predate the redesign (unused variables and props in the large view files). New modules are lint-clean.
