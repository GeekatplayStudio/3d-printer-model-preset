# ResinLogic AI

Agentic backend and operations console for resin 3D printing optimization across printers and resins.

The system combines:

- geometry analysis from STL/OBJ files,
- rule-based and profile-based parameter optimization,
- technical catalog management for printers/resins/profiles,
- feedback ingestion and history-aware tuning,
- operational tooling (local storage, jobs, audit, metrics, schedules).

## Project Idea

ResinLogic AI is designed to let an agentic workflow take a high-level print goal and execute it through modular phases:

1. analyze geometry risk and print intent,
2. derive printer-resin settings with Mars 5 Ultra specific logic and catalog overrides,
3. ingest real-world feedback and adapt future recommendations.

Beyond Mars 5 Ultra, the catalog architecture supports all printers and all resins by storing normalized technical data and compatibility profiles.

## Current Status

Implemented:

- FastAPI backend with modular phase pipeline.
- STL geometry integrity report with optional auto-repair (duplicate/degenerate cleanup, hole-fill attempt, post-repair cleanup pass, topology notes).
- Analysis depth profiles (`minimum`, `balanced`, `deep`) with per-step runtime telemetry, adaptive large-model throttling, and grouped unsupported-region reporting.
- Catalog CRUD for printers, resins, and profiles.
- CSV and JSON import/export for technical database maintenance.
- Catalog versioning and rollback snapshots.
- Audit logging for operational mutations.
- Async job queue with persistence, cancellation, and retention cleanup.
- Sync engine with schedule store and optional background scheduler.
- GitHub technical sync endpoint and schedule support for pulling a repo-hosted data file.
- Feedback ingestion (files, URLs, YouTube transcripts) and history storage.
- History-aware optimization endpoint.
- Camera log analyzer endpoint for failure-driven adjustments.
- Standalone local mode by default (per-user SQLite directory, no central server required).
- Optional role-based API access when standalone mode is disabled.
- Metrics endpoint and Prometheus export.
- Two browser UIs:
  - `/wizard` for the guided step-by-step end-user workflow.
  - `/app` for full operations console (dark mode).
  - `/catalog/admin` for catalog maintenance with search and row-level edit/delete.
- Wizard STL UX and runtime improvements:
  - STL-only upload enforcement,
  - printer -> compatible resin dropdown flow,
  - live progress/status polling during analyze,
  - per-stage timing visibility and in-place cancel support,
  - adaptive timeout windows for large models,
  - chunked upload staging to avoid large-file OOM failures.
- Geometry analysis detail improvements:
  - exact minimum-mode multiplane slicing for small meshes,
  - surface-span weighted minimum-mode fallback for larger meshes,
  - voxel reuse between cross-section fallback and cavity/island detection,
  - grouped island regions with layer spans and centroids,
  - richer analysis notes for peak cross-section, cavity totals, island severity, and curvature summary.
- Settings provenance and trust surface:
  - `settings.provenance.data_quality` (`verified_sources`, `catalog_unverified`, `fallback_defaults`),
  - `settings.provenance.real_data_backed`,
  - `settings.provenance.confidence_score`,
  - source references with links from catalog metadata.
- Automated test suite with focused Docker regression coverage for geometry, wizard flow, catalog, jobs, scheduler, sync, and security paths.

Planned next:

- direct SDCP bidirectional integration with slicer/printer workflows,
- stronger suction-cup confidence filtering to separate peel-risk pockets from benign sealed voids,
- dedicated React + Three.js stress-map frontend,
- stronger data quality workflows for external community dataset sync.

## Documentation Map

- Product requirements: `docs/PRD.md`
- Technical requirements: `docs/TRD.md`
- Agentic task roadmap: `docs/TASK.md`
- Project status and next milestones: `docs/PROJECT_STATUS.md`
- Gemini prompt-to-implementation mapping: `docs/REFERENCE_MAPPING.md`
- Official seed provenance and source list: `docs/OFFICIAL_DATASET.md`

## Installation

### Prerequisites

Before you start, install the following tools:

- Python 3.12 or newer
- `pip`
- Git
- Docker Desktop (optional, for the containerized setup)

### Option 1: Local Python setup

1. Clone the repository.

```bash
git clone https://github.com/GeekatplayStudio/3d-printer-model-preset.git
cd 3d-printer-model-preset
```

2. Create a virtual environment.

```bash
python -m venv .venv
```

3. Activate the virtual environment.

macOS / Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

4. Install the application.

```bash
pip install .
```

5. Start the API server.

```bash
uvicorn app.main:app --reload
```

6. Open the application in your browser.

- `http://127.0.0.1:8000/wizard`
- `http://127.0.0.1:8000/app`
- `http://127.0.0.1:8000/catalog/admin`

### Option 2: Docker Compose setup

1. Clone the repository.

```bash
git clone https://github.com/GeekatplayStudio/3d-printer-model-preset.git
cd 3d-printer-model-preset
```

2. Build and start the containers.

```bash
docker compose up --build -d
```

3. Open the running services.

- App UI: `http://127.0.0.1:8000/app`
- Wizard UI: `http://127.0.0.1:8000/wizard`
- Catalog admin: `http://127.0.0.1:8000/catalog/admin`
- Prometheus: `http://127.0.0.1:9090`

4. Stop the stack when you are done.

```bash
docker compose down
```

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install .
uvicorn app.main:app --reload
```

Open:

- `http://127.0.0.1:8000/wizard`
- `http://127.0.0.1:8000/app`
- `http://127.0.0.1:8000/catalog/admin`

## Test

Local environment:

```bash
python -m pytest -q
```

Docker environment:

```bash
docker compose run --rm api sh -lc "python -m pip install pytest && python -m pytest -q"
```

The runtime image stays slim and does not bundle `pytest`, so Docker test runs install it transiently.

## Official Catalog Seed

Source-attributed baseline data is shipped in:

- `data/official_catalog_sync.json`

It includes official printer specs, resin entries, and profile defaults with `metadata.source_urls` on every record.

Import into your local standalone database:

```bash
python3 scripts/import_official_catalog.py --replace-existing
```

See `docs/OFFICIAL_DATASET.md` for provenance details and source list.

## GitHub Smart Update (Phase 1)

Manual one-shot pull from repo file:

```bash
curl -X POST http://127.0.0.1:8000/sync/technical/github \
  -H "Content-Type: application/json" \
  -d '{
    "owner": "your-org",
    "repo": "your-repo",
    "path": "data/resin_sync.json",
    "ref": "main",
    "replace_existing": false
  }'
```

Scheduled pull (stored in `sync_schedules.db`) via `payload.github`:

```json
{
  "name": "github-hourly-sync",
  "source": "github_repo",
  "interval_seconds": 3600,
  "enabled": true,
  "replace_existing": false,
  "payload": {
    "github": {
      "owner": "your-org",
      "repo": "your-repo",
      "path": "data/resin_sync.json",
      "ref": "main",
      "retry_attempts": 3,
      "retry_backoff_seconds": 1.0
    }
  }
}
```

Sync responses now include a `curation` summary (`source_reliability`, `average_profile_quality`, `average_profile_confidence`, `confidence_distribution`, `input_counts`, `kept_counts`, `dropped_counts`, `notes`) so you can see what was clamped, deduped, dropped, and how confident the imported profile set is.

Wizard settings responses now include provenance metadata so each recommendation can be traced to source URLs and confidence scoring.

## Core Storage

- `~/.resinlogic/tech_catalog.db`: printers, resins, compatibility profiles
- `~/.resinlogic/feedback_history.db`: ingested feedback history
- `~/.resinlogic/audit_log.db`: audit events + catalog snapshots
- `~/.resinlogic/jobs.db`: async job records
- `~/.resinlogic/sync_schedules.db`: scheduled sync definitions
- `data/resin_profiles.json`: embedded baseline profile dataset
- `data/official_catalog_sync.json`: source-attributed official catalog seed payload

## Key Environment Variables

- `RESINLOGIC_STANDALONE_MODE`: local standalone mode, defaults to `1`
- `RESINLOGIC_DATA_DIR`: override storage directory (defaults to `~/.resinlogic`)
- `RESINLOGIC_ENFORCE_AUTH`: enable token auth only when standalone mode is `0`
- `RESINLOGIC_ENABLE_DEFAULT_KEYS`: enable built-in dev keys
- `RESINLOGIC_ADMIN_API_KEY`: admin role key
- `RESINLOGIC_OPERATOR_API_KEY`: operator role key
- `RESINLOGIC_VIEWER_API_KEY`: viewer role key
- `RESINLOGIC_ENABLE_SCHEDULER`: start background schedule runner when `1`
- `RESINLOGIC_SCHEDULER_POLL_SECONDS`: scheduler polling interval

Auth headers (only needed when standalone mode is disabled):

- `Authorization: Bearer <token>` (or `Authorization: token <token>`)
- `Actor: <username-or-service-name>` (optional audit identity)

## Deployment

```bash
docker compose up --build
```

Prometheus scrapes `/ops/metrics/prometheus` via `ops/prometheus.yml`.
Default compose runs in standalone mode and persists to `./local-data`.

## License

MIT. See `LICENSE`.
