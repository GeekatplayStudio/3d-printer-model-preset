from app.catalog_store import create_or_upsert_printer, create_or_upsert_profile, create_or_upsert_resin
from app.resin_db import find_resin_profile, list_resins, load_resin_database


def test_resin_db_uses_catalog_db_path(tmp_path):
    db_path = tmp_path / "catalog.db"
    printer = create_or_upsert_printer({"name": "Phrozen Sonic Mini 8K S"}, db_path=db_path)
    resin = create_or_upsert_resin({"name": "Phrozen Aqua 8K"}, db_path=db_path)
    create_or_upsert_profile(
        {
            "printer_id": printer["id"],
            "resin_id": resin["id"],
            "profile_name": "Mini 8K S Detail",
            "layer_height_mm": 0.05,
            "exposure_s": 2.2,
            "bottom_exposure_s": 30.0,
            "is_default": True,
        },
        db_path=db_path,
    )

    profile = find_resin_profile(
        resin_type="Phrozen Aqua 8K",
        printer="Phrozen Sonic Mini 8K S",
        db_path=db_path,
    )
    assert profile is not None
    assert profile["exposure_s"] == 2.2

    resins = list_resins(printer="Phrozen Sonic Mini 8K S", db_path=db_path)
    assert resins == ["Phrozen Aqua 8K"]

    db = load_resin_database(db_path=db_path)
    assert db["schema_version"] == "catalog-1"
    assert len(db["profiles"]) == 1
