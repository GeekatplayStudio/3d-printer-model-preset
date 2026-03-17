# Product Requirements Document (PRD)

## Product Name

ResinLogic AI

## Product Vision

Build an agentic assistant for resin printing that converts high-level goals into reliable, explainable printer settings, while also giving operators full control to maintain technical data for all printers and all resins.

## Problem Statement

Resin printing setup is fragmented, manual, and error-prone:

- printer and resin parameters vary by hardware and environment,
- online recommendations are inconsistent,
- operators need fast updates to technical profiles without breaking production.

ResinLogic AI solves this by combining geometry analysis, catalog intelligence, and feedback-driven adaptation in a modular workflow.

## Target Users

- Hobby and professional resin print operators.
- Lab/studio teams maintaining multiple printer models and resin lines.
- Technical admins managing profile data, sync jobs, and operational auditability.

## Core User Outcomes

- Get high-quality first-pass settings for a model, printer, and resin.
- Reduce print failures from peel-force stress, blooming, delamination, and environment mismatch.
- Maintain a shared technical database with safe rollback and clear audit trails.

## Product Scope (Current)

### Workflow

- Upload model and run full pipeline (`/pipeline`).
- Analyze geometry only (`/phase1/analyze`).
- Generate optimized settings (`/phase2/optimize` and history-aware variant).
- Import and summarize feedback from files, URLs, YouTube, and camera logs.

### Data Maintenance

- CRUD APIs for printers, resins, and compatibility profiles.
- CSV/JSON import/export.
- Catalog snapshots and restore.
- Search and row-level editing from admin UI.

### Operations

- RBAC auth with API keys.
- Async jobs with status, cancel, and cleanup.
- Scheduled sync definitions and manual/background execution.
- Metrics and Prometheus export.

## Use Cases

### Miniature

- Priority: maximum detail.
- Typical defaults: low layer height, fast tilt only when cross-section risk is low.

### Collectible

- Priority: surface finish.
- Typical defaults: anti-aliasing + slower peel behavior for larger sections.

### Heavy Use

- Priority: structural strength and fit.
- Typical defaults: stronger adhesion and conservative exposure behavior.

## Functional Requirements

1. Geometry Intent Detection
- compute SAR/detail-density features and stress proxies from mesh input.

2. Parameter Decision Engine
- produce explainable settings from geometry + resin + printer + environment.

3. Catalog Intelligence
- load profile overrides from maintained printer-resin compatibility catalog.

4. Feedback Intelligence
- ingest and persist field feedback and use it to adapt future recommendations.

5. Ops and Governance
- enforce RBAC, track audit events, and provide rollback path for catalog edits.

6. Scalability of Data Maintenance
- support all printer and resin entries, not only Mars 5 Ultra.

## Non-Functional Requirements

- Deterministic and explainable outputs.
- Persistent storage for catalog, jobs, audit, schedules, feedback.
- Secure-by-config auth for production mode.
- Scriptable and API-first design for automation.

## Success Metrics

- Reduction in failed prints after first recommendation cycle.
- Catalog update turnaround time.
- Percentage of jobs completed without manual parameter override.
- Increase in operator confidence from explainability and rollback tooling.

## Out of Scope (Current Release)

- Full real-time printer control loop for all vendors.
- Fully automated closed-loop camera correction without operator confirmation.
- Final polished enterprise frontend beyond current ops/admin UIs.

## Release Priorities

1. Stability and data quality hardening.
2. Frontend UX uplift.
3. Wider integration coverage (SDCP and external data connectors).
