# Product Requirements Document

## Primary User Goals

- Auto-derive stable, quality-focused Mars 5 Ultra settings from geometry + resin context
- Minimize failed prints caused by peel force, blooming, and delamination
- Provide explainable recommendations per use case

## Use Cases

### Miniature

- Target: max detail
- Defaults: 18um XY, 0.02mm layer, 1.8s baseline exposure

### Collectible

- Target: smooth surface
- Defaults: anti-aliasing 4, grayscale level 2, slower tilt profile

### Heavy Use

- Target: structural strength
- Defaults: +15% bottom exposure, 0.05mm layer, tough-resin recommendation

## Feature Requirements

- Tilt-release prediction and tilt-angle recommendation
- Multi-parameter slicing profile output (supports vs delicate features)
- Temperature-aware exposure compensation
- Vat film life compensation once release count is high
- Persistent feedback memory with query/summary by printer+resin+date
- History-aware optimization loop: use prior blooming/delamination outcomes to tune next exposure suggestion
- Camera log loop: parse Warp/Empty-plate errors and suggest next-job overrides
- Intent classifier with SAR-backed routing between miniature/collectible/heavy-use modes
- Full catalog maintenance APIs for all printers and all resins (create/update/delete/import/export)
- Catalog version snapshots and restore workflow for safe rollback
- Async job execution for heavy operations (pipeline and technical sync)
- Role-based operational access with API key policy controls
- Unified browser UI for operators (`/app`)

## Future Work

- Real-time Mars 5 Ultra AI camera/error log feedback loop
- Direct SDCP bidirectional communication with slicer/printer stack
- Dedicated React + Three.js 3D stress-map frontend replacing static-console MVP
