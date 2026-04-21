#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.sync_service import apply_technical_sync


def _default_db_path() -> Path:
    data_dir = Path(os.getenv("RESINLOGIC_DATA_DIR", str(Path.home() / ".resinlogic"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "tech_catalog.db"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import source-attributed official resin/printer catalog seed data into a local ResinLogic DB.",
    )
    parser.add_argument(
        "--seed",
        default=str(REPO_ROOT / "data" / "official_catalog_sync.json"),
        help="Path to sync-style JSON payload (default: data/official_catalog_sync.json).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to tech_catalog.db (default: RESINLOGIC_DATA_DIR/tech_catalog.db or ~/.resinlogic/tech_catalog.db).",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing catalog entries before importing.",
    )
    args = parser.parse_args()

    seed_path = Path(args.seed).expanduser().resolve()
    if not seed_path.exists():
        raise SystemExit(f"Seed file not found: {seed_path}")

    db_path = Path(args.db).expanduser().resolve() if args.db else _default_db_path().resolve()
    payload = json.loads(seed_path.read_text(encoding="utf-8"))

    # Keep provenance metadata but let operator choose replacement behavior.
    payload["replace_existing"] = bool(args.replace_existing)

    result = apply_technical_sync(
        payload=payload,
        source="official_catalog_seed",
        db_path=db_path,
        replace_existing=bool(args.replace_existing),
        source_metadata={
            "kind": "official_docs",
            "verified": True,
            "trust_score": 0.92,
            "seed_file": str(seed_path),
        },
    )

    print(json.dumps({"db_path": str(db_path), **result}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
