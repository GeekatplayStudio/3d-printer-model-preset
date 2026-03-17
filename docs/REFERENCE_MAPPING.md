# Gemini Reference Mapping

This file maps the provided Gemini roadmap to the current implementation.

## Agent A: Geometric Visionary

- SAR (`surface_area_ratio`) computed in `app/geometry.py`
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

## Agent C: Integration

- SDCP payload and `.cfg` generation in `app/chitubox.py`
- New fields included:
  - `transition_layers`
  - rest times
  - scale compensation

## Camera Feedback Loop

- `POST /phase3/camera-log/analyze`
- Parses `.log` text for:
  - warp
  - empty plate
  - delamination
  - blooming

