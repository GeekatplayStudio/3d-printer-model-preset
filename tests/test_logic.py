from app.logic import get_optimal_settings, get_temp_offset
from app.models import (
    GeometryAnalysis,
    SliceArea,
)
from app.catalog_store import create_or_upsert_printer, create_or_upsert_profile, create_or_upsert_resin


def _analysis_stub(max_ratio: float = 0.25, detail_density: float = 2.0) -> GeometryAnalysis:
    return GeometryAnalysis(
        file_name="dummy.stl",
        mesh_volume_mm3=1000.0,
        surface_area_mm2=500.0,
        surface_area_ratio=0.5,
        triangle_count=1200,
        detail_density=detail_density,
        curvature_proxy=None,
        slice_height_mm=0.01,
        build_plate_area_mm2=12000.0,
        max_cross_section_mm2=max_ratio * 12000.0,
        max_cross_section_ratio=max_ratio,
        slice_areas=[SliceArea(z_mm=0.0, area_mm2=100.0)],
        suction_cups=[],
        islands=[],
        structural_risk_score=42.0,
        notes=[],
    )


def test_tilt_speed_high_ratio():
    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.25),
        resin_type="Elegoo ABS-Like v3.0",
        use_case="miniature",
    )
    assert settings.tilt_speed_mm_min == 70.0


def test_tilt_speed_low_ratio():
    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.03),
        resin_type="Elegoo ABS-Like v3.0",
        use_case="miniature",
    )
    assert settings.tilt_speed_mm_min == 150.0


def test_temperature_compensation():
    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.1),
        resin_type="Elegoo ABS-Like v3.0",
        use_case="collectible",
        ambient_temp_c=20.0,
    )
    assert settings.exposure_s > 2.6
    assert settings.heater_required is False


def test_film_compensation():
    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.1),
        resin_type="Elegoo ABS-Like v3.0",
        use_case="collectible",
        film_releases=60000,
    )
    assert settings.rest_time_after_retract_s >= 1.0
    assert any("60,000" in warning for warning in settings.warnings)


def test_temp_offset_requires_heater_under_18c():
    adjusted, heater_required, notes = get_temp_offset(2.0, 17.0)
    assert adjusted > 2.0
    assert heater_required is True
    assert any("Heater required" in note for note in notes)


def test_scale_compensation_for_anycubic_high_speed():
    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.12),
        resin_type="Anycubic High Speed 2.0",
        use_case="heavy_use",
    )
    assert settings.scale_compensation_percent == 100.5


def test_printer_specific_profile_lookup_from_catalog(tmp_path):
    db_path = tmp_path / "catalog.db"
    printer_a = create_or_upsert_printer({"name": "Printer A"}, db_path=db_path)
    printer_b = create_or_upsert_printer({"name": "Printer B"}, db_path=db_path)
    resin = create_or_upsert_resin({"name": "Resin X"}, db_path=db_path)
    create_or_upsert_profile(
        {
            "printer_id": printer_a["id"],
            "resin_id": resin["id"],
            "profile_name": "A Profile",
            "layer_height_mm": 0.05,
            "exposure_s": 1.9,
            "bottom_exposure_s": 25.0,
            "is_default": True,
        },
        db_path=db_path,
    )
    create_or_upsert_profile(
        {
            "printer_id": printer_b["id"],
            "resin_id": resin["id"],
            "profile_name": "B Profile",
            "layer_height_mm": 0.05,
            "exposure_s": 2.7,
            "bottom_exposure_s": 32.0,
            "is_default": True,
        },
        db_path=db_path,
    )

    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.1),
        resin_type="Resin X",
        use_case="collectible",
        printer="Printer B",
        catalog_db_path=db_path,
    )
    assert settings.printer == "Printer B"
    assert settings.exposure_s == 2.7
    assert settings.source_profile == "B Profile"


def test_settings_provenance_verified_sources(tmp_path):
    db_path = tmp_path / "catalog.db"
    printer = create_or_upsert_printer(
        {
            "name": "Printer Provenance",
            "metadata": {
                "source_urls": ["https://manufacturer.example/printer-provenance"],
                "source_type": "manufacturer_product_page",
            },
        },
        db_path=db_path,
    )
    resin = create_or_upsert_resin(
        {
            "name": "Resin Provenance",
            "metadata": {
                "source_urls": ["https://manufacturer.example/resin-provenance"],
                "source_type": "manufacturer_material_page",
            },
        },
        db_path=db_path,
    )
    create_or_upsert_profile(
        {
            "printer_id": printer["id"],
            "resin_id": resin["id"],
            "profile_name": "Profile Provenance",
            "layer_height_mm": 0.05,
            "exposure_s": 2.1,
            "bottom_exposure_s": 28.0,
            "is_default": True,
            "metadata": {
                "source_urls": ["https://manufacturer.example/settings/provenance"],
                "source_type": "manufacturer_settings_guide",
                "sync_confidence_score": 0.82,
            },
        },
        db_path=db_path,
    )

    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.1),
        resin_type="Resin Provenance",
        use_case="collectible",
        printer="Printer Provenance",
        catalog_db_path=db_path,
    )

    assert settings.provenance.data_quality == "verified_sources"
    assert settings.provenance.real_data_backed is True
    assert settings.provenance.source_count >= 1
    assert settings.provenance.confidence_score == 0.82
    assert settings.provenance.references


def test_settings_provenance_fallback_defaults():
    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.1),
        resin_type="Unknown Resin",
        use_case="collectible",
        printer="Unknown Printer",
    )

    assert settings.provenance.data_quality == "fallback_defaults"
    assert settings.provenance.real_data_backed is False


def test_fdm_printer_profile_lookup_returns_fdm_settings(tmp_path):
    db_path = tmp_path / "catalog.db"
    printer = create_or_upsert_printer(
        {
            "name": "Prusa MK4S",
            "technology": "FDM",
            "xy_resolution_um": 50,
        },
        db_path=db_path,
    )
    filament = create_or_upsert_resin(
        {
            "name": "Prusament PLA Galaxy Black",
            "material_type": "filament",
            "material_family": "PLA",
            "nozzle_temp_min_c": 205.0,
            "nozzle_temp_max_c": 215.0,
            "bed_temp_c": 60.0,
        },
        db_path=db_path,
    )
    create_or_upsert_profile(
        {
            "printer_id": printer["id"],
            "resin_id": filament["id"],
            "profile_name": "PLA Quality",
            "layer_height_mm": 0.2,
            "nozzle_temp_c": 210.0,
            "bed_temp_c": 60.0,
            "print_speed_mm_s": 55.0,
            "first_layer_speed_mm_s": 20.0,
            "travel_speed_mm_s": 180.0,
            "retraction_distance_mm": 0.8,
            "retraction_speed_mm_s": 35.0,
            "nozzle_diameter_mm": 0.4,
            "fan_speed_percent": 100,
            "infill_percent": 15.0,
            "wall_count": 2,
            "support_style": "tree",
            "is_default": True,
        },
        db_path=db_path,
    )

    settings = get_optimal_settings(
        analysis_data=_analysis_stub(max_ratio=0.12, detail_density=2.4),
        resin_type="Prusament PLA Galaxy Black",
        use_case="collectible",
        printer="Prusa MK4S",
        ambient_temp_c=17.0,
        catalog_db_path=db_path,
    )

    assert settings.process_technology == "FDM"
    assert settings.fdm is not None
    assert settings.exposure_s is None
    assert settings.fdm.nozzle_temp_c >= 215.0
    assert settings.fdm.bed_temp_c == 65.0
    assert settings.fdm.wall_count >= 3
    assert settings.source_profile == "PLA Quality"
