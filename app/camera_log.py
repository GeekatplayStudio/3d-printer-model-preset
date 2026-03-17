from __future__ import annotations

from app.models import CameraLogAdjustment, CameraLogAnalysisResult


def analyze_camera_log(log_text: str) -> CameraLogAnalysisResult:
    text = log_text.lower()
    events: list[str] = []
    adjustments: list[CameraLogAdjustment] = []
    notes: list[str] = []

    if "warp" in text or "warping" in text:
        events.append("warp_detected")
        adjustments.append(
            CameraLogAdjustment(
                parameter="bottom_exposure_s",
                recommended_value="+5.0s",
                reason="Warping usually indicates adhesion loss in early layers.",
            )
        )
        adjustments.append(
            CameraLogAdjustment(
                parameter="tilt_speed_mm_min",
                recommended_value="-15%",
                reason="Lower peel stress to reduce edge lift and support shear.",
            )
        )

    if "empty plate" in text or "empty build plate" in text:
        events.append("empty_plate_detected")
        adjustments.append(
            CameraLogAdjustment(
                parameter="bottom_exposure_s",
                recommended_value="+6.0s",
                reason="Empty plate indicates first-layer failure or severe detachment.",
            )
        )
        notes.append("Verify build plate leveling and clean ACF film before reprint.")

    if "delamination" in text or "layer separation" in text or "split layer" in text:
        events.append("delamination_detected")
        adjustments.append(
            CameraLogAdjustment(
                parameter="exposure_s",
                recommended_value="+0.15s",
                reason="Delamination can indicate under-cure for current geometry/resin state.",
            )
        )
        adjustments.append(
            CameraLogAdjustment(
                parameter="rest_time_after_retract_s",
                recommended_value="+0.5s",
                reason="Additional settle time helps reduce dynamic peel shock.",
            )
        )

    if "blooming" in text or "overcure" in text or "light bleed" in text:
        events.append("blooming_detected")
        adjustments.append(
            CameraLogAdjustment(
                parameter="exposure_s",
                recommended_value="-0.10s",
                reason="Blooming/overcure indicates excess resin crosslinking near feature edges.",
            )
        )

    if not events:
        notes.append("No known Mars 5 Ultra failure signatures detected in this log.")

    return CameraLogAnalysisResult(
        matched_events=events,
        adjustments=adjustments,
        notes=notes,
    )

