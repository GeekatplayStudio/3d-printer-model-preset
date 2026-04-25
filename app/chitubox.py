from __future__ import annotations

from pathlib import Path

from app.models import GeometryAnalysis, OptimalSettings


def _ensure_msla_settings(settings: OptimalSettings) -> None:
    if str(settings.process_technology).upper() in {"FDM", "FFF"}:
        raise ValueError(
            "Chitubox export currently supports only resin/MSLA profiles. Use /phase2/optimize for FDM JSON settings."
        )
    required_fields = [
        settings.exposure_s,
        settings.bottom_exposure_s,
        settings.tilt_speed_mm_min,
        settings.tilt_angle_deg,
        settings.multi_parameter,
    ]
    if any(field is None for field in required_fields):
        raise ValueError("Incomplete resin/MSLA settings were provided for Chitubox export.")


def build_sdcp_payload(
    settings: OptimalSettings,
    analysis: GeometryAnalysis | None = None,
) -> dict:
    _ensure_msla_settings(settings)
    payload = {
        "protocol": "SDCP",
        "printer": settings.printer,
        "action": "set_slicing_parameters",
        "parameters": {
            "layer_height_mm": settings.layer_height_mm,
            "exposure_s": settings.exposure_s,
            "bottom_exposure_s": settings.bottom_exposure_s,
            "tilt_speed_mm_min": settings.tilt_speed_mm_min,
            "tilt_speed_mm_h": settings.tilt_speed_mm_h,
            "tilt_angle_deg": settings.tilt_angle_deg,
            "rest_time_before_print_s": settings.rest_time_before_print_s,
            "rest_time_after_retract_s": settings.rest_time_after_retract_s,
            "transition_layers": settings.transition_layers,
            "scale_compensation_percent": settings.scale_compensation_percent,
            "anti_aliasing": settings.anti_aliasing,
            "grayscale_level": settings.grayscale_level,
            "xy_resolution_um": settings.xy_resolution_um,
            "multi_parameter": settings.multi_parameter.model_dump(),
        },
    }

    if analysis is not None:
        payload["analysis_summary"] = {
            "max_cross_section_ratio": analysis.max_cross_section_ratio,
            "suction_cup_count": len(analysis.suction_cups),
            "island_count": len(analysis.islands),
            "structural_risk_score": analysis.structural_risk_score,
        }

    return payload


def render_chitubox_cfg(settings: OptimalSettings) -> str:
    _ensure_msla_settings(settings)
    lines = [
        "[General]",
        f"Printer={settings.printer}",
        f"Resin={settings.resin_type}",
        "",
        "[Exposure]",
        f"LayerHeightMM={settings.layer_height_mm}",
        f"NormalExposureS={settings.exposure_s}",
        f"BottomExposureS={settings.bottom_exposure_s}",
        f"TransitionLayers={settings.transition_layers}",
        "",
        "[TiltRelease]",
        f"TiltSpeedMMMin={settings.tilt_speed_mm_min}",
        f"TiltSpeedMMH={settings.tilt_speed_mm_h or ''}",
        f"TiltAngleDeg={settings.tilt_angle_deg}",
        "",
        "[Timing]",
        f"WaitBeforePrintS={settings.rest_time_before_print_s}",
        f"WaitAfterRetractS={settings.rest_time_after_retract_s}",
        "",
        "[Scaling]",
        f"ModelScalePercent={settings.scale_compensation_percent}",
        "",
        "[Quality]",
        f"XYResolutionUM={settings.xy_resolution_um or ''}",
        f"AntiAliasing={settings.anti_aliasing or ''}",
        f"GrayscaleLevel={settings.grayscale_level or ''}",
        "",
        "[MultiParameterSlicing]",
        f"ModelExposureS={settings.multi_parameter.model_exposure_s}",
        f"SupportExposureS={settings.multi_parameter.support_exposure_s}",
        f"DelicateExposureS={settings.multi_parameter.delicate_feature_exposure_s}",
    ]
    return "\n".join(lines).strip() + "\n"


def write_chitubox_cfg(settings: OptimalSettings, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_chitubox_cfg(settings), encoding="utf-8")
    return path
