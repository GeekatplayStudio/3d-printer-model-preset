# Technical Requirements Document (TRD)

## 1. System Architecture

Geekatplay Studio 3D Print Ops is a modular FastAPI service with phase-oriented orchestration and a local-first persistence model.

Core architecture elements:

- phase modules (`geometry`, `logic`, `feedback`) coordinated by `ModularAgenticPipeline`,
- data services (`catalog_store`, `feedback_store`, `audit_store`, `job_store`, `sync_schedule_store`),
- sync/scrape services (`sync_service`, `web_catalog_scraper`),
- ops services (`auth`, `job_queue`, `scheduler`, `monitoring`),
- static operator interfaces (`/wizard`, `/app`, `/catalog/admin`).

Legacy compatibility note: internal package names and data-store names still use `resinlogic`/`resin` identifiers.

## 2. Runtime and Stack

- Python 3.12
- FastAPI + Pydantic
- SQLite persistence
- Thread-based async job execution
- APScheduler-backed schedule execution
- Prometheus-compatible metrics export

Primary libraries:

- `fastapi`, `pydantic`, `uvicorn` for the API/service layer,
- `numpy`, `trimesh[easy]`, and `pyvista` for geometry and mesh processing,
- `pandas` for structured import/export transforms,
- `python-multipart` for upload handling,
- optional `pymeshlab` for prototype repair,
- optional `open3d` for voxel experiments,
- optional `youtube-transcript-api` for transcript ingestion.

External runtime tooling:

- Blender for headless retopology,
- Docker Compose for the reproducible local stack,
- Prometheus for metrics scraping.

## 3. Domain Data Model

### Catalog

- printers: machine technical specs and metadata,
- materials: still stored through the `resins` entity naming for API compatibility, but now includes resin and filament rows,
- profiles: printer-material compatibility and recommended settings,
- each record stores provenance in metadata (`source_urls`, `source_type`, `retrieved_at`).

### Settings Output

- `OptimalSettings` includes `material_name` and `process_technology`.
- MSLA results use exposure/tilt-focused fields.
- FDM results populate the nested `fdm` block with nozzle, bed, chamber, speed, retraction, infill, wall, fan, and support-style settings.
- wizard settings responses add `target_process`, `slicer_name`, `slicer_profile_text`, download IDs, and download URLs.

### Settings Provenance

- `OptimalSettings.provenance` includes:
  - `data_quality`: `verified_sources | catalog_unverified | fallback_defaults`,
  - `real_data_backed`,
  - `confidence_score`,
  - `source_count`,
  - `references[]` (title/source type/source URL/retrieved timestamp/applies-to),
  - `notes[]`.

### Operational Data

- feedback history (normalized field signals),
- audit events and catalog snapshots,
- async job records and lifecycle state,
- sync schedule records for interval automation.
- bundled official seed state tracking (`official_catalog_seed_state.json`) for startup refresh bookkeeping.

### Geometry Analysis Output

- `GeometryAnalysis.performance_ms` captures per-stage timings plus total runtime.
- `GeometryAnalysis.notes` includes runtime adaptation notes plus peak cross-section, cavity, island, curvature, and support-risk summaries where available.
- `GeometryAnalysis` now includes `downskin_area_ratio` and `fdm_support_risk_score`.
- `Island` records can represent grouped unsupported regions with:
  - `end_layer_index`,
  - `z_end_mm`,
  - `layer_span`,
  - `total_voxel_count`,
  - `xy_centroid_mm`.

### Wizard Catalog Output

- `WizardCatalogOptionsResponse` provides:
  - flattened printer/material lists,
  - `targets`,
  - `printers_by_target`,
  - `materials_by_target`,
  - `compatibility_by_target`,
  - `slicers_by_target`.

### Wizard Analysis Progress Output

- `WizardAnalysisProgressResponse` provides:
  - `status` (`queued | running | cancelling | completed | failed | cancelled | unknown`),
  - `stage`,
  - `message`,
  - `elapsed_seconds`,
  - `stage_elapsed_seconds`,
  - `cancel_requested`,
  - `performance_ms`,
  - `stage_timings_ms`.

## 4. Persistence Requirements

- `~/.resinlogic/tech_catalog.db`: catalog entities and relationships.
- `~/.resinlogic/feedback_history.db`: normalized feedback records.
- `~/.resinlogic/audit_log.db`: mutation audit and version snapshots.
- `~/.resinlogic/jobs.db`: persisted job status across restarts.
- `~/.resinlogic/sync_schedules.db`: remote update definitions and run status.
- `data/resin_profiles.json`: legacy baseline profile dataset.
- `data/official_catalog_sync.json`: source-attributed bundled seed payload.

All stores must initialize automatically at startup if missing.

## 5. API and Service Requirements

### Pipeline and Analysis

- `POST /phase1/analyze`: geometry analysis.
- `POST /phase2/optimize`: parameter optimization.
- `POST /phase2/optimize/history-aware`: optimization with historical adaptation.
- `POST /pipeline`: end-to-end workflow.
- `POST /jobs/pipeline`: async pipeline submission.
- geometry analysis supports `analysis_level` (`minimum`, `balanced`, `deep`, `extreme`) and returns step-level performance timings.
- minimum mode uses exact multiplane slicing for smaller meshes and a memory-safe surface-span weighted fallback for larger meshes.
- balanced/deep analysis adapt voxel pitch and slice counts for large meshes and reuse voxel fallback data where possible.
- geometry results return grouped island-region summaries instead of only per-layer unsupported hits.

### Wizard Runtime Analysis and Export

- `POST /wizard/model/check`: guided model analysis for `STL`, `GLB`, and `3MF` uploads with optional `progress_job_id`.
- `GET /wizard/model/check/status/{job_id}`: live wizard analysis status and stage timing lookup.
- `POST /wizard/model/check/status/{job_id}/cancel`: cooperative cancellation for a running wizard analysis job.
- `POST /wizard/settings/recommend`: target-aware settings/export generation.
- the STL browser preview must support wire, mesh, and solid modes plus a maximize/restore fullscreen overlay that mirrors the current model statistics panel.
- upload staging for wizard analysis must stream file chunks instead of buffering the full model in memory.
- geometry analysis responses must expose best-effort `source_metadata` including format, encoding, author/tool hints, embedded timestamps, extracted metadata fields, AI heuristic status, and warnings when the file format does not preserve enough evidence.

### Catalog and Governance

- full CRUD for printers, materials, and profiles,
- import/export in JSON and CSV,
- version snapshot and restore endpoints,
- audit read endpoint,
- `/wizard/catalog/options` must return target-aware compatibility and slicer grouping.

### Sync and Remote Update Ops

- `POST /sync/technical/github`: GitHub-hosted JSON import.
- `POST /sync/technical/scrape`: supported vendor-page scrape import.
- `POST /wizard/database/setup`: wizard-managed setup/update with `official_local | github | web_json | web_scrape`.
- `GET /wizard/updates/status`: saved update schedule state.
- `POST /wizard/updates/run-now`: on-demand execution of a saved schedule.
- schedule CRUD and scheduler tick/health.
- jobs list/get/cancel/cleanup/health.

### Feedback Intelligence

- community file/url ingestion endpoints,
- YouTube ingestion endpoint,
- camera log analysis endpoint,
- history query/summary endpoints.

## 6. Security Requirements

- Standalone local mode is the default runtime profile.
- In standalone mode, auth is disabled and the local operator is treated as admin.
- Optional token authentication with role mapping for shared/server deployments:
  - `viewer`,
  - `operator`,
  - `admin`.
- Header contract:
  - `Authorization: Bearer <token>`,
  - `Actor` (audit identity).
- Remote JSON and scrape URLs must reject localhost, private IPs, and embedded credentials.
- Shared/server mode must support strict auth via environment configuration.

## 7. Reliability and Observability

- Request-level metrics for count, status, and latency.
- Prometheus text endpoint for scrape integration.
- Persistent job history to avoid losing operational context on restart.
- Catalog mutation audit trail and rollback capability.
- Wizard analysis progress state must expose stage-level timing and cancellation visibility for long-running model checks.
- Saved remote update schedules must expose last-run status, last error, and manual run-now support.

## 8. Testing Requirements

Required automated coverage areas:

- core logic and geometry behavior,
- catalog store and CSV import flows,
- API security and operational endpoints,
- schedule store and scheduler behavior,
- job queue persistence and cancellation behavior,
- wizard flow validation for provenance, compatibility, target selection, remote update controls, live progress, and cancellation behavior,
- large-model minimum-mode regression checks,
- grouped island-region regression coverage for adjacent unsupported layers,
- supported vendor-page scrape parsing and sync application coverage.

## 9. Deployment Requirements

- local and containerized startup supported,
- per-user local data directory supported (`RESINLOGIC_DATA_DIR`, default `~/.resinlogic`),
- docker-compose-based run path with Prometheus service,
- environment-based configuration for auth, scheduler mode, and Blender path,
- Docker persistence behavior documented clearly so rebuilds are not mistaken for DB replacement, while bundled official seed startup refresh behavior remains explicit.

## 10. Technical Gaps To Close

- explicit schema migration tooling and versioning policy,
- stronger external data validation and dedupe pipelines,
- broader FDM guidance and additional slicer/export targets,
- richer frontend architecture for large-scale operator workflows,
- deeper SDCP live integration path,
- additional supported HTML scrapers beyond the current narrow vendor set.
