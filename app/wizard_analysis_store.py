from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_WIZARD_ANALYSIS_DB_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "wizard_analysis_progress.db"
)

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS wizard_analysis_progress (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT,
    message TEXT NOT NULL,
    cancel_requested INTEGER,
    error TEXT,
    started_at_ts REAL NOT NULL,
    stage_started_at_ts REAL NOT NULL,
    updated_at_ts REAL NOT NULL,
    performance_json TEXT NOT NULL,
    stage_timings_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wizard_analysis_progress_updated_at
ON wizard_analysis_progress(updated_at_ts DESC);
"""


def init_wizard_analysis_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_WIZARD_ANALYSIS_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
    return path


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = init_wizard_analysis_store(db_path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _encode_json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=True, separators=(",", ":"))


def _decode_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def upsert_wizard_analysis_progress(
    *,
    job_id: str,
    status: str,
    stage: str | None,
    message: str,
    cancel_requested: bool | None,
    error: str | None,
    started_at_ts: float,
    stage_started_at_ts: float,
    updated_at_ts: float,
    performance_ms: dict[str, float] | None,
    stage_timings_ms: dict[str, float] | None,
    db_path: str | Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO wizard_analysis_progress (
                job_id,
                status,
                stage,
                message,
                cancel_requested,
                error,
                started_at_ts,
                stage_started_at_ts,
                updated_at_ts,
                performance_json,
                stage_timings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status=excluded.status,
                stage=excluded.stage,
                message=excluded.message,
                cancel_requested=COALESCE(
                    excluded.cancel_requested,
                    wizard_analysis_progress.cancel_requested
                ),
                error=excluded.error,
                started_at_ts=excluded.started_at_ts,
                stage_started_at_ts=excluded.stage_started_at_ts,
                updated_at_ts=excluded.updated_at_ts,
                performance_json=excluded.performance_json,
                stage_timings_json=excluded.stage_timings_json
            """,
            (
                job_id,
                status,
                stage,
                message,
                None if cancel_requested is None else int(cancel_requested),
                error,
                float(started_at_ts),
                float(stage_started_at_ts),
                float(updated_at_ts),
                _encode_json(performance_ms),
                _encode_json(stage_timings_ms),
            ),
        )
        conn.commit()


def get_wizard_analysis_progress(
    job_id: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM wizard_analysis_progress WHERE job_id=?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "job_id": row["job_id"],
        "status": row["status"],
        "stage": row["stage"],
        "message": row["message"],
        "cancel_requested": bool(row["cancel_requested"]),
        "error": row["error"],
        "started_at_ts": float(row["started_at_ts"]),
        "stage_started_at_ts": float(row["stage_started_at_ts"]),
        "updated_at_ts": float(row["updated_at_ts"]),
        "performance_ms": _decode_json(row["performance_json"]),
        "stage_timings_ms": _decode_json(row["stage_timings_json"]),
    }


def cleanup_wizard_analysis_progress(
    *,
    max_age_seconds: int,
    db_path: str | Path | None = None,
) -> int:
    cutoff_ts = time.time() - max(0, max_age_seconds)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM wizard_analysis_progress WHERE updated_at_ts < ?",
            (cutoff_ts,),
        )
        conn.commit()
    return max(0, cursor.rowcount)