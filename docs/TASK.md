# Agentic Coding Roadmap (Execution Tracker)

## Legend

- `[x]` completed
- `[~]` in progress
- `[ ]` planned

## Phase 1: Geometry and Structure Agent

- `[x]` mesh ingestion and basic geometry features
- `[x]` cross-section area scan pipeline
- `[x]` detail density and SAR metrics
- `[x]` intent estimation heuristic (`miniature`, `collectible`, `heavy_use`)
- `[x]` analysis depth modes with performance telemetry (`minimum`, `balanced`, `deep`)
- `[x]` large-model minimum-mode optimization (memory-safe fast path)
- `[~]` suction-cup/island heuristics hardening
- `[ ]` advanced stress confidence scoring and benchmark suite

Primary module: `app/geometry.py`

## Phase 2: Parameter Injection Agent

- `[x]` use-case routing and baseline parameter templates
- `[x]` Mars 5 Ultra tilt logic gates
- `[x]` temperature compensation
- `[x]` ACF film age compensation
- `[x]` multi-parameter slicing output structure
- `[x]` Chitubox cfg text export and SDCP payload structure
- `[x]` history-aware optimization endpoint
- `[x]` settings provenance classification and source-reference output
- `[ ]` direct SDCP bidirectional runtime integration

Primary modules: `app/logic.py`, `app/chitubox.py`

## Phase 3: Resin Intelligence Agent

- `[x]` community sheet ingestion from file and URL
- `[x]` YouTube transcript ingestion flow
- `[x]` camera log parser for warp/empty-plate/delamination/blooming cues
- `[x]` persisted feedback history with query and summary endpoints
- `[~]` stronger trust/quality scoring for external feedback sources
- `[ ]` adaptive weighting by source reliability and recency

Primary modules: `app/feedback_sources.py`, `app/feedback_store.py`, `app/camera_log.py`

## Catalog Maintenance Agent

- `[x]` normalized printer/resin/profile schema
- `[x]` full CRUD APIs
- `[x]` JSON and CSV import/export
- `[x]` catalog snapshot and restore
- `[x]` audit trail for catalog mutations
- `[x]` admin UI with search and row-level edit/delete
- `[x]` compatibility mapping for wizard printer->resin constrained selection
- `[ ]` migration/version policy for schema changes
- `[ ]` stricter data validation and dedupe rules for bulk ingestion

Primary modules: `app/catalog_store.py`, `app/audit_store.py`, `app/static/catalog_admin.html`

## Operations Agent

- `[x]` RBAC auth model
- `[x]` async job queue with persistence
- `[x]` job cancellation and retention cleanup
- `[x]` sync schedule store and scheduler tick/health endpoints
- `[x]` optional background scheduler
- `[x]` metrics endpoint and Prometheus export
- `[x]` unified ops console (`/app`)
- `[ ]` workflow-specific dashboards and SLO alerting presets

Primary modules: `app/auth.py`, `app/job_queue.py`, `app/scheduler.py`, `app/monitoring.py`, `app/static/app.html`

## Immediate Next Sprint

1. Harden geometry heuristics with benchmark corpus and confidence calibration.
2. Add stricter trust gating policies (optional block on non-verified settings in wizard).
3. Start frontend redesign plan (React migration, stress-map UX).
4. Define schema migration strategy and release policy.
