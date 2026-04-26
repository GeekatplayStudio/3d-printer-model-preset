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
- `[x]` extreme analysis mode for highest-detail scans
- `[x]` large-model minimum-mode optimization (memory-safe fast path)
- `[x]` exact minimum-mode slicing for small meshes and surface-span weighted fallback for larger meshes
- `[x]` adaptive balanced/deep throttling and voxel reuse between cross-section fallback and island/cavity analysis
- `[x]` grouped island-region reporting with layer spans and centroids
- `[x]` richer mesh statistics and before/after repair outcome reporting
- `[x]` Blender-backed retopology flow with quad and voxel remesh modes
- `[~]` suction-cup false-positive filtering and cavity confidence ranking
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
- `[x]` initial FDM/filament optimization branch alongside resin/MSLA settings
- `[x]` wizard target selector plus Cura export for the first FDM workflow
- `[x]` wizard source selection for local, GitHub, and curated web JSON catalog updates
- `[x]` supported vendor HTML scraping path for Anycubic product pages and settings guide
- `[x]` selection-step provenance panel for printer/material/profile evidence in the wizard
- `[~]` broader FDM wizard guidance and extra slicer/export targets
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
- `[x]` material typing and FDM/filament profile support without breaking resin compatibility
- `[x]` provenance-backed official catalog aggregator with multi-slice bundled seed inputs
- `[x]` row-aware dedupe that prefers stronger source tier and row confidence over file order
- `[x]` startup refresh for bundled official seed updates without wiping operator-added catalog rows
- `[ ]` migration/version policy for schema changes
- `[~]` broader trust gating and validation coverage for remote/community bulk ingestion

Primary modules: `app/catalog_store.py`, `app/audit_store.py`, `app/static/catalog_admin.html`

## Operations Agent

- `[x]` RBAC auth model
- `[x]` async job queue with persistence
- `[x]` job cancellation and retention cleanup
- `[x]` sync schedule store and scheduler tick/health endpoints
- `[x]` optional background scheduler
- `[x]` metrics endpoint and Prometheus export
- `[x]` unified ops console (`/app`)
- `[x]` wizard analysis live progress, per-stage timings, and cancel flow
- `[x]` wizard local 3D preview, repair report, and retopology report surfaces
- `[x]` Step 2 maximize/restore preview overlay with embedded model statistics mirror
- `[ ]` workflow-specific dashboards and SLO alerting presets

Primary modules: `app/auth.py`, `app/job_queue.py`, `app/scheduler.py`, `app/monitoring.py`, `app/static/app.html`

## Immediate Next Sprint

1. Broaden Tier 1 manufacturer-backed FDM coverage and add more verified printer-material bundles.
2. Add stronger trust gating policies, including optional operator warnings or blocks for low-tier profile selections.
3. Harden suction-cup confidence with benchmark corpus and cavity classification checks.
4. Define schema migration strategy and release policy.
5. Broaden source-specific HTML scrapers for more vendors/community sites when a normalized JSON feed is not available.
