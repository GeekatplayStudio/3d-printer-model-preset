from __future__ import annotations

from pathlib import Path

from app.catalog_store import find_profile_by_names, list_printers, list_resins
from app.models import (
    FdmSettings,
    GeometryAnalysis,
    MultiParameterSettings,
    OptimalSettings,
    SettingReference,
    SettingsProvenance,
    UseCase,
)
from app.resin_db import find_resin_profile

PRINTER_NAME = "Elegoo Mars 5 Ultra"
_FDM_TECHNOLOGIES = {"FDM", "FFF"}


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


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _source_urls(metadata: object) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    urls = metadata.get("source_urls")
    if not isinstance(urls, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in urls:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _as_confidence(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return round(parsed, 3)


def _normalize_process_technology(value: object) -> str:
    normalized = str(value or "MSLA").strip().upper()
    if normalized == "FFF":
        return "FDM"
    return normalized or "MSLA"


def _is_fdm_process(value: object) -> bool:
    return _normalize_process_technology(value) in _FDM_TECHNOLOGIES


def _first_float(*values: object) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None


def _first_int(*values: object) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None


def _exact_catalog_match(records: list[dict[str, object]], name: str) -> dict[str, object] | None:
    key = name.strip().lower()
    for record in records:
        if str(record.get("name", "")).strip().lower() == key:
            return record
    return None


def _catalog_printer_record(
    printer: str,
    catalog_db_path: str | Path | None,
) -> dict[str, object] | None:
    return _exact_catalog_match(
        list_printers(db_path=catalog_db_path, query=printer, limit=50),
        printer,
    )


def _catalog_material_record(
    material_name: str,
    catalog_db_path: str | Path | None,
    *,
    material_type: str | None = None,
) -> dict[str, object] | None:
    exact = _exact_catalog_match(
        list_resins(
            db_path=catalog_db_path,
            query=material_name,
            material_type=material_type,
            limit=50,
        ),
        material_name,
    )
    if exact is not None or material_type is None:
        return exact
    return _exact_catalog_match(
        list_resins(db_path=catalog_db_path, query=material_name, limit=50),
        material_name,
    )


def _build_settings_provenance(
    *,
    profile: dict,
    profile_found: bool,
    printer: str,
    material_name: str,
    process_technology: str,
) -> SettingsProvenance:
    is_fdm = _is_fdm_process(process_technology) or str(profile.get("material_type", "")).lower() == "filament"
    material_label = "Filament" if is_fdm else "Resin"
    profile_fields = (
        [
            "layer_height_mm",
            "nozzle_temp_c",
            "bed_temp_c",
            "print_speed_mm_s",
            "retraction_distance_mm",
        ]
        if is_fdm
        else [
            "layer_height_mm",
            "exposure_s",
            "bottom_exposure_s",
            "transition_layers",
            "tilt_speed_mm_min",
        ]
    )
    printer_fields = ["xy_resolution_um", "nozzle_diameter_mm"] if is_fdm else ["xy_resolution_um", "tilt_angle_deg"]
    material_fields = (
        ["nozzle_temp_c", "bed_temp_c", "print_speed_mm_s"]
        if is_fdm
        else ["exposure_s", "bottom_exposure_s"]
    )

    if not profile_found:
        return SettingsProvenance(
            data_quality="fallback_defaults",
            real_data_backed=False,
            confidence_score=None,
            source_count=0,
            references=[],
            notes=[
                "No matching printer+material profile was found in the local catalog, so fallback defaults were used."
            ],
        )

    profile_name = str(profile.get("name") or profile.get("profile_name") or "Unnamed profile").strip()
    profile_meta = _as_dict(profile.get("metadata"))
    printer_meta = _as_dict(profile.get("printer_metadata"))
    resin_meta = _as_dict(profile.get("resin_metadata"))

    profile_urls = _source_urls(profile_meta)
    printer_urls = _source_urls(printer_meta)
    resin_urls = _source_urls(resin_meta)

    confidence = _as_confidence(
        profile_meta.get("sync_confidence_score", profile_meta.get("confidence_score"))
    )

    references: list[SettingReference] = []
    for url in profile_urls[:3]:
        references.append(
            SettingReference(
                title=f"Profile preset: {profile_name}",
                source_type=str(profile_meta.get("source_type") or "profile_source"),
                source_name=str(profile_meta.get("source_mode") or profile_name),
                source_url=url,
                retrieved_at=str(profile_meta.get("retrieved_at") or "") or None,
                confidence_score=confidence,
                applies_to=profile_fields,
            )
        )
    for url in printer_urls[:2]:
        references.append(
            SettingReference(
                title=f"Printer specs: {printer}",
                source_type=str(printer_meta.get("source_type") or "printer_source"),
                source_name=str(printer_meta.get("manufacturer") or printer),
                source_url=url,
                retrieved_at=str(printer_meta.get("retrieved_at") or "") or None,
                confidence_score=None,
                applies_to=printer_fields,
            )
        )
    for url in resin_urls[:2]:
        references.append(
            SettingReference(
                title=f"{material_label} data: {material_name}",
                source_type=str(resin_meta.get("source_type") or "resin_source"),
                source_name=str(resin_meta.get("manufacturer") or material_name),
                source_url=url,
                retrieved_at=str(resin_meta.get("retrieved_at") or "") or None,
                confidence_score=None,
                applies_to=material_fields,
            )
        )

    has_verified_sources = len(references) > 0
    notes: list[str] = []
    if has_verified_sources:
        if confidence is not None and confidence < 0.45:
            notes.append("Profile confidence is low; verify settings with a short validation print.")
        return SettingsProvenance(
            data_quality="verified_sources",
            real_data_backed=True,
            confidence_score=confidence,
            source_count=len(references),
            references=references,
            notes=notes,
        )

    notes.append("Profile exists in local catalog but has no source URLs; treat as unverified.")
    return SettingsProvenance(
        data_quality="catalog_unverified",
        real_data_backed=False,
        confidence_score=confidence,
        source_count=0,
        references=[],
        notes=notes,
    )


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


def _infer_filament_family(
    material_name: str,
    profile: dict | None,
    material_record: dict[str, object] | None,
) -> str:
    family = str(
        (material_record or {}).get("material_family")
        or (profile or {}).get("material_family")
        or ""
    ).strip()
    if family:
        return family.upper()

    haystack = " ".join(
        part
        for part in [
            material_name,
            str((material_record or {}).get("series") or "").strip(),
            str((profile or {}).get("series") or "").strip(),
        ]
        if part
    ).lower()
    for token, family_name in [
        ("petg", "PETG"),
        ("pet-g", "PETG"),
        ("pla", "PLA"),
        ("asa", "ASA"),
        ("abs", "ABS"),
        ("tpu", "TPU"),
        ("nylon", "NYLON"),
        ("pc", "PC"),
        ("hips", "HIPS"),
    ]:
        if token in haystack:
            return family_name
    return "GENERIC"


def _fdm_defaults_for_family(family: str) -> dict[str, object]:
    defaults: dict[str, dict[str, object]] = {
        "PLA": {
            "layer_height_mm": 0.2,
            "nozzle_temp_c": 210.0,
            "bed_temp_c": 60.0,
            "chamber_temp_c": None,
            "print_speed_mm_s": 60.0,
            "first_layer_speed_mm_s": 22.0,
            "travel_speed_mm_s": 180.0,
            "retraction_distance_mm": 0.8,
            "retraction_speed_mm_s": 35.0,
            "nozzle_diameter_mm": 0.4,
            "fan_speed_percent": 100,
            "infill_percent": 15.0,
            "wall_count": 2,
            "support_style": "tree",
        },
        "PETG": {
            "layer_height_mm": 0.2,
            "nozzle_temp_c": 240.0,
            "bed_temp_c": 80.0,
            "chamber_temp_c": None,
            "print_speed_mm_s": 45.0,
            "first_layer_speed_mm_s": 20.0,
            "travel_speed_mm_s": 160.0,
            "retraction_distance_mm": 0.8,
            "retraction_speed_mm_s": 30.0,
            "nozzle_diameter_mm": 0.4,
            "fan_speed_percent": 45,
            "infill_percent": 20.0,
            "wall_count": 3,
            "support_style": "tree",
        },
        "ABS": {
            "layer_height_mm": 0.2,
            "nozzle_temp_c": 250.0,
            "bed_temp_c": 100.0,
            "chamber_temp_c": 45.0,
            "print_speed_mm_s": 50.0,
            "first_layer_speed_mm_s": 18.0,
            "travel_speed_mm_s": 170.0,
            "retraction_distance_mm": 1.0,
            "retraction_speed_mm_s": 35.0,
            "nozzle_diameter_mm": 0.4,
            "fan_speed_percent": 20,
            "infill_percent": 25.0,
            "wall_count": 3,
            "support_style": "lines",
        },
        "ASA": {
            "layer_height_mm": 0.2,
            "nozzle_temp_c": 255.0,
            "bed_temp_c": 100.0,
            "chamber_temp_c": 45.0,
            "print_speed_mm_s": 48.0,
            "first_layer_speed_mm_s": 18.0,
            "travel_speed_mm_s": 165.0,
            "retraction_distance_mm": 1.0,
            "retraction_speed_mm_s": 35.0,
            "nozzle_diameter_mm": 0.4,
            "fan_speed_percent": 15,
            "infill_percent": 25.0,
            "wall_count": 3,
            "support_style": "lines",
        },
        "TPU": {
            "layer_height_mm": 0.2,
            "nozzle_temp_c": 225.0,
            "bed_temp_c": 50.0,
            "chamber_temp_c": None,
            "print_speed_mm_s": 28.0,
            "first_layer_speed_mm_s": 16.0,
            "travel_speed_mm_s": 120.0,
            "retraction_distance_mm": 0.5,
            "retraction_speed_mm_s": 18.0,
            "nozzle_diameter_mm": 0.4,
            "fan_speed_percent": 60,
            "infill_percent": 15.0,
            "wall_count": 2,
            "support_style": "tree",
        },
        "NYLON": {
            "layer_height_mm": 0.2,
            "nozzle_temp_c": 260.0,
            "bed_temp_c": 90.0,
            "chamber_temp_c": 45.0,
            "print_speed_mm_s": 40.0,
            "first_layer_speed_mm_s": 18.0,
            "travel_speed_mm_s": 150.0,
            "retraction_distance_mm": 1.0,
            "retraction_speed_mm_s": 25.0,
            "nozzle_diameter_mm": 0.4,
            "fan_speed_percent": 20,
            "infill_percent": 25.0,
            "wall_count": 3,
            "support_style": "lines",
        },
    }
    generic = {
        "layer_height_mm": 0.2,
        "nozzle_temp_c": 220.0,
        "bed_temp_c": 60.0,
        "chamber_temp_c": None,
        "print_speed_mm_s": 55.0,
        "first_layer_speed_mm_s": 22.0,
        "travel_speed_mm_s": 170.0,
        "retraction_distance_mm": 0.8,
        "retraction_speed_mm_s": 35.0,
        "nozzle_diameter_mm": 0.4,
        "fan_speed_percent": 60,
        "infill_percent": 15.0,
        "wall_count": 2,
        "support_style": "tree",
    }
    return {**generic, **defaults.get(family, {})}


def _fdm_support_risk_score(analysis_data: GeometryAnalysis) -> float:
    if analysis_data.fdm_support_risk_score is not None:
        return round(float(analysis_data.fdm_support_risk_score), 1)
    downskin_ratio = analysis_data.downskin_area_ratio or 0.0
    score = (
        downskin_ratio * 220.0
        + analysis_data.max_cross_section_ratio * 110.0
        + analysis_data.structural_risk_score * 0.35
        + min(len(analysis_data.islands) * 3.0, 15.0)
    )
    return round(max(0.0, min(100.0, score)), 1)


def _average_temperature_range(low: object, high: object) -> float | None:
    low_value = _first_float(low)
    high_value = _first_float(high)
    if low_value is None and high_value is None:
        return None
    if low_value is None:
        return high_value
    if high_value is None:
        return low_value
    return round((low_value + high_value) / 2.0, 1)


def _get_fdm_optimal_settings(
    *,
    analysis_data: GeometryAnalysis,
    material_name: str,
    use_case: UseCase,
    printer: str,
    ambient_temp_c: float | None,
    printer_record: dict[str, object] | None,
    material_record: dict[str, object] | None,
    profile: dict[str, object] | None,
) -> OptimalSettings:
    warnings: list[str] = []
    recommendations: list[str] = []
    family = _infer_filament_family(material_name, profile, material_record)
    defaults = _fdm_defaults_for_family(family)
    profile_found = bool(profile)

    if not profile_found:
        warnings.append("FDM profile not found in local DB; used family defaults.")

    intent_used = analysis_data.estimated_intent or use_case
    detail_tier = _detail_tier(analysis_data.detail_density)

    layer_height_mm = _first_float(
        (profile or {}).get("layer_height_mm"),
        defaults.get("layer_height_mm"),
    ) or 0.2
    nozzle_temp_c = _first_float(
        (profile or {}).get("nozzle_temp_c"),
        _average_temperature_range(
            (profile or {}).get("nozzle_temp_min_c") or (material_record or {}).get("nozzle_temp_min_c"),
            (profile or {}).get("nozzle_temp_max_c") or (material_record or {}).get("nozzle_temp_max_c"),
        ),
        defaults.get("nozzle_temp_c"),
    ) or 220.0
    bed_temp_c = _first_float(
        (profile or {}).get("bed_temp_c"),
        (profile or {}).get("material_bed_temp_c"),
        (material_record or {}).get("bed_temp_c"),
        defaults.get("bed_temp_c"),
    )
    chamber_temp_c = _first_float((profile or {}).get("chamber_temp_c"), defaults.get("chamber_temp_c"))
    print_speed_mm_s = _first_float((profile or {}).get("print_speed_mm_s"), defaults.get("print_speed_mm_s")) or 55.0
    first_layer_speed_mm_s = _first_float(
        (profile or {}).get("first_layer_speed_mm_s"),
        defaults.get("first_layer_speed_mm_s"),
    )
    travel_speed_mm_s = _first_float((profile or {}).get("travel_speed_mm_s"), defaults.get("travel_speed_mm_s")) or 170.0
    retraction_distance_mm = _first_float(
        (profile or {}).get("retraction_distance_mm"),
        defaults.get("retraction_distance_mm"),
    ) or 0.8
    retraction_speed_mm_s = _first_float(
        (profile or {}).get("retraction_speed_mm_s"),
        defaults.get("retraction_speed_mm_s"),
    ) or 35.0
    nozzle_diameter_mm = _first_float(
        (profile or {}).get("nozzle_diameter_mm"),
        defaults.get("nozzle_diameter_mm"),
    ) or 0.4
    fan_speed_percent = _first_int((profile or {}).get("fan_speed_percent"), defaults.get("fan_speed_percent"))
    infill_percent = _first_float((profile or {}).get("infill_percent"), defaults.get("infill_percent")) or 15.0
    wall_count = _first_int((profile or {}).get("wall_count"), defaults.get("wall_count")) or 2
    support_style = str((profile or {}).get("support_style") or defaults.get("support_style") or "tree")
    heater_required = False

    if intent_used == "miniature":
        layer_height_mm = min(layer_height_mm, 0.12)
        print_speed_mm_s *= 0.72
        wall_count = max(wall_count, 3)
        infill_percent = max(infill_percent, 18.0)
        recommendations.append("Miniature mode: finer layers, slower walls, and extra perimeter strength.")
    elif intent_used == "collectible":
        layer_height_mm = min(layer_height_mm, 0.16)
        print_speed_mm_s *= 0.82
        wall_count = max(wall_count, 3)
        recommendations.append("Collectible mode: slower exterior motion for cleaner visible surfaces.")
    elif intent_used == "heavy_use":
        layer_height_mm = max(layer_height_mm, 0.2)
        print_speed_mm_s = min(print_speed_mm_s, 50.0)
        if family not in {"PLA", "TPU"}:
            nozzle_temp_c += 5.0
        infill_percent = max(infill_percent, 30.0)
        wall_count = max(wall_count, 4)
        recommendations.append("Heavy-use mode: higher infill, more walls, and stronger thermal bonding.")

    if detail_tier in {"ultra_detail", "high_detail"}:
        layer_height_mm = min(layer_height_mm, 0.16 if detail_tier == "high_detail" else 0.12)
        print_speed_mm_s *= 0.9
    if detail_tier == "ultra_detail" and nozzle_diameter_mm > 0.4:
        warnings.append("Ultra-detail geometry may benefit from a 0.25-0.4mm nozzle for cleaner edge definition.")

    support_risk_score = _fdm_support_risk_score(analysis_data)
    downskin_area_ratio = analysis_data.downskin_area_ratio or 0.0
    if support_risk_score >= 45.0:
        print_speed_mm_s *= 0.85
        support_style = "lines" if intent_used == "heavy_use" else "tree"
        warnings.append("High FDM support risk detected from geometry; reduce wall speed and plan support placement.")
        recommendations.append("Enable supports for major down-facing regions before slicing.")
    elif support_risk_score >= 25.0:
        recommendations.append("Moderate support risk detected; inspect overhangs before final slicing.")
    if downskin_area_ratio >= 0.12:
        recommendations.append("Significant downward-facing surface area detected; tree or organic supports are recommended.")

    if analysis_data.structural_risk_score >= 60.0 and intent_used == "heavy_use":
        infill_percent = max(infill_percent, 35.0)
        wall_count = max(wall_count, 4)
        recommendations.append("High structural risk geometry: add infill and walls to reduce weak spans.")

    if ambient_temp_c is not None and ambient_temp_c < 18.0:
        nozzle_temp_c += 5.0
        if bed_temp_c is not None:
            bed_temp_c += 5.0
        warnings.append("Low ambient temperature detected: nozzle and bed temperatures increased by 5C.")

    if family in {"ABS", "ASA", "NYLON", "PC"}:
        chamber_temp_c = _first_float(chamber_temp_c, defaults.get("chamber_temp_c"))
        if ambient_temp_c is not None and ambient_temp_c < 22.0:
            heater_required = True
            warnings.append("Engineering filament in a cool room benefits from an enclosure or heated chamber.")
            recommendations.append("Use an enclosure and keep drafts away from the print path.")

    if family == "TPU":
        retraction_distance_mm = min(retraction_distance_mm, 0.8)
        retraction_speed_mm_s = min(retraction_speed_mm_s, 22.0)
        print_speed_mm_s = min(print_speed_mm_s, 30.0)
        recommendations.append("Flexible filament guardrails applied: short retraction and reduced print speed.")

    if first_layer_speed_mm_s is None:
        first_layer_speed_mm_s = max(15.0, print_speed_mm_s * 0.4)

    profile_note = str((profile or {}).get("notes", "")).lower()
    if "warp" in profile_note:
        warnings.append("Profile notes mention warping sensitivity; use adhesion aids or an enclosure.")
    if "string" in profile_note:
        warnings.append("Profile notes mention stringing sensitivity; keep retraction and fan tuning conservative.")

    provenance = _build_settings_provenance(
        profile=profile or {},
        profile_found=profile_found,
        printer=printer,
        material_name=material_name,
        process_technology="FDM",
    )
    if provenance.data_quality == "catalog_unverified":
        warnings.append("Catalog profile has no source references; validate temperatures with a short calibration print.")
    if provenance.confidence_score is not None and provenance.confidence_score < 0.45:
        warnings.append("Source confidence is low; calibrate flow and temperature before long prints.")
    if provenance.source_count > 0:
        recommendations.append(
            f"Provenance attached: {provenance.source_count} source reference(s) from catalog metadata."
        )

    return OptimalSettings(
        printer=printer,
        resin_type=material_name,
        material_name=material_name,
        process_technology="FDM",
        use_case=use_case,
        intent_used=intent_used,
        layer_height_mm=round(layer_height_mm, 4),
        exposure_s=None,
        bottom_exposure_s=None,
        tilt_speed_mm_min=None,
        tilt_speed_mm_h=None,
        tilt_angle_deg=None,
        rest_time_before_print_s=None,
        rest_time_after_retract_s=None,
        transition_layers=None,
        scale_compensation_percent=100.0,
        heater_required=heater_required,
        anti_aliasing=None,
        grayscale_level=None,
        xy_resolution_um=_first_int((printer_record or {}).get("xy_resolution_um")),
        detail_tier=detail_tier,
        multi_parameter=None,
        fdm=FdmSettings(
            nozzle_temp_c=round(nozzle_temp_c, 1),
            bed_temp_c=round(bed_temp_c, 1) if bed_temp_c is not None else None,
            chamber_temp_c=round(chamber_temp_c, 1) if chamber_temp_c is not None else None,
            print_speed_mm_s=round(print_speed_mm_s, 2),
            first_layer_speed_mm_s=round(first_layer_speed_mm_s, 2),
            travel_speed_mm_s=round(travel_speed_mm_s, 2),
            retraction_distance_mm=round(retraction_distance_mm, 2),
            retraction_speed_mm_s=round(retraction_speed_mm_s, 2),
            nozzle_diameter_mm=round(nozzle_diameter_mm, 2),
            fan_speed_percent=fan_speed_percent,
            infill_percent=round(infill_percent, 2),
            wall_count=wall_count,
            support_style=support_style,
        ),
        warnings=warnings,
        recommendations=recommendations,
        source_profile=str((profile or {}).get("name") or (profile or {}).get("profile_name") or f"{family} family defaults"),
        provenance=provenance,
    )


def get_optimal_settings(
    analysis_data: GeometryAnalysis,
    resin_type: str,
    use_case: UseCase,
    printer: str = PRINTER_NAME,
    ambient_temp_c: float | None = None,
    film_releases: int = 0,
    catalog_db_path: str | Path | None = None,
) -> OptimalSettings:
    printer_record = _catalog_printer_record(printer, catalog_db_path)
    catalog_profile = find_profile_by_names(
        printer_name=printer,
        resin_name=resin_type,
        db_path=catalog_db_path,
    )
    process_technology = _normalize_process_technology(
        (printer_record or {}).get("technology") or (catalog_profile or {}).get("process_technology")
    )
    material_type = str((catalog_profile or {}).get("material_type") or "").lower()
    if _is_fdm_process(process_technology) or material_type == "filament":
        return _get_fdm_optimal_settings(
            analysis_data=analysis_data,
            material_name=resin_type,
            use_case=use_case,
            printer=printer,
            ambient_temp_c=ambient_temp_c,
            printer_record=printer_record,
            material_record=_catalog_material_record(resin_type, catalog_db_path, material_type="filament"),
            profile=catalog_profile,
        )

    profile = find_resin_profile(
        resin_type=resin_type,
        printer=printer,
        db_path=catalog_db_path,
    )
    profile_found = bool(profile)

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
            "metadata": {},
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

    provenance = _build_settings_provenance(
        profile=profile,
        profile_found=profile_found,
        printer=printer,
        material_name=resin_type,
        process_technology=process_technology,
    )
    if provenance.data_quality == "catalog_unverified":
        warnings.append("Catalog profile has no source references; verify settings before production prints.")
    if provenance.confidence_score is not None and provenance.confidence_score < 0.45:
        warnings.append("Source confidence is low; run a small validation print before full production.")
    if provenance.source_count > 0:
        recommendations.append(
            f"Provenance attached: {provenance.source_count} source reference(s) from catalog metadata."
        )

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
        material_name=resin_type,
        process_technology=process_technology,
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
        provenance=provenance,
    )
