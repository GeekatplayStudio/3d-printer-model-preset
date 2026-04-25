from __future__ import annotations

from app.catalog_store import list_printers, list_profiles, list_resins
from app.sync_service import (
    apply_technical_sync,
    build_github_raw_url,
    curate_technical_sync_payload,
    normalize_technical_sync_payload,
)


def test_normalize_legacy_payload_to_catalog_sync_shape():
    payload = {
        "schema_version": "legacy-1",
        "profiles": [
            {
                "name": "Legacy Profile",
                "printer": "Printer L",
                "resin_type": "Resin L",
                "layer_height_mm": 0.05,
                "exposure_s": 2.2,
                "bottom_exposure_s": 30.0,
                "is_default": True,
            }
        ],
    }

    normalized = normalize_technical_sync_payload(payload=payload)

    assert normalized["replace_existing"] is False
    assert len(normalized["printers"]) == 1
    assert len(normalized["resins"]) == 1
    assert len(normalized["profiles"]) == 1
    assert normalized["profiles"][0]["printer_name"] == "Printer L"
    assert normalized["profiles"][0]["resin_name"] == "Resin L"


def test_apply_technical_sync_accepts_legacy_payload(tmp_path):
    db_path = tmp_path / "catalog.db"
    payload = {
        "profiles": [
            {
                "name": "Legacy Profile",
                "printer": "Printer L",
                "resin_type": "Resin L",
                "layer_height_mm": 0.05,
                "exposure_s": 2.2,
                "bottom_exposure_s": 30.0,
                "is_default": True,
            }
        ]
    }

    result = apply_technical_sync(payload=payload, db_path=db_path)

    assert result["printers_upserted"] == 1
    assert result["resins_upserted"] == 1
    assert result["profiles_upserted"] == 1
    assert list_printers(db_path=db_path)[0]["name"] == "Printer L"
    assert list_resins(db_path=db_path)[0]["name"] == "Resin L"
    assert list_profiles(db_path=db_path)[0]["profile_name"] == "Legacy Profile"


def test_build_github_raw_url_blocks_parent_path_escape():
    try:
        build_github_raw_url(owner="org", repo="repo", ref="main", path="../secrets.json")
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_curate_sync_payload_clamps_and_dedupes():
    payload = {
        "replace_existing": False,
        "printers": [
            {"name": "Printer A", "xy_resolution_um": 1},
            {"name": "Printer A", "xy_resolution_um": 500},
        ],
        "resins": [
            {"name": "Resin A", "shrinkage_percent": -5},
            {"name": "Resin A", "shrinkage_percent": 40},
        ],
        "profiles": [
            {
                "printer_name": "Printer A",
                "resin_name": "Resin A",
                "profile_name": "Profile A",
                "exposure_s": 0.05,
                "bottom_exposure_s": 999,
            },
            {
                "printer_name": "Printer A",
                "resin_name": "Resin A",
                "profile_name": "Profile A",
                "exposure_s": 100,
                "bottom_exposure_s": -2,
            },
        ],
    }

    curated, report = curate_technical_sync_payload(payload)

    assert len(curated["printers"]) == 1
    assert len(curated["resins"]) == 1
    assert len(curated["profiles"]) == 1
    assert curated["printers"][0]["xy_resolution_um"] == 200
    assert curated["resins"][0]["shrinkage_percent"] == 20.0
    assert curated["profiles"][0]["exposure_s"] == 30.0
    assert curated["profiles"][0]["bottom_exposure_s"] == 1.0
    assert report["dropped_counts"]["printers"] == 1
    assert report["dropped_counts"]["resins"] == 1
    assert report["dropped_counts"]["profiles"] == 1
    assert report["average_profile_quality"] is not None


def test_curate_sync_payload_drops_low_signal_profiles():
    payload = {
        "replace_existing": False,
        "printers": [{"name": "Printer A"}],
        "resins": [{"name": "Resin A"}],
        "profiles": [
            {
                "printer_name": "Printer A",
                "resin_name": "Resin A",
                "profile_name": "No Signal",
            }
        ],
    }

    curated, report = curate_technical_sync_payload(payload)

    assert len(curated["profiles"]) == 0
    assert report["dropped_counts"]["profiles"] == 1
    assert any("no core setting values" in note.lower() for note in report["notes"])


def test_curate_sync_payload_scores_source_reliability_and_confidence():
    payload = {
        "replace_existing": False,
        "printers": [{"name": "Printer A"}],
        "resins": [{"name": "Resin A"}],
        "profiles": [
            {
                "printer_name": "Printer A",
                "resin_name": "Resin A",
                "profile_name": "Confidence Profile",
                "exposure_s": 2.5,
                "bottom_exposure_s": 30.0,
                "tilt_speed_reference_mm_h": 90.0,
                "updated_at": "2026-04-20",
            }
        ],
    }

    curated, report = curate_technical_sync_payload(
        payload,
        source="github_repo",
        source_metadata={
            "kind": "github",
            "owner": "org",
            "repo": "repo",
            "path": "data/sync.json",
            "ref": "main",
        },
    )

    assert report["source"] == "github_repo"
    assert report["source_reliability"] >= 0.8
    assert report["average_profile_confidence"] is not None
    assert report["confidence_distribution"]["medium"] >= 1
    profile = curated["profiles"][0]
    assert profile["metadata"]["source_reliability"] == report["source_reliability"]
    assert profile["metadata"]["sync_confidence_score"] >= 0.55


def test_curate_sync_payload_duplicate_profile_prefers_higher_confidence():
    payload = {
        "replace_existing": False,
        "printers": [{"name": "Printer A"}],
        "resins": [{"name": "Resin A"}],
        "profiles": [
            {
                "printer_name": "Printer A",
                "resin_name": "Resin A",
                "profile_name": "Profile A",
                "exposure_s": 2.0,
                "bottom_exposure_s": 25.0,
                "updated_at": "2022-01-01",
            },
            {
                "printer_name": "Printer A",
                "resin_name": "Resin A",
                "profile_name": "Profile A",
                "exposure_s": 2.8,
                "bottom_exposure_s": 33.0,
                "updated_at": "2026-04-20",
            },
        ],
    }

    curated, report = curate_technical_sync_payload(payload, source="github_repo")

    assert len(curated["profiles"]) == 1
    assert curated["profiles"][0]["exposure_s"] == 2.8
    assert any("replaced prior row" in note.lower() for note in report["notes"])


def test_apply_technical_sync_returns_curation_summary(tmp_path):
    db_path = tmp_path / "catalog.db"
    result = apply_technical_sync(
        payload={
            "printers": [{"name": "P1"}],
            "resins": [{"name": "R1"}],
            "profiles": [
                {
                    "printer_name": "P1",
                    "resin_name": "R1",
                    "profile_name": "Profile1",
                    "exposure_s": 2.5,
                }
            ],
        },
        db_path=db_path,
    )

    assert result["printers_upserted"] == 1
    assert result["profiles_upserted"] == 1
    assert "curation" in result
    assert result["curation"]["kept_counts"]["profiles"] == 1


def test_curate_sync_payload_keeps_fdm_materials_and_profiles():
    payload = {
        "replace_existing": False,
        "printers": [{"name": "Kobra 3", "technology": "FDM"}],
        "resins": [
            {
                "name": "High Speed PLA",
                "material_type": "filament",
                "material_family": "PLA",
                "filament_diameter_mm": 1.75,
                "nozzle_temp_min_c": 200.0,
                "nozzle_temp_max_c": 220.0,
                "bed_temp_c": 60.0,
            }
        ],
        "profiles": [
            {
                "printer_name": "Kobra 3",
                "resin_name": "High Speed PLA",
                "profile_name": "Draft PLA",
                "layer_height_mm": 0.2,
                "nozzle_temp_c": 210.0,
                "bed_temp_c": 60.0,
                "print_speed_mm_s": 55.0,
                "retraction_distance_mm": 0.8,
                "retraction_speed_mm_s": 35.0,
                "nozzle_diameter_mm": 0.4,
                "fan_speed_percent": 100,
                "is_default": True,
            }
        ],
    }

    curated, report = curate_technical_sync_payload(
        payload,
        source="web_scrape",
        source_metadata={
            "kind": "web_scrape",
            "supported_scraper": True,
            "source_urls": ["https://store.anycubic.com/products/high-speed-pla"],
        },
    )

    assert len(curated["resins"]) == 1
    assert curated["resins"][0]["material_type"] == "filament"
    assert curated["resins"][0]["material_family"] == "PLA"
    assert curated["resins"][0]["filament_diameter_mm"] == 1.75
    assert len(curated["profiles"]) == 1
    assert curated["profiles"][0]["nozzle_temp_c"] == 210.0
    assert curated["profiles"][0]["print_speed_mm_s"] == 55.0
    assert curated["profiles"][0]["metadata"]["profile_process"] == "fdm"
    assert report["kept_counts"]["profiles"] == 1
    assert report["source_reliability"] >= 0.76


def test_apply_technical_sync_preserves_fdm_fields(tmp_path):
    db_path = tmp_path / "catalog.db"
    result = apply_technical_sync(
        payload={
            "printers": [{"name": "Kobra 3", "technology": "FDM"}],
            "resins": [
                {
                    "name": "PETG Filament",
                    "material_type": "filament",
                    "material_family": "PETG",
                    "filament_diameter_mm": 1.75,
                    "nozzle_temp_min_c": 230.0,
                    "nozzle_temp_max_c": 250.0,
                    "bed_temp_c": 75.0,
                }
            ],
            "profiles": [
                {
                    "printer_name": "Kobra 3",
                    "resin_name": "PETG Filament",
                    "profile_name": "PETG Default",
                    "layer_height_mm": 0.2,
                    "nozzle_temp_c": 240.0,
                    "bed_temp_c": 75.0,
                    "print_speed_mm_s": 45.0,
                    "retraction_distance_mm": 0.7,
                    "retraction_speed_mm_s": 30.0,
                    "is_default": True,
                }
            ],
        },
        db_path=db_path,
    )

    assert result["resins_upserted"] == 1
    assert result["profiles_upserted"] == 1
    resin = list_resins(db_path=db_path)[0]
    profile = list_profiles(db_path=db_path)[0]
    assert resin["material_type"] == "filament"
    assert resin["material_family"] == "PETG"
    assert profile["nozzle_temp_c"] == 240.0
    assert profile["print_speed_mm_s"] == 45.0
