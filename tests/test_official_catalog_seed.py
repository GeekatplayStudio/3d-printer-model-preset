from __future__ import annotations

import json
from pathlib import Path

from app.catalog_store import list_printers, list_profiles, list_resins
from app.sync_service import apply_technical_sync


def _seed_payload() -> dict:
    path = Path(__file__).resolve().parent.parent / "data" / "official_catalog_sync.json"
    return json.loads(path.read_text(encoding="utf-8"))


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
            assert isinstance(metadata.get("retrieved_at"), str)


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

    assert result["printers_upserted"] == len(payload["printers"])
    assert result["resins_upserted"] == len(payload["resins"])
    assert result["profiles_upserted"] >= 18

    printers = list_printers(db_path=db_path)
    resins = list_resins(db_path=db_path)
    profiles = list_profiles(db_path=db_path, active_only=False)

    assert len(printers) >= len(payload["printers"])
    assert len(resins) >= len(payload["resins"])
    assert len(profiles) >= 18
    assert any(isinstance(item.get("metadata"), dict) and item["metadata"].get("source_urls") for item in profiles)
