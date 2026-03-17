from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.job_store import get_job as get_job_record
from app.job_store import init_job_store, list_jobs as list_job_records, upsert_job


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InMemoryJobQueue:
    def __init__(self, max_workers: int = 2, db_path: str | Path | None = None) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._db_path = Path(db_path) if db_path else None
        if self._db_path is not None:
            init_job_store(self._db_path)

    def submit(
        self,
        *,
        job_type: str,
        fn: Callable[[], Any],
        actor: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        record = {
            "id": job_id,
            "type": job_type,
            "status": "queued",
            "actor": actor,
            "metadata": metadata or {},
            "created_at": _utc_now_iso(),
            "started_at": None,
            "completed_at": None,
            "error": None,
            "result": None,
        }
        with self._lock:
            self._jobs[job_id] = record
        self._persist(record)

        def _run() -> Any:
            self._mark(job_id, status="running", started_at=_utc_now_iso())
            return fn()

        future = self._executor.submit(_run)
        with self._lock:
            self._futures[job_id] = future
        future.add_done_callback(lambda fut: self._on_done(job_id, fut))
        return self.get(job_id)

    def _mark(self, job_id: str, **updates: Any) -> None:
        updated: dict[str, Any] | None = None
        with self._lock:
            current = self._jobs.get(job_id)
            if not current:
                return
            current.update(updates)
            updated = dict(current)
        if updated is not None:
            self._persist(updated)

    def _persist(self, record: dict[str, Any]) -> None:
        if self._db_path is None:
            return
        upsert_job(record, db_path=self._db_path)

    def _on_done(self, job_id: str, future: Future[Any]) -> None:
        try:
            result = future.result()
            self._mark(
                job_id,
                status="succeeded",
                completed_at=_utc_now_iso(),
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark(
                job_id,
                status="failed",
                completed_at=_utc_now_iso(),
                error=str(exc),
            )
        finally:
            with self._lock:
                self._futures.pop(job_id, None)

    def get(self, job_id: str) -> dict[str, Any]:
        if self._db_path is not None:
            row = get_job_record(job_id, db_path=self._db_path)
            if row is not None:
                return row
        with self._lock:
            item = self._jobs.get(job_id)
            if item is None:
                raise KeyError(job_id)
            return dict(item)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if self._db_path is not None:
            return list_job_records(limit=limit, offset=offset, db_path=self._db_path)
        with self._lock:
            items = list(self._jobs.values())
        items.sort(key=lambda j: j["created_at"], reverse=True)
        start = max(0, offset)
        end = start + max(1, min(limit, 1000))
        return [dict(item) for item in items[start:end]]

    def cancel(self, job_id: str) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            future = self._futures.get(job_id)
            current = self._jobs.get(job_id)

        if current is None and self._db_path is not None:
            persisted = get_job_record(job_id, db_path=self._db_path)
            if persisted is not None:
                return False, persisted

        if current is None:
            raise KeyError(job_id)

        if future is None:
            return False, self.get(job_id)

        if future.cancel():
            self._mark(
                job_id,
                status="cancelled",
                completed_at=_utc_now_iso(),
                error="Cancelled by user.",
            )
            with self._lock:
                self._futures.pop(job_id, None)
            return True, self.get(job_id)

        # Could not cancel (likely running or already done).
        return False, self.get(job_id)
