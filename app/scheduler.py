from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False

    @property
    def poll_seconds(self) -> float:
        return self._poll_seconds

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="sync-scheduler", daemon=True)
            self._thread.start()
            self._running = True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            thread = self._thread
            self._thread = None
            self._running = False
        if thread is not None:
            thread.join(timeout=2.0)

    def is_running(self) -> bool:
        with self._lock:
            return self._running

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

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.tick_once()
            self._stop_event.wait(self._poll_seconds)
