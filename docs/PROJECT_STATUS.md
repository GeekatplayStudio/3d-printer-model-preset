# Project Status

## What This Project Is

Geekatplay Studio's ResinLogic AI is an agentic resin-print optimization platform.

It combines:

- mesh geometry analysis,
- printer-resin profile intelligence,
- parameter decision logic,
- operational tooling for maintaining technical databases across all supported printers and resins.

The immediate production target started around Elegoo Mars 5 Ultra behavior, but the data model and APIs are built to generalize to any printer and resin combination.

## What Has Been Done

### Core Pipeline

- Phase 1 geometry analysis endpoint with:
  - cross-section scan support,
  - suction cup and island heuristic detection,
  - SAR/detail density metrics,
  - adaptive balanced/deep runtime throttling,
  - exact minimum-mode multiplane slicing for small meshes,
  - surface-span weighted minimum fallback for larger meshes,
  - grouped unsupported-region summaries with layer spans and centroids.
- Phase 2 optimization endpoint with:
  - use-case templates (`miniature`, `collectible`, `heavy_use`),
  - tilt-speed gates,
  - temperature scaling,
  - film-age compensation,
  - profile-aware overrides from catalog.
- Phase 3 feedback layer with:
  - community sheet ingestion (file/url),
  - YouTube transcript ingestion,
  - camera log parsing and recommendation output,
  - persistent feedback history query/summary APIs.

### Data and Catalog

- SQLite catalog store with normalized entities:
  - printers,
  - resins,
  - compatibility profiles.
- Full CRUD + search APIs.
- Bulk import/export in JSON and CSV.
- Admin UI for hands-on maintenance, including row-level edit/delete.
- Version snapshots and restore.
- Audit logs for all key catalog mutations.

### Operations and Reliability

- Standalone local-first runtime with per-user SQLite storage.
- Optional RBAC authentication via `Authorization` tokens (`viewer`, `operator`, `admin`).
- Async jobs with persistent history.
- Job cancellation and retention cleanup.
- Scheduled sync definitions and manual/background schedule execution via APScheduler.
- Request metrics and Prometheus output.
- Dockerized run path and Prometheus config.
- Source-attributed official seed dataset (`data/official_catalog_sync.json`) plus local import script.
- Wizard pipeline UX hardening for large models:
  - STL-only upload constraints,
  - chunked upload staging for large STL files,
  - live stage-by-stage analyze status polling,
  - per-stage timing visibility and user-triggered cancellation,
  - adaptive timeout windows by analysis depth + model size.
- Auth-aware artifact downloads for repaired STL, CFG, and JSON outputs in shared/server mode.
- Memory-safe minimum analysis path with large-model runtime optimizations and step timing telemetry.
- Cross-section fallback voxel reuse and post-repair duplicate/degenerate cleanup for more stable geometry output.
- Optional PyMeshLab prototype repair backend and opt-in Open3D voxel backend spike with benchmark scripts.
- Settings provenance surface:
  - data quality classification (`verified_sources`, `catalog_unverified`, `fallback_defaults`),
  - confidence score passthrough where available,
  - source reference list exposed in wizard output.
- Compatibility-aware wizard dropdown behavior (printer selection filters resin list to real profile coverage).
- Test coverage across core modules and API behavior, including focused regressions for large-model geometry, wizard progress/cancel flow, and grouped island-region reporting.

## What Still Needs To Be Done

### High Priority

- Implement stronger validation and curation pipelines for external data ingestion to reduce noisy profile inputs.
- Add production-grade migration/version strategy for SQLite schemas.
- Introduce configuration profiles per printer family (not only Mars-first defaults).
- Separate true suction-cup peel-risk pockets from benign sealed voids to reduce cavity false positives.

### Product and UX

- Replace static console style pages with a dedicated React frontend.
- Add 3D stress-map visualization (Three.js) for peel-force and suction-risk explanation.
- Expand wizard onboarding/tooltips and richer operator guardrails for low-confidence profile recommendations.

### Integrations

- Implement direct SDCP round-trip integration where available (not just cfg generation).
- Add optional connectors for external resin/printer data providers.

### ML/Agentic Improvements

- Improve suction-cup confidence classification and cavity severity ranking.
- Add adaptive confidence scoring for recommended settings.
- Expand history-aware adaptation to include weighted trends over time and environment context.

## Near-Term Milestones

1. Open3D voxel benchmark and occupancy-fidelity hardening.
2. Data quality and validation hardening.
3. Frontend redesign with workflow-first UX.
4. Release candidate stabilization (security defaults, migrations, observability).
