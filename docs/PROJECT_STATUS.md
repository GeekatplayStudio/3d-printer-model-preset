# Project Status

## What This Project Is

Geekatplay Studio 3D Print Ops is an agentic print optimization platform with mature resin/MSLA workflows and a now-proven provenance-aware FDM/filament branch.

It combines:

- mesh geometry analysis,
- printer-material profile intelligence,
- parameter decision logic,
- operational tooling for maintaining technical databases across supported printers, resins, and filament materials.

The immediate production target started around Elegoo Mars 5 Ultra behavior. The catalog and optimization layers now include a broader FDM/filament branch with bundled manufacturer-backed filament coverage, row-aware dedupe, and selection-step provenance surfaced in the wizard.

## What Has Been Done

### Core Pipeline

- Phase 1 geometry analysis endpoint with:
  - cross-section scan support,
  - suction cup and island heuristic detection,
  - SAR/detail density metrics,
  - adaptive balanced/deep runtime throttling,
  - extreme analysis mode for highest-detail scans,
  - exact minimum-mode multiplane slicing for small meshes,
  - surface-span weighted minimum fallback for larger meshes,
  - grouped unsupported-region summaries with layer spans and centroids,
  - richer mesh statistics including vertex count, bounding box, center of mass, and Euler number,
  - save/reload-aware repair outcome reporting.
- Phase 2 optimization endpoint with:
  - use-case templates (`miniature`, `collectible`, `heavy_use`),
  - tilt-speed gates,
  - temperature scaling,
  - film-age compensation,
  - profile-aware overrides from catalog.
- Wizard model workflow with:
  - always-visible local 3D preview,
  - wire/mesh/solid render modes,
  - repair and retopology report panels,
  - live preview scan state during long-running analysis,
  - browser-console diagnostics for local preview runtime failures.
- Retopology backend with:
  - Blender headless execution,
  - quad and voxel remesh modes,
  - repair pre-pass before retopology when the source mesh still fails health checks,
  - downloadable remeshed STL artifacts from the wizard.
- Phase 3 feedback layer with:
  - community sheet ingestion (file/url),
  - YouTube transcript ingestion,
  - camera log parsing and recommendation output,
  - persistent feedback history query/summary APIs.

### Data and Catalog

- SQLite catalog store with normalized entities:
  - printers,
  - materials (currently stored through the existing `resins` entity name for API compatibility),
  - compatibility profiles.
- Full CRUD + search APIs.
- Bulk import/export in JSON and CSV.
- Admin UI for hands-on maintenance, including row-level edit/delete.
- Material typing and first-pass FDM profile support:
  - filament vs resin material typing,
  - FDM profile fields for nozzle/bed/speed/retraction settings,
  - initial FDM optimizer branch with family-aware defaults,
  - geometry output now includes down-facing surface ratio and an FDM support-risk score.
- Wizard target routing and export support:
  - users can now choose `MSLA / resin` vs `FDM / filament` at the start of the wizard,
  - wizard catalog dropdowns are filtered by the selected target,
  - resin/MSLA exports still use Chitubox Free,
  - FDM exports now generate an Ultimaker Cura profile instead of stopping at JSON-only settings.
- Wizard catalog refresh and remote update support:
  - Step 1 now exposes explicit catalog source selection,
  - local setup can still seed from the bundled official dataset,
  - remote setup can now pull normalized sync JSON from GitHub or a direct web URL,
  - Step 1 can also scrape supported vendor web pages into normalized printer/material/profile records,
  - supported page scraping currently targets Anycubic official product pages and the official Anycubic resin settings guide,
  - remote sources can be saved as wizard auto-update schedules and triggered manually from the wizard.
- Bundled official seed dataset refreshed through `2026-04-26` and now ships as a four-slice provenance-backed aggregator:
  - conservative MSLA baseline,
  - FDM printer and generic filament expansion,
  - manufacturer-backed filament rows and normalized FDM profiles,
  - community cross-check duplicates held behind row-aware dedupe rules.
- Live Docker reseed revalidated the bundled catalog against the running wizard endpoints with 7 curated FDM printers, 15 curated FDM materials, and 47 curated compatibility profiles in `/wizard/catalog/options` and `/wizard/database/status`.
- Wizard Step 3 provenance surface now shows source tier, confidence, and source links for the selected printer, material, and preferred compatibility profile.
- Version snapshots and restore.
- Audit logs for all key catalog mutations.

### Operations and Reliability

- Standalone local-first runtime with per-user SQLite storage.
- Optional RBAC authentication via `Authorization` tokens (`viewer`, `operator`, `admin`).
- Async jobs with persistent history.
- Job cancellation and retention cleanup.
- Scheduled sync definitions and manual/background schedule execution via APScheduler.
- Request metrics and Prometheus output.
- Dockerized run path and Prometheus config.
- Docker image and compose updates for optional Blender-backed retopology.
- Source-attributed official seed dataset (`data/official_catalog_sync.json`) plus local import script.
- Wizard pipeline UX hardening for large models:
  - STL-only upload constraints,
  - chunked upload staging for large STL files,
  - live stage-by-stage analyze status polling,
  - per-stage timing visibility and user-triggered cancellation,
  - adaptive timeout windows by analysis depth + model size.
- Auth-aware artifact downloads for repaired STL, CFG, and JSON outputs in shared/server mode.
- Memory-safe minimum analysis path with large-model runtime optimizations and step timing telemetry.
- Cross-section fallback voxel reuse and post-repair duplicate/degenerate cleanup for more stable geometry output.
- Optional PyMeshLab prototype repair backend and opt-in Open3D voxel backend spike with benchmark scripts.
- Open3D solid-occupancy hardening now fills interior voxels via raycast occupancy, reducing false cavity hits on the first closed-box benchmark case.
- Settings provenance surface:
  - data quality classification (`verified_sources`, `catalog_unverified`, `fallback_defaults`),
  - confidence score passthrough where available,
  - source reference list exposed in wizard output.
- Compatibility-aware wizard dropdown behavior (printer selection filters resin list to real profile coverage).
- Test coverage across core modules and API behavior, including focused regressions for large-model geometry, wizard progress/cancel flow, grouped island-region reporting, and local preview asset serving.

## What Still Needs To Be Done

### High Priority

- Broaden the current row-aware validation and trust gating beyond bundled seeds so more remote/community inputs can be accepted without lowering operator confidence.
- Add production-grade migration/version strategy for SQLite schemas.
- Introduce configuration profiles per printer family (not only Mars-first defaults).
- Broaden FDM wizard guidance, filament-family coverage, and additional slicer/export targets beyond Cura.
- Separate true suction-cup peel-risk pockets from benign sealed voids to reduce cavity false positives.

### Product and UX

- Replace static console style pages with a dedicated React frontend.
- Add 3D stress-map visualization (Three.js) for peel-force and suction-risk explanation.
- Expand wizard onboarding/tooltips and richer operator guardrails for low-confidence profile recommendations.

### Integrations

- Implement direct SDCP round-trip integration where available (not just cfg generation).
- Add optional connectors for external resin/printer data providers.

### ML/Agentic Improvements

- Improve suction-cup confidence classification and cavity severity ranking.
- Add adaptive confidence scoring for recommended settings.
- Expand history-aware adaptation to include weighted trends over time and environment context.

## Near-Term Milestones

1. Broaden manufacturer-backed FDM coverage and add stronger low-confidence guardrails in the wizard.
2. Define schema migration/version strategy for long-lived SQLite deployments.
3. Broader Open3D corpus benchmark coverage and tuning.
4. Frontend redesign with workflow-first UX.
