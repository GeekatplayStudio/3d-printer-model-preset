from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_WIZARD_ARTIFACT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "wizard_artifacts.db"

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS wizard_artifacts (
    artifact_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    download_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    created_at_ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wizard_artifacts_created_at
ON wizard_artifacts(created_at_ts DESC);
"""


def init_wizard_artifact_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_WIZARD_ARTIFACT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
    return path


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = init_wizard_artifact_store(db_path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def create_wizard_artifact(
    *,
    artifact_id: str,
    path: str | Path,
    download_name: str,
    media_type: str,
    created_at_ts: float | None = None,
    db_path: str | Path | None = None,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO wizard_artifacts (
                artifact_id,
                path,
                download_name,
                media_type,
                created_at_ts
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                str(path),
                download_name,
                media_type,
                float(time.time() if created_at_ts is None else created_at_ts),
            ),
        )
        conn.commit()


def get_wizard_artifact(
    artifact_id: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM wizard_artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "artifact_id": row["artifact_id"],
        "path": row["path"],
        "download_name": row["download_name"],
        "media_type": row["media_type"],
        "created_at": float(row["created_at_ts"]),
    }


def delete_wizard_artifact(
    artifact_id: str,
    db_path: str | Path | None = None,
) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM wizard_artifacts WHERE artifact_id=?",
            (artifact_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def cleanup_wizard_artifacts(
    *,
    max_age_seconds: int,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    cutoff_ts = time.time() - max(0, max_age_seconds)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM wizard_artifacts WHERE created_at_ts < ?",
            (cutoff_ts,),
        ).fetchall()
        if rows:
            conn.executemany(
                "DELETE FROM wizard_artifacts WHERE artifact_id=?",
                [(row["artifact_id"],) for row in rows],
            )
            conn.commit()
    return [
        {
            "artifact_id": row["artifact_id"],
            "path": row["path"],
            "download_name": row["download_name"],
            "media_type": row["media_type"],
            "created_at": float(row["created_at_ts"]),
        }
        for row in rows
    ]