# ResinLogic AI

Agentic backend and operations console for resin 3D printing optimization across printers and resins.

The system combines:

- geometry analysis from STL/OBJ files,
- rule-based and profile-based parameter optimization,
- technical catalog management for printers/resins/profiles,
- feedback ingestion and history-aware tuning,
- operational tooling (auth, jobs, audit, metrics, schedules).

## Project Idea

ResinLogic AI is designed to let an agentic workflow take a high-level print goal and execute it through modular phases:

1. analyze geometry risk and print intent,
2. derive printer-resin settings with Mars 5 Ultra specific logic and catalog overrides,
3. ingest real-world feedback and adapt future recommendations.

Beyond Mars 5 Ultra, the catalog architecture supports all printers and all resins by storing normalized technical data and compatibility profiles.

## Current Status

Implemented:

- FastAPI backend with modular phase pipeline.
- Catalog CRUD for printers, resins, and profiles.
- CSV and JSON import/export for technical database maintenance.
- Catalog versioning and rollback snapshots.
- Audit logging for operational mutations.
- Async job queue with persistence, cancellation, and retention cleanup.
- Sync engine with schedule store and optional background scheduler.
- Feedback ingestion (files, URLs, YouTube transcripts) and history storage.
- History-aware optimization endpoint.
- Camera log analyzer endpoint for failure-driven adjustments.
- Role-based API access (`viewer`, `operator`, `admin`) with API keys.
- Metrics endpoint and Prometheus export.
- Two browser UIs:
  - `/app` for full operations console.
  - `/catalog/admin` for catalog maintenance with search and row-level edit/delete.
- Automated test suite (`45` tests currently passing).

Planned next:

- direct SDCP bidirectional integration with slicer/printer workflows,
- richer geometry heuristics for suction cup/island confidence,
- dedicated React + Three.js stress-map frontend,
- stronger data quality workflows for external community dataset sync.

## Documentation Map

- Product requirements: `docs/PRD.md`
- Technical requirements: `docs/TRD.md`
- Agentic task roadmap: `docs/TASK.md`
- Project status and next milestones: `docs/PROJECT_STATUS.md`
- Gemini prompt-to-implementation mapping: `docs/REFERENCE_MAPPING.md`

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install .
uvicorn app.main:app --reload
```

Open:

- `http://127.0.0.1:8000/app`
- `http://127.0.0.1:8000/catalog/admin`

## Test

```bash
python -m pytest -q
```

## Core Storage

- `data/tech_catalog.db`: printers, resins, compatibility profiles
- `data/feedback_history.db`: ingested feedback history
- `data/audit_log.db`: audit events + catalog snapshots
- `data/jobs.db`: async job records
- `data/sync_schedules.db`: scheduled sync definitions
- `data/resin_profiles.json`: embedded baseline profile dataset

## Key Environment Variables

- `RESINLOGIC_ENFORCE_AUTH`: enable API key auth when set to `1`
- `RESINLOGIC_ENABLE_DEFAULT_KEYS`: enable built-in dev keys
- `RESINLOGIC_ADMIN_API_KEY`: admin role key
- `RESINLOGIC_OPERATOR_API_KEY`: operator role key
- `RESINLOGIC_VIEWER_API_KEY`: viewer role key
- `RESINLOGIC_ENABLE_SCHEDULER`: start background schedule runner when `1`
- `RESINLOGIC_SCHEDULER_POLL_SECONDS`: scheduler polling interval

## Deployment

```bash
docker compose up --build
```

Prometheus scrapes `/ops/metrics/prometheus` via `ops/prometheus.yml`.

## License

MIT. See `LICENSE`.
