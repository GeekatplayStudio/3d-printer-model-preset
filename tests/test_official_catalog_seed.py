from __future__ import annotations

from pathlib import Path

from app.catalog_store import find_profile_by_names, list_printers, list_profiles, list_resins
from app.official_catalog import load_official_sync_payload
from app.sync_service import apply_technical_sync


def _seed_payload() -> dict:
    return load_official_sync_payload()


def test_official_seed_has_source_metadata() -> None:
    payload = _seed_payload()

    for section in ("printers", "resins", "profiles"):
        rows = payload.get(section, [])
        assert rows, f"{section} should not be empty"
        for row in rows:
            metadata = row.get("metadata")
            assert isinstance(metadata, dict), f"{section} row missing metadata"
            source_urls = metadata.get("source_urls")
            assert isinstance(source_urls, list) and source_urls, f"{section} row missing source_urls"
            assert all(str(url).startswith("http") for url in source_urls)
            assert str(metadata.get("source_url", "")).startswith("http")
            assert isinstance(metadata.get("retrieved_at"), str)
            assert metadata.get("source_priority_tier") in {1, 2, 3, 4}
            assert metadata.get("sync_confidence_score") is not None
            assert isinstance(metadata.get("license_note"), str) and metadata.get("license_note")


def test_official_seed_imports_with_sync_pipeline(tmp_path: Path) -> None:
    payload = _seed_payload()
    db_path = tmp_path / "catalog.db"

    result = apply_technical_sync(
        payload=payload,
        source="official_seed_test",
        db_path=db_path,
        replace_existing=True,
        source_metadata={"kind": "official_docs", "verified": True, "trust_score": 0.92},
    )

    assert result["printers_upserted"] >= 7
    assert result["resins_upserted"] >= 12
    assert result["profiles_upserted"] >= 35

    printers = list_printers(db_path=db_path)
    resins = list_resins(db_path=db_path)
    profiles = list_profiles(db_path=db_path, active_only=False)

    assert len(printers) >= 7
    assert len(resins) >= 12
    assert len(profiles) >= 35
    assert any(isinstance(item.get("metadata"), dict) and item["metadata"].get("source_urls") for item in profiles)
    assert any(str(item.get("technology", "")).upper() == "FDM" for item in printers)
    assert any(str(item.get("material_type", "")).lower() == "filament" for item in resins)
    assert any(item.get("nozzle_temp_c") is not None for item in profiles)
    assert any(item.get("name") == "Bambu Lab A1" for item in printers)
    assert any(item.get("name") == "Generic ABS" for item in resins)
    assert any(item.get("name") == "Anycubic ABS Filament" for item in resins)
    assert any(item.get("name") == "Prusament PA11 Carbon Fiber Black 800g (NFC)" for item in resins)


def test_official_seed_prefers_stronger_provenance_for_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "catalog.db"

    apply_technical_sync(
        payload=_seed_payload(),
        source="official_seed_test",
        db_path=db_path,
        replace_existing=True,
        source_metadata={"kind": "official_docs", "verified": True, "trust_score": 0.92},
    )

    printers = {item["name"]: item for item in list_printers(db_path=db_path)}
    resins = {item["name"]: item for item in list_resins(db_path=db_path)}

    assert printers["Bambu Lab A1"]["metadata"]["source_priority_tier"] == 2
    assert "github.com" in printers["Bambu Lab A1"]["metadata"]["source_url"]

    abs_resin = resins["Anycubic ABS Filament"]
    assert abs_resin["metadata"]["source_priority_tier"] == 1
    assert "store.anycubic.com/products/abs-filament" in abs_resin["metadata"]["source_url"]

    pa11_resin = resins["Prusament PA11 Carbon Fiber Black 800g (NFC)"]
    assert pa11_resin["metadata"]["source_priority_tier"] == 1
    assert "prusa3d.com" in pa11_resin["metadata"]["source_url"]

    pla_resin = resins["Prusament PLA Galaxy Black"]
    assert pla_resin["metadata"]["source_priority_tier"] == 1
    assert "prusament-pla-prusa-galaxy-black-1kg-nfc" in pla_resin["metadata"]["source_url"]

    abs_profile = find_profile_by_names("Anycubic Kobra S1", "Anycubic ABS Filament", db_path=db_path)
    assert abs_profile is not None
    assert abs_profile["profile_name"] == "Anycubic ABS Structural"
    assert abs_profile["nozzle_temp_c"] == 250.0
    assert abs_profile["metadata"]["source_priority_tier"] == 1
    assert abs_profile["metadata"]["sync_confidence_score"] >= 0.8

    pa11_profile = find_profile_by_names(
        "Prusa MK4S",
        "Prusament PA11 Carbon Fiber Black 800g (NFC)",
        db_path=db_path,
    )
    assert pa11_profile is not None
    assert pa11_profile["profile_name"] == "Prusament PA11-CF Engineering"
    assert pa11_profile["nozzle_temp_c"] == 285.0
    assert pa11_profile["metadata"]["source_priority_tier"] == 1

    high_speed_pla_profile = find_profile_by_names("Anycubic Kobra S1", "Anycubic High Speed PLA", db_path=db_path)
    assert high_speed_pla_profile is not None
    assert high_speed_pla_profile["profile_name"] == "Anycubic High Speed PLA Rapid"
    assert high_speed_pla_profile["metadata"]["source_priority_tier"] == 1
    assert high_speed_pla_profile["metadata"]["sync_confidence_score"] >= 0.8

    petg_profile = find_profile_by_names("Anycubic Kobra S1", "Anycubic PETG Filament", db_path=db_path)
    assert petg_profile is not None
    assert petg_profile["profile_name"] == "Anycubic PETG Durable"
    assert petg_profile["metadata"]["source_priority_tier"] == 1

    prusa_pla_profile = find_profile_by_names("Prusa MK4S", "Prusament PLA Galaxy Black", db_path=db_path)
    assert prusa_pla_profile is not None
    assert prusa_pla_profile["profile_name"] == "PLA Quality"
    assert prusa_pla_profile["nozzle_temp_c"] == 215.0
    assert "prusament-pla-prusa-galaxy-black-1kg-nfc" in prusa_pla_profile["metadata"]["source_url"]
