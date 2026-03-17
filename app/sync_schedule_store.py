from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SYNC_SCHEDULE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sync_schedules.db"

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sync_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    replace_existing INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sync_schedules_enabled ON sync_schedules(enabled);
CREATE INDEX IF NOT EXISTS idx_sync_schedules_updated_at ON sync_schedules(updated_at DESC);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def init_sync_schedule_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_SYNC_SCHEDULE_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
    return path


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = init_sync_schedule_store(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _encode_json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=True, separators=(",", ":"))


def _decode_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _row_to_schedule(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "source": row["source"],
        "interval_seconds": row["interval_seconds"],
        "enabled": bool(row["enabled"]),
        "replace_existing": bool(row["replace_existing"]),
        "payload": _decode_json(row["payload_json"]),
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_or_upsert_sync_schedule(
    data: dict[str, Any],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Schedule name is required.")
    source = str(data.get("source", "")).strip()
    if not source:
        raise ValueError("Schedule source is required.")
    interval_seconds = int(data.get("interval_seconds") or 0)
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0.")

    with _connect(db_path) as conn:
        row = conn.execute("SELECT id FROM sync_schedules WHERE lower(name)=lower(?)", (name,)).fetchone()
        if row:
            schedule_id = int(row["id"])
            conn.execute(
                """
                UPDATE sync_schedules
                SET source=?, interval_seconds=?, enabled=?, replace_existing=?,
                    payload_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    source,
                    interval_seconds,
                    1 if bool(data.get("enabled", True)) else 0,
                    1 if bool(data.get("replace_existing", False)) else 0,
                    _encode_json(data.get("payload")),
                    schedule_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO sync_schedules (
                    name, source, interval_seconds, enabled, replace_existing, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    source,
                    interval_seconds,
                    1 if bool(data.get("enabled", True)) else 0,
                    1 if bool(data.get("replace_existing", False)) else 0,
                    _encode_json(data.get("payload")),
                ),
            )
            schedule_id = int(cur.lastrowid)
        conn.commit()
    return get_sync_schedule(schedule_id, db_path=db_path)


def get_sync_schedule(schedule_id: int, db_path: str | Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM sync_schedules WHERE id=?", (schedule_id,)).fetchone()
    if row is None:
        raise ValueError(f"Schedule id {schedule_id} not found.")
    return _row_to_schedule(row)


def list_sync_schedules(
    *,
    enabled_only: bool = False,
    limit: int = 200,
    offset: int = 0,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM sync_schedules WHERE 1=1"
    params: list[Any] = []
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 1000)), max(0, offset)])
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_schedule(row) for row in rows]


def update_sync_schedule(
    schedule_id: int,
    updates: dict[str, Any],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    current = get_sync_schedule(schedule_id, db_path=db_path)
    merged = {**current, **{k: v for k, v in updates.items() if v is not None}}
    merged.pop("id", None)
    merged.pop("created_at", None)
    merged.pop("updated_at", None)
    merged.pop("last_run_at", None)
    merged.pop("last_status", None)
    merged.pop("last_error", None)
    merged["name"] = current["name"] if updates.get("name") is None else updates["name"]
    return create_or_upsert_sync_schedule(merged, db_path=db_path)


def delete_sync_schedule(schedule_id: int, db_path: str | Path | None = None) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("DELETE FROM sync_schedules WHERE id=?", (schedule_id,))
        conn.commit()
    return cur.rowcount > 0


def mark_sync_schedule_run(
    schedule_id: int,
    *,
    status: str,
    error: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE sync_schedules
            SET last_run_at=?, last_status=?, last_error=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (_utc_now_iso(), status, error, schedule_id),
        )
        conn.commit()


def list_due_sync_schedules(
    *,
    now: datetime | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    now_dt = now or datetime.now(timezone.utc)
    schedules = list_sync_schedules(enabled_only=True, db_path=db_path, limit=1000, offset=0)
    due: list[dict[str, Any]] = []
    for item in schedules:
        last = _parse_iso(item.get("last_run_at"))
        if last is None:
            due.append(item)
            continue
        elapsed = (now_dt - last).total_seconds()
        if elapsed >= float(item["interval_seconds"]):
            due.append(item)
    return due
