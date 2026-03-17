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

### Operational Data

- feedback history (normalized field signals),
- audit events and catalog snapshots,
- async job records and lifecycle state,
- sync schedule records for interval automation.

## 4. Persistence Requirements

- `data/tech_catalog.db`: catalog entities and relationships.
- `data/feedback_history.db`: normalized feedback records.
- `data/audit_log.db`: mutation audit + version snapshots.
- `data/jobs.db`: persisted job status across restarts.
- `data/sync_schedules.db`: sync interval definitions and run status.
- `data/resin_profiles.json`: embedded baseline profile dataset.

All stores must initialize automatically at startup if missing.

## 5. API and Service Requirements

### Pipeline and Analysis

- `POST /phase1/analyze`: geometry analysis.
- `POST /phase2/optimize`: parameter optimization.
- `POST /phase2/optimize/history-aware`: optimization with historical adaptation.
- `POST /pipeline`: end-to-end workflow.
- `POST /jobs/pipeline`: async pipeline submission.

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

### Sync and Job Ops

- sync apply endpoints (direct/file/job),
- schedule CRUD and scheduler tick/health,
- jobs list/get/cancel/cleanup/health.

## 6. Security Requirements

- API key authentication with role mapping:
  - `viewer`,
  - `operator`,
  - `admin`.
- Header contract:
  - `X-API-Key` (credential),
  - `X-Actor` (audit identity).
- Production mode must support strict auth via env configuration.

## 7. Reliability and Observability

- Request-level metrics for count, status, and latency.
- Prometheus text endpoint for scrape integration.
- Persistent job history to avoid losing operational context on restart.
- Catalog mutation audit trail and rollback capability.

## 8. Testing Requirements

Required automated coverage areas:

- core logic and geometry behavior,
- catalog store + CSV import flows,
- API security and operational endpoints,
- schedule store and scheduler behavior,
- job queue persistence and cancellation behavior.

## 9. Deployment Requirements

- local and containerized startup supported,
- docker-compose based run path with prometheus service,
- environment-based configuration for auth and scheduler mode.

## 10. Technical Gaps To Close

- explicit schema migration tooling and versioning policy,
- stronger external data validation and dedupe pipeline,
- richer frontend architecture for large-scale operator workflows,
- deeper SDCP live integration path.
