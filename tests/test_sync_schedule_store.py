from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.sync_schedule_store import (
    create_or_upsert_sync_schedule,
    delete_sync_schedule,
    get_sync_schedule,
    init_sync_schedule_store,
    list_due_sync_schedules,
    list_sync_schedules,
    mark_sync_schedule_run,
    update_sync_schedule,
)


def test_sync_schedule_store_crud_and_due_logic(tmp_path):
    db_path = tmp_path / "sync_schedules.db"
    init_sync_schedule_store(db_path=db_path)

    schedule = create_or_upsert_sync_schedule(
        {
            "name": "daily-sync",
            "source": "test_source",
            "interval_seconds": 60,
            "enabled": True,
            "replace_existing": False,
            "payload": {"printers": [], "resins": [], "profiles": []},
        },
        db_path=db_path,
    )
    schedule_id = int(schedule["id"])
    assert schedule["enabled"] is True

    listed = list_sync_schedules(db_path=db_path)
    assert len(listed) == 1
    assert listed[0]["id"] == schedule_id

    due_initial = list_due_sync_schedules(db_path=db_path)
    assert [item["id"] for item in due_initial] == [schedule_id]

    mark_sync_schedule_run(schedule_id, status="succeeded", db_path=db_path)
    updated = get_sync_schedule(schedule_id, db_path=db_path)
    assert updated["last_status"] == "succeeded"
    assert updated["last_run_at"] is not None

    last_run_at = datetime.fromisoformat(updated["last_run_at"].replace("Z", "+00:00"))
    not_due = list_due_sync_schedules(now=last_run_at + timedelta(seconds=30), db_path=db_path)
    assert not_due == []

    due_again = list_due_sync_schedules(now=last_run_at + timedelta(seconds=61), db_path=db_path)
    assert [item["id"] for item in due_again] == [schedule_id]

    disabled = update_sync_schedule(schedule_id, {"enabled": False}, db_path=db_path)
    assert disabled["enabled"] is False
    due_disabled = list_due_sync_schedules(
        now=datetime.now(timezone.utc) + timedelta(days=1),
        db_path=db_path,
    )
    assert due_disabled == []

    assert delete_sync_schedule(schedule_id, db_path=db_path) is True
    assert list_sync_schedules(db_path=db_path) == []


def test_sync_schedule_upsert_by_name_is_case_insensitive(tmp_path):
    db_path = tmp_path / "sync_schedules.db"
    init_sync_schedule_store(db_path=db_path)

    first = create_or_upsert_sync_schedule(
        {
            "name": "Nightly",
            "source": "nightly_source",
            "interval_seconds": 3600,
            "enabled": True,
            "replace_existing": False,
            "payload": {"printers": [{"name": "P1"}]},
        },
        db_path=db_path,
    )
    second = create_or_upsert_sync_schedule(
        {
            "name": "nightly",
            "source": "nightly_source_2",
            "interval_seconds": 1800,
            "enabled": False,
            "replace_existing": True,
            "payload": {"resins": [{"name": "R1"}]},
        },
        db_path=db_path,
    )

    assert first["id"] == second["id"]
    assert second["interval_seconds"] == 1800
    assert second["enabled"] is False
    assert second["replace_existing"] is True
    assert second["payload"] == {"resins": [{"name": "R1"}]}
