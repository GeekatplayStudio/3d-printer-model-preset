# Agentic Coding Roadmap

## Phase 1: Geometry & Structure Agent

- Extract cross-sectional area per slice (`0.01mm` default requested)
- Detect suction-cup risk via enclosed-volume analysis
- Detect unsupported islands layer-by-layer
- Compute detail density (`triangle_count / surface_area`)
- Compute Surface Area Ratio (SAR) and infer intent (`miniature`, `collectible`, `heavy_use`)

Primary module: `app/geometry.py`

## Phase 2: Parameter Injection Agent

- Apply Mars 5 Ultra tilt-release mapping
- Generate use-case specific settings
- Inject multi-parameter slicing guidance
- Export Chitubox-compatible `.cfg` text and SDCP payload structure
- Add history-aware adaptation mode to tune exposure using stored Phase 3 outcomes

Primary modules: `app/logic.py`, `app/chitubox.py`

## Phase 3: Resin Intelligence Agent

- Ingest curated community feedback data
- Separate likely marketing-style posts from parameter-backed logs
- Flag blooming/delamination reports for printer+resin combinations
- Parse Mars 5 Ultra camera/error logs and suggest direct parameter corrections

## Catalog Maintenance Agent

- Maintain printer technical metadata for all supported devices
- Maintain resin technical metadata across vendors and generations
- Maintain printer-resin compatibility profiles via CRUD and import/export workflows
- Create versioned catalog snapshots and allow rollback to prior revisions
- Track all operational mutations in audit history
- Expose async jobs for long-running maintenance and sync operations

Primary modules: `app/catalog_store.py`, `app/audit_store.py`

## Operations Agent

- Enforce RBAC policies for viewer/operator/admin workflows
- Surface request metrics and Prometheus export
- Run async pipeline/sync jobs and expose live job status APIs
- Provide a single browser console (`/app`) for operators

Primary modules: `app/auth.py`, `app/job_queue.py`, `app/monitoring.py`
