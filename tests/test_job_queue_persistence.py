from __future__ import annotations

import time

from app.job_queue import InMemoryJobQueue
from app.job_store import count_jobs


def test_job_queue_persists_jobs_to_sqlite(tmp_path):
    db_path = tmp_path / "jobs.db"
    queue = InMemoryJobQueue(max_workers=1, db_path=db_path)
    created = queue.submit(job_type="unit", actor="tester", metadata={"k": "v"}, fn=lambda: {"ok": 1})
    job_id = created["id"]

    for _ in range(60):
        current = queue.get(job_id)
        if current["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    current = queue.get(job_id)
    assert current["status"] == "succeeded"
    assert current["result"] == {"ok": 1}

    restarted = InMemoryJobQueue(max_workers=1, db_path=db_path)
    loaded = restarted.get(job_id)
    assert loaded["status"] == "succeeded"
    assert loaded["result"] == {"ok": 1}

    listed_ids = [item["id"] for item in restarted.list(limit=20)]
    assert job_id in listed_ids
    counts = count_jobs(db_path=db_path)
    assert counts["total"] >= 1
