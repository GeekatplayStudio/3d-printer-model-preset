from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from app.sync_service import parse_technical_sync_json

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OFFICIAL_SYNC_FILES = (
    DATA_DIR / "official_catalog_sync.json",
    DATA_DIR / "official_catalog_fdm_aggregator.json",
    DATA_DIR / "official_catalog_manufacturer_filaments.json",
    DATA_DIR / "official_catalog_community_crosscheck.json",
)
_SECTION_KEYS = ("printers", "resins", "profiles")

_SOURCE_TYPE_TO_TIER = {
    "manufacturer_product_page": 1,
    "manufacturer_support_doc": 1,
    "manufacturer_settings_guide": 1,
    "manufacturer_tech_specs": 1,
    "slicer_profile_repository": 2,
    "aggregated_profile_bundle": 2,
    "community_profile_repository": 3,
    "community_database": 3,
    "reference_material_guide": 4,
    "educational_reference": 4,
}

_TIER_TO_CONFIDENCE = {
    1: 0.94,
    2: 0.84,
    3: 0.72,
    4: 0.58,
}

_HOST_LICENSE_NOTES = {
    "github.com": (
        "Derived from upstream GitHub repositories. Preserve attribution and review the upstream "
        "repository license before redistributing raw profile content."
    ),
    "wiki.anycubic.com": "Official Anycubic reference content aggregated with attribution.",
    "store.anycubic.com": "Official manufacturer reference content aggregated with attribution.",
    "www.prusa3d.com": "Official Prusa reference content aggregated with attribution.",
    "www.simplify3d.com": (
        "Reference-only values normalized from the Simplify3D materials guide. Retain attribution and "
        "verify upstream usage terms before redistributing raw tables."
    ),
    "3dprintmatrix.org": (
        "Community directory data is provisional. Verify critical specs against official manufacturer "
        "sources before production use."
    ),
    "community.element14.com": (
        "Community article data is provisional. Verify critical specs against official manufacturer "
        "sources before production use."
    ),
}


def official_sync_paths(paths: list[str | Path] | tuple[str | Path, ...] | None = None) -> list[Path]:
    selected = list(paths) if paths is not None else list(DEFAULT_OFFICIAL_SYNC_FILES)
    resolved_paths: list[Path] = []
    missing: list[Path] = []
    for item in selected:
        path = Path(item)
        candidate = path if path.is_absolute() else (DATA_DIR / path)
        if candidate.exists():
            resolved_paths.append(candidate)
        else:
            missing.append(candidate)
    if not resolved_paths:
        missing_display = ", ".join(str(path) for path in missing) or str(DATA_DIR)
        raise FileNotFoundError(f"Official catalog file(s) not found: {missing_display}")
    return resolved_paths


def load_official_sync_payload(paths: list[str | Path] | tuple[str | Path, ...] | None = None) -> dict:
    source_files = official_sync_paths(paths)
    payloads = [parse_technical_sync_json(path.read_text(encoding="utf-8")) for path in source_files]
    return merge_official_sync_payloads(payloads, source_files=source_files)


def merge_official_sync_payloads(payloads: list[dict], source_files: list[Path] | None = None) -> dict:
    updated_at_values = [
        str(payload.get("updated_at")).strip()
        for payload in payloads
        if str(payload.get("updated_at") or "").strip()
    ]
    merged = {
        "schema_version": "sync-1.3-aggregated",
        "updated_at": max(updated_at_values) if updated_at_values else None,
        "replace_existing": False,
        "printers": [],
        "resins": [],
        "profiles": [],
        "metadata": {
            "aggregator": "Geekatplay Studio provenance-backed catalog aggregator",
            "source_files": [str(path) for path in (source_files or [])],
        },
    }

    for index, payload in enumerate(payloads):
        source_file = source_files[index] if source_files and index < len(source_files) else None
        for section in _SECTION_KEYS:
            rows = payload.get(section, [])
            if not isinstance(rows, list):
                continue
            merged[section].extend(
                _enrich_row_metadata(row, section=section, source_file=source_file) for row in rows
            )

    return merged


def _enrich_row_metadata(row: object, *, section: str, source_file: Path | None) -> dict:
    payload_row = deepcopy(row) if isinstance(row, dict) else {}
    metadata = dict(payload_row.get("metadata") or {})
    source_urls = [str(url).strip() for url in metadata.get("source_urls", []) if str(url).strip()]
    source_type = str(metadata.get("source_type") or _default_source_type(section)).strip()

    tier = metadata.get("source_priority_tier")
    try:
        source_priority_tier = int(tier) if tier is not None else _SOURCE_TYPE_TO_TIER.get(source_type, 4)
    except (TypeError, ValueError):
        source_priority_tier = _SOURCE_TYPE_TO_TIER.get(source_type, 4)

    confidence_raw = metadata.get("confidence", metadata.get("sync_confidence_score"))
    try:
        confidence = round(float(confidence_raw), 2) if confidence_raw is not None else _TIER_TO_CONFIDENCE[source_priority_tier]
    except (TypeError, ValueError):
        confidence = _TIER_TO_CONFIDENCE[source_priority_tier]

    metadata["source_urls"] = source_urls
    metadata["source_url"] = str(metadata.get("source_url") or (source_urls[0] if source_urls else ""))
    metadata["source_type"] = source_type
    metadata["source_priority_tier"] = source_priority_tier
    metadata["confidence"] = confidence
    metadata["sync_confidence_score"] = confidence
    metadata["license_note"] = str(metadata.get("license_note") or _license_note_for_sources(source_urls, source_type))
    metadata.setdefault("aggregated_by", "Geekatplay Studio provenance-backed catalog aggregator")
    if source_file is not None:
        metadata.setdefault("seed_file", str(source_file))

    payload_row["metadata"] = metadata
    return payload_row


def _default_source_type(section: str) -> str:
    if section == "profiles":
        return "aggregated_profile_bundle"
    return "reference_material_guide" if section == "resins" else "community_profile_repository"


def _license_note_for_sources(source_urls: list[str], source_type: str) -> str:
    for url in source_urls:
        host = urlparse(url).netloc.lower()
        for known_host, note in _HOST_LICENSE_NOTES.items():
            if host == known_host or host.endswith(f".{known_host}"):
                return note

    if source_type in {
        "manufacturer_product_page",
        "manufacturer_support_doc",
        "manufacturer_settings_guide",
        "manufacturer_tech_specs",
    }:
        return "Official manufacturer reference content aggregated with attribution."
    if source_type in {"slicer_profile_repository", "aggregated_profile_bundle"}:
        return (
            "Derived from upstream slicer profile repositories. Preserve attribution and review upstream "
            "licenses before redistributing raw profile content."
        )
    if source_type in {"community_profile_repository", "community_database"}:
        return "Community-contributed source. Verify critical specs against official sources before production use."
    return "Reference-only normalized source. Retain attribution and verify upstream usage terms before redistribution."