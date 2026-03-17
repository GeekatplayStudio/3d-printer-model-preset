from app.catalog_store import import_catalog_csv, list_printers, list_profiles, list_resins


def test_catalog_csv_import_printers(tmp_path):
    db_path = tmp_path / "catalog.db"
    csv_text = """name,brand,model,xy_resolution_um
Elegoo Mars 5 Ultra,Elegoo,Mars 5 Ultra,18
Anycubic Photon Mono M7 Pro,Anycubic,Photon Mono M7 Pro,17
"""
    result = import_catalog_csv("printers", csv_text, db_path=db_path)
    assert result["rows_processed"] == 2
    assert result["upserted"] == 2
    assert result["failed"] == 0
    assert len(list_printers(db_path=db_path)) == 2


def test_catalog_csv_import_profiles_by_names(tmp_path):
    db_path = tmp_path / "catalog.db"
    import_catalog_csv(
        "printers",
        "name\nElegoo Mars 5 Ultra\n",
        db_path=db_path,
    )
    import_catalog_csv(
        "resins",
        "name\nElegoo 8K Standard\n",
        db_path=db_path,
    )
    csv_text = """printer_name,resin_name,profile_name,layer_height_mm,exposure_s,bottom_exposure_s,is_default
Elegoo Mars 5 Ultra,Elegoo 8K Standard,Default Detail,0.05,2.3,30.0,true
"""
    result = import_catalog_csv("profiles", csv_text, db_path=db_path)
    assert result["upserted"] == 1
    assert len(list_resins(db_path=db_path)) == 1
    profiles = list_profiles(db_path=db_path)
    assert len(profiles) == 1
    assert profiles[0]["profile_name"] == "Default Detail"
