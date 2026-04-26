# Continuity and Reference Guide

This document is the repo-local anti-drift handoff for the catalog, wizard, and provenance system. Update it when shipped behavior changes so future work stays grounded in the codebase instead of chat history.

## Purpose

Use this file as the first reference when changing:

- bundled catalog seed slices,
- sync curation and duplicate precedence,
- wizard catalog option payloads,
- wizard Step 3 provenance UX,
- feature tracking and near-term planning.

## Framework and Runtime References

- API framework: FastAPI in `app/main.py`
- Request and response models: Pydantic models in `app/models.py`
- Persistence: SQLite store helpers in `app/catalog_store.py`, `app/audit_store.py`, `app/job_store.py`, and `app/sync_schedule_store.py`
- Frontend: server-rendered HTML and JavaScript in `app/static/wizard.html`, `app/static/app.html`, and `app/static/catalog_admin.html`
- Catalog sync and curation: `app/sync_service.py`
- Bundled seed merge loader: `app/official_catalog.py`
- Settings generation: `app/logic.py`
- Exporters: `app/chitubox.py` for MSLA and `app/cura.py` for FDM

## Source-of-Truth Code Anchors

### Bundled Catalog Aggregator

- `app/official_catalog.py`
- `DEFAULT_OFFICIAL_SYNC_FILES` is the authoritative list of checked-in seed slices merged into `official_local`.
- `_enrich_row_metadata(...)` backfills provenance fields so every row exposes a stable metadata contract.

### Import and Duplicate Resolution

- `app/sync_service.py`
- `apply_technical_sync(...)` is the import entry point.
- `_curate_printers(...)`, `_curate_resins(...)`, and `_curate_profiles(...)` are the current duplicate-resolution control points.
- `_metadata_precedence(...)` is the current precedence rule: lower source tier wins first, then stronger row confidence, then newer retrieval date, then row order.

### Wizard Catalog Contract

- `app/models.py`
- `WizardCatalogOptionsResponse` is the API contract for Step 3.
- `WizardCatalogEntitySummary`, `WizardCatalogCompatibilitySummary`, and `WizardCatalogProvenanceSummary` are the provenance-aware shapes the UI now depends on.

### Wizard Step 3 UI

- `app/static/wizard.html`
- `refreshOptions()` hydrates dropdowns and structured provenance details.
- `renderCatalogSelectionProvenance()` is the current selection-step evidence panel.
- `renderCatalogList()` is the current operator-facing catalog list renderer.

### Regression Coverage

- `tests/test_official_catalog_seed.py` verifies merged seed metadata, import success, and duplicate precedence.
- `tests/test_wizard_flow.py` verifies the wizard HTML surface and the `/wizard/catalog/options` response contract.

## Current Bundled Seed Slices

The checked-in `official_local` bundle currently merges:

- `data/official_catalog_sync.json`: conservative MSLA-first baseline manufacturer data.
- `data/official_catalog_fdm_aggregator.json`: normalized FDM printer, generic filament, and slicer-repository-backed profile coverage.
- `data/official_catalog_manufacturer_filaments.json`: Tier 1 manufacturer-backed filament rows and normalized FDM profiles, including fresher Anycubic High Speed PLA, Anycubic PETG, and live Prusament PLA NFC provenance.
- `data/official_catalog_community_crosscheck.json`: lower-tier duplicate rows used as cross-check inputs and dedupe tests.

## Shipped Behavior Snapshot

### Catalog and Provenance

- Every bundled row now carries `source_url`, `source_urls`, `retrieved_at`, `source_type`, `source_priority_tier`, `confidence`, `sync_confidence_score`, and `license_note`.
- Wizard Step 3 now exposes provenance for the selected printer, material, and preferred compatibility profile without changing the downstream settings-generation flow.
- Community cross-check rows are intentionally allowed into the merged payload, but import curation should keep stronger Tier 1 and Tier 2 rows when names collide.
- The `2026-04-26` refresh added Tier 1 Kobra S1 compatibility rows for official Anycubic High Speed PLA and PETG, and replaced the stale Prusament PLA Galaxy Black source URL with the live NFC product page.
- After the live Docker reseed, `/wizard/database/status` reported 22 curated printers, 26 curated materials, and 47 curated profiles, while `/wizard/catalog/options` exposed 7 FDM printers and 15 FDM materials.

### Wizard Flow

- Step 0 chooses `MSLA / resin` or `FDM / filament`.
- Step 1 can seed from `official_local`, GitHub JSON, direct web JSON, or supported vendor HTML pages.
- Step 2 handles STL analysis, repair, retopology, progress polling, and cancellation.
- Step 3 filters printer/material options by target and now shows selection-step source evidence before export.

## Anti-Drift Update Rules

1. If you add or remove a bundled seed slice, update `app/official_catalog.py`, `docs/OFFICIAL_DATASET.md`, `docs/CATALOG_AGGREGATOR.md`, and `tests/test_official_catalog_seed.py` together.
2. If you change `/wizard/catalog/options`, update `app/models.py`, `app/main.py`, `app/static/wizard.html`, and `tests/test_wizard_flow.py` together.
3. If you change duplicate precedence or confidence rules, update `app/sync_service.py` and add or adjust a regression in `tests/test_official_catalog_seed.py`.
4. If you change what the operator sees in Step 3, update this document, `docs/PROJECT_STATUS.md`, and `docs/TASK.md` so repo planning stays aligned with shipped behavior.

## Validation Baseline

Run these before treating catalog or wizard changes as complete:

```bash
pytest tests/test_official_catalog_seed.py tests/test_wizard_flow.py -q
```

When the change touches broader sync behavior, also run:

```bash
pytest tests/test_sync_service.py tests/test_main_catalog_api.py -q
```

## Current Next Plan

1. Add stronger operator guardrails when a selected profile falls below the preferred provenance threshold.
2. Define a formal SQLite migration/version policy.
3. Expand supported vendor scrapers only when a normalized JSON feed is not available.
4. Continue cautious Tier 1 FDM additions only when a live official source gives new material or printer coverage.
