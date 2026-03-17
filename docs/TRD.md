# Technical Requirements Document

## Architecture

- Backend pattern: modular node-like phase graph
- Runtime: Python 3.12
- API: FastAPI

## Core Libraries

- `trimesh` for STL/OBJ loading and slicing
- `pyvista` for optional curvature proxy
- `numpy` for volumetric and mask operations
- `pandas` reserved for exposure matrix analytics

## Data

- Local resin profile store: `data/resin_profiles.json`
- Feedback ingest from JSON/CSV records
- Persistent feedback history store: `data/feedback_history.db` (SQLite)
- Resin reference includes 2026 technical fields: viscosity, hardness, exposure ranges, tilt reference
- Maintainable technical catalog: `data/tech_catalog.db` (SQLite, CRUD for printers/resins/profiles)
- Audit and version store: `data/audit_log.db` (audit events + catalog snapshots for rollback)
- Async job store: `data/jobs.db` (persistent queue state and job history)
- Sync schedule store: `data/sync_schedules.db` (interval policies for automated technical sync)

## Integrations

- Chitubox:
  - `.cfg` generation
  - SDCP payload schema for direct integration layer
- Mars 5 Ultra camera log ingestion endpoint for failure-aware recommendation loops

## Catalog Ops

- API-level CRUD for printers, resins, and compatibility profiles
- CSV bulk import for operational maintenance
- Built-in admin web page (`/catalog/admin`) for manual updates
- Full operations console (`/app`) for pipeline, catalog, sync jobs, metrics, and audit review

## Security and Operations

- API key RBAC with roles (`viewer`, `operator`, `admin`) via `X-API-Key` and `X-Actor`
- Configurable auth enforcement (`RESINLOGIC_ENFORCE_AUTH`)
- In-memory async job queue for long-running pipeline/sync tasks
- Job cancellation and cleanup endpoints for retention management
- Request metrics endpoint (`/ops/metrics`) plus Prometheus text export (`/ops/metrics/prometheus`)
- Optional background scheduler for interval-based sync execution

## Extensibility

- `ModularAgenticPipeline` exposes phase entry points so each phase can run independently or as a full chain.
