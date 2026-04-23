# Architecture and Process Flow

## What The Application Does

ResinLogic AI is a local-first FastAPI application for resin-print workflow support.

It helps an operator move through three connected concerns:

1. inspect and repair incoming STL geometry,
2. produce printer-resin settings from catalog data plus heuristics,
3. ingest feedback and operational data so later recommendations can improve.

The application is intentionally split between an end-user wizard flow and an operator/admin surface:

- `/wizard` guides a user through data readiness, model analysis, repair, and settings generation.
- `/app` exposes the broader operations console for jobs, sync, metrics, and catalog state.
- `/catalog/admin` is a focused maintenance UI for printers, resins, and compatibility profiles.

## Core Libraries

The runtime dependencies come from [pyproject.toml](../pyproject.toml) and are used as follows:

- `fastapi`: HTTP API, request validation, dependency injection, and static UI serving.
- `pydantic`: request/response models for geometry analysis, wizard status, catalog entities, jobs, and settings.
- `uvicorn`: ASGI server for local and containerized startup.
- `numpy`: vectorized numeric work inside geometry analysis and heuristic scoring.
- `pandas`: catalog import/export transforms and feedback ingestion helpers.
- `python-multipart`: file upload handling for STL and CSV inputs.
- `trimesh[easy]`: primary mesh loading, repair helpers, voxelization, slicing, and geometry calculations.
- `pyvista`: optional higher-cost surface-detail proxy calculations for deeper geometry analysis.
- `httpx` and `pytest` in the dev extra: API testing and regression coverage.
- `youtube-transcript-api` in the optional `sources` extra: transcript ingestion for feedback signals.

Indirectly, `trimesh[easy]` brings in helpers like `scipy`, `rtree`, and mesh-processing extras that support slicing, path handling, and spatial operations.

## High-Level Runtime Shape

The application starts in [app/main.py](../app/main.py). That file does four important things:

1. resolves a writable local data directory (`RESINLOGIC_DATA_DIR`, then fallbacks),
2. initializes SQLite-backed stores for catalog, feedback, audit, jobs, and sync schedules,
3. creates the FastAPI app plus in-memory runtime helpers for jobs, metrics, wizard artifacts, and wizard progress,
4. exposes API routes and serves the static browser UIs.

The orchestration layer lives in [app/pipeline.py](../app/pipeline.py). `ModularAgenticPipeline` is the thin composition point that calls the focused modules:

- [app/geometry.py](../app/geometry.py) for phase 1 model analysis,
- [app/logic.py](../app/logic.py) for settings generation,
- [app/feedback.py](../app/feedback.py), [app/feedback_adaptation.py](../app/feedback_adaptation.py), and [app/feedback_sources.py](../app/feedback_sources.py) for historical and external feedback,
- [app/chitubox.py](../app/chitubox.py) for emitted slicer config output,
- store modules for catalog, jobs, sync, feedback, and audit persistence.

## Main Process Flows

### 1. Startup and Storage Initialization

At process start, [app/main.py](../app/main.py) creates or opens the local databases:

- `tech_catalog.db`
- `feedback_history.db`
- `audit_log.db`
- `jobs.db`
- `sync_schedules.db`

It also seeds the catalog from legacy bundled JSON, prepares a temporary wizard artifact directory, and wires in-memory objects for:

- live wizard analysis progress,
- background job execution,
- request metrics,
- optional background sync scheduling.

This design keeps the runtime local-first and avoids needing a separate managed database for normal use.

### 2. Wizard Flow

The wizard is the clearest end-to-end flow in the app.

The user journey is:

1. check whether the local catalog is ready,
2. optionally seed or sync technical data,
3. upload an STL,
4. run geometry analysis,
5. optionally auto-fix the STL,
6. select printer and resin,
7. generate settings and downloadable artifacts.

The browser code in [app/static/wizard.html](../app/static/wizard.html) talks to wizard routes in [app/main.py](../app/main.py). Those routes are designed for long-running geometry work:

- uploads are streamed to disk in chunks,
- geometry analysis is offloaded with `asyncio.to_thread`,
- in-memory status is tracked by `progress_job_id`,
- the UI polls status routes for live stage messages and timings,
- cancellation is cooperative and checked inside the geometry engine.

That is why the wizard can show stage names like cross-section analysis, voxelization, cavity detection, island detection, and finalization while a large STL is being processed.

### 3. Phase 1 Geometry Analysis

Geometry analysis is implemented in [app/geometry.py](../app/geometry.py). It is the heaviest computational part of the system.

The rough sequence is:

1. load and normalize the mesh,
2. inspect mesh health and optionally attempt repair,
3. choose an analysis profile (`minimum`, `balanced`, `deep`),
4. adapt slice and voxel settings for large meshes,
5. compute cross-sections,
6. voxelize when needed,
7. detect cavity candidates and unsupported islands,
8. optionally estimate curvature/detail density,
9. derive aggregate metrics, intent hints, structural risk, and notes.

Some important implementation details:

- `minimum` mode favors memory safety and runtime predictability.
- small meshes can use exact multiplane slicing.
- large meshes can fall back to a lighter surface-span approximation.
- balanced and deep modes can throttle voxel pitch and slice counts for very large models.
- voxel fallback data can be reused so later stages do not repeat expensive work.
- cavity detection now distinguishes broad pocket-like cavities from narrow sealed shafts and assigns confidence metadata.
- island detection groups adjacent unsupported layers into higher-level regions instead of noisy per-layer reports.

The result is serialized as `GeometryAnalysis`, which includes:

- cross-section metrics,
- mesh health,
- cavity and island findings,
- estimated print intent,
- structural risk score,
- notes,
- per-stage performance timings.

### 4. Phase 2 Settings Generation

Once geometry analysis exists, [app/logic.py](../app/logic.py) turns it into machine settings.

Inputs include:

- geometry metrics,
- chosen use case,
- printer name,
- resin type,
- optional ambient temperature,
- optional film release count,
- optional catalog profile data.

The settings logic mixes:

- built-in heuristics,
- printer defaults,
- resin compatibility profiles,
- temperature compensation,
- cross-section and cavity-sensitive tilt decisions,
- data provenance and confidence reporting.

The output is `OptimalSettings`, and [app/chitubox.py](../app/chitubox.py) can render a Chitubox-compatible CFG text export from that result.

### 5. Phase 3 Feedback and Adaptation

Feedback processing gives the system a historical adjustment layer.

There are three major paths:

- direct feedback record ingestion,
- community sheet import from bytes or URL,
- YouTube transcript ingestion for text-derived signals.

Those flows land in the feedback store and are summarized into a `FeedbackSummary`. [app/feedback_adaptation.py](../app/feedback_adaptation.py) can then adjust base settings when enough historical signal exists.

This is why the pipeline exposes both:

- normal optimization,
- history-aware optimization.

### 6. Catalog, Sync, and Governance

The technical catalog is central to trustworthy settings generation.

Catalog modules manage:

- printers,
- resins,
- printer-resin compatibility profiles,
- import/export,
- catalog version snapshots,
- audit logging for mutations.

Sync functionality lets the app pull structured data from local files or GitHub-hosted JSON payloads. Schedule definitions are persisted, and the scheduler can execute them in the background when enabled.

This means the app is not just an analyzer. It also acts as a local operations system for maintaining the technical source of truth used by the recommendation engine.

### 7. Jobs, Metrics, and Operations

Operational capabilities live alongside the core workflow:

- job submission and lifecycle tracking,
- cancellation and cleanup,
- audit history,
- Prometheus metrics,
- scheduler health,
- sync execution history.

The metrics surface is used both by the operator UI and by Prometheus in Docker Compose.

## File and Module Map

The most important files for understanding the app are:

- [app/main.py](../app/main.py): process startup, store initialization, HTTP routes, wizard runtime state.
- [app/pipeline.py](../app/pipeline.py): orchestration between geometry, logic, and feedback phases.
- [app/geometry.py](../app/geometry.py): STL analysis, repair, slicing, voxel work, cavity/island heuristics.
- [app/logic.py](../app/logic.py): settings generation and rule-based print parameter decisions.
- [app/models.py](../app/models.py): shared request and response schema definitions.
- [app/catalog_store.py](../app/catalog_store.py): printer/resin/profile persistence.
- [app/feedback_store.py](../app/feedback_store.py): feedback persistence and querying.
- [app/job_store.py](../app/job_store.py) and [app/job_queue.py](../app/job_queue.py): async job persistence and execution.
- [app/sync_service.py](../app/sync_service.py) and [app/scheduler.py](../app/scheduler.py): sync execution and schedule orchestration.
- [app/static/wizard.html](../app/static/wizard.html): guided end-user UI.
- [app/static/app.html](../app/static/app.html): operations console.
- [app/static/catalog_admin.html](../app/static/catalog_admin.html): catalog maintenance UI.

## End-To-End Request Example

For a wizard STL analysis request, the flow is:

1. the browser posts STL bytes to `POST /wizard/model/check`,
2. [app/main.py](../app/main.py) streams the upload to a temp file,
3. the route calls `pipeline.run_phase_1_geometry(...)` in a worker thread,
4. [app/geometry.py](../app/geometry.py) emits progress callbacks during each major stage,
5. progress is stored in the in-memory wizard progress map,
6. the browser polls `GET /wizard/model/check/status/{job_id}`,
7. when analysis completes, the response returns `GeometryAnalysis`,
8. the UI can then call STL repair or settings generation using that result.

That same pattern is what makes large-model analysis usable in the browser even when slicing or voxelization takes noticeable time.

## Deployment Model

The app supports two normal launch modes:

- local Python with `uvicorn app.main:app --reload`,
- Docker Compose with `api` and `prometheus` services.

The Docker path is the easiest way to reproduce the full runtime because it matches:

- the API service,
- the metrics scrape path,
- the local Prometheus configuration,
- the validation flow used during recent regression testing.

## Current Design Tradeoffs

The project intentionally favors:

- local-first persistence over network service dependencies,
- explicit heuristics over opaque ML inference,
- browser polling and cooperative cancellation over hidden long-running uploads,
- human-readable provenance and notes over black-box recommendations.

The main area still being tuned is cavity confidence and peel-risk discrimination, especially on unusual hollow models.