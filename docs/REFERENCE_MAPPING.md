# Gemini Reference Mapping

This file maps the provided Gemini roadmap to the current implementation.

## Agent A: Geometric Visionary

- SAR (`surface_area_ratio`) computed in `app/geometry.py`
- FDM support heuristics now computed in `app/geometry.py`:
  - `downskin_area_ratio`
  - `fdm_support_risk_score`
- Intent inference (`estimated_intent`) for:
  - `miniature`
  - `collectible`
  - `heavy_use`

## Agent B: Decision Engine

- Tilt gate logic:
  - `cross_section > 25%` -> slow gate (`70`)
  - `cross_section > 30%` -> ultra-slow (`60`)
  - `miniature && cross_section < 5%` -> fast (`150`)
- Temperature scaling:
  - `<22C` -> +5% exposure per degree via `get_temp_offset`
  - `<18C` -> `heater_required = true`
- ACF film logic:
  - `>=30,000` -> `rest_time_after_retract +0.5s`
  - `>=60,000` -> service warning + tilt reduction
- Shrinkage compensation:
  - `Anycubic High Speed 2.0` + `heavy_use` -> `100.5%` scale compensation
- Target-aware process routing:
  - printer/material profile lookup can now branch into FDM output,
  - family-aware FDM defaults currently cover PLA/PETG/ABS/ASA/TPU/Nylon,
  - low ambient temperature increases FDM nozzle/bed targets,
  - geometry-derived support-risk notes feed into FDM recommendations.
- Settings provenance:
  - output includes `provenance.data_quality` (`verified_sources`, `catalog_unverified`, `fallback_defaults`)
  - output includes `provenance.references[]` with source links and optional confidence

## Agent C: Integration

- SDCP payload and `.cfg` generation in `app/chitubox.py` for MSLA only
- Cura profile generation in `app/cura.py` for FDM output
- New fields included:
  - `transition_layers`
  - rest times
  - scale compensation
  - FDM nozzle/bed/speed/retraction/support fields

## Camera Feedback Loop

- `POST /phase3/camera-log/analyze`
- Parses `.log` text for:
  - warp
  - empty plate
  - delamination
  - blooming

## Wizard Mapping

- Guided STL, GLB, and 3MF upload and analysis flow in `/wizard`
- Step 0 target selector now routes the wizard between `MSLA / resin` and `FDM / filament`
- Step 1 catalog source modes now include:
  - bundled official dataset
  - GitHub sync feed
  - direct web JSON feed
  - supported vendor pages
- analysis depth selector with large-model runtime optimizations
- printer/material compatibility dropdown behavior driven by catalog profile coverage and filtered by target
- saved remote update schedules are exposed in the wizard and can be run on demand
- settings summary now includes provenance quality, confidence, source links, and target-specific slicer export metadata
