from __future__ import annotations

from app.models import OptimalSettings


def render_cura_profile(settings: OptimalSettings) -> str:
    if str(settings.process_technology).upper() not in {"FDM", "FFF"}:
        raise ValueError("Cura export currently supports only FDM/filament settings.")
    if settings.fdm is None:
        raise ValueError("Incomplete FDM settings were provided for Cura export.")

    support_style = str(settings.fdm.support_style or "tree").strip().lower()
    support_enable = support_style not in {"", "none", "off", "disabled"}
    support_structure = "tree" if support_style == "tree" else "normal"
    adhesion_type = "brim" if settings.use_case == "heavy_use" else "skirt"
    quality_type = {
        "miniature": "super",
        "collectible": "fine",
        "heavy_use": "draft",
    }.get(settings.use_case, "standard")

    lines = [
        "[general]",
        "version = 4",
        f"name = {settings.source_profile or settings.material_name or settings.resin_type}",
        "definition = custom",
        "",
        "[metadata]",
        "type = quality_changes",
        f"quality_type = {quality_type}",
        "setting_version = 25",
        "",
        "[values]",
        f"layer_height = {settings.layer_height_mm}",
        f"line_width = {settings.fdm.nozzle_diameter_mm or 0.4}",
        f"machine_nozzle_size = {settings.fdm.nozzle_diameter_mm or 0.4}",
        f"material_print_temperature = {settings.fdm.nozzle_temp_c}",
        f"material_print_temperature_layer_0 = {settings.fdm.nozzle_temp_c}",
        f"material_bed_temperature = {settings.fdm.bed_temp_c or 0}",
        f"material_bed_temperature_layer_0 = {settings.fdm.bed_temp_c or 0}",
        f"speed_print = {settings.fdm.print_speed_mm_s}",
        f"speed_layer_0 = {settings.fdm.first_layer_speed_mm_s or settings.fdm.print_speed_mm_s}",
        f"speed_travel = {settings.fdm.travel_speed_mm_s or 150.0}",
        f"retraction_enable = {'True' if (settings.fdm.retraction_distance_mm or 0) > 0 else 'False'}",
        f"retraction_amount = {settings.fdm.retraction_distance_mm or 0}",
        f"retraction_speed = {settings.fdm.retraction_speed_mm_s or 0}",
        f"wall_line_count = {settings.fdm.wall_count or 2}",
        f"infill_sparse_density = {settings.fdm.infill_percent or 15.0}",
        f"cool_fan_speed = {settings.fdm.fan_speed_percent or 0}",
        f"support_enable = {'True' if support_enable else 'False'}",
        f"support_structure = {support_structure}",
        f"adhesion_type = {adhesion_type}",
    ]

    if settings.fdm.chamber_temp_c is not None:
        lines.append(f"build_volume_temperature = {settings.fdm.chamber_temp_c}")

    return "\n".join(lines).strip() + "\n"