from __future__ import annotations

from pathlib import Path

from app.models import GeometryAnalysis, MultiParameterSettings, OptimalSettings, UseCase
from app.resin_db import find_resin_profile

PRINTER_NAME = "Elegoo Mars 5 Ultra"


def _detail_tier(detail_density: float) -> str:
    if detail_density >= 3.0:
        return "ultra_detail"
    if detail_density >= 1.7:
        return "high_detail"
    if detail_density >= 0.8:
        return "medium_detail"
    return "smooth_collectible"


def _tilt_speed_mm_min(cross_section_ratio: float) -> float:
    ratio_pct = cross_section_ratio * 100.0
    if ratio_pct >= 20.0:
        return 40.0
    if ratio_pct <= 5.0:
        return 150.0

    alpha = (ratio_pct - 5.0) / 15.0
    speed = 150.0 - alpha * (150.0 - 40.0)
    return round(speed, 2)


def get_temp_offset(base_exposure: float, current_temp: float) -> tuple[float, bool, list[str]]:
    if current_temp >= 22.0:
        return round(base_exposure, 3), False, []

    delta = 22.0 - current_temp
    multiplier = 1.0 + (0.05 * delta)
    adjusted = round(base_exposure * multiplier, 3)
    heater_required = current_temp < 18.0
    notes = [
        f"Temperature compensation applied: +{delta:.1f}C below 22C with 5%/C exposure scaling."
    ]
    if heater_required:
        notes.append("Heater required: ambient temperature below 18C.")
    return adjusted, heater_required, notes


def _estimate_tilt_angle_deg(cross_section_ratio: float, suction_cups: int) -> float:
    adjusted = cross_section_ratio + min(suction_cups * 0.02, 0.12)
    magnitude = min(max(adjusted / 0.22, 0.0), 1.0) * 4.0
    return round(-magnitude, 2)


def _reference_tilt_speed_mm_min(
    *,
    intent: UseCase,
    cross_section_ratio: float,
    profile: dict | None,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    cross_section_pct = cross_section_ratio * 100.0
    profile_speed = None
    if profile is not None:
        profile_speed = profile.get("tilt_speed_reference_mm_h") or profile.get("tilt_speed_reference_mm_min")
        if profile_speed is not None:
            try:
                profile_speed = float(profile_speed)
            except (TypeError, ValueError):
                profile_speed = None

    if cross_section_pct > 30.0:
        reasons.append("Cross-section >30%: forced ultra-slow tilt mode to reduce support shear.")
        return 60.0, reasons
    if cross_section_pct > 25.0:
        reasons.append("Cross-section >25%: forced slow tilt gate regardless of resin type.")
        return 70.0, reasons
    if intent == "miniature" and cross_section_pct < 5.0:
        reasons.append("Miniature + cross-section <5%: selected fast tilt mode.")
        return 150.0, reasons

    if profile_speed is not None:
        reasons.append("Using resin technical-reference tilt speed.")
        return float(profile_speed), reasons

    fallback = _tilt_speed_mm_min(cross_section_ratio)
    reasons.append("Using baseline geometric tilt map fallback.")
    return fallback, reasons


def get_optimal_settings(
    analysis_data: GeometryAnalysis,
    resin_type: str,
    use_case: UseCase,
    printer: str = PRINTER_NAME,
    ambient_temp_c: float | None = None,
    film_releases: int = 0,
    catalog_db_path: str | Path | None = None,
) -> OptimalSettings:
    profile = find_resin_profile(
        resin_type=resin_type,
        printer=printer,
        db_path=catalog_db_path,
    )

    warnings: list[str] = []
    recommendations: list[str] = []

    if not profile:
        profile = {
            "name": "Fallback profile",
            "resin_type": resin_type,
            "layer_height_mm": 0.05,
            "exposure_s": 2.0,
            "bottom_exposure_s": 28.0,
            "tilt_speed_reference_mm_h": 90.0,
        }
        warnings.append("Resin profile not found in local DB; used fallback defaults.")

    intent_used = analysis_data.estimated_intent or use_case
    layer_height_mm = float(profile.get("layer_height_mm", 0.05))
    exposure_s = float(profile.get("exposure_s", 2.0))
    bottom_exposure_s = float(profile.get("bottom_exposure_s", 28.0))
    rest_time_before_print_s = 2.0
    rest_time_after_retract_s = 0.5
    transition_layers = 6
    scale_compensation_percent = 100.0
    heater_required = False

    if use_case == "miniature":
        layer_height_mm = 0.02
        exposure_s = max(1.8, exposure_s)
        recommendations.append("High Precision Mode: 18um XY + 0.02mm layers.")
    elif use_case == "collectible":
        recommendations.append("Surface finish mode with anti-aliasing priority.")
    elif use_case == "heavy_use":
        layer_height_mm = 0.05
        bottom_exposure_s *= 1.15
        transition_layers = 8
        recommendations.append("Structural bond mode: +15% bottom exposure, tough resin advised.")

    if use_case != "heavy_use":
        transition_layers = 5

    if layer_height_mm < 0.03:
        exposure_s = max(0.8, exposure_s - 0.2)
        recommendations.append("Sub-0.03mm layer detected: exposure reduced by 0.2s for pixel sharpness.")

    if ambient_temp_c is not None:
        exposure_s, heater_required, temp_notes = get_temp_offset(exposure_s, ambient_temp_c)
        warnings.extend(temp_notes)

    if film_releases >= 30000:
        rest_time_after_retract_s += 0.5
        recommendations.append("Film age >30,000 releases: rest time after retract increased by 0.5s.")
    if film_releases >= 60000:
        warnings.append("ACF film exceeds 60,000 releases: service recommended and tilt reduced.")
        recommendations.append("Replace or inspect ACF film soon.")

    detail_tier = _detail_tier(analysis_data.detail_density)

    if detail_tier in {"ultra_detail", "high_detail"}:
        model_exposure_s = max(0.7, exposure_s - 0.15)
        support_exposure_s = exposure_s + 0.25
        delicate_feature_exposure_s = max(0.65, exposure_s - 0.20)
    else:
        model_exposure_s = exposure_s
        support_exposure_s = exposure_s + 0.18
        delicate_feature_exposure_s = max(0.7, exposure_s - 0.10)

    profile_note = str(profile.get("notes", "")).lower()
    if "bloom" in profile_note:
        warnings.append("Profile indicates possible blooming at aggressive exposure settings.")
    if "delamination" in profile_note:
        warnings.append("Profile indicates potential delamination at very low exposure values.")

    tilt_speed, tilt_reasons = _reference_tilt_speed_mm_min(
        intent=intent_used,
        cross_section_ratio=analysis_data.max_cross_section_ratio,
        profile=profile,
    )
    recommendations.extend(tilt_reasons)
    if use_case == "collectible":
        tilt_speed = min(tilt_speed, 80.0)
    if film_releases >= 60000:
        tilt_speed = max(40.0, round(tilt_speed * 0.85, 2))

    resin_lower = resin_type.lower()
    if use_case == "heavy_use" and "anycubic" in resin_lower and "high speed" in resin_lower:
        scale_compensation_percent = 100.5
        recommendations.append("Applied 100.5% scale compensation for high-speed resin shrinkage control.")

    anti_aliasing = None
    grayscale_level = None
    xy_resolution_um = None

    if use_case == "miniature":
        xy_resolution_um = 18
    if use_case == "collectible":
        anti_aliasing = 4
        grayscale_level = 2

    return OptimalSettings(
        printer=printer,
        resin_type=resin_type,
        use_case=use_case,
        intent_used=intent_used,
        layer_height_mm=round(layer_height_mm, 4),
        exposure_s=round(exposure_s, 3),
        bottom_exposure_s=round(bottom_exposure_s, 3),
        tilt_speed_mm_min=tilt_speed,
        tilt_speed_mm_h=round(tilt_speed, 2),
        tilt_angle_deg=_estimate_tilt_angle_deg(
            analysis_data.max_cross_section_ratio,
            len(analysis_data.suction_cups),
        ),
        rest_time_before_print_s=round(rest_time_before_print_s, 3),
        rest_time_after_retract_s=round(rest_time_after_retract_s, 3),
        transition_layers=transition_layers,
        scale_compensation_percent=round(scale_compensation_percent, 3),
        heater_required=heater_required,
        anti_aliasing=anti_aliasing,
        grayscale_level=grayscale_level,
        xy_resolution_um=xy_resolution_um,
        detail_tier=detail_tier,
        multi_parameter=MultiParameterSettings(
            model_exposure_s=round(model_exposure_s, 3),
            support_exposure_s=round(support_exposure_s, 3),
            delicate_feature_exposure_s=round(delicate_feature_exposure_s, 3),
        ),
        warnings=warnings,
        recommendations=recommendations,
        source_profile=str(profile.get("name", "Unnamed profile")),
    )
