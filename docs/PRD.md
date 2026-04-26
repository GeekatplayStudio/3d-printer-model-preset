# Product Requirements Document (PRD)

## Product Name

Geekatplay Studio 3D Print Ops

## Product Vision

Build an agentic assistant for 3D printing that converts high-level goals into reliable, explainable slicer settings while giving operators full control over the technical catalog that backs those recommendations.

The current production path is strongest for resin/MSLA, with an initial FDM/filament branch now live for catalog selection, optimization, and Cura export.

## Problem Statement

3D printing setup is fragmented, manual, and error-prone:

- printer and material parameters vary by hardware, process, and environment,
- online recommendations are inconsistent and often lack provenance,
- operators need fast catalog updates without breaking production,
- remote data often arrives as a mix of checked-in JSON, hosted JSON, or vendor HTML pages.

Geekatplay Studio solves this by combining geometry analysis, catalog intelligence, sync/scrape tooling, and feedback-driven adaptation in a modular workflow.

## Target Users

- Hobby and professional MSLA and FDM print operators.
- Lab/studio teams maintaining multiple printer models and material lines.
- Technical admins managing profile data, sync jobs, and operational auditability.

## Core User Outcomes

- Get high-quality first-pass settings for a model, printer, and material.
- Reduce print failures from geometry risk, peel-force stress, support risk, and environment mismatch.
- Maintain a shared technical database with safe rollback, audit trails, and source attribution.
- Complete the full flow through an extremely simple step-by-step wizard with minimal manual data entry.
- See exactly where recommended settings came from before printing.

## Product Scope (Current)

### Workflow

- Upload a model and run the full pipeline (`/pipeline`).
- Analyze geometry only (`/phase1/analyze`).
- Generate optimized settings (`/phase2/optimize` and the history-aware variant).
- Import and summarize feedback from files, URLs, YouTube, and camera logs.
- Guided end-user wizard (`/wizard`) with ordered steps:
  1. Choose the analyzer target (`MSLA / resin` or `FDM / filament`).
  2. Setup/update the catalog from the bundled official dataset, a GitHub feed, a direct web JSON feed, or supported vendor pages.
  3. Review catalog status/completeness and readiness.
  4. Upload an STL, GLB, or 3MF model and run integrity plus geometry analysis with live stage progress, timing detail, cancel support, and best-effort metadata inspection.
  5. Auto-fix or retopologize the mesh where possible and provide downloadable artifacts.
  6. Select printer plus material and generate settings with on-screen results, source references, trust level, and slicer-ready downloads.

### Data Maintenance

- CRUD APIs for printers, materials, and compatibility profiles.
- CSV/JSON import/export.
- Catalog snapshots and restore.
- Search and row-level editing from the admin UI.
- Source-attributed catalog records stored per row in metadata.
- Scheduled remote updates for GitHub, direct web JSON, and supported vendor pages.

### Operations

- Standalone local deployment by default.
- Optional RBAC auth with `Authorization` tokens when running in shared/server mode.
- Async jobs with status, cancel, and cleanup.
- Long-running wizard analysis with live status, stage timings, and operator cancellation.
- Scheduled sync definitions and manual/background execution.
- Metrics and Prometheus export.

## Use Cases

### Miniature

- Priority: maximum detail.
- Typical bias: finer layers and slower quality-first settings.

### Collectible

- Priority: visible surface finish.
- Typical bias: cleaner surfaces, controlled speeds, and support caution.

### Heavy Use

- Priority: structural strength and dimensional fit.
- Typical bias: stronger bonding, conservative thermal choices, and more robust support assumptions.

## Functional Requirements

1. Geometry Intent Detection
- compute SAR/detail-density features, grouped unsupported-region summaries, peel-risk proxies, and first-pass FDM support-risk signals from mesh input.

2. Parameter Decision Engine
- produce explainable settings from geometry + material + printer + environment.
- branch between MSLA and FDM output shapes without hiding which process path was selected.
- include provenance classification for every settings result (`verified_sources`, `catalog_unverified`, `fallback_defaults`).

3. Catalog Intelligence
- load profile overrides from a maintained printer-material compatibility catalog.
- keep per-record provenance (`source_urls`, retrieval date, source type) for auditable settings quality.
- expose compatibility-aware printer/material selection so wizard defaults remain data-backed.
- support local, GitHub, web JSON, and supported HTML scrape catalog refresh paths.

4. Feedback Intelligence
- ingest and persist field feedback and use it to adapt future recommendations.

5. Ops and Governance
- enforce RBAC, track audit events, and provide rollback paths for catalog edits and remote updates.

6. Scalability of Data Maintenance
- support all printer and material entries, not only Mars 5 Ultra or resin-only data.

7. Wizard-First UX
- provide a no-code, no-JSON path for non-technical users.
- enforce sequential completion of critical steps before continuing.
- keep each step clear, with one primary action and explicit status output.
- expose live progress, cancellation controls, and source-selection clarity for long-running tasks.

## Non-Functional Requirements

- Deterministic and explainable outputs.
- Persistent storage for catalog, jobs, audit, schedules, and feedback.
- Secure-by-config auth for production mode.
- Scriptable and API-first design for automation.
- Large-model robustness: upload staging and minimum analysis mode must remain memory-safe and provide progress/cancel clarity.
- Remote ingestion paths must validate destinations conservatively and reject local/private targets.

## Success Metrics

- Reduction in failed prints after the first recommendation cycle.
- Catalog update turnaround time.
- Percentage of jobs completed without manual parameter override.
- Increase in operator confidence from explainability, provenance, and rollback tooling.

## Out of Scope (Current Release)

- Full real-time printer control loop for all vendors.
- Fully automated closed-loop camera correction without operator confirmation.
- Broad, arbitrary web scraping beyond the explicitly supported vendor-page set.
- Final polished enterprise frontend beyond the current ops/admin UIs.

## Release Priorities

1. Stability and data quality hardening.
2. FDM guidance and broader export coverage.
3. Frontend UX uplift.
4. Wider integration coverage (SDCP and external data connectors).
