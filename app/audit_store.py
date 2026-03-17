from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.catalog_store import export_catalog, import_catalog

DEFAULT_AUDIT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit_log.db"

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events(action);

CREATE TABLE IF NOT EXISTS catalog_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor TEXT NOT NULL,
    note TEXT,
    source_action TEXT,
    snapshot_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalog_versions_created_at ON catalog_versions(created_at DESC);
"""


def init_audit_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_AUDIT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
    return path


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = init_audit_store(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def log_audit_event(
    *,
    actor: str,
    role: str,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    status: str = "success",
    details: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO audit_events (
                actor, role, action, resource_type, resource_id, status, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor,
                role,
                action,
                resource_type,
                resource_id,
                status,
                json.dumps(details or {}, ensure_ascii=True, separators=(",", ":")),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_audit_events(
    *,
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM audit_events WHERE 1=1"
    params: list[Any] = []
    if action:
        sql += " AND action=?"
        params.append(action)
    if resource_type:
        sql += " AND resource_type=?"
        params.append(resource_type)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 1000)), max(0, offset)])

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        details_text = row["details_json"]
        details: dict[str, Any] = {}
        if details_text:
            try:
                parsed = json.loads(details_text)
                if isinstance(parsed, dict):
                    details = parsed
            except json.JSONDecodeError:
                details = {}
        out.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "role": row["role"],
                "action": row["action"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "status": row["status"],
                "details": details,
            }
        )
    return out


def create_catalog_version(
    *,
    actor: str,
    note: str | None = None,
    source_action: str | None = None,
    catalog_db_path: str | Path | None = None,
    audit_db_path: str | Path | None = None,
) -> dict[str, Any]:
    snapshot = export_catalog(db_path=catalog_db_path)
    payload = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"))
    with _connect(audit_db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO catalog_versions (actor, note, source_action, snapshot_json)
            VALUES (?, ?, ?, ?)
            """,
            (actor, note, source_action, payload),
        )
        conn.commit()
        version_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM catalog_versions WHERE id=?", (version_id,)).fetchone()
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "actor": row["actor"],
        "note": row["note"],
        "source_action": row["source_action"],
    }


def list_catalog_versions(
    *,
    limit: int = 100,
    offset: int = 0,
    audit_db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    with _connect(audit_db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, actor, note, source_action
            FROM catalog_versions
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (max(1, min(limit, 500)), max(0, offset)),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "actor": row["actor"],
            "note": row["note"],
            "source_action": row["source_action"],
        }
        for row in rows
    ]


def get_catalog_version(
    version_id: int,
    *,
    include_snapshot: bool = True,
    audit_db_path: str | Path | None = None,
) -> dict[str, Any]:
    with _connect(audit_db_path) as conn:
        row = conn.execute("SELECT * FROM catalog_versions WHERE id=?", (version_id,)).fetchone()
    if row is None:
        raise ValueError(f"Catalog version {version_id} not found.")

    out = {
        "id": row["id"],
        "created_at": row["created_at"],
        "actor": row["actor"],
        "note": row["note"],
        "source_action": row["source_action"],
    }
    if include_snapshot:
        try:
            snapshot = json.loads(row["snapshot_json"])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Catalog version {version_id} contains invalid snapshot JSON.") from exc
        out["snapshot"] = snapshot
    return out


def restore_catalog_version(
    version_id: int,
    *,
    actor: str,
    catalog_db_path: str | Path | None = None,
    audit_db_path: str | Path | None = None,
) -> dict[str, Any]:
    version = get_catalog_version(version_id, include_snapshot=True, audit_db_path=audit_db_path)
    snapshot = version.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError(f"Catalog version {version_id} has no valid snapshot payload.")

    result = import_catalog(
        {
            "replace_existing": True,
            "printers": snapshot.get("printers", []),
            "resins": snapshot.get("resins", []),
            "profiles": snapshot.get("profiles", []),
        },
        db_path=catalog_db_path,
    )
    log_audit_event(
        actor=actor,
        role="admin",
        action="catalog.restore_version",
        resource_type="catalog_version",
        resource_id=str(version_id),
        details=result,
        db_path=audit_db_path,
    )
    return result
