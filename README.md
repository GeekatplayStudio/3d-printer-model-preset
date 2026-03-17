# Mars 5 Ultra Agentic Slicer Backend

Modular FastAPI backend for agentic resin-print optimization:

- Phase 1: Geometry analysis from STL/OBJ (`trimesh` + `pyvista` proxy metrics)
- Phase 2: Mars 5 Ultra parameter injection logic + Chitubox `.cfg` export
- Phase 3: Resin feedback triage (real-world vs marketing-style signals)
- Ops Layer: Auth/RBAC, technical sync jobs, catalog versioning, and metrics

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install .
uvicorn app.main:app --reload
```

Then open:
- `http://127.0.0.1:8000/app` for the full ops console UI
- `http://127.0.0.1:8000/catalog/admin` for the focused catalog admin page

## API Endpoints

- `GET /health`
- `GET /auth/whoami`
- `GET /ops/metrics`
- `GET /ops/metrics/prometheus`
- `GET /ops/audit/events`
- `GET /catalog/health`
- `GET /catalog/printers`
- `POST /catalog/printers`
- `PATCH /catalog/printers/{printer_id}`
- `DELETE /catalog/printers/{printer_id}`
- `GET /catalog/resins`
- `POST /catalog/resins`
- `PATCH /catalog/resins/{resin_id}`
- `DELETE /catalog/resins/{resin_id}`
- `GET /catalog/profiles`
- `POST /catalog/profiles`
- `PATCH /catalog/profiles/{profile_id}`
- `DELETE /catalog/profiles/{profile_id}`
- `GET /catalog/export`
- `POST /catalog/import`
- `POST /catalog/import/csv`
- `GET /catalog/versions`
- `POST /catalog/versions/snapshot`
- `GET /catalog/versions/{version_id}`
- `POST /catalog/versions/restore`
- `GET /catalog/admin` (browser UI for maintenance operations)
- `GET /app` (full workflow/ops browser UI)
- `POST /sync/technical`
- `POST /sync/technical/file`
- `POST /sync/technical/job`
- `GET /sync/schedules`
- `POST /sync/schedules`
- `GET /sync/schedules/{schedule_id}`
- `PATCH /sync/schedules/{schedule_id}`
- `DELETE /sync/schedules/{schedule_id}`
- `POST /sync/schedules/{schedule_id}/run-now`
- `GET /sync/scheduler/health`
- `POST /sync/scheduler/tick`
- `GET /jobs`
- `GET /jobs/health`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/cancel`
- `POST /jobs/cleanup`
- `POST /jobs/pipeline`
- `POST /ops/jobs/run/seed-legacy`
- `GET /reference/resins` (inspect embedded 2026 resin reference profiles)
- `POST /phase1/analyze` (multipart file upload + `slice_height_mm`)
- `POST /phase2/optimize` (JSON body with analysis + resin/use-case)
- `POST /phase2/optimize/history-aware` (same as phase2 + uses stored feedback history to auto-adjust)
- `POST /phase3/feedback` (JSON batch feedback summary)
- `POST /phase3/camera-log/analyze` (upload Mars 5 Ultra `.log` and get targeted parameter adjustments)
- `POST /phase3/import/community-sheet/file` (CSV/JSON upload -> normalized records + summary)
- `POST /phase3/import/community-sheet/url` (fetch remote sheet URL -> normalized records + summary)
- `POST /phase3/import/youtube` (YouTube URLs -> transcript-backed records + summary)
- `GET /phase3/history/health` (SQLite storage status/path)
- `POST /phase3/history/query` (filter stored feedback by printer/resin/source/date)
- `POST /phase3/history/summary` (summary from stored history)
- `POST /pipeline` (end-to-end from model upload to settings + cfg text)

## Use Cases Encoded

- `miniature`: 18um XY, 0.02mm layer, high precision bias
- `collectible`: anti-aliasing 4, grayscale level 2, slower tilt profile
- `heavy_use`: +15% bottom exposure and tough-resin recommendation

## Mars 5 Ultra Logic Included

- Tilt speed mapping:
  - `>25%` plate cross-section -> forced slow tilt gate (`70` speed units)
  - `>30%` plate cross-section -> ultra-slow (`60` speed units)
  - `Miniature` + `<5%` cross-section -> fast tilt (`150` speed units)
- Tilt angle suggestion in range `0` to `-4` degrees
- Multi-parameter slicing (supports vs delicate surfaces)
- Temperature compensation (`<22C` -> +5% exposure per degree, `<18C` heater warning)
- Vat film compensation (`>=30,000` releases -> +0.5s retract rest time, `>=60,000` service warning)
- Transition layer defaults tuned for tilt-release behavior
- Scale compensation hint for shrink-prone heavy-use profiles

## Notes

- For YouTube ingestion, install `youtube-transcript-api` if not already present:
  - `pip install youtube-transcript-api`
- Authentication:
  - Set `RESINLOGIC_ENFORCE_AUTH=1` to require API keys.
  - Use `X-API-Key` and optional `X-Actor` headers.
  - Roles: `viewer`, `operator`, `admin`.
  - Set `RESINLOGIC_ENABLE_SCHEDULER=1` to auto-run due sync schedules.
  - See `.env.example` for secure key variables.
- Technical catalog persists to `data/tech_catalog.db` and is used by resin/profile lookup in optimization.
- Feedback history persists to `data/feedback_history.db`.
- Audit log and catalog versions persist to `data/audit_log.db`.
- Async job records persist to `data/jobs.db` (survive API restarts).
- Sync schedules persist to `data/sync_schedules.db`.
- Resin dataset now includes 2026 technical reference fields (viscosity, hardness, exposure ranges, tilt reference).
- `GET /catalog/profiles` now supports `query` filtering across printer/resin/profile names.
- Catalog admin UI now supports search plus row-level edit/delete for printers, resins, and profiles.
- Geometry cavity/island detection is heuristic and should be validated on your print set before production use.

## Deployment

Run with Docker Compose (API + Prometheus):

```bash
docker compose up --build
```

Prometheus scrapes the API at `/ops/metrics/prometheus` using `ops/prometheus.yml`.
