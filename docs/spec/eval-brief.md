# Eval Brief

This file is the compact eval-facing summary for Strata.

Use it when you need the project’s intent and current operating model without loading the full README.

## Core Thesis

Strata is designed to extract useful work from small local models by pushing rigor into the surrounding system rather than relying on the model alone.

The system should:
- refine outputs through multiple steps
- validate outputs against downstream data
- measure outcomes explicitly instead of assuming them
- use stronger models to improve the harness until weaker models can improve the system themselves

## Current Priorities

- improve weak-tier performance through harness and system changes
- keep changes explainable with provenance, telemetry, and eval evidence
- support small-context models through modular code, progressive disclosure, and compact artifacts
- favor safe bounded improvements over broad speculative rewrites

## Operational Guidance

- the durable project intent lives in `.knowledge/specs/project_spec.md`
- the broader design rationale lives in `docs/spec/project-philosophy.md`
- the repo map lives in `docs/spec/codemap.md`
- when the system wants a property, it should become an eval target
- when the system proposes durable direction changes, they should flow through spec review rather than casual mutation
