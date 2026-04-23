# Technical Requirements Document (TRD)

## 1. System Architecture

ResinLogic AI is a modular FastAPI service with phase-oriented orchestration.

Core architecture elements:

- phase modules (`geometry`, `logic`, `feedback`) coordinated by `ModularAgenticPipeline`,
- data services (`catalog_store`, `feedback_store`, `audit_store`, `job_store`, `sync_schedule_store`),
- ops services (`auth`, `job_queue`, `scheduler`, `monitoring`),
- static operator interfaces (`/app`, `/catalog/admin`).

## 2. Runtime and Stack

- Python 3.12
- FastAPI + Pydantic
- SQLite persistence
- Thread-based async job executor
- Prometheus-compatible metrics export

Primary libraries:

- `trimesh` for mesh loading and geometric calculations,
- `pyvista` for additional geometry proxies where available,
- `numpy` and `pandas` for numeric/data transforms,
- optional `youtube-transcript-api` for transcript ingestion.

## 3. Domain Data Model

### Catalog

- printers: machine technical specs and metadata,
- resins: material technical specs and metadata,
- profiles: printer-resin compatibility and recommended settings.
- each record stores provenance in metadata (`source_urls`, `source_type`, `retrieved_at`).

### Settings Output Provenance

- `OptimalSettings` now includes `provenance` with:
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

### Geometry Analysis Output

- `GeometryAnalysis.performance_ms` captures per-stage timings plus total runtime.
- `GeometryAnalysis.notes` includes runtime adaptation notes plus peak cross-section, cavity, island, and curvature summaries where available.
- `Island` records can now represent grouped unsupported regions with:
  - `end_layer_index`,
  - `z_end_mm`,
  - `layer_span`,
  - `total_voxel_count`,
  - `xy_centroid_mm`.

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
- `~/.resinlogic/audit_log.db`: mutation audit + version snapshots.
- `~/.resinlogic/jobs.db`: persisted job status across restarts.
- `~/.resinlogic/sync_schedules.db`: sync interval definitions and run status.
- `data/resin_profiles.json`: embedded baseline profile dataset.
- `data/official_catalog_sync.json`: source-attributed manufacturer seed payload for sync import.

All stores must initialize automatically at startup if missing.

## 5. API and Service Requirements

### Pipeline and Analysis

- `POST /phase1/analyze`: geometry analysis.
- `POST /phase2/optimize`: parameter optimization.
- `POST /phase2/optimize/history-aware`: optimization with historical adaptation.
- `POST /pipeline`: end-to-end workflow.
- `POST /jobs/pipeline`: async pipeline submission.
- geometry analysis supports `analysis_level` (`minimum`, `balanced`, `deep`) and returns step-level performance timings.
- minimum mode uses exact multiplane slicing for smaller meshes and a memory-safe surface-span weighted fallback for larger meshes.
- balanced/deep analysis adapt voxel pitch and slice counts for large meshes and reuse voxel fallback data where possible.
- geometry results return grouped island-region summaries instead of only per-layer unsupported hits.

### Wizard Runtime Analysis

- `POST /wizard/model/check`: guided STL analysis with optional `progress_job_id`.
- `GET /wizard/model/check/status/{job_id}`: live wizard analysis status and stage timing lookup.
- `POST /wizard/model/check/status/{job_id}/cancel`: cooperative cancellation for a running wizard analysis job.
- upload staging for wizard analysis must stream file chunks instead of buffering the full STL in memory.

### Feedback Intelligence

- community file/url ingestion endpoints,
- YouTube ingestion endpoint,
- camera log analysis endpoint,
- history query/summary endpoints.

### Catalog and Governance

- full CRUD for printers, resins, profiles,
- import/export in JSON and CSV,
- version snapshot and restore endpoints,
- audit read endpoint.
- `/wizard/catalog/options` returns compatibility mapping (`printer -> supported resin list`) for data-backed dropdown filtering.

### Sync and Job Ops

- sync apply endpoints (direct/file/job),
- schedule CRUD and scheduler tick/health,
- jobs list/get/cancel/cleanup/health.

## 6. Security Requirements

- Standalone local mode is the default runtime profile.
- In standalone mode, auth is disabled and the local operator is treated as admin.
- Optional token authentication with role mapping for shared/server deployments:
  - `viewer`,
  - `operator`,
  - `admin`.
- Header contract:
  - `Authorization: Bearer <token>` (credential, including GitHub token style),
  - `Actor` (audit identity).
- Shared/server mode must support strict auth via env configuration.

## 7. Reliability and Observability

- Request-level metrics for count, status, and latency.
- Prometheus text endpoint for scrape integration.
- Persistent job history to avoid losing operational context on restart.
- Catalog mutation audit trail and rollback capability.
- Wizard analysis progress state must expose stage-level timing and cancellation visibility for long-running model checks.

## 8. Testing Requirements

Required automated coverage areas:

- core logic and geometry behavior,
- catalog store + CSV import flows,
- API security and operational endpoints,
- schedule store and scheduler behavior,
- job queue persistence and cancellation behavior.
- wizard flow validation for provenance, compatibility, live progress, and cancellation behavior.
- large-model minimum-mode regression checks (no voxelized fast-path dependency).
- grouped island-region regression coverage for adjacent unsupported layers.

## 9. Deployment Requirements

- local and containerized startup supported,
- per-user local data directory supported (`RESINLOGIC_DATA_DIR`, default `~/.resinlogic`),
- docker-compose based run path with prometheus service,
- environment-based configuration for auth and scheduler mode.

## 10. Technical Gaps To Close

- explicit schema migration tooling and versioning policy,
- stronger external data validation and dedupe pipeline,
- richer frontend architecture for large-scale operator workflows,
- deeper SDCP live integration path.
