from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from app.models import FeedbackRecord

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "feedback_history.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS feedback_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    printer TEXT NOT NULL,
    resin_type TEXT NOT NULL,
    text TEXT NOT NULL,
    layer_height_mm REAL,
    exposure_s REAL,
    ambient_temp_c REAL,
    sponsored INTEGER,
    created_at TEXT,
    inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feedback_printer_resin ON feedback_records(printer, resin_type);
CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback_records(created_at);
CREATE INDEX IF NOT EXISTS idx_feedback_source ON feedback_records(source);
"""


def init_feedback_store(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
    return path


def save_feedback_records(
    records: list[FeedbackRecord],
    db_path: str | Path | None = None,
) -> int:
    if not records:
        return 0

    path = init_feedback_store(db_path)
    rows = [
        (
            record.source,
            record.printer,
            record.resin_type,
            record.text,
            record.layer_height_mm,
            record.exposure_s,
            record.ambient_temp_c,
            _bool_to_int(record.sponsored),
            record.created_at.isoformat() if record.created_at else None,
        )
        for record in records
    ]

    with sqlite3.connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO feedback_records (
                source,
                printer,
                resin_type,
                text,
                layer_height_mm,
                exposure_s,
                ambient_temp_c,
                sponsored,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def query_feedback_records(
    *,
    db_path: str | Path | None = None,
    printer: str | None = None,
    resin_type: str | None = None,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[FeedbackRecord]:
    path = init_feedback_store(db_path)
    where: list[str] = []
    params: list[object] = []

    if printer:
        where.append("printer = ?")
        params.append(printer)
    if resin_type:
        where.append("resin_type = ?")
        params.append(resin_type)
    if source:
        where.append("source = ?")
        params.append(source)
    if date_from:
        where.append("created_at IS NOT NULL AND date(created_at) >= date(?)")
        params.append(date_from.isoformat())
    if date_to:
        where.append("created_at IS NOT NULL AND date(created_at) <= date(?)")
        params.append(date_to.isoformat())

    sql = """
        SELECT source, printer, resin_type, text, layer_height_mm, exposure_s, ambient_temp_c, sponsored, created_at
        FROM feedback_records
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(created_at, inserted_at) DESC LIMIT ? OFFSET ?"
    params.extend([max(1, min(limit, 1000)), max(0, offset)])

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    records: list[FeedbackRecord] = []
    for row in rows:
        records.append(
            FeedbackRecord(
                source=row["source"],
                printer=row["printer"],
                resin_type=row["resin_type"],
                text=row["text"],
                layer_height_mm=row["layer_height_mm"],
                exposure_s=row["exposure_s"],
                ambient_temp_c=row["ambient_temp_c"],
                sponsored=_int_to_bool(row["sponsored"]),
                created_at=_to_date(row["created_at"]),
            )
        )
    return records


def count_feedback_records(
    *,
    db_path: str | Path | None = None,
    printer: str | None = None,
    resin_type: str | None = None,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> int:
    path = init_feedback_store(db_path)
    where: list[str] = []
    params: list[object] = []

    if printer:
        where.append("printer = ?")
        params.append(printer)
    if resin_type:
        where.append("resin_type = ?")
        params.append(resin_type)
    if source:
        where.append("source = ?")
        params.append(source)
    if date_from:
        where.append("created_at IS NOT NULL AND date(created_at) >= date(?)")
        params.append(date_from.isoformat())
    if date_to:
        where.append("created_at IS NOT NULL AND date(created_at) <= date(?)")
        params.append(date_to.isoformat())

    sql = "SELECT COUNT(*) FROM feedback_records"
    if where:
        sql += " WHERE " + " AND ".join(where)

    with sqlite3.connect(path) as conn:
        value = conn.execute(sql, params).fetchone()[0]
    return int(value or 0)


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _int_to_bool(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None

