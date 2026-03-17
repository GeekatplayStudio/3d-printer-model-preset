from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_CATALOG_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tech_catalog.db"
LEGACY_PROFILES_PATH = Path(__file__).resolve().parent.parent / "data" / "resin_profiles.json"

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    brand TEXT,
    model TEXT,
    technology TEXT,
    xy_resolution_um INTEGER,
    build_volume_x_mm REAL,
    build_volume_y_mm REAL,
    build_volume_z_mm REAL,
    tilt_release_supported INTEGER,
    wifi_supported INTEGER,
    notes TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    brand TEXT,
    series TEXT,
    technical_goal TEXT,
    viscosity_cp REAL,
    shore_hardness TEXT,
    shrinkage_percent REAL,
    notes TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    printer_id INTEGER NOT NULL REFERENCES printers(id) ON DELETE CASCADE,
    resin_id INTEGER NOT NULL REFERENCES resins(id) ON DELETE CASCADE,
    profile_name TEXT NOT NULL,
    layer_height_mm REAL,
    exposure_s REAL,
    bottom_exposure_s REAL,
    transition_layers INTEGER,
    tilt_speed_reference_mm_h REAL,
    rest_time_before_print_s REAL,
    rest_time_after_retract_s REAL,
    is_default INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(printer_id, resin_id, profile_name, layer_height_mm)
);

CREATE INDEX IF NOT EXISTS idx_profiles_printer_resin ON profiles(printer_id, resin_id);
CREATE INDEX IF NOT EXISTS idx_profiles_default ON profiles(is_default);
CREATE INDEX IF NOT EXISTS idx_profiles_active ON profiles(is_active);
"""


def init_catalog_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_CATALOG_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
    return path


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = init_catalog_store(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _infer_brand_model(name: str) -> tuple[str | None, str | None]:
    tokens = (name or "").strip().split()
    if not tokens:
        return None, None
    if len(tokens) == 1:
        return tokens[0], None
    return tokens[0], " ".join(tokens[1:])


def _json_dumps(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _row_to_printer(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "brand": row["brand"],
        "model": row["model"],
        "technology": row["technology"],
        "xy_resolution_um": row["xy_resolution_um"],
        "build_volume_x_mm": row["build_volume_x_mm"],
        "build_volume_y_mm": row["build_volume_y_mm"],
        "build_volume_z_mm": row["build_volume_z_mm"],
        "tilt_release_supported": _int_to_bool(row["tilt_release_supported"]),
        "wifi_supported": _int_to_bool(row["wifi_supported"]),
        "notes": row["notes"],
        "metadata": _json_loads(row["metadata_json"]),
    }


def _row_to_resin(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "brand": row["brand"],
        "series": row["series"],
        "technical_goal": row["technical_goal"],
        "viscosity_cp": row["viscosity_cp"],
        "shore_hardness": row["shore_hardness"],
        "shrinkage_percent": row["shrinkage_percent"],
        "notes": row["notes"],
        "metadata": _json_loads(row["metadata_json"]),
    }


def _row_to_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "printer_id": row["printer_id"],
        "resin_id": row["resin_id"],
        "printer_name": row["printer_name"],
        "resin_name": row["resin_name"],
        "profile_name": row["profile_name"],
        "layer_height_mm": row["layer_height_mm"],
        "exposure_s": row["exposure_s"],
        "bottom_exposure_s": row["bottom_exposure_s"],
        "transition_layers": row["transition_layers"],
        "tilt_speed_reference_mm_h": row["tilt_speed_reference_mm_h"],
        "rest_time_before_print_s": row["rest_time_before_print_s"],
        "rest_time_after_retract_s": row["rest_time_after_retract_s"],
        "is_default": bool(row["is_default"]),
        "is_active": bool(row["is_active"]),
        "notes": row["notes"],
        "metadata": _json_loads(row["metadata_json"]),
    }


def create_or_upsert_printer(data: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Printer name is required.")
    brand = data.get("brand")
    model = data.get("model")
    if not brand or not model:
        inferred_brand, inferred_model = _infer_brand_model(name)
        brand = brand or inferred_brand
        model = model or inferred_model

    with _connect(db_path) as conn:
        row = conn.execute("SELECT id FROM printers WHERE lower(name)=lower(?)", (name,)).fetchone()
        if row:
            printer_id = int(row["id"])
            conn.execute(
                """
                UPDATE printers SET
                    name=?, brand=?, model=?, technology=?, xy_resolution_um=?,
                    build_volume_x_mm=?, build_volume_y_mm=?, build_volume_z_mm=?,
                    tilt_release_supported=?, wifi_supported=?, notes=?, metadata_json=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    name,
                    brand,
                    model,
                    data.get("technology"),
                    data.get("xy_resolution_um"),
                    data.get("build_volume_x_mm"),
                    data.get("build_volume_y_mm"),
                    data.get("build_volume_z_mm"),
                    _bool_to_int(data.get("tilt_release_supported")),
                    _bool_to_int(data.get("wifi_supported")),
                    data.get("notes"),
                    _json_dumps(data.get("metadata") or {}),
                    printer_id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO printers (
                    name, brand, model, technology, xy_resolution_um,
                    build_volume_x_mm, build_volume_y_mm, build_volume_z_mm,
                    tilt_release_supported, wifi_supported, notes, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    brand,
                    model,
                    data.get("technology"),
                    data.get("xy_resolution_um"),
                    data.get("build_volume_x_mm"),
                    data.get("build_volume_y_mm"),
                    data.get("build_volume_z_mm"),
                    _bool_to_int(data.get("tilt_release_supported")),
                    _bool_to_int(data.get("wifi_supported")),
                    data.get("notes"),
                    _json_dumps(data.get("metadata") or {}),
                ),
            )
            printer_id = int(cursor.lastrowid)
        conn.commit()
    return get_printer(printer_id, db_path=db_path)


def get_printer(printer_id: int, db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM printers WHERE id=?", (printer_id,)).fetchone()
    if row is None:
        raise ValueError(f"Printer id {printer_id} not found.")
    return _row_to_printer(row)


def list_printers(
    db_path: str | Path | None = None,
    query: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM printers"
    params: list[Any] = []
    if query:
        sql += " WHERE lower(name) LIKE ? OR lower(brand) LIKE ? OR lower(model) LIKE ?"
        q = f"%{query.lower()}%"
        params.extend([q, q, q])
    sql += " ORDER BY name LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 1000)), max(0, offset)])
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_printer(row) for row in rows]


def update_printer(printer_id: int, updates: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    current = get_printer(printer_id, db_path=db_path)
    merged = {**current, **{k: v for k, v in updates.items() if v is not None}}
    merged.pop("id", None)
    return create_or_upsert_printer(merged, db_path=db_path)


def delete_printer(printer_id: int, db_path: str | Path | None = None) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM printers WHERE id=?", (printer_id,))
        conn.commit()
    return cur.rowcount > 0


def create_or_upsert_resin(data: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Resin name is required.")
    brand = data.get("brand") or _infer_brand_model(name)[0]
    with _connect(db_path) as conn:
        row = conn.execute("SELECT id FROM resins WHERE lower(name)=lower(?)", (name,)).fetchone()
        if row:
            resin_id = int(row["id"])
            conn.execute(
                """
                UPDATE resins SET
                    name=?, brand=?, series=?, technical_goal=?, viscosity_cp=?,
                    shore_hardness=?, shrinkage_percent=?, notes=?, metadata_json=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    name,
                    brand,
                    data.get("series"),
                    data.get("technical_goal"),
                    data.get("viscosity_cp"),
                    data.get("shore_hardness"),
                    data.get("shrinkage_percent"),
                    data.get("notes"),
                    _json_dumps(data.get("metadata") or {}),
                    resin_id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO resins (
                    name, brand, series, technical_goal, viscosity_cp,
                    shore_hardness, shrinkage_percent, notes, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    brand,
                    data.get("series"),
                    data.get("technical_goal"),
                    data.get("viscosity_cp"),
                    data.get("shore_hardness"),
                    data.get("shrinkage_percent"),
                    data.get("notes"),
                    _json_dumps(data.get("metadata") or {}),
                ),
            )
            resin_id = int(cursor.lastrowid)
        conn.commit()
    return get_resin(resin_id, db_path=db_path)


def get_resin(resin_id: int, db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM resins WHERE id=?", (resin_id,)).fetchone()
    if row is None:
        raise ValueError(f"Resin id {resin_id} not found.")
    return _row_to_resin(row)


def list_resins(
    db_path: str | Path | None = None,
    query: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM resins"
    params: list[Any] = []
    if query:
        sql += " WHERE lower(name) LIKE ? OR lower(brand) LIKE ? OR lower(series) LIKE ?"
        q = f"%{query.lower()}%"
        params.extend([q, q, q])
    sql += " ORDER BY name LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 1000)), max(0, offset)])
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_resin(row) for row in rows]


def update_resin(resin_id: int, updates: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    current = get_resin(resin_id, db_path=db_path)
    merged = {**current, **{k: v for k, v in updates.items() if v is not None}}
    merged.pop("id", None)
    return create_or_upsert_resin(merged, db_path=db_path)


def delete_resin(resin_id: int, db_path: str | Path | None = None) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM resins WHERE id=?", (resin_id,))
        conn.commit()
    return cur.rowcount > 0


def _resolve_printer_id(
    conn: sqlite3.Connection,
    printer_id: int | None,
    printer_name: str | None,
) -> int:
    if printer_id is not None:
        row = conn.execute("SELECT id FROM printers WHERE id=?", (printer_id,)).fetchone()
        if row is None:
            raise ValueError(f"Printer id {printer_id} not found.")
        return int(printer_id)
    if not printer_name:
        raise ValueError("printer_id or printer_name is required.")
    row = conn.execute("SELECT id FROM printers WHERE lower(name)=lower(?)", (printer_name,)).fetchone()
    if row is None:
        raise ValueError(f"Printer '{printer_name}' not found.")
    return int(row["id"])


def _resolve_resin_id(
    conn: sqlite3.Connection,
    resin_id: int | None,
    resin_name: str | None,
) -> int:
    if resin_id is not None:
        row = conn.execute("SELECT id FROM resins WHERE id=?", (resin_id,)).fetchone()
        if row is None:
            raise ValueError(f"Resin id {resin_id} not found.")
        return int(resin_id)
    if not resin_name:
        raise ValueError("resin_id or resin_name is required.")
    row = conn.execute("SELECT id FROM resins WHERE lower(name)=lower(?)", (resin_name,)).fetchone()
    if row is None:
        raise ValueError(f"Resin '{resin_name}' not found.")
    return int(row["id"])


def create_or_upsert_profile(data: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    profile_name = str(data.get("profile_name", "")).strip()
    if not profile_name:
        raise ValueError("Profile name is required.")

    with _connect(db_path) as conn:
        printer_id = _resolve_printer_id(conn, data.get("printer_id"), data.get("printer_name"))
        resin_id = _resolve_resin_id(conn, data.get("resin_id"), data.get("resin_name"))
        layer_height = data.get("layer_height_mm")
        row = conn.execute(
            """
            SELECT id FROM profiles
            WHERE printer_id=? AND resin_id=? AND profile_name=? AND
                  ((layer_height_mm IS NULL AND ? IS NULL) OR layer_height_mm=?)
            """,
            (printer_id, resin_id, profile_name, layer_height, layer_height),
        ).fetchone()

        if bool(data.get("is_default")):
            conn.execute(
                "UPDATE profiles SET is_default=0, updated_at=CURRENT_TIMESTAMP WHERE printer_id=? AND resin_id=?",
                (printer_id, resin_id),
            )

        if row:
            profile_id = int(row["id"])
            conn.execute(
                """
                UPDATE profiles SET
                    printer_id=?, resin_id=?, profile_name=?, layer_height_mm=?,
                    exposure_s=?, bottom_exposure_s=?, transition_layers=?,
                    tilt_speed_reference_mm_h=?, rest_time_before_print_s=?,
                    rest_time_after_retract_s=?, is_default=?, is_active=?, notes=?,
                    metadata_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    printer_id,
                    resin_id,
                    profile_name,
                    data.get("layer_height_mm"),
                    data.get("exposure_s"),
                    data.get("bottom_exposure_s"),
                    data.get("transition_layers"),
                    data.get("tilt_speed_reference_mm_h"),
                    data.get("rest_time_before_print_s"),
                    data.get("rest_time_after_retract_s"),
                    _bool_to_int(data.get("is_default")),
                    _bool_to_int(data.get("is_active", True)),
                    data.get("notes"),
                    _json_dumps(data.get("metadata") or {}),
                    profile_id,
                ),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO profiles (
                    printer_id, resin_id, profile_name, layer_height_mm,
                    exposure_s, bottom_exposure_s, transition_layers,
                    tilt_speed_reference_mm_h, rest_time_before_print_s,
                    rest_time_after_retract_s, is_default, is_active,
                    notes, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    printer_id,
                    resin_id,
                    profile_name,
                    data.get("layer_height_mm"),
                    data.get("exposure_s"),
                    data.get("bottom_exposure_s"),
                    data.get("transition_layers"),
                    data.get("tilt_speed_reference_mm_h"),
                    data.get("rest_time_before_print_s"),
                    data.get("rest_time_after_retract_s"),
                    _bool_to_int(data.get("is_default")),
                    _bool_to_int(data.get("is_active", True)),
                    data.get("notes"),
                    _json_dumps(data.get("metadata") or {}),
                ),
            )
            profile_id = int(cursor.lastrowid)
        conn.commit()

    return get_profile(profile_id, db_path=db_path)


def get_profile(profile_id: int, db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT p.*, pr.name AS printer_name, r.name AS resin_name
            FROM profiles p
            JOIN printers pr ON pr.id = p.printer_id
            JOIN resins r ON r.id = p.resin_id
            WHERE p.id=?
            """,
            (profile_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Profile id {profile_id} not found.")
    return _row_to_profile(row)


def list_profiles(
    db_path: str | Path | None = None,
    printer_id: int | None = None,
    resin_id: int | None = None,
    query: str | None = None,
    active_only: bool = True,
    limit: int = 300,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = """
        SELECT p.*, pr.name AS printer_name, r.name AS resin_name
        FROM profiles p
        JOIN printers pr ON pr.id = p.printer_id
        JOIN resins r ON r.id = p.resin_id
        WHERE 1=1
    """
    params: list[Any] = []
    if printer_id is not None:
        sql += " AND p.printer_id=?"
        params.append(printer_id)
    if resin_id is not None:
        sql += " AND p.resin_id=?"
        params.append(resin_id)
    if query:
        q = f"%{query.lower()}%"
        sql += " AND (lower(pr.name) LIKE ? OR lower(r.name) LIKE ? OR lower(p.profile_name) LIKE ?)"
        params.extend([q, q, q])
    if active_only:
        sql += " AND p.is_active=1"
    sql += " ORDER BY pr.name, r.name, p.is_default DESC, p.profile_name LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 2000)), max(0, offset)])

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_profile(row) for row in rows]


def update_profile(profile_id: int, updates: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    current = get_profile(profile_id, db_path=db_path)
    merged = {**current, **{k: v for k, v in updates.items() if v is not None}}
    merged.pop("id", None)
    merged.pop("printer_name", None)
    merged.pop("resin_name", None)
    return create_or_upsert_profile(merged, db_path=db_path)


def delete_profile(profile_id: int, db_path: str | Path | None = None) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM profiles WHERE id=?", (profile_id,))
        conn.commit()
    return cur.rowcount > 0


def find_profile_by_names(
    printer_name: str,
    resin_name: str,
    layer_height_mm: float | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        sql = """
            SELECT p.*, pr.name AS printer_name, r.name AS resin_name,
                   r.brand AS resin_brand, r.series AS resin_series,
                   r.technical_goal, r.viscosity_cp, r.shore_hardness, r.shrinkage_percent,
                   r.notes AS resin_notes, pr.notes AS printer_notes
            FROM profiles p
            JOIN printers pr ON pr.id = p.printer_id
            JOIN resins r ON r.id = p.resin_id
            WHERE lower(pr.name)=lower(?) AND lower(r.name)=lower(?) AND p.is_active=1
        """
        params: list[Any] = [printer_name, resin_name]
        if layer_height_mm is None:
            sql += " ORDER BY p.is_default DESC, p.layer_height_mm ASC LIMIT 1"
        else:
            sql += " ORDER BY ABS(COALESCE(p.layer_height_mm, 0.05) - ?) ASC, p.is_default DESC LIMIT 1"
            params.append(layer_height_mm)
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    out = _row_to_profile(row)
    out.update(
        {
            "name": out["profile_name"],
            "printer": out["printer_name"],
            "resin_type": out["resin_name"],
            "brand": row["resin_brand"],
            "series": row["resin_series"],
            "technical_goal": row["technical_goal"],
            "viscosity_cp": row["viscosity_cp"],
            "shore_hardness": row["shore_hardness"],
            "shrinkage_percent": row["shrinkage_percent"],
            "notes": out["notes"] or row["resin_notes"] or row["printer_notes"],
        }
    )
    return out


def list_resin_names_for_printer(
    printer_name: str,
    db_path: str | Path | None = None,
) -> list[str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT r.name
            FROM profiles p
            JOIN printers pr ON pr.id = p.printer_id
            JOIN resins r ON r.id = p.resin_id
            WHERE lower(pr.name)=lower(?) AND p.is_active=1
            ORDER BY r.name
            """,
            (printer_name,),
        ).fetchall()
    return [str(row["name"]) for row in rows]


def export_catalog(db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        printer_rows = conn.execute("SELECT * FROM printers ORDER BY name").fetchall()
        resin_rows = conn.execute("SELECT * FROM resins ORDER BY name").fetchall()
        profile_rows = conn.execute(
            """
            SELECT p.*, pr.name AS printer_name, r.name AS resin_name
            FROM profiles p
            JOIN printers pr ON pr.id=p.printer_id
            JOIN resins r ON r.id=p.resin_id
            ORDER BY pr.name, r.name, p.profile_name
            """
        ).fetchall()
    return {
        "schema_version": "catalog-1",
        "printers": [_row_to_printer(r) for r in printer_rows],
        "resins": [_row_to_resin(r) for r in resin_rows],
        "profiles": [_row_to_profile(r) for r in profile_rows],
    }


def import_catalog(payload: dict[str, Any], db_path: str | Path | None = None) -> dict[str, int]:
    replace_existing = bool(payload.get("replace_existing"))
    printers = payload.get("printers", []) or []
    resins = payload.get("resins", []) or []
    profiles = payload.get("profiles", []) or []

    if replace_existing:
        with _connect(db_path) as conn:
            conn.execute("DELETE FROM profiles")
            conn.execute("DELETE FROM resins")
            conn.execute("DELETE FROM printers")
            conn.commit()

    printer_count = 0
    resin_count = 0
    profile_count = 0

    for item in printers:
        create_or_upsert_printer(dict(item), db_path=db_path)
        printer_count += 1

    for item in resins:
        create_or_upsert_resin(dict(item), db_path=db_path)
        resin_count += 1

    for item in profiles:
        create_or_upsert_profile(dict(item), db_path=db_path)
        profile_count += 1

    return {
        "printers_upserted": printer_count,
        "resins_upserted": resin_count,
        "profiles_upserted": profile_count,
    }


def seed_catalog_from_legacy_json(
    db_path: str | Path | None = None,
    json_path: str | Path | None = None,
) -> dict[str, int]:
    path = Path(json_path) if json_path else LEGACY_PROFILES_PATH
    if not path.exists():
        return {"printers_upserted": 0, "resins_upserted": 0, "profiles_upserted": 0}

    with _connect(db_path) as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0])
    if count > 0:
        return {"printers_upserted": 0, "resins_upserted": 0, "profiles_upserted": 0}

    data = json.loads(path.read_text(encoding="utf-8"))
    profiles = data.get("profiles", [])

    printer_names: set[str] = set()
    resin_names: set[str] = set()
    profile_count = 0

    for profile in profiles:
        printer_name = str(profile.get("printer", "")).strip()
        resin_name = str(profile.get("resin_type", "")).strip()
        if not printer_name or not resin_name:
            continue

        printer_payload = {
            "name": printer_name,
            "brand": profile.get("printer_brand") or _infer_brand_model(printer_name)[0],
            "model": profile.get("printer_model") or _infer_brand_model(printer_name)[1],
            "technology": "MSLA",
            "xy_resolution_um": profile.get("xy_resolution_um") or 18,
            "tilt_release_supported": True,
            "wifi_supported": True,
            "notes": profile.get("printer_notes"),
            "metadata": {},
        }
        resin_payload = {
            "name": resin_name,
            "brand": profile.get("brand") or _infer_brand_model(resin_name)[0],
            "series": profile.get("series"),
            "technical_goal": profile.get("technical_goal"),
            "viscosity_cp": profile.get("viscosity_cp"),
            "shore_hardness": profile.get("shore_hardness"),
            "shrinkage_percent": profile.get("shrinkage_percent"),
            "notes": profile.get("notes"),
            "metadata": {
                "exposure_range_s": profile.get("exposure_range_s"),
            },
        }
        create_or_upsert_printer(printer_payload, db_path=db_path)
        create_or_upsert_resin(resin_payload, db_path=db_path)
        printer_names.add(printer_name)
        resin_names.add(resin_name)

        profile_payload = {
            "printer_name": printer_name,
            "resin_name": resin_name,
            "profile_name": profile.get("name") or f"{resin_name} Profile",
            "layer_height_mm": profile.get("layer_height_mm"),
            "exposure_s": profile.get("exposure_s"),
            "bottom_exposure_s": profile.get("bottom_exposure_s"),
            "transition_layers": profile.get("transition_layers"),
            "tilt_speed_reference_mm_h": profile.get("tilt_speed_reference_mm_h"),
            "rest_time_before_print_s": profile.get("rest_time_before_print_s") or 2.0,
            "rest_time_after_retract_s": profile.get("rest_time_after_retract_s") or 0.5,
            "is_default": profile.get("is_default", False),
            "is_active": True,
            "notes": profile.get("notes"),
            "metadata": {
                "legacy_schema_version": data.get("schema_version"),
                "legacy_updated_at": data.get("updated_at"),
                "legacy_profile": profile,
            },
        }
        create_or_upsert_profile(profile_payload, db_path=db_path)
        profile_count += 1

    return {
        "printers_upserted": len(printer_names),
        "resins_upserted": len(resin_names),
        "profiles_upserted": profile_count,
    }


def list_legacy_profile_view(
    db_path: str | Path | None = None,
    resin_name: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT p.*, pr.name AS printer_name, r.name AS resin_name,
               r.brand, r.series, r.technical_goal, r.viscosity_cp, r.shore_hardness, r.shrinkage_percent,
               r.notes AS resin_notes
        FROM profiles p
        JOIN printers pr ON pr.id = p.printer_id
        JOIN resins r ON r.id = p.resin_id
        WHERE p.is_active=1
    """
    params: list[Any] = []
    if resin_name:
        sql += " AND lower(r.name)=lower(?)"
        params.append(resin_name)
    sql += " ORDER BY pr.name, r.name, p.is_default DESC, p.profile_name"

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    output: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "name": row["profile_name"],
            "printer": row["printer_name"],
            "resin_type": row["resin_name"],
            "brand": row["brand"],
            "series": row["series"],
            "technical_goal": row["technical_goal"],
            "viscosity_cp": row["viscosity_cp"],
            "shore_hardness": row["shore_hardness"],
            "shrinkage_percent": row["shrinkage_percent"],
            "layer_height_mm": row["layer_height_mm"],
            "exposure_s": row["exposure_s"],
            "bottom_exposure_s": row["bottom_exposure_s"],
            "transition_layers": row["transition_layers"],
            "tilt_speed_reference_mm_h": row["tilt_speed_reference_mm_h"],
            "rest_time_before_print_s": row["rest_time_before_print_s"],
            "rest_time_after_retract_s": row["rest_time_after_retract_s"],
            "is_default": bool(row["is_default"]),
            "notes": row["notes"] or row["resin_notes"],
        }
        output.append(item)
    return output


def import_catalog_csv(
    entity: str,
    csv_text: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    normalized = entity.strip().lower()
    if normalized not in {"printers", "resins", "profiles"}:
        raise ValueError("entity must be one of: printers, resins, profiles")

    reader = csv.DictReader(io.StringIO(csv_text))
    rows = [dict(row) for row in reader]
    upserted = 0
    failed = 0
    notes: list[str] = []

    for idx, row in enumerate(rows, start=1):
        try:
            payload = _normalize_csv_row(normalized, row)
            if normalized == "printers":
                create_or_upsert_printer(payload, db_path=db_path)
            elif normalized == "resins":
                create_or_upsert_resin(payload, db_path=db_path)
            else:
                create_or_upsert_profile(payload, db_path=db_path)
            upserted += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            notes.append(f"Row {idx} failed: {exc}")

    return {
        "entity": normalized,
        "rows_processed": len(rows),
        "upserted": upserted,
        "failed": failed,
        "notes": notes[:100],
    }


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _int_to_bool(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _normalize_csv_row(entity: str, row: dict[str, Any]) -> dict[str, Any]:
    clean = {
        str(k).strip(): (None if v is None or str(v).strip() == "" else str(v).strip())
        for k, v in row.items()
    }

    if entity == "printers":
        return {
            "name": clean.get("name"),
            "brand": clean.get("brand"),
            "model": clean.get("model"),
            "technology": clean.get("technology"),
            "xy_resolution_um": _to_int(clean.get("xy_resolution_um")),
            "build_volume_x_mm": _to_float(clean.get("build_volume_x_mm")),
            "build_volume_y_mm": _to_float(clean.get("build_volume_y_mm")),
            "build_volume_z_mm": _to_float(clean.get("build_volume_z_mm")),
            "tilt_release_supported": _to_bool(clean.get("tilt_release_supported")),
            "wifi_supported": _to_bool(clean.get("wifi_supported")),
            "notes": clean.get("notes"),
            "metadata": _to_json_dict(clean.get("metadata")),
        }

    if entity == "resins":
        return {
            "name": clean.get("name"),
            "brand": clean.get("brand"),
            "series": clean.get("series"),
            "technical_goal": clean.get("technical_goal"),
            "viscosity_cp": _to_float(clean.get("viscosity_cp")),
            "shore_hardness": clean.get("shore_hardness"),
            "shrinkage_percent": _to_float(clean.get("shrinkage_percent")),
            "notes": clean.get("notes"),
            "metadata": _to_json_dict(clean.get("metadata")),
        }

    return {
        "printer_id": _to_int(clean.get("printer_id")),
        "resin_id": _to_int(clean.get("resin_id")),
        "printer_name": clean.get("printer_name"),
        "resin_name": clean.get("resin_name"),
        "profile_name": clean.get("profile_name"),
        "layer_height_mm": _to_float(clean.get("layer_height_mm")),
        "exposure_s": _to_float(clean.get("exposure_s")),
        "bottom_exposure_s": _to_float(clean.get("bottom_exposure_s")),
        "transition_layers": _to_int(clean.get("transition_layers")),
        "tilt_speed_reference_mm_h": _to_float(clean.get("tilt_speed_reference_mm_h")),
        "rest_time_before_print_s": _to_float(clean.get("rest_time_before_print_s")),
        "rest_time_after_retract_s": _to_float(clean.get("rest_time_after_retract_s")),
        "is_default": _to_bool(clean.get("is_default")) if clean.get("is_default") is not None else False,
        "is_active": _to_bool(clean.get("is_active")) if clean.get("is_active") is not None else True,
        "notes": clean.get("notes"),
        "metadata": _to_json_dict(clean.get("metadata")),
    }


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    return None


def _to_json_dict(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
