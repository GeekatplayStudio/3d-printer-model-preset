from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main
from app.catalog_store import create_or_upsert_resin, init_catalog_store, list_resins


def _analysis_payload() -> dict:
    return {
        "file_name": "dummy.stl",
        "mesh_volume_mm3": 1000.0,
        "surface_area_mm2": 500.0,
        "surface_area_ratio": 0.5,
        "triangle_count": 1200,
        "detail_density": 1.2,
        "curvature_proxy": None,
        "slice_height_mm": 0.01,
        "build_plate_area_mm2": 12000.0,
        "max_cross_section_mm2": 1200.0,
        "max_cross_section_ratio": 0.1,
        "slice_areas": [{"z_mm": 0.0, "area_mm2": 100.0}],
        "suction_cups": [],
        "islands": [],
        "structural_risk_score": 25.0,
        "notes": [],
    }


def _build_client_with_temp_catalog(tmp_path, monkeypatch) -> TestClient:
    catalog_path = tmp_path / "catalog.db"
    init_catalog_store(db_path=catalog_path)
    monkeypatch.setattr(main, "CATALOG_PATH", catalog_path)
    return TestClient(main.app)


def _tetra_stl_bytes() -> bytes:
    text = """solid tetra
facet normal 0 0 -1
  outer loop
    vertex 0 0 0
    vertex 0 1 0
    vertex 1 0 0
  endloop
endfacet
facet normal 0 -1 0
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 0 1
  endloop
endfacet
facet normal -1 0 0
  outer loop
    vertex 0 0 0
    vertex 0 0 1
    vertex 0 1 0
  endloop
endfacet
facet normal 1 1 1
  outer loop
    vertex 1 0 0
    vertex 0 1 0
    vertex 0 0 1
  endloop
endfacet
endsolid tetra
"""
    return text.encode("utf-8")


def test_official_seed_refresh_bootstraps_stale_catalog_without_wiping_existing_rows(tmp_path, monkeypatch) -> None:
    catalog_path = tmp_path / "catalog.db"
    state_path = tmp_path / "official_catalog_state.json"
    init_catalog_store(db_path=catalog_path)
    create_or_upsert_resin({"name": "Custom Resin", "brand": "Local"}, db_path=catalog_path)

    monkeypatch.setattr(
        main,
        "_read_official_sync_payload",
        lambda: {
            "updated_at": "2026-04-26",
            "replace_existing": False,
            "printers": [],
            "resins": [
                {
                    "name": "ELEGOO Tough Resin",
                    "brand": "ELEGOO",
                    "series": "Tough Resin",
                    "technical_goal": "Impact-resistant functional parts",
                }
            ],
            "profiles": [],
        },
    )

    first = main._refresh_official_catalog_seed_if_needed(db_path=catalog_path, state_path=state_path)

    assert first is not None
    resin_names = [item["name"] for item in list_resins(db_path=catalog_path, limit=1000)]
    assert "Custom Resin" in resin_names
    assert "ELEGOO Tough Resin" in resin_names
    assert state_path.exists()

    second = main._refresh_official_catalog_seed_if_needed(db_path=catalog_path, state_path=state_path)

    assert second is None


def test_catalog_admin_page_served(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    response = client.get("/catalog/admin")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Catalog" in response.text
    assert "/static/geekatplay-mark.svg" in response.text
    assert "Materials" in response.text
    assert "Quick Admin Guide" in response.text
    assert 'data-help-key="profile_layer"' in response.text
    assert "select option," in response.text


def test_catalog_csv_import_endpoint_printers(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    csv_text = "name,brand,model\nAnycubic Photon Mono M7 Pro,Anycubic,Photon Mono M7 Pro\n"
    response = client.post(
        "/catalog/import/csv",
        data={"entity": "printers"},
        files={"file": ("printers.csv", csv_text, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["upserted"] == 1
    assert body["failed"] == 0

    listed = client.get("/catalog/printers")
    assert listed.status_code == 200
    names = [item["name"] for item in listed.json()]
    assert "Anycubic Photon Mono M7 Pro" in names


def test_catalog_csv_import_invalid_entity_returns_400(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    response = client.post(
        "/catalog/import/csv",
        data={"entity": "unknown"},
        files={"file": ("x.csv", "name\nA\n", "text/csv")},
    )

    assert response.status_code == 400
    assert "entity must be one of" in response.json()["detail"]


def test_catalog_csv_import_endpoint_resins_and_profiles(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    printer_csv = "name\nPrinter Z\n"
    resin_csv = "name,brand,series,technical_goal,viscosity_cp,shore_hardness\nResin Z,BrandZ,Series Z,Detail,160,84D\n"
    profile_csv = (
        "printer_name,resin_name,profile_name,layer_height_mm,exposure_s,bottom_exposure_s,is_default\n"
        "Printer Z,Resin Z,Z Default,0.05,2.6,31.0,true\n"
    )

    printer_result = client.post(
        "/catalog/import/csv",
        data={"entity": "printers"},
        files={"file": ("printers.csv", printer_csv, "text/csv")},
    )
    assert printer_result.status_code == 200
    assert printer_result.json()["upserted"] == 1

    resin_result = client.post(
        "/catalog/import/csv",
        data={"entity": "resins"},
        files={"file": ("resins.csv", resin_csv, "text/csv")},
    )
    assert resin_result.status_code == 200
    resin_body = resin_result.json()
    assert resin_body["upserted"] == 1
    assert resin_body["failed"] == 0

    profile_result = client.post(
        "/catalog/import/csv",
        data={"entity": "profiles"},
        files={"file": ("profiles.csv", profile_csv, "text/csv")},
    )
    assert profile_result.status_code == 200
    profile_body = profile_result.json()
    assert profile_body["upserted"] == 1
    assert profile_body["failed"] == 0

    resins = client.get("/catalog/resins")
    assert resins.status_code == 200
    resin_names = [item["name"] for item in resins.json()]
    assert "Resin Z" in resin_names

    profiles = client.get("/catalog/profiles")
    assert profiles.status_code == 200
    assert any(item["profile_name"] == "Z Default" for item in profiles.json())


def test_catalog_csv_import_profiles_missing_reference_reports_failed_rows(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    profile_csv = (
        "profile_name,layer_height_mm,exposure_s,bottom_exposure_s\n"
        "Broken Profile,0.05,2.5,30.0\n"
    )
    response = client.post(
        "/catalog/import/csv",
        data={"entity": "profiles"},
        files={"file": ("profiles.csv", profile_csv, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rows_processed"] == 1
    assert body["upserted"] == 0
    assert body["failed"] == 1
    assert any("printer_id or printer_name is required" in note for note in body["notes"])


def test_phase2_optimize_uses_selected_printer_profile(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    seed_payload = {
        "replace_existing": True,
        "printers": [
            {"name": "Printer A"},
            {"name": "Printer B"},
        ],
        "resins": [{"name": "Resin X"}],
        "profiles": [
            {
                "printer_name": "Printer A",
                "resin_name": "Resin X",
                "profile_name": "A Default",
                "layer_height_mm": 0.05,
                "exposure_s": 1.95,
                "bottom_exposure_s": 28.0,
                "tilt_speed_reference_mm_h": 130.0,
                "is_default": True,
                "is_active": True,
            },
            {
                "printer_name": "Printer B",
                "resin_name": "Resin X",
                "profile_name": "B Default",
                "layer_height_mm": 0.05,
                "exposure_s": 2.75,
                "bottom_exposure_s": 33.0,
                "tilt_speed_reference_mm_h": 70.0,
                "is_default": True,
                "is_active": True,
            },
        ],
    }
    imported = client.post("/catalog/import", json=seed_payload)
    assert imported.status_code == 200

    optimize_payload = {
        "analysis": _analysis_payload(),
        "resin_type": "Resin X",
        "use_case": "collectible",
        "printer": "Printer B",
        "ambient_temp_c": None,
        "film_releases": 0,
    }
    response = client.post("/phase2/optimize", json=optimize_payload)
    assert response.status_code == 200
    body = response.json()

    assert body["printer"] == "Printer B"
    assert body["exposure_s"] == 2.75
    assert body["source_profile"] == "B Default"
    assert body["tilt_speed_mm_min"] == 70.0


def test_phase2_optimize_returns_fdm_settings_for_filament_profile(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)
    seed_payload = {
        "replace_existing": True,
        "printers": [
            {
                "name": "Prusa MK4S",
                "technology": "FDM",
                "xy_resolution_um": 50,
            }
        ],
        "resins": [
            {
                "name": "Prusament PLA Galaxy Black",
                "material_type": "filament",
                "material_family": "PLA",
                "nozzle_temp_min_c": 205.0,
                "nozzle_temp_max_c": 215.0,
                "bed_temp_c": 60.0,
            }
        ],
        "profiles": [
            {
                "printer_name": "Prusa MK4S",
                "resin_name": "Prusament PLA Galaxy Black",
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
                "is_active": True,
            }
        ],
    }
    imported = client.post("/catalog/import", json=seed_payload)
    assert imported.status_code == 200

    response = client.post(
        "/phase2/optimize",
        json={
            "analysis": _analysis_payload(),
            "resin_type": "Prusament PLA Galaxy Black",
            "use_case": "collectible",
            "printer": "Prusa MK4S",
            "ambient_temp_c": 17.0,
            "film_releases": 0,
        },
    )
    assert response.status_code == 200
    body = response.json()

    assert body["process_technology"] == "FDM"
    assert body["fdm"]["nozzle_temp_c"] >= 215.0
    assert body["fdm"]["bed_temp_c"] == 65.0
    assert body["exposure_s"] is None
    assert body["source_profile"] == "PLA Quality"


def test_pipeline_endpoint_passes_printer_to_run_full_pipeline(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)
    calls: dict = {}

    def fake_run_full_pipeline(**kwargs):
        calls.update(kwargs)
        return {
            "analysis": _analysis_payload(),
            "settings": {
                "printer": kwargs["printer"],
                "resin_type": kwargs["resin_type"],
                "use_case": kwargs["use_case"],
                "intent_used": kwargs["use_case"],
                "layer_height_mm": 0.05,
                "exposure_s": 2.2,
                "bottom_exposure_s": 30.0,
                "tilt_speed_mm_min": 90.0,
                "tilt_speed_mm_h": 90.0,
                "tilt_angle_deg": -1.0,
                "rest_time_before_print_s": 2.0,
                "rest_time_after_retract_s": 0.5,
                "transition_layers": 5,
                "scale_compensation_percent": 100.0,
                "heater_required": False,
                "anti_aliasing": None,
                "grayscale_level": None,
                "xy_resolution_um": 18,
                "detail_tier": "medium_detail",
                "multi_parameter": {
                    "model_exposure_s": 2.1,
                    "support_exposure_s": 2.4,
                    "delicate_feature_exposure_s": 2.0,
                },
                "warnings": [],
                "recommendations": [],
                "source_profile": "Mock Profile",
            },
            "chitubox_cfg": "layer_height_mm=0.05\n",
        }

    monkeypatch.setattr(main.pipeline, "run_full_pipeline", fake_run_full_pipeline)

    response = client.post(
        "/pipeline",
        data={
            "resin_type": "Resin Mock",
            "use_case": "miniature",
            "printer": "Printer Mock",
            "ambient_temp_c": "21.0",
            "film_releases": "1234",
            "slice_height_mm": "0.02",
        },
        files={"file": ("model.stl", b"solid s\nendsolid s\n", "model/stl")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["printer"] == "Printer Mock"
    assert calls["printer"] == "Printer Mock"
    assert calls["resin_type"] == "Resin Mock"
    assert calls["catalog_db_path"] == main.CATALOG_PATH


def test_pipeline_smoke_with_real_geometry_and_catalog_profile(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    imported = client.post(
        "/catalog/import",
        json={
            "replace_existing": True,
            "printers": [{"name": "Printer Real"}],
            "resins": [{"name": "Resin Real"}],
            "profiles": [
                {
                    "printer_name": "Printer Real",
                    "resin_name": "Resin Real",
                    "profile_name": "Real Default",
                    "layer_height_mm": 0.05,
                    "exposure_s": 2.4,
                    "bottom_exposure_s": 30.0,
                    "is_default": True,
                    "is_active": True,
                }
            ],
        },
    )
    assert imported.status_code == 200

    response = client.post(
        "/pipeline",
        data={
            "resin_type": "Resin Real",
            "use_case": "miniature",
            "printer": "Printer Real",
            "slice_height_mm": "0.2",
        },
        files={"file": ("tetra.stl", _tetra_stl_bytes(), "model/stl")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["file_name"].endswith(".stl")
    assert body["analysis"]["mesh_volume_mm3"] > 0
    assert body["settings"]["printer"] == "Printer Real"
    assert body["settings"]["source_profile"] == "Real Default"
    assert body["chitubox_cfg"]


def test_catalog_profiles_query_filter_endpoint(tmp_path, monkeypatch):
    client = _build_client_with_temp_catalog(tmp_path, monkeypatch)

    imported = client.post(
        "/catalog/import",
        json={
            "replace_existing": True,
            "printers": [{"name": "Printer Query"}],
            "resins": [{"name": "Resin Query"}],
            "profiles": [
                {
                    "printer_name": "Printer Query",
                    "resin_name": "Resin Query",
                    "profile_name": "Fine Detail Profile",
                    "is_default": True,
                    "is_active": True,
                },
                {
                    "printer_name": "Printer Query",
                    "resin_name": "Resin Query",
                    "profile_name": "Legacy Disabled Profile",
                    "is_default": False,
                    "is_active": False,
                },
            ],
        },
    )
    assert imported.status_code == 200

    active_query = client.get("/catalog/profiles?query=detail")
    assert active_query.status_code == 200
    rows = active_query.json()
    assert len(rows) == 1
    assert rows[0]["profile_name"] == "Fine Detail Profile"

    inactive_hidden = client.get("/catalog/profiles?query=legacy")
    assert inactive_hidden.status_code == 200
    assert inactive_hidden.json() == []

    include_inactive = client.get("/catalog/profiles?query=legacy&active_only=false")
    assert include_inactive.status_code == 200
    rows = include_inactive.json()
    assert len(rows) == 1
    assert rows[0]["profile_name"] == "Legacy Disabled Profile"
