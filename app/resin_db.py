from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.catalog_store import (
    DEFAULT_CATALOG_DB_PATH,
    find_profile_by_names,
    list_legacy_profile_view,
    list_resin_names_for_printer,
)

_REPO_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "resin_profiles.json"
_PACKAGE_DB_PATH = Path(__file__).resolve().parent / "data" / "resin_profiles.json"
DEFAULT_DB_PATH = _REPO_DB_PATH if _REPO_DB_PATH.exists() else _PACKAGE_DB_PATH


def _catalog_db_path(db_path: str | Path | None) -> Path | None:
    if db_path and str(db_path).endswith(".db"):
        return Path(db_path)
    return DEFAULT_CATALOG_DB_PATH if DEFAULT_CATALOG_DB_PATH.exists() else None


def load_resin_database(db_path: str | Path | None = None) -> dict[str, Any]:
    catalog_path = _catalog_db_path(db_path)
    if catalog_path is not None and catalog_path.exists():
        profiles = list_legacy_profile_view(db_path=catalog_path)
        if profiles:
            return {
                "schema_version": "catalog-1",
                "updated_at": "live",
                "profiles": profiles,
            }

    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def find_resin_profile(
    resin_type: str,
    printer: str = "Elegoo Mars 5 Ultra",
    layer_height_mm: float | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    catalog_path = _catalog_db_path(db_path)
    profile = find_profile_by_names(
        printer_name=printer,
        resin_name=resin_type,
        layer_height_mm=layer_height_mm,
        db_path=catalog_path,
    )
    if profile is not None:
        return profile

    db = load_resin_database(db_path)
    profiles: list[dict[str, Any]] = db.get("profiles", [])
    resin_key = resin_type.strip().lower()
    printer_key = printer.strip().lower()

    candidates = [
        profile
        for profile in profiles
        if profile.get("resin_type", "").strip().lower() == resin_key
        and profile.get("printer", "").strip().lower() == printer_key
    ]
    if not candidates:
        return None

    if layer_height_mm is None:
        defaults = [p for p in candidates if p.get("is_default")]
        return defaults[0] if defaults else candidates[0]

    return min(
        candidates,
        key=lambda p: abs(float(p.get("layer_height_mm", 0.05)) - layer_height_mm),
    )


def list_resins(
    printer: str = "Elegoo Mars 5 Ultra",
    db_path: str | Path | None = None,
) -> list[str]:
    catalog_resins = list_resin_names_for_printer(printer, db_path=_catalog_db_path(db_path))
    if catalog_resins:
        return catalog_resins

    db = load_resin_database(db_path)
    printer_key = printer.strip().lower()
    resins = {
        profile.get("resin_type", "").strip()
        for profile in db.get("profiles", [])
        if profile.get("printer", "").strip().lower() == printer_key
    }
    return sorted(r for r in resins if r)
