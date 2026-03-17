from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.catalog_store import import_catalog


def apply_technical_sync(
    payload: dict[str, Any],
    *,
    source: str = "manual",
    db_path: str | Path | None = None,
    replace_existing: bool | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    normalized = {
        "replace_existing": bool(payload.get("replace_existing") if replace_existing is None else replace_existing),
        "printers": payload.get("printers", []) or [],
        "resins": payload.get("resins", []) or [],
        "profiles": payload.get("profiles", []) or [],
    }
    result = import_catalog(normalized, db_path=db_path)
    result["source"] = source
    return result


def parse_technical_sync_json(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON payload for technical sync.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Sync payload must be a JSON object.")
    return payload
