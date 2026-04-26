# Architecture and Process Flow

## What The Application Does

Geekatplay Studio 3D Print Ops is a local-first FastAPI application for 3D print workflow support. It now covers resin/MSLA flows end to end and includes an initial FDM/filament branch for cataloging, optimization, and Cura export.

Legacy compatibility note: package names, database names, and many environment variables still use `resinlogic` or `resin` identifiers. The external product and documentation surface is branded as Geekatplay Studio.

The application helps an operator move through four connected concerns:

1. maintain a trustworthy local printer/material/profile catalog,
2. inspect, repair, and retopologize incoming model geometry (`STL`, `GLB`, `3MF`),
3. generate target-aware slicer settings and exports,
4. ingest feedback and operational signals so later recommendations can improve.

The runtime is intentionally split between three browser surfaces:

- `/wizard`: target-aware guided flow for catalog readiness, geometry analysis, and export generation.
- `/app`: broader operations console for jobs, sync, metrics, and system status.
- `/catalog/admin`: focused maintenance UI for printers, materials, and compatibility profiles.

## Core Libraries and Tooling

The direct runtime dependencies are defined in [pyproject.toml](../pyproject.toml). The short version is:

- `fastapi`, `pydantic`, and `uvicorn` provide the HTTP/service layer.
- `numpy`, `trimesh[easy]`, and `pyvista` power the geometry and mesh-analysis stack.
- `pandas` and `python-multipart` support import/export and uploads.
- `apscheduler` powers persisted remote-update schedules.
- optional extras add `pytest`, `httpx`, `pymeshlab`, `open3d`, and `youtube-transcript-api` for testing and experiments.

See [docs/LIBRARIES.md](./LIBRARIES.md) for the fuller dependency and tooling breakdown, including Blender, Docker, and the major indirect geometry packages.

## High-Level Runtime Shape

The application starts in [app/main.py](../app/main.py). That file does four important things:

1. resolves a writable local data directory,
2. initializes SQLite-backed stores for catalog, feedback, audit, jobs, and sync schedules,
3. creates the FastAPI app plus in-memory helpers for jobs, metrics, wizard artifacts, and wizard progress,
4. exposes API routes and serves the static browser UIs.

The orchestration layer lives in [app/pipeline.py](../app/pipeline.py). `ModularAgenticPipeline` is the thin composition point that calls the focused modules:

- [app/geometry.py](../app/geometry.py) for phase 1 model analysis,
- [app/logic.py](../app/logic.py) for settings generation,
- [app/chitubox.py](../app/chitubox.py) for MSLA/Chitubox exports,
- [app/cura.py](../app/cura.py) for FDM/Cura exports,
- [app/feedback.py](../app/feedback.py), [app/feedback_adaptation.py](../app/feedback_adaptation.py), and [app/feedback_sources.py](../app/feedback_sources.py) for historical and external feedback,
- [app/sync_service.py](../app/sync_service.py) and [app/web_catalog_scraper.py](../app/web_catalog_scraper.py) for normalized sync ingestion and supported HTML scraping,
- the store modules for catalog, jobs, sync, feedback, and audit persistence.

## Main Process Flows

### 1. Startup and Storage Initialization

At process start, [app/main.py](../app/main.py) creates or opens the local databases:

- `tech_catalog.db`
- `feedback_history.db`
- `audit_log.db`
- `jobs.db`
- `sync_schedules.db`

It also prepares the wizard artifact directory and wires in-memory objects for:

- live wizard analysis progress,
- background job execution,
- request metrics,
- optional background schedule execution.

This design keeps the runtime local-first and avoids needing a separate managed database for normal use. Under Docker Compose, the data directory is mounted from `./local-data`, so container rebuilds do not replace the persisted catalog DB wholesale. To keep the bundled official seed current, startup now records `official_catalog_seed_state.json` beside the active catalog DB and automatically upserts bundled official seed rows when the bundled `updated_at` value is newer than the last recorded seed import or no seed state file exists.

### 2. Wizard Flow

The wizard is the clearest end-to-end flow in the app.

The user journey is:

1. choose the target process (`MSLA / resin` or `FDM / filament`),
2. seed or update the local catalog from the official dataset or a remote source,
3. upload an STL, GLB, or 3MF model, inspect the local preview, and run geometry analysis,
4. optionally auto-fix or retopologize the mesh,
5. select a compatible printer and material for the chosen target,
6. generate slicer-ready settings and download the resulting export.

The browser code in [app/static/wizard.html](../app/static/wizard.html) talks to wizard routes in [app/main.py](../app/main.py). Those routes are designed for long-running geometry work and operator-friendly catalog maintenance:

- uploads are streamed to disk in chunks,
- best-effort source metadata is extracted from STL headers, GLB asset metadata, or 3MF package metadata and surfaced back into the wizard,
- geometry analysis is offloaded with `asyncio.to_thread`,
- in-memory status is tracked by `progress_job_id`,
- the UI polls status routes for live stage messages and timings,
- remote catalog sources can be saved as schedules and manually triggered,
- repaired STL and settings downloads are fetched through auth-aware browser requests when server-mode auth is enabled,
- local browser preview remains STL-only even though analysis accepts STL, GLB, and 3MF,
- STL preview supports wire, mesh, and solid modes plus a maximize/restore overlay that mirrors the model statistics panel,
- cancellation is cooperative and checked inside the geometry engine.

Wizard Step 1 catalog modes are:

- `official_local`: bundled official catalog aggregator slices loaded through `app/official_catalog.py`,
- `github`: normalized sync JSON from a GitHub repo/path/ref,
- `web_json`: normalized sync JSON from a direct web URL,
- `web_scrape`: supported vendor pages translated into normalized catalog rows.

### 3. Phase 1 Geometry Analysis

Geometry analysis is implemented in [app/geometry.py](../app/geometry.py). It is the heaviest computational part of the system.

The rough sequence is:

1. inspect embedded file metadata, then load and normalize the mesh,
2. inspect mesh health and optionally attempt repair,
3. choose an analysis profile (`minimum`, `balanced`, `deep`, `extreme`),
4. adapt slice and voxel settings for large meshes,
5. compute cross-sections,
6. voxelize when needed,
7. detect cavity candidates and unsupported islands,
8. estimate surface/detail/support-risk signals,
9. derive aggregate metrics, intent hints, structural risk, and notes.

Important implementation details:

- `minimum` mode favors memory safety and runtime predictability.
- small meshes can use exact multiplane slicing.
- large meshes can fall back to a lighter surface-span approximation.
- balanced and deep modes throttle voxel pitch and slice counts for very large models.
- voxel fallback data is reused so later stages do not repeat expensive work.
- cavity detection distinguishes broad pocket-like cavities from narrow sealed shafts and assigns confidence metadata.
- island detection groups adjacent unsupported layers into higher-level regions instead of noisy per-layer reports.
- the geometry output now includes FDM-oriented signals such as `downskin_area_ratio` and `fdm_support_risk_score`.

The result is serialized as `GeometryAnalysis`, which includes:

- cross-section metrics,
- mesh health,
- cavity and island findings,
- estimated print intent,
- structural risk score,
- notes,
- per-stage performance timings,
- FDM support-risk hints.

### 4. Phase 2 Settings Generation

Once geometry analysis exists, [app/logic.py](../app/logic.py) turns it into machine settings.

Inputs include:

- geometry metrics,
- chosen use case,
- printer name,
- material name,
- optional ambient temperature,
- optional film release count,
- optional catalog profile data.

The settings logic mixes:

- built-in heuristics,
- printer defaults,
- target-specific profile overrides,
- temperature compensation,
- cross-section and cavity-sensitive MSLA decisions,
- family-aware FDM defaults,
- data provenance and confidence reporting.

`OptimalSettings` can describe either process branch:

- MSLA results keep exposure, bottom exposure, tilt, and peel-related parameters.
- FDM results populate the nested `fdm` settings block with nozzle, bed, speed, retraction, infill, wall, and support-style fields.

Export rendering is split accordingly:

- [app/chitubox.py](../app/chitubox.py) renders Chitubox-compatible CFG output for MSLA only.
- [app/cura.py](../app/cura.py) renders Cura profile output for FDM only.

### 5. Phase 3 Feedback and Adaptation

Feedback processing gives the system a historical adjustment layer.

There are three major paths:

- direct feedback record ingestion,
- community sheet import from bytes or URL,
- YouTube transcript ingestion for text-derived signals.

Those flows land in the feedback store and are summarized into a `FeedbackSummary`. [app/feedback_adaptation.py](../app/feedback_adaptation.py) can then adjust base settings when enough historical signal exists.

At the moment, history-aware adaptation is still resin-first. When FDM settings are selected, the current behavior is to return the FDM baseline settings unchanged and report that limitation explicitly.

### 6. Catalog, Sync, and Governance

The technical catalog is central to trustworthy settings generation.

Catalog modules manage:

- printers,
- materials (still stored through the `resins` entity name for API compatibility),
- printer-material compatibility profiles,
- JSON/CSV import/export,
- catalog version snapshots,
- audit logging for mutations.

Sync functionality supports:

- bundled official dataset import,
- GitHub-hosted normalized JSON,
- direct web-hosted normalized JSON,
- supported HTML page scraping.

Supported HTML scraping is intentionally narrow today and currently focuses on Anycubic product pages plus the Anycubic resin settings guide.

Schedule definitions are persisted, and the scheduler can execute them in the background when enabled. This makes the app a local operations system for maintaining the technical source of truth used by the recommendation engine, not just a mesh analyzer.

### 7. Jobs, Metrics, and Operations

Operational capabilities live alongside the core workflow:

- job submission and lifecycle tracking,
- cancellation and cleanup,
- audit history,
- Prometheus metrics,
- scheduler health,
- sync execution history,
- wizard update status and run-now controls.

The metrics surface is used both by the operator UI and by Prometheus in Docker Compose.

## File and Module Map

The most important files for understanding the app are:

- [app/main.py](../app/main.py): process startup, store initialization, HTTP routes, wizard runtime state.
- [app/pipeline.py](../app/pipeline.py): orchestration between geometry, logic, and feedback phases.
- [app/geometry.py](../app/geometry.py): STL/GLB/3MF analysis, metadata extraction, repair, slicing, voxel work, cavity/island heuristics, and FDM support metrics.
- [app/logic.py](../app/logic.py): target-aware settings generation for MSLA and FDM.
- [app/chitubox.py](../app/chitubox.py): MSLA slicer export rendering.
- [app/cura.py](../app/cura.py): FDM Cura profile rendering.
- [app/models.py](../app/models.py): shared request and response schema definitions.
- [app/catalog_store.py](../app/catalog_store.py): printer/material/profile persistence.
- [app/sync_service.py](../app/sync_service.py): normalized sync fetch/apply logic.
- [app/web_catalog_scraper.py](../app/web_catalog_scraper.py): supported vendor HTML parsing.
- [app/feedback_store.py](../app/feedback_store.py): feedback persistence and querying.
- [app/job_store.py](../app/job_store.py) and [app/job_queue.py](../app/job_queue.py): async job persistence and execution.
- [app/scheduler.py](../app/scheduler.py): persisted schedule orchestration.
- [app/static/wizard.html](../app/static/wizard.html): guided target-aware wizard UI.
- [app/static/app.html](../app/static/app.html): operations console.
- [app/static/catalog_admin.html](../app/static/catalog_admin.html): catalog maintenance UI.

## End-To-End Request Example

For a wizard model analysis request, the flow is:

1. the browser posts STL, GLB, or 3MF bytes to `POST /wizard/model/check`,
2. [app/main.py](../app/main.py) streams the upload to a temp file,
3. the route calls `pipeline.run_phase_1_geometry(...)` in a worker thread,
4. [app/geometry.py](../app/geometry.py) emits progress callbacks during each major stage,
5. progress is stored in the in-memory wizard progress map,
6. the browser polls `GET /wizard/model/check/status/{job_id}`,
7. when analysis completes, the response returns `GeometryAnalysis`,
8. the UI can then call repair or `POST /wizard/settings/recommend` using the selected target, printer, and material.

For a remote catalog refresh:

1. the browser posts `POST /wizard/database/setup` with `mode=github`, `web_json`, or `web_scrape`,
2. [app/main.py](../app/main.py) fetches or scrapes the source,
3. [app/sync_service.py](../app/sync_service.py) curates and upserts the normalized rows,
4. the wizard refreshes `/wizard/catalog/options` and `/wizard/database/status`,
5. an optional schedule is persisted for future updates.

## Deployment Model

The app supports two normal launch modes:

- local Python with `uvicorn app.main:app --reload`,
- Docker Compose with `api` and `prometheus` services.

The Docker path is the easiest way to reproduce the full runtime because it matches:

- the API service,
- the metrics scrape path,
- the local Prometheus configuration,
- the `./local-data` persistence model,
- the validation flow used during recent regression testing.

## Current Design Tradeoffs

The project intentionally favors:

- local-first persistence over network service dependencies,
- explicit heuristics over opaque ML inference,
- browser polling and cooperative cancellation over hidden long-running uploads,
- human-readable provenance and notes over black-box recommendations,
- narrow, supported HTML scrapers over broad unbounded scraping logic.

The main areas still being tuned are cavity confidence, peel-risk discrimination on unusual hollow models, and deeper FDM guidance beyond the current first-pass branch.