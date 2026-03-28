# UI Operator Audit

This note tracks which important operator-facing Strata surfaces are visible in the UI and which still remain mostly backend-driven.

## Current intent

The UI should make the system legible enough that an operator does not need private endpoint knowledge to understand:

- which tier is doing what
- whether the supervision loop is active
- which durable specs are in force
- whether knowledge, retention, and context systems are healthy
- what important governance or promotion work is waiting

## Now visible in the UI

- routing summary for chat, strong, weak, and supervision
- worker controls and worker status
- durable spec presence and recent spec proposal records
- recent knowledge pages
- retention policy/runtime summary
- active supervision jobs
- eval snapshots, recent promotion reports, provider transport telemetry, and context pressure

## Still partially shadowed

- direct creation or resolution of spec proposals
- manual knowledge compaction and page upsert flows
- retention maintenance triggers
- prediction/calibration and variant-rating inspection
- context load/unload controls
- experiment comparison and trace-review workflows

## Audit conclusion

The UI is now materially better at showing operator-relevant state, but it is not yet a full operator console.

The next useful pass would be to expose a small "Operations" drawer or admin panel for the remaining backend-only mutation workflows instead of requiring endpoint-level knowledge.
