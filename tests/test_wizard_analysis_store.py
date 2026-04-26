from __future__ import annotations

import time

from app.wizard_analysis_store import (
    cleanup_wizard_analysis_progress,
    get_wizard_analysis_progress,
    init_wizard_analysis_store,
    upsert_wizard_analysis_progress,
)


def test_wizard_analysis_progress_preserves_cancel_requested_between_updates(tmp_path):
    db_path = init_wizard_analysis_store(tmp_path / "wizard_analysis_progress.db")

    upsert_wizard_analysis_progress(
        job_id="job-1",
        status="running",
        stage="detect_islands",
        message="Scanning sliced layers.",
        cancel_requested=False,
        error=None,
        started_at_ts=10.0,
        stage_started_at_ts=10.0,
        updated_at_ts=10.0,
        performance_ms={"cross_section": 12.0},
        stage_timings_ms={"cross_section": 12.0},
        db_path=db_path,
    )
    upsert_wizard_analysis_progress(
        job_id="job-1",
        status="cancelling",
        stage="detect_islands",
        message="Cancellation requested.",
        cancel_requested=True,
        error=None,
        started_at_ts=10.0,
        stage_started_at_ts=10.0,
        updated_at_ts=11.0,
        performance_ms={"cross_section": 12.0},
        stage_timings_ms={"cross_section": 12.0},
        db_path=db_path,
    )
    upsert_wizard_analysis_progress(
        job_id="job-1",
        status="cancelling",
        stage="detect_islands",
        message="Waiting for the next cancellation checkpoint.",
        cancel_requested=None,
        error=None,
        started_at_ts=10.0,
        stage_started_at_ts=10.0,
        updated_at_ts=12.0,
        performance_ms={"cross_section": 12.0},
        stage_timings_ms={"cross_section": 12.0, "detect_islands": 5.0},
        db_path=db_path,
    )

    progress = get_wizard_analysis_progress("job-1", db_path=db_path)
    assert progress is not None
    assert progress["cancel_requested"] is True
    assert progress["message"] == "Waiting for the next cancellation checkpoint."
    assert progress["stage_timings_ms"]["detect_islands"] == 5.0


def test_wizard_analysis_progress_cleanup_removes_stale_rows(tmp_path):
    db_path = init_wizard_analysis_store(tmp_path / "wizard_analysis_progress.db")
    now = time.time()

    upsert_wizard_analysis_progress(
        job_id="stale-job",
        status="completed",
        stage="completed",
        message="Done.",
        cancel_requested=False,
        error=None,
        started_at_ts=now - 120.0,
        stage_started_at_ts=now - 120.0,
        updated_at_ts=now - 120.0,
        performance_ms={"total": 10.0},
        stage_timings_ms={"completed": 10.0},
        db_path=db_path,
    )
    upsert_wizard_analysis_progress(
        job_id="fresh-job",
        status="running",
        stage="cross_section",
        message="Working.",
        cancel_requested=False,
        error=None,
        started_at_ts=now,
        stage_started_at_ts=now,
        updated_at_ts=now,
        performance_ms={},
        stage_timings_ms={},
        db_path=db_path,
    )

    deleted = cleanup_wizard_analysis_progress(max_age_seconds=60, db_path=db_path)

    assert deleted == 1
    assert get_wizard_analysis_progress("stale-job", db_path=db_path) is None
    assert get_wizard_analysis_progress("fresh-job", db_path=db_path) is not None