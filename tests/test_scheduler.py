from __future__ import annotations

import time

from app.scheduler import TechnicalSyncScheduler
from app.sync_schedule_store import (
    create_or_upsert_sync_schedule,
    get_sync_schedule,
    init_sync_schedule_store,
    mark_sync_schedule_run,
)


def test_scheduler_tick_once_submits_and_marks_failed(tmp_path):
    db_path = tmp_path / "sync_schedules.db"
    init_sync_schedule_store(db_path=db_path)

    ok = create_or_upsert_sync_schedule(
        {
            "name": "ok",
            "source": "ok-source",
            "interval_seconds": 1,
            "enabled": True,
            "replace_existing": False,
            "payload": {"printers": [{"name": "A"}]},
        },
        db_path=db_path,
    )
    bad = create_or_upsert_sync_schedule(
        {
            "name": "bad",
            "source": "bad-source",
            "interval_seconds": 1,
            "enabled": True,
            "replace_existing": False,
            "payload": {"printers": [{"name": "B"}]},
        },
        db_path=db_path,
    )

    submitted_ids: list[int] = []

    def submit(schedule: dict) -> None:
        schedule_id = int(schedule["id"])
        if schedule["name"] == "bad":
            raise RuntimeError("expected test failure")
        submitted_ids.append(schedule_id)

    scheduler = TechnicalSyncScheduler(
        schedule_db_path=db_path,
        submit_schedule_fn=submit,
        poll_seconds=0.5,
    )
    result = scheduler.tick_once()

    assert result == {"due": 2, "submitted": 1, "failed": 1}
    assert submitted_ids == [int(ok["id"])]

    ok_state = get_sync_schedule(int(ok["id"]), db_path=db_path)
    bad_state = get_sync_schedule(int(bad["id"]), db_path=db_path)
    assert ok_state["last_status"] == "queued"
    assert bad_state["last_status"] == "failed"
    assert "expected test failure" in (bad_state["last_error"] or "")


def test_scheduler_background_start_stop_runs_tick_loop(tmp_path):
    db_path = tmp_path / "sync_schedules.db"
    init_sync_schedule_store(db_path=db_path)
    schedule = create_or_upsert_sync_schedule(
        {
            "name": "loop",
            "source": "loop-source",
            "interval_seconds": 600,
            "enabled": True,
            "replace_existing": False,
            "payload": {},
        },
        db_path=db_path,
    )
    # Force this schedule to be due before the loop starts.
    mark_sync_schedule_run(int(schedule["id"]), status="queued", db_path=db_path)
    state = get_sync_schedule(int(schedule["id"]), db_path=db_path)
    assert state["last_run_at"] is not None
    # Move next due window close by making interval tiny.
    create_or_upsert_sync_schedule(
        {
            "name": "loop",
            "source": "loop-source",
            "interval_seconds": 1,
            "enabled": True,
            "replace_existing": False,
            "payload": {},
        },
        db_path=db_path,
    )

    calls = {"count": 0}

    def submit(_: dict) -> None:
        calls["count"] += 1

    scheduler = TechnicalSyncScheduler(
        schedule_db_path=db_path,
        submit_schedule_fn=submit,
        poll_seconds=0.5,
    )

    scheduler.start()
    try:
        time.sleep(1.2)
    finally:
        scheduler.stop()

    assert calls["count"] >= 1
    assert scheduler.is_running() is False
