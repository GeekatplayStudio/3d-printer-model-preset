from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler

from app.sync_schedule_store import list_due_sync_schedules, mark_sync_schedule_run


class TechnicalSyncScheduler:
    def __init__(
        self,
        *,
        schedule_db_path: str | Path,
        submit_schedule_fn: Callable[[dict[str, Any]], Any],
        poll_seconds: float = 2.0,
    ) -> None:
        self._schedule_db_path = Path(schedule_db_path)
        self._submit_schedule_fn = submit_schedule_fn
        self._poll_seconds = max(0.5, poll_seconds)
        self._scheduler: BackgroundScheduler | None = None

    @property
    def poll_seconds(self) -> float:
        return self._poll_seconds

    def start(self) -> None:
        if self.is_running():
            return
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            self.tick_once,
            trigger="interval",
            seconds=self._poll_seconds,
            id="technical-sync-tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        self._scheduler = scheduler

    def stop(self) -> None:
        scheduler = self._scheduler
        if scheduler is None:
            return
        self._scheduler = None
        scheduler.shutdown(wait=False)

    def is_running(self) -> bool:
        scheduler = self._scheduler
        return bool(scheduler is not None and scheduler.running)

    def tick_once(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        due = list_due_sync_schedules(now=now, db_path=self._schedule_db_path)
        submitted = 0
        failed = 0
        for schedule in due:
            try:
                self._submit_schedule_fn(schedule)
                mark_sync_schedule_run(
                    int(schedule["id"]),
                    status="queued",
                    error=None,
                    db_path=self._schedule_db_path,
                )
                submitted += 1
            except Exception as exc:  # noqa: BLE001
                mark_sync_schedule_run(
                    int(schedule["id"]),
                    status="failed",
                    error=str(exc),
                    db_path=self._schedule_db_path,
                )
                failed += 1
        return {"due": len(due), "submitted": submitted, "failed": failed}
