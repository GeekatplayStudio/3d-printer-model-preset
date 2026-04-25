from __future__ import annotations

import json
from ipaddress import ip_address
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from app.catalog_store import import_catalog

_VALID_REPO_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")
_VALID_REF = re.compile(r"^[A-Za-z0-9._/-]+$")
_CURATION_NOTES_LIMIT = 120
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def apply_technical_sync(
    payload: dict[str, Any],
    *,
    source: str = "manual",
    db_path: str | Path | None = None,
    replace_existing: bool | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    normalized = normalize_technical_sync_payload(
        payload=payload,
        replace_existing=replace_existing,
    )
    curated_payload, curation = curate_technical_sync_payload(
        normalized,
        source=source,
        source_metadata=source_metadata,
    )
    result = import_catalog(curated_payload, db_path=db_path)
    result["source"] = source
    result["curation"] = curation
    result["notes"] = curation.get("notes", [])
    return result


def fetch_technical_sync_from_github(
    *,
    owner: str,
    repo: str,
    path: str,
    ref: str = "main",
    timeout_s: float = 20.0,
) -> tuple[dict[str, Any], str]:
    raw_url = build_github_raw_url(owner=owner, repo=repo, path=path, ref=ref)
    request = Request(raw_url, headers={"User-Agent": "ResinLogic-AI/0.1"})
    with urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    return parse_technical_sync_json(body), raw_url


def fetch_technical_sync_from_url(
    *,
    url: str,
    timeout_s: float = 20.0,
) -> tuple[dict[str, Any], str]:
    normalized_url = _normalize_remote_json_url(url)
    request = Request(
        normalized_url,
        headers={
            "User-Agent": "ResinLogic-AI/0.1",
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    with urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    return parse_technical_sync_json(body), normalized_url


def build_github_raw_url(*, owner: str, repo: str, path: str, ref: str = "main") -> str:
    normalized_owner = owner.strip()
    normalized_repo = repo.strip()
    normalized_ref = ref.strip()
    normalized_path = _normalize_repo_path(path)

    if not _VALID_REPO_TOKEN.fullmatch(normalized_owner):
        raise ValueError("Invalid GitHub owner.")
    if not _VALID_REPO_TOKEN.fullmatch(normalized_repo):
        raise ValueError("Invalid GitHub repo.")
    if not _VALID_REF.fullmatch(normalized_ref):
        raise ValueError("Invalid GitHub ref.")

    encoded_segments = [quote(segment, safe="-._~") for segment in normalized_path.split("/")]
    encoded_path = "/".join(encoded_segments)
    return f"https://raw.githubusercontent.com/{normalized_owner}/{normalized_repo}/{normalized_ref}/{encoded_path}"


def _normalize_remote_json_url(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        raise ValueError("Remote JSON URL is required.")

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Remote JSON URL must use http or https.")
    if parsed.username or parsed.password:
        raise ValueError("Remote JSON URL must not include embedded credentials.")

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("Remote JSON URL must include a hostname.")
    if hostname in _LOCAL_HOSTS or hostname.endswith(".local"):
        raise ValueError("Remote JSON URL must not point to localhost.")

    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        parsed_ip = None
    if parsed_ip and (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_multicast
        or parsed_ip.is_reserved
        or parsed_ip.is_unspecified
    ):
        raise ValueError("Remote JSON URL must not point to a private or local IP address.")

    return parsed._replace(fragment="").geturl()


def normalize_technical_sync_payload(
    *,
    payload: dict[str, Any],
    replace_existing: bool | None = None,
) -> dict[str, Any]:
    normalized_replace = bool(payload.get("replace_existing") if replace_existing is None else replace_existing)

    profiles = payload.get("profiles")
    native_profiles = isinstance(profiles, list) and any(
        isinstance(item, dict) and any(key in item for key in ("printer_name", "resin_name", "printer_id", "resin_id"))
        for item in profiles
    )
    if "printers" in payload or "resins" in payload or native_profiles:
        return {
            "replace_existing": normalized_replace,
            "printers": payload.get("printers", []) or [],
            "resins": payload.get("resins", []) or [],
            "profiles": payload.get("profiles", []) or [],
        }

    if isinstance(profiles, list):
        converted = _legacy_profiles_to_sync_payload(profiles)
        converted["replace_existing"] = normalized_replace
        return converted

    raise ValueError(
        "Unsupported sync payload format. Expected {printers,resins,profiles} or legacy {'profiles':[...]}."
    )


def curate_technical_sync_payload(
    payload: dict[str, Any],
    *,
    source: str = "manual",
    source_metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    replace_existing = bool(payload.get("replace_existing", False))
    raw_printers = payload.get("printers", [])
    raw_resins = payload.get("resins", [])
    raw_profiles = payload.get("profiles", [])

    printers_in = raw_printers if isinstance(raw_printers, list) else []
    resins_in = raw_resins if isinstance(raw_resins, list) else []
    profiles_in = raw_profiles if isinstance(raw_profiles, list) else []

    source_reliability = _estimate_source_reliability(
        source=source,
        source_metadata=source_metadata,
    )
    report: dict[str, Any] = {
        "source": source,
        "source_reliability": source_reliability,
        "input_counts": {
            "printers": len(printers_in),
            "resins": len(resins_in),
            "profiles": len(profiles_in),
        },
        "kept_counts": {"printers": 0, "resins": 0, "profiles": 0},
        "dropped_counts": {"printers": 0, "resins": 0, "profiles": 0},
        "average_profile_quality": None,
        "average_profile_confidence": None,
        "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
        "notes": [],
    }

    curated_printers = _curate_printers(printers_in, report)
    curated_resins = _curate_resins(resins_in, report)
    curated_profiles, quality_scores, confidence_scores = _curate_profiles(
        profiles_in,
        report,
        source_reliability=source_reliability,
    )

    if quality_scores:
        report["average_profile_quality"] = round(sum(quality_scores) / len(quality_scores), 3)
    if confidence_scores:
        report["average_profile_confidence"] = round(sum(confidence_scores) / len(confidence_scores), 3)
        report["confidence_distribution"] = {
            "high": sum(score >= 0.75 for score in confidence_scores),
            "medium": sum(0.45 <= score < 0.75 for score in confidence_scores),
            "low": sum(score < 0.45 for score in confidence_scores),
        }
    report["kept_counts"] = {
        "printers": len(curated_printers),
        "resins": len(curated_resins),
        "profiles": len(curated_profiles),
    }
    report["dropped_counts"] = {
        "printers": len(printers_in) - len(curated_printers),
        "resins": len(resins_in) - len(curated_resins),
        "profiles": len(profiles_in) - len(curated_profiles),
    }

    curated_payload = {
        "replace_existing": replace_existing,
        "printers": curated_printers,
        "resins": curated_resins,
        "profiles": curated_profiles,
    }
    return curated_payload, report


def parse_technical_sync_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON payload for technical sync.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Sync payload must be a JSON object.")
    return payload


def _curate_printers(rows: list[Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            _add_curation_note(report, f"Printer row {idx} dropped: expected JSON object.")
            continue

        name = _clean_text(row.get("name"))
        if not name:
            _add_curation_note(report, f"Printer row {idx} dropped: missing name.")
            continue

        brand = _clean_text(row.get("brand"))
        model = _clean_text(row.get("model"))
        if not brand or not model:
            inferred_brand, inferred_model = _infer_brand_model(name)
            brand = brand or inferred_brand
            model = model or inferred_model

        printer = {
            "name": name,
            "brand": brand,
            "model": model,
            "technology": _clean_text(row.get("technology")),
            "xy_resolution_um": _clamp_int(
                _as_int(row.get("xy_resolution_um")),
                minimum=5,
                maximum=200,
                field_name=f"printer '{name}' xy_resolution_um",
                report=report,
            ),
            "build_volume_x_mm": _clamp_float(
                _as_float(row.get("build_volume_x_mm")),
                minimum=1.0,
                maximum=2000.0,
                field_name=f"printer '{name}' build_volume_x_mm",
                report=report,
            ),
            "build_volume_y_mm": _clamp_float(
                _as_float(row.get("build_volume_y_mm")),
                minimum=1.0,
                maximum=2000.0,
                field_name=f"printer '{name}' build_volume_y_mm",
                report=report,
            ),
            "build_volume_z_mm": _clamp_float(
                _as_float(row.get("build_volume_z_mm")),
                minimum=1.0,
                maximum=3000.0,
                field_name=f"printer '{name}' build_volume_z_mm",
                report=report,
            ),
            "tilt_release_supported": _as_optional_bool(row.get("tilt_release_supported")),
            "wifi_supported": _as_optional_bool(row.get("wifi_supported")),
            "notes": _clean_text(row.get("notes")),
            "metadata": _as_dict(row.get("metadata")),
        }

        key = name.lower()
        if key in by_name:
            _add_curation_note(report, f"Duplicate printer '{name}' found; keeping latest row.")
        by_name[key] = printer
    return list(by_name.values())


def _curate_resins(rows: list[Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            _add_curation_note(report, f"Resin row {idx} dropped: expected JSON object.")
            continue

        name = _clean_text(row.get("name"))
        if not name:
            _add_curation_note(report, f"Resin row {idx} dropped: missing name.")
            continue

        brand = _clean_text(row.get("brand")) or _infer_brand_model(name)[0]
        material_type = (_clean_text(row.get("material_type")) or "resin").lower()
        resin = {
            "name": name,
            "material_type": material_type,
            "material_family": _clean_text(row.get("material_family")),
            "brand": brand,
            "series": _clean_text(row.get("series")),
            "technical_goal": _clean_text(row.get("technical_goal")),
            "viscosity_cp": _clamp_float(
                _as_float(row.get("viscosity_cp")),
                minimum=1.0,
                maximum=10000.0,
                field_name=f"resin '{name}' viscosity_cp",
                report=report,
            ),
            "shore_hardness": _clean_text(row.get("shore_hardness")),
            "shrinkage_percent": _clamp_float(
                _as_float(row.get("shrinkage_percent")),
                minimum=0.0,
                maximum=20.0,
                field_name=f"resin '{name}' shrinkage_percent",
                report=report,
            ),
            "density_g_cm3": _clamp_float(
                _as_float(row.get("density_g_cm3")),
                minimum=0.1,
                maximum=20.0,
                field_name=f"resin '{name}' density_g_cm3",
                report=report,
            ),
            "filament_diameter_mm": _clamp_float(
                _as_float(row.get("filament_diameter_mm")),
                minimum=0.5,
                maximum=5.0,
                field_name=f"resin '{name}' filament_diameter_mm",
                report=report,
            ),
            "nozzle_temp_min_c": _clamp_float(
                _as_float(row.get("nozzle_temp_min_c")),
                minimum=50.0,
                maximum=450.0,
                field_name=f"resin '{name}' nozzle_temp_min_c",
                report=report,
            ),
            "nozzle_temp_max_c": _clamp_float(
                _as_float(row.get("nozzle_temp_max_c")),
                minimum=50.0,
                maximum=450.0,
                field_name=f"resin '{name}' nozzle_temp_max_c",
                report=report,
            ),
            "bed_temp_c": _clamp_float(
                _as_float(row.get("bed_temp_c")),
                minimum=0.0,
                maximum=200.0,
                field_name=f"resin '{name}' bed_temp_c",
                report=report,
            ),
            "notes": _clean_text(row.get("notes")),
            "metadata": _as_dict(row.get("metadata")),
        }

        key = name.lower()
        if key in by_name:
            _add_curation_note(report, f"Duplicate resin '{name}' found; keeping latest row.")
        by_name[key] = resin
    return list(by_name.values())


def _curate_profiles(
    rows: list[Any],
    report: dict[str, Any],
    *,
    source_reliability: float,
) -> tuple[list[dict[str, Any]], list[float], list[float]]:
    by_key: dict[tuple[Any, Any, str, float | None], tuple[dict[str, Any], float, float, int]] = {}
    quality_scores: list[float] = []
    confidence_scores: list[float] = []

    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            _add_curation_note(report, f"Profile row {idx} dropped: expected JSON object.")
            continue

        printer_id = _as_int(row.get("printer_id"))
        resin_id = _as_int(row.get("resin_id"))
        printer_name = _clean_text(row.get("printer_name"))
        resin_name = _clean_text(row.get("resin_name"))
        profile_name = _clean_text(row.get("profile_name"))

        if printer_id is None and not printer_name:
            _add_curation_note(report, f"Profile row {idx} dropped: missing printer_id/printer_name.")
            continue
        if resin_id is None and not resin_name:
            _add_curation_note(report, f"Profile row {idx} dropped: missing resin_id/resin_name.")
            continue
        if not profile_name:
            _add_curation_note(report, f"Profile row {idx} dropped: missing profile_name.")
            continue

        layer_height = _clamp_float(
            _as_float(row.get("layer_height_mm")),
            minimum=0.005,
            maximum=0.2,
            field_name=f"profile '{profile_name}' layer_height_mm",
            report=report,
        )
        exposure = _clamp_float(
            _as_float(row.get("exposure_s")),
            minimum=0.2,
            maximum=30.0,
            field_name=f"profile '{profile_name}' exposure_s",
            report=report,
        )
        bottom_exposure = _clamp_float(
            _as_float(row.get("bottom_exposure_s")),
            minimum=1.0,
            maximum=300.0,
            field_name=f"profile '{profile_name}' bottom_exposure_s",
            report=report,
        )
        transition_layers = _clamp_int(
            _as_int(row.get("transition_layers")),
            minimum=0,
            maximum=100,
            field_name=f"profile '{profile_name}' transition_layers",
            report=report,
        )
        tilt_speed = _clamp_float(
            _as_float(row.get("tilt_speed_reference_mm_h")),
            minimum=5.0,
            maximum=500.0,
            field_name=f"profile '{profile_name}' tilt_speed_reference_mm_h",
            report=report,
        )
        rest_before = _clamp_float(
            _as_float(row.get("rest_time_before_print_s")),
            minimum=0.0,
            maximum=60.0,
            field_name=f"profile '{profile_name}' rest_time_before_print_s",
            report=report,
        )
        rest_after = _clamp_float(
            _as_float(row.get("rest_time_after_retract_s")),
            minimum=0.0,
            maximum=60.0,
            field_name=f"profile '{profile_name}' rest_time_after_retract_s",
            report=report,
        )
        nozzle_temp = _clamp_float(
            _as_float(row.get("nozzle_temp_c")),
            minimum=50.0,
            maximum=450.0,
            field_name=f"profile '{profile_name}' nozzle_temp_c",
            report=report,
        )
        bed_temp = _clamp_float(
            _as_float(row.get("bed_temp_c")),
            minimum=0.0,
            maximum=200.0,
            field_name=f"profile '{profile_name}' bed_temp_c",
            report=report,
        )
        chamber_temp = _clamp_float(
            _as_float(row.get("chamber_temp_c")),
            minimum=0.0,
            maximum=120.0,
            field_name=f"profile '{profile_name}' chamber_temp_c",
            report=report,
        )
        print_speed = _clamp_float(
            _as_float(row.get("print_speed_mm_s")),
            minimum=1.0,
            maximum=1000.0,
            field_name=f"profile '{profile_name}' print_speed_mm_s",
            report=report,
        )
        first_layer_speed = _clamp_float(
            _as_float(row.get("first_layer_speed_mm_s")),
            minimum=1.0,
            maximum=1000.0,
            field_name=f"profile '{profile_name}' first_layer_speed_mm_s",
            report=report,
        )
        travel_speed = _clamp_float(
            _as_float(row.get("travel_speed_mm_s")),
            minimum=1.0,
            maximum=1000.0,
            field_name=f"profile '{profile_name}' travel_speed_mm_s",
            report=report,
        )
        retraction_distance = _clamp_float(
            _as_float(row.get("retraction_distance_mm")),
            minimum=0.0,
            maximum=20.0,
            field_name=f"profile '{profile_name}' retraction_distance_mm",
            report=report,
        )
        retraction_speed = _clamp_float(
            _as_float(row.get("retraction_speed_mm_s")),
            minimum=0.0,
            maximum=200.0,
            field_name=f"profile '{profile_name}' retraction_speed_mm_s",
            report=report,
        )
        nozzle_diameter = _clamp_float(
            _as_float(row.get("nozzle_diameter_mm")),
            minimum=0.1,
            maximum=5.0,
            field_name=f"profile '{profile_name}' nozzle_diameter_mm",
            report=report,
        )
        fan_speed = _clamp_int(
            _as_int(row.get("fan_speed_percent")),
            minimum=0,
            maximum=100,
            field_name=f"profile '{profile_name}' fan_speed_percent",
            report=report,
        )
        infill = _clamp_float(
            _as_float(row.get("infill_percent")),
            minimum=0.0,
            maximum=100.0,
            field_name=f"profile '{profile_name}' infill_percent",
            report=report,
        )
        wall_count = _clamp_int(
            _as_int(row.get("wall_count")),
            minimum=0,
            maximum=50,
            field_name=f"profile '{profile_name}' wall_count",
            report=report,
        )
        support_style = _clean_text(row.get("support_style"))

        msla_core_fields = sum(value is not None for value in (layer_height, exposure, bottom_exposure, tilt_speed))
        fdm_core_fields = sum(
            value is not None
            for value in (
                layer_height,
                nozzle_temp,
                bed_temp,
                print_speed,
                retraction_distance,
                retraction_speed,
                nozzle_diameter,
            )
        )
        uses_fdm_fields = any(
            value is not None
            for value in (
                nozzle_temp,
                bed_temp,
                chamber_temp,
                print_speed,
                first_layer_speed,
                travel_speed,
                retraction_distance,
                retraction_speed,
                nozzle_diameter,
                fan_speed,
                infill,
                wall_count,
                support_style,
            )
        )
        if msla_core_fields == 0 and fdm_core_fields == 0:
            _add_curation_note(
                report,
                f"Profile row {idx} ('{profile_name}') dropped: no core setting values were provided.",
            )
            continue

        profile_process = "fdm" if uses_fdm_fields and fdm_core_fields >= msla_core_fields else "msla"
        populated_core_fields = fdm_core_fields if profile_process == "fdm" else msla_core_fields
        expected_core_fields = 7 if profile_process == "fdm" else 4

        quality = _profile_quality_score(
            populated_core_fields=populated_core_fields,
            expected_core_fields=expected_core_fields,
            is_default=_as_bool(row.get("is_default"), default=False),
            has_rest_timing=(rest_before is not None and rest_after is not None),
        )
        recency_weight = _recency_weight_for_profile(row)
        confidence = round(quality * source_reliability * recency_weight, 3)

        profile = {
            "printer_id": printer_id,
            "resin_id": resin_id,
            "printer_name": printer_name,
            "resin_name": resin_name,
            "profile_name": profile_name,
            "layer_height_mm": layer_height,
            "exposure_s": exposure,
            "bottom_exposure_s": bottom_exposure,
            "transition_layers": transition_layers,
            "tilt_speed_reference_mm_h": tilt_speed,
            "rest_time_before_print_s": rest_before,
            "rest_time_after_retract_s": rest_after,
            "nozzle_temp_c": nozzle_temp,
            "bed_temp_c": bed_temp,
            "chamber_temp_c": chamber_temp,
            "print_speed_mm_s": print_speed,
            "first_layer_speed_mm_s": first_layer_speed,
            "travel_speed_mm_s": travel_speed,
            "retraction_distance_mm": retraction_distance,
            "retraction_speed_mm_s": retraction_speed,
            "nozzle_diameter_mm": nozzle_diameter,
            "fan_speed_percent": fan_speed,
            "infill_percent": infill,
            "wall_count": wall_count,
            "support_style": support_style,
            "is_default": _as_bool(row.get("is_default"), default=False),
            "is_active": _as_bool(row.get("is_active"), default=True),
            "notes": _clean_text(row.get("notes")),
            "metadata": {
                **_as_dict(row.get("metadata")),
                "profile_process": profile_process,
                "sync_quality_score": quality,
                "sync_confidence_score": confidence,
                "source_reliability": source_reliability,
                "recency_weight": recency_weight,
            },
        }

        printer_ref: Any = printer_id if printer_id is not None else f"n:{str(printer_name).lower()}"
        resin_ref: Any = resin_id if resin_id is not None else f"n:{str(resin_name).lower()}"
        key = (printer_ref, resin_ref, profile_name.lower(), layer_height)
        if key in by_key:
            _, old_quality, old_confidence, old_idx = by_key[key]
            replace = confidence > old_confidence or (confidence == old_confidence and idx > old_idx)
            if replace:
                _add_curation_note(
                    report,
                    f"Duplicate profile '{profile_name}' found; replaced prior row using higher confidence score.",
                )
                by_key[key] = (profile, quality, confidence, idx)
            else:
                _add_curation_note(
                    report,
                    f"Duplicate profile '{profile_name}' found; kept prior row with stronger confidence score.",
                )
        else:
            by_key[key] = (profile, quality, confidence, idx)

    profiles: list[dict[str, Any]] = []
    for profile, quality, confidence, _ in by_key.values():
        profiles.append(profile)
        quality_scores.append(quality)
        confidence_scores.append(confidence)
    return profiles, quality_scores, confidence_scores


def _legacy_profiles_to_sync_payload(profiles: list[Any]) -> dict[str, Any]:
    printers_by_name: dict[str, dict[str, Any]] = {}
    resins_by_name: dict[str, dict[str, Any]] = {}
    normalized_profiles: list[dict[str, Any]] = []

    for row in profiles:
        if not isinstance(row, dict):
            continue
        printer_name = str(row.get("printer", "")).strip()
        resin_name = str(row.get("resin_type", "")).strip()
        if not printer_name or not resin_name:
            continue

        if printer_name not in printers_by_name:
            brand, model = _infer_brand_model(printer_name)
            printers_by_name[printer_name] = {
                "name": printer_name,
                "brand": row.get("printer_brand") or brand,
                "model": row.get("printer_model") or model,
                "technology": "MSLA",
                "xy_resolution_um": _as_int(row.get("xy_resolution_um")),
                "tilt_release_supported": True,
                "wifi_supported": True,
                "notes": row.get("printer_notes"),
                "metadata": {},
            }

        if resin_name not in resins_by_name:
            resin_brand, _ = _infer_brand_model(resin_name)
            resins_by_name[resin_name] = {
                "name": resin_name,
                "brand": row.get("brand") or resin_brand,
                "series": row.get("series"),
                "technical_goal": row.get("technical_goal"),
                "viscosity_cp": _as_float(row.get("viscosity_cp")),
                "shore_hardness": row.get("shore_hardness"),
                "shrinkage_percent": _as_float(row.get("shrinkage_percent")),
                "notes": row.get("notes"),
                "metadata": {
                    "exposure_range_s": row.get("exposure_range_s"),
                },
            }

        normalized_profiles.append(
            {
                "printer_name": printer_name,
                "resin_name": resin_name,
                "profile_name": str(row.get("name") or f"{resin_name} Profile"),
                "layer_height_mm": _as_float(row.get("layer_height_mm")),
                "exposure_s": _as_float(row.get("exposure_s")),
                "bottom_exposure_s": _as_float(row.get("bottom_exposure_s")),
                "transition_layers": _as_int(row.get("transition_layers")),
                "tilt_speed_reference_mm_h": _as_float(row.get("tilt_speed_reference_mm_h")),
                "rest_time_before_print_s": _as_float(row.get("rest_time_before_print_s")) or 2.0,
                "rest_time_after_retract_s": _as_float(row.get("rest_time_after_retract_s")) or 0.5,
                "is_default": _as_bool(row.get("is_default"), default=False),
                "is_active": True,
                "notes": row.get("notes"),
                "metadata": {"legacy_profile": row},
            }
        )

    return {
        "replace_existing": False,
        "printers": list(printers_by_name.values()),
        "resins": list(resins_by_name.values()),
        "profiles": normalized_profiles,
    }


def _normalize_repo_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/").lstrip("/")
    if not normalized:
        raise ValueError("GitHub path is required.")
    segments = [segment for segment in normalized.split("/") if segment not in {"", "."}]
    if any(segment == ".." for segment in segments):
        raise ValueError("GitHub path cannot contain '..'.")
    return "/".join(segments)


def _infer_brand_model(name: str) -> tuple[str | None, str | None]:
    tokens = name.split()
    if not tokens:
        return None, None
    if len(tokens) == 1:
        return tokens[0], None
    return tokens[0], " ".join(tokens[1:])


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _clamp_float(
    value: float | None,
    *,
    minimum: float,
    maximum: float,
    field_name: str,
    report: dict[str, Any],
) -> float | None:
    if value is None:
        return None
    if value < minimum:
        _add_curation_note(report, f"{field_name} clamped from {value} to {minimum}.")
        return float(minimum)
    if value > maximum:
        _add_curation_note(report, f"{field_name} clamped from {value} to {maximum}.")
        return float(maximum)
    return float(value)


def _clamp_int(
    value: int | None,
    *,
    minimum: int,
    maximum: int,
    field_name: str,
    report: dict[str, Any],
) -> int | None:
    if value is None:
        return None
    if value < minimum:
        _add_curation_note(report, f"{field_name} clamped from {value} to {minimum}.")
        return int(minimum)
    if value > maximum:
        _add_curation_note(report, f"{field_name} clamped from {value} to {maximum}.")
        return int(maximum)
    return int(value)


def _profile_quality_score(
    *,
    populated_core_fields: int,
    expected_core_fields: int,
    is_default: bool,
    has_rest_timing: bool,
) -> float:
    core = min(1.0, max(0.0, populated_core_fields / max(1.0, float(expected_core_fields))))
    bonus = 0.0
    if is_default:
        bonus += 0.05
    if has_rest_timing:
        bonus += 0.05
    return round(min(1.0, core + bonus), 3)


def _recency_weight_for_profile(row: dict[str, Any]) -> float:
    parsed = _extract_profile_date(row)
    if parsed is None:
        return 0.8

    today = datetime.now(timezone.utc).date()
    age_days = max(0, (today - parsed).days)
    if age_days <= 180:
        return 1.0
    if age_days <= 730:
        return 0.85
    return 0.7


def _extract_profile_date(row: dict[str, Any]) -> date | None:
    metadata = row.get("metadata")
    metadata_obj = metadata if isinstance(metadata, dict) else {}
    for key in (
        "updated_at",
        "created_at",
        "last_verified_at",
        "tested_at",
        "date",
        "timestamp",
    ):
        raw = row.get(key)
        parsed = _parse_date_value(raw)
        if parsed is not None:
            return parsed
        parsed_meta = _parse_date_value(metadata_obj.get(key))
        if parsed_meta is not None:
            return parsed_meta
    return None


def _parse_date_value(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _estimate_source_reliability(
    *,
    source: str,
    source_metadata: dict[str, Any] | None,
) -> float:
    source_key = source.strip().lower()
    reliability = 0.65

    if "github" in source_key:
        reliability = 0.78
    elif "scrape" in source_key:
        reliability = 0.74
    elif source_key in {"manual", "file_upload"} or source_key.startswith("unit_test"):
        reliability = 0.72
    elif "community" in source_key:
        reliability = 0.58
    elif "youtube" in source_key:
        reliability = 0.5

    meta = source_metadata if isinstance(source_metadata, dict) else {}
    if meta:
        kind = str(meta.get("kind", "")).strip().lower()
        if kind == "github":
            reliability = max(reliability, 0.8)
            ref = str(meta.get("ref", "")).strip().lower()
            if ref and ref not in {"main", "master"}:
                reliability -= 0.05
            else:
                reliability += 0.03
            if meta.get("owner") and meta.get("repo") and meta.get("path"):
                reliability += 0.02
        if kind == "web_scrape":
            reliability = max(reliability, 0.76)
            if _as_bool(meta.get("supported_scraper"), default=False):
                reliability += 0.04
            source_urls = meta.get("source_urls")
            if isinstance(source_urls, list) and source_urls:
                reliability += 0.02
        if _as_bool(meta.get("verified"), default=False):
            reliability += 0.08

        trust = meta.get("trust_score")
        trust_float = _as_float(trust)
        if trust_float is not None:
            trust_clamped = max(0.0, min(trust_float, 1.0))
            reliability = (reliability * 0.7) + (trust_clamped * 0.3)

    return round(max(0.2, min(1.0, reliability)), 3)


def _add_curation_note(report: dict[str, Any], note: str) -> None:
    notes_obj = report.get("notes")
    if not isinstance(notes_obj, list):
        return
    if len(notes_obj) >= _CURATION_NOTES_LIMIT:
        return
    notes_obj.append(note)
