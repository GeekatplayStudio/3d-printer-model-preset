from app.catalog_store import (
    create_or_upsert_printer,
    create_or_upsert_profile,
    create_or_upsert_resin,
    export_catalog,
    find_profile_by_names,
    import_catalog,
    list_printers,
    list_profiles,
    list_resin_names_for_printer,
    list_resins,
)


def test_catalog_crud_and_lookup(tmp_path):
    db_path = tmp_path / "catalog.db"

    printer = create_or_upsert_printer(
        {
            "name": "Anycubic Photon Mono M7 Pro",
            "technology": "MSLA",
            "xy_resolution_um": 17,
            "tilt_release_supported": False,
            "wifi_supported": True,
        },
        db_path=db_path,
    )
    resin = create_or_upsert_resin(
        {
            "name": "Anycubic ABS-Like Pro 2",
            "series": "ABS-Like Pro 2",
            "technical_goal": "Engineering/Stress",
            "viscosity_cp": 210,
            "shore_hardness": "86D",
            "shrinkage_percent": 2.1,
        },
        db_path=db_path,
    )
    profile = create_or_upsert_profile(
        {
            "printer_id": printer["id"],
            "resin_id": resin["id"],
            "profile_name": "M7 Pro Engineering",
            "layer_height_mm": 0.05,
            "exposure_s": 2.8,
            "bottom_exposure_s": 34.0,
            "tilt_speed_reference_mm_h": 60,
            "is_default": True,
            "is_active": True,
        },
        db_path=db_path,
    )

    assert profile["printer_name"] == "Anycubic Photon Mono M7 Pro"
    assert profile["resin_name"] == "Anycubic ABS-Like Pro 2"
    assert len(list_printers(db_path=db_path)) == 1
    assert len(list_resins(db_path=db_path)) == 1
    assert len(list_profiles(db_path=db_path)) == 1

    resolved = find_profile_by_names(
        printer_name="Anycubic Photon Mono M7 Pro",
        resin_name="Anycubic ABS-Like Pro 2",
        layer_height_mm=0.05,
        db_path=db_path,
    )
    assert resolved is not None
    assert resolved["exposure_s"] == 2.8

    resin_names = list_resin_names_for_printer("Anycubic Photon Mono M7 Pro", db_path=db_path)
    assert resin_names == ["Anycubic ABS-Like Pro 2"]


def test_catalog_import_export_roundtrip(tmp_path):
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"

    printer = create_or_upsert_printer({"name": "Elegoo Saturn 4 Ultra"}, db_path=source_db)
    resin = create_or_upsert_resin({"name": "Elegoo 8K Standard"}, db_path=source_db)
    create_or_upsert_profile(
        {
            "printer_id": printer["id"],
            "resin_id": resin["id"],
            "profile_name": "Saturn 4 Ultra Detail",
            "layer_height_mm": 0.05,
            "exposure_s": 2.3,
            "bottom_exposure_s": 30.0,
            "is_default": True,
        },
        db_path=source_db,
    )

    exported = export_catalog(db_path=source_db)
    result = import_catalog(
        {
            "replace_existing": True,
            "printers": exported["printers"],
            "resins": exported["resins"],
            "profiles": [
                {
                    "printer_name": item["printer_name"],
                    "resin_name": item["resin_name"],
                    "profile_name": item["profile_name"],
                    "layer_height_mm": item["layer_height_mm"],
                    "exposure_s": item["exposure_s"],
                    "bottom_exposure_s": item["bottom_exposure_s"],
                    "transition_layers": item["transition_layers"],
                    "tilt_speed_reference_mm_h": item["tilt_speed_reference_mm_h"],
                    "rest_time_before_print_s": item["rest_time_before_print_s"],
                    "rest_time_after_retract_s": item["rest_time_after_retract_s"],
                    "is_default": item["is_default"],
                    "is_active": item["is_active"],
                    "notes": item["notes"],
                    "metadata": item["metadata"],
                }
                for item in exported["profiles"]
            ],
        },
        db_path=target_db,
    )
    assert result["printers_upserted"] == 1
    assert result["resins_upserted"] == 1
    assert result["profiles_upserted"] == 1
    assert len(list_profiles(db_path=target_db)) == 1


def test_list_profiles_query_and_active_filter(tmp_path):
    db_path = tmp_path / "catalog.db"

    printer = create_or_upsert_printer({"name": "Elegoo Mars 5 Ultra"}, db_path=db_path)
    resin = create_or_upsert_resin({"name": "Elegoo ABS-Like 3.0"}, db_path=db_path)

    create_or_upsert_profile(
        {
            "printer_id": printer["id"],
            "resin_id": resin["id"],
            "profile_name": "Miniature High Detail",
            "is_default": True,
            "is_active": True,
        },
        db_path=db_path,
    )
    create_or_upsert_profile(
        {
            "printer_id": printer["id"],
            "resin_id": resin["id"],
            "profile_name": "Archived Rough Draft",
            "is_default": False,
            "is_active": False,
        },
        db_path=db_path,
    )

    detail_only = list_profiles(db_path=db_path, query="detail")
    assert len(detail_only) == 1
    assert detail_only[0]["profile_name"] == "Miniature High Detail"

    none_active = list_profiles(db_path=db_path, query="archived")
    assert none_active == []

    include_inactive = list_profiles(db_path=db_path, query="archived", active_only=False)
    assert len(include_inactive) == 1
    assert include_inactive[0]["profile_name"] == "Archived Rough Draft"
