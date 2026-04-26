# Geekatplay Studio 3D Print Ops

Geekatplay Studio 3D Print Ops is a local-first FastAPI backend and browser toolkit for analyzing printable geometry, maintaining a source-attributed printer and material catalog, and generating slicer-ready exports for MSLA/resin and first-pass FDM/filament workflows.

Legacy compatibility note: internal package names, environment variables, SQLite file names, and some route parameters still use `resinlogic` or `resin` identifiers. The external product/documentation surface is branded as Geekatplay Studio.

## What Ships Today

- Guided `/wizard` flow with Step 0 target selection: `MSLA / resin` or `FDM / filament`.
- Step 1 catalog setup/update from four source modes:
  - bundled official dataset,
  - GitHub-hosted sync JSON,
  - direct web JSON feed,
  - supported vendor HTML pages.
- STL, GLB, and 3MF analysis, repair, retopology, live progress polling, cooperative cancel, and post-repair save/recheck verification.
- Wizard model metadata inspection for STL, GLB, and 3MF uploads with best-effort author, software, timestamp, and AI/tool hints.
- Local 3D preview for STL uploads, with clear in-app fallback messaging when the selected upload is GLB or 3MF.
- Settings generation with provenance, confidence, and downloadable slicer artifacts.
- Chitubox Free export for MSLA and Ultimaker Cura profile export for FDM.
- Step 3 selection-stage provenance panel showing source tier, confidence, and source link for the chosen printer, material, and preferred profile.
- Catalog CRUD, CSV/JSON import/export, version snapshots, audit logs, jobs, schedules, and Prometheus metrics.
- Source-attributed official seed data updated through `2026-04-26`, now merged as a provenance-backed catalog aggregator with manufacturer-backed filament rows, community cross-check slices, expanded FDM printers, generic filament references, refreshed live Prusament PLA provenance, and normalized FDM profiles.

## End-To-End Workflow

1. Open `/wizard` and choose the analyzer target.
2. Seed or update the local catalog from the official dataset or a remote source.
3. Upload an STL, GLB, or 3MF model, run geometry analysis, inspect the extracted metadata, and optionally repair or retopologize the mesh. Auto-Fix saves the repaired STL and re-checks that saved file before reporting the result.
4. Choose a compatible printer and material for the selected target.
5. Generate slicer-ready settings, review provenance, and download the exported profile.

Supporting UIs:

- `/wizard`: guided operator flow.
- `/app`: broader operations console for jobs, sync, metrics, and data readiness.
- `/catalog/admin`: direct printer/material/profile maintenance UI.

## Catalog Source Modes

| Mode | What it does | When to use it |
| --- | --- | --- |
| `official_local` | Imports the checked-in catalog aggregator files bundled with the repo or Docker image. | Best default for local setup and reproducible baseline data with both MSLA and FDM coverage. |
| `github` | Pulls a normalized sync JSON file from a GitHub repo/path/ref. | Best when your team curates catalog data in GitHub. |
| `web_json` | Pulls a normalized sync JSON file from a direct HTTP/HTTPS URL. | Best for published feeds outside GitHub. |
| `web_scrape` | Scrapes supported vendor pages into normalized printer/material/profile rows. | Best when a vendor only publishes specs/settings in HTML. |

Remote modes can also be saved as wizard auto-update schedules. The scheduler state is persisted in `sync_schedules.db`, surfaced through `/wizard/updates/status`, and can be triggered manually with `/wizard/updates/run-now`.

Supported vendor pages are intentionally narrow today. The current supported scraper targets:

- Anycubic official product pages.
- The official Anycubic resin settings guide.

## GitHub and Remote Catalog Updates

### GitHub One-Shot Import

```bash
curl -X POST http://127.0.0.1:8000/sync/technical/github \
  -H "Content-Type: application/json" \
  -d '{
    "owner": "your-org",
    "repo": "your-repo",
    "path": "data/catalog-sync.json",
    "ref": "main",
    "replace_existing": false
  }'
```

### Supported Vendor Page Import

```bash
curl -X POST http://127.0.0.1:8000/sync/technical/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "source": "web_scrape",
    "replace_existing": false,
    "urls": [
      "https://store.anycubic.com/products/photon-mono-m7-pro",
      "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers"
    ]
  }'
```

### Wizard-Managed Remote Setup

Use `POST /wizard/database/setup` when you want the same flow the UI uses, including optional saved auto-update schedules.

Example GitHub-backed wizard setup:

```bash
curl -X POST http://127.0.0.1:8000/wizard/database/setup \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "github",
    "owner": "your-org",
    "repo": "your-repo",
    "path": "data/catalog-sync.json",
    "ref": "main",
    "replace_existing": false,
    "auto_update": true,
    "auto_update_interval_seconds": 86400,
    "source": "wizard_setup_github"
  }'
```

Every sync response includes a `curation` summary so you can inspect how many rows were kept, dropped, deduplicated, or clamped, plus an aggregate source reliability and profile confidence view.

## How The Backend Works

At runtime, `app/main.py` initializes local SQLite-backed stores and serves the three browser surfaces. The main processing path is phase-oriented:

1. `app/geometry.py` loads STL, GLB, 3MF, and OBJ meshes, extracts best-effort embedded metadata, repairs obvious topology issues, computes cross-sections, voxelizes when needed, and emits cavity/island/detail/support-risk metrics.
2. `app/logic.py` combines geometry metrics, the selected printer/material profile, and rule-based heuristics to produce print settings.
3. `app/chitubox.py` renders MSLA exports and `app/cura.py` renders FDM Cura profiles.
4. `app/sync_service.py` applies normalized sync payloads and `app/web_catalog_scraper.py` translates supported vendor pages into those normalized rows.
5. Feedback modules ingest field signals, summarize operational history, and optionally adapt future recommendations.

The wizard persists both analysis progress and downloadable artifact metadata in local SQLite files so long-running analysis and follow-up downloads stay available across worker hops.

For a live Docker multi-worker smoke check, start the API with more than one worker and run:

```bash
python scripts/smoke_test_multiworker.py --base-url http://127.0.0.1:8000 --cancel-model-path /absolute/path/to/large-model.stl
```

## Library Stack

The direct dependencies declared in `pyproject.toml` are:

- `fastapi`: HTTP API, dependency injection, and static UI serving.
- `uvicorn`: ASGI server for local and containerized runtime.
- `apscheduler`: persistent scheduled catalog refresh execution.
- `numpy`: vectorized geometry math and heuristic scoring.
- `pandas`: catalog import/export transforms and feedback ingestion helpers.
- `python-multipart`: model, CSV, and upload form handling.

## Model Metadata Detection

Wizard Step 2 now reports best-effort source metadata for supported model uploads:

- `STL`: header comments or binary header text when present.
- `GLB`: glTF asset metadata such as `asset.generator` and exporter extras.
- `3MF`: package metadata like `Application`, `Designer`, and `CreationDate`.

The UI surfaces the detected format, encoding, embedded author/tool/timestamp hints, extracted metadata fields, and an AI-origin heuristic. These signals depend entirely on what the exporting tool wrote into the file. Missing fields are normal, and AI detection remains heuristic rather than authoritative.
- `trimesh[easy]`: primary mesh loading, repair helpers, slicing, voxelization, and geometry calculations.
- `pyvista`: richer geometry/detail probes in deeper analysis paths.

Optional extras:

- `httpx` and `pytest`: API testing and regression coverage.
- `pymeshlab`: optional prototype mesh-repair backend.
- `open3d`: optional voxel-backend experimentation and benchmarking.
- `youtube-transcript-api`: transcript ingestion for feedback signals.

Notable indirect/runtime packages used by the geometry stack include `pydantic`, `starlette`, `vtk`, `scipy`, `rtree`, `networkx`, `jsonschema`, `lxml`, `pycollada`, `mapbox_earcut`, `manifold3d`, `embreex`, and `vhacdx`.

See `docs/LIBRARIES.md` for the fuller dependency breakdown and where each dependency is used.

## Documentation Map

- Architecture and runtime flow: `docs/ARCHITECTURE.md`
- Library and tooling breakdown: `docs/LIBRARIES.md`
- Official seed provenance and source list: `docs/OFFICIAL_DATASET.md`
- Provenance-backed catalog aggregator tiers and credits: `docs/CATALOG_AGGREGATOR.md`
- Continuity and anti-drift reference: `docs/CONTINUITY_REFERENCE.md`
- Product requirements: `docs/PRD.md`
- Technical requirements: `docs/TRD.md`
- Project status and near-term milestones: `docs/PROJECT_STATUS.md`
- Execution tracker / roadmap: `docs/TASK.md`
- Gemini prompt-to-implementation mapping: `docs/REFERENCE_MAPPING.md`

## Installation

### Prerequisites

- Python 3.12+
- Git
- `pip`
- Docker Desktop if you want the containerized path

### Local Python Setup

```bash
git clone https://github.com/GeekatplayStudio/3d-printer-model-preset.git
cd 3d-printer-model-preset
python -m venv .venv
```

macOS / Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the app:

```bash
pip install -e .
```

Useful extras:

```bash
pip install -e .[dev]
pip install -e .[dev,mesh-repair]
pip install -e .[dev,open3d-spike]
```

Run the API:

```bash
uvicorn app.main:app --reload
```

Open:

- `http://127.0.0.1:8000/wizard`
- `http://127.0.0.1:8000/app`
- `http://127.0.0.1:8000/catalog/admin`

### Docker Compose Setup

```bash
git clone https://github.com/GeekatplayStudio/3d-printer-model-preset.git
cd 3d-printer-model-preset
docker compose up --build -d
```

Services:

- Wizard: `http://127.0.0.1:8000/wizard`
- Ops console: `http://127.0.0.1:8000/app`
- Catalog admin: `http://127.0.0.1:8000/catalog/admin`
- Prometheus: `http://127.0.0.1:9090`

Stop the stack:

```bash
docker compose down
```

Docker persistence note: `docker-compose.yml` mounts `./local-data` into `/app/local-data`. Rebuilding the image does not replace the persisted catalog database. If you change `data/official_catalog_sync.json` or update a remote source definition, rerun the Step 1 catalog import/update so the live DB picks up the new data.

## Tests

Local:

```bash
python -m pytest -q
```

Docker:

```bash
docker compose run --rm -v "${PWD}:/app" api sh -lc "python -m pip install -e .[dev] && python -m pytest -q"
```

The runtime image intentionally stays slimmer than the development environment, so `pytest` is installed transiently for Docker test runs.

## Wizard Artifact Downloads

In standalone mode, the wizard downloads repaired STL files and generated slicer artifacts directly.

Auto-Fix reports against the saved repaired STL, not just the in-memory mesh. After each repair, the wizard writes the STL artifact, re-runs mesh-health checks on that saved file, and then reports whether any blocking issues still remain.

In shared/server mode with auth enabled, keep `Authorization` and `Actor` populated in the wizard before clicking repair or download actions. The wizard fetches files through the same authenticated request path as the API calls, so downloads continue to work when direct browser links would otherwise be rejected.

## Official Catalog Seed

The bundled baseline dataset lives in `data/official_catalog_sync.json`.

It contains source-attributed printer, material, and profile rows, including a baseline FDM set added in the `2026-04-24` update. Every row includes `metadata.source_urls` and `metadata.retrieved_at` so the wizard can surface provenance references back to the operator.

Import the bundled dataset into the local DB:

```bash
python scripts/import_official_catalog.py --replace-existing
```

For more detail, see `docs/OFFICIAL_DATASET.md` and `docs/CATALOG_AGGREGATOR.md`.

## Core Storage

- `~/.resinlogic/tech_catalog.db`: printers, materials, and compatibility profiles.
- `~/.resinlogic/feedback_history.db`: ingested feedback history.
- `~/.resinlogic/audit_log.db`: audit events and catalog snapshots.
- `~/.resinlogic/jobs.db`: async job records.
- `~/.resinlogic/sync_schedules.db`: saved remote update schedules.
- `data/official_catalog_sync.json`: bundled official seed payload.
- `data/resin_profiles.json`: legacy baseline profile data still used for compatibility.

Under Docker Compose, these databases live under `./local-data` because `RESINLOGIC_DATA_DIR=/app/local-data`.

## Key Environment Variables

- `RESINLOGIC_STANDALONE_MODE`: defaults to `1`; when enabled, local operator actions run as admin.
- `RESINLOGIC_DATA_DIR`: override storage directory.
- `RESINLOGIC_ENFORCE_AUTH`: enable token auth when standalone mode is `0`.
- `RESINLOGIC_ENABLE_DEFAULT_KEYS`: enable built-in development keys.
- `RESINLOGIC_ADMIN_API_KEY`: admin role key.
- `RESINLOGIC_OPERATOR_API_KEY`: operator role key.
- `RESINLOGIC_VIEWER_API_KEY`: viewer role key.
- `RESINLOGIC_ENABLE_SCHEDULER`: run the APScheduler-backed sync scheduler when `1`.
- `RESINLOGIC_SCHEDULER_POLL_SECONDS`: scheduler polling interval.
- `RESINLOGIC_BLENDER_EXECUTABLE`: path to the Blender binary used for headless retopology.

Auth headers in shared/server mode:

- `Authorization: Bearer <token>` or `Authorization: token <token>`
- `Actor: <username-or-service-name>`

## License

MIT. See `LICENSE`.
