from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_JOB_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    metadata_json TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def init_job_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_JOB_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
    return path


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = init_job_store(db_path)
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


def upsert_job(record: dict[str, Any], db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, type, status, actor, metadata_json, result_json, error,
                created_at, started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type=excluded.type,
                status=excluded.status,
                actor=excluded.actor,
                metadata_json=excluded.metadata_json,
                result_json=excluded.result_json,
                error=excluded.error,
                created_at=excluded.created_at,
                started_at=excluded.started_at,
                completed_at=excluded.completed_at
            """,
            (
                record["id"],
                record["type"],
                record["status"],
                record["actor"],
                _encode_json(record.get("metadata")),
                _encode_json(record.get("result")),
                record.get("error"),
                record["created_at"],
                record.get("started_at"),
                record.get("completed_at"),
            ),
        )
        conn.commit()


def get_job(job_id: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "actor": row["actor"],
        "metadata": _decode_json(row["metadata_json"]),
        "result": _decode_json(row["result_json"]) if row["result_json"] else None,
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


def list_jobs(
    *,
    limit: int = 100,
    offset: int = 0,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (max(1, min(limit, 1000)), max(0, offset)),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "type": row["type"],
            "status": row["status"],
            "actor": row["actor"],
            "metadata": _decode_json(row["metadata_json"]),
            "result": _decode_json(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }
        for row in rows
    ]


def count_jobs(db_path: str | Path | None = None) -> dict[str, int]:
    with _connect(db_path) as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        by_status_rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"
        ).fetchall()
    by_status = {str(row["status"]): int(row["c"]) for row in by_status_rows}
    return {"total": total, **by_status}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def cleanup_jobs(
    *,
    max_age_seconds: int,
    keep_latest: int = 1000,
    db_path: str | Path | None = None,
) -> dict[str, int]:
    terminal = {"succeeded", "failed", "cancelled"}
    now = datetime.now(timezone.utc)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, status, created_at, completed_at
            FROM jobs
            ORDER BY created_at DESC
            """
        ).fetchall()

        keep_latest = max(0, keep_latest)
        keep_ids = {str(row["id"]) for row in rows[:keep_latest]}
        delete_ids: list[str] = []
        for row in rows:
            job_id = str(row["id"])
            if job_id in keep_ids:
                continue
            status = str(row["status"])
            if status not in terminal:
                continue
            completed_at = _parse_iso(row["completed_at"]) or _parse_iso(row["created_at"])
            if completed_at is None:
                continue
            age_seconds = (now - completed_at).total_seconds()
            if age_seconds >= max(0, max_age_seconds):
                delete_ids.append(job_id)

        for job_id in delete_ids:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()

    return {"deleted": len(delete_ids), "remaining": max(0, len(rows) - len(delete_ids))}
