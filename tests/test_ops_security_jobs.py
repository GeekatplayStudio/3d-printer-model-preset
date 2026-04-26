from __future__ import annotations

import time

from fastapi.testclient import TestClient

import app.main as main
from app.audit_store import init_audit_store
from app.catalog_store import init_catalog_store
from app.job_queue import InMemoryJobQueue
from app.job_store import init_job_store
from app.scheduler import TechnicalSyncScheduler
from app.sync_schedule_store import init_sync_schedule_store


def _client(tmp_path, monkeypatch) -> TestClient:
    catalog_path = tmp_path / "catalog.db"
    audit_path = tmp_path / "audit.db"
    jobs_path = tmp_path / "jobs.db"
    schedules_path = tmp_path / "sync_schedules.db"
    init_catalog_store(db_path=catalog_path)
    init_audit_store(db_path=audit_path)
    init_job_store(db_path=jobs_path)
    init_sync_schedule_store(db_path=schedules_path)
    monkeypatch.setenv("RESINLOGIC_ENABLE_SCHEDULER", "0")
    monkeypatch.setattr(main, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(main, "AUDIT_PATH", audit_path)
    monkeypatch.setattr(main, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(main, "SCHEDULES_PATH", schedules_path)
    monkeypatch.setattr(main, "JOB_QUEUE", InMemoryJobQueue(max_workers=1, db_path=jobs_path))
    scheduler = TechnicalSyncScheduler(
        schedule_db_path=schedules_path,
        submit_schedule_fn=lambda schedule: main._submit_sync_schedule_job(schedule, actor="scheduler-test"),
        poll_seconds=0.5,
    )
    monkeypatch.setattr(main, "SCHEDULER", scheduler)
    return TestClient(main.app)


def _headers(token: str | None = None, actor: str = "tester") -> dict[str, str]:
    headers = {"Actor": actor}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_app_ui_route_served(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/app")
    assert response.status_code == 200
    assert "Geekatplay Studio Ops Console" in response.text
    assert "/static/geekatplay-mark.svg" in response.text
    assert "Add Material (JSON)" in response.text
    assert "Quick Ops Guide" in response.text
    assert 'data-help-key="pipeline_material"' in response.text
    assert "select option," in response.text
    assert "Advanced mode:" in response.text
    assert "Back To Wizard Mode" in response.text


def test_auth_whoami_default_dev_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("RESINLOGIC_ENFORCE_AUTH", raising=False)
    monkeypatch.delenv("RESINLOGIC_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("RESINLOGIC_OPERATOR_API_KEY", raising=False)
    monkeypatch.delenv("RESINLOGIC_VIEWER_API_KEY", raising=False)
    client = _client(tmp_path, monkeypatch)

    response = client.get("/auth/whoami")
    assert response.status_code == 200
    body = response.json()
    assert body["auth_enforced"] is False
    assert body["role"] == "admin"
    assert body["api_key_present"] is False


def test_auth_enforced_rbac(tmp_path, monkeypatch):
    monkeypatch.setenv("RESINLOGIC_STANDALONE_MODE", "0")
    monkeypatch.setenv("RESINLOGIC_ENFORCE_AUTH", "1")
    monkeypatch.setenv("RESINLOGIC_ENABLE_DEFAULT_KEYS", "0")
    monkeypatch.setenv("RESINLOGIC_ADMIN_API_KEY", "admin-1")
    monkeypatch.setenv("RESINLOGIC_OPERATOR_API_KEY", "operator-1")
    monkeypatch.setenv("RESINLOGIC_VIEWER_API_KEY", "viewer-1")
    client = _client(tmp_path, monkeypatch)

    no_key = client.get("/auth/whoami")
    assert no_key.status_code == 401

    viewer = client.get("/auth/whoami", headers=_headers("viewer-1", "viewer-user"))
    assert viewer.status_code == 200
    assert viewer.json()["role"] == "viewer"

    forbidden = client.post(
        "/catalog/printers",
        json={"name": "Viewer Cannot Write"},
        headers=_headers("viewer-1", "viewer-user"),
    )
    assert forbidden.status_code == 403

    operator = client.post(
        "/catalog/printers",
        json={"name": "Operator Can Write"},
        headers=_headers("operator-1", "operator-user"),
    )
    assert operator.status_code == 200


def test_auth_enforced_accepts_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("RESINLOGIC_STANDALONE_MODE", "0")
    monkeypatch.setenv("RESINLOGIC_ENFORCE_AUTH", "1")
    monkeypatch.setenv("RESINLOGIC_ENABLE_DEFAULT_KEYS", "0")
    monkeypatch.setenv("RESINLOGIC_ADMIN_API_KEY", "admin-1")
    monkeypatch.setenv("RESINLOGIC_OPERATOR_API_KEY", "operator-1")
    monkeypatch.setenv("RESINLOGIC_VIEWER_API_KEY", "viewer-1")
    client = _client(tmp_path, monkeypatch)

    response = client.get("/auth/whoami", headers={"Authorization": "Bearer viewer-1", "Actor": "bearer-user"})
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "viewer"
    assert body["api_key_present"] is True

    github_style = client.get("/auth/whoami", headers={"Authorization": "token viewer-1", "Actor": "github-user"})
    assert github_style.status_code == 200
    assert github_style.json()["role"] == "viewer"

    prom = client.get("/ops/metrics/prometheus", headers={"Authorization": "Bearer viewer-1"})
    assert prom.status_code == 200
    assert "resinlogic_requests_total" in prom.text


def test_standalone_mode_disables_auth_even_when_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("RESINLOGIC_STANDALONE_MODE", "1")
    monkeypatch.setenv("RESINLOGIC_ENFORCE_AUTH", "1")
    monkeypatch.setenv("RESINLOGIC_ENABLE_DEFAULT_KEYS", "0")
    client = _client(tmp_path, monkeypatch)

    response = client.get("/auth/whoami")
    assert response.status_code == 200
    body = response.json()
    assert body["auth_enforced"] is False
    assert body["role"] == "admin"


def test_catalog_audit_and_versions_flow(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    created = client.post("/catalog/printers", json={"name": "Printer A"})
    assert created.status_code == 200

    versions = client.get("/catalog/versions")
    assert versions.status_code == 200
    assert len(versions.json()) >= 1

    events = client.get("/ops/audit/events?limit=50")
    assert events.status_code == 200
    actions = [item["action"] for item in events.json()]
    assert "catalog.printer.upsert" in actions


def test_catalog_restore_version_endpoint(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    first = client.post("/catalog/printers", json={"name": "Restore One"})
    assert first.status_code == 200

    snap = client.post("/catalog/versions/snapshot", json={"note": "snapshot before second printer"})
    assert snap.status_code == 200
    snap_id = snap.json()["id"]

    second = client.post("/catalog/printers", json={"name": "Restore Two"})
    assert second.status_code == 200

    restore = client.post("/catalog/versions/restore", json={"version_id": snap_id})
    assert restore.status_code == 200

    printers = client.get("/catalog/printers")
    names = [item["name"] for item in printers.json()]
    assert "Restore One" in names
    assert "Restore Two" not in names


def test_sync_and_async_job_endpoints(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    sync_response = client.post(
        "/sync/technical",
        json={
            "source": "unit_test",
            "replace_existing": False,
            "payload": {
                "printers": [{"name": "Sync Printer"}],
                "resins": [{"name": "Sync Resin"}],
                "profiles": [
                    {
                        "printer_name": "Sync Printer",
                        "resin_name": "Sync Resin",
                        "profile_name": "Sync Profile",
                        "layer_height_mm": 0.05,
                        "exposure_s": 2.2,
                        "bottom_exposure_s": 30.0,
                        "is_default": True,
                        "is_active": True,
                    }
                ],
            },
        },
    )
    assert sync_response.status_code == 200
    assert sync_response.json()["profiles_upserted"] == 1
    assert sync_response.json()["curation"]["kept_counts"]["profiles"] == 1

    job_submit = client.post(
        "/sync/technical/job",
        json={
            "source": "unit_test_job",
            "replace_existing": False,
            "payload": {"printers": [{"name": "Sync Printer 2"}], "resins": [], "profiles": []},
        },
    )
    assert job_submit.status_code == 200
    job_id = job_submit.json()["job"]["id"]

    status = ""
    last = {}
    for _ in range(60):
        poll = client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        last = poll.json()
        status = last["status"]
        if status in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert status == "succeeded"
    assert last["result"]["printers_upserted"] == 1


def test_sync_technical_github_endpoint(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_fetch(*, owner: str, repo: str, path: str, ref: str = "main", timeout_s: float = 20.0):
        assert owner == "my-org"
        assert repo == "my-repo"
        assert path == "data/sync.json"
        assert ref == "main"
        return (
            {
                "printers": [{"name": "GitHub Printer"}],
                "resins": [{"name": "GitHub Resin"}],
                "profiles": [
                    {
                        "printer_name": "GitHub Printer",
                        "resin_name": "GitHub Resin",
                        "profile_name": "GitHub Default",
                        "layer_height_mm": 0.05,
                        "exposure_s": 2.4,
                        "bottom_exposure_s": 30.0,
                        "is_default": True,
                        "is_active": True,
                    }
                ],
            },
            "https://raw.githubusercontent.com/my-org/my-repo/main/data/sync.json",
        )

    monkeypatch.setattr(main, "fetch_technical_sync_from_github", fake_fetch)

    response = client.post(
        "/sync/technical/github",
        json={
            "owner": "my-org",
            "repo": "my-repo",
            "path": "data/sync.json",
            "ref": "main",
            "source": "github_repo",
            "replace_existing": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["owner"] == "my-org"
    assert body["repo"] == "my-repo"
    assert body["path"] == "data/sync.json"
    assert body["profiles_upserted"] == 1
    assert body["curation"]["kept_counts"]["profiles"] == 1
    assert body["curation"]["source_reliability"] >= 0.8
    assert body["raw_url"].startswith("https://raw.githubusercontent.com/")

    printers = client.get("/catalog/printers")
    assert printers.status_code == 200
    assert any(item["name"] == "GitHub Printer" for item in printers.json())

def test_sync_technical_scrape_endpoint(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_scrape(*, urls: list[str], timeout_s: float = 20.0, fetch_html=None):
        assert urls == [
            "https://store.anycubic.com/products/photon-mono-m7-pro",
            "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
        ]
        assert timeout_s == 15.0
        return (
            {
                "printers": [{"name": "Anycubic Photon Mono M7 Pro", "technology": "MSLA"}],
                "resins": [{"name": "Standard Resin", "material_type": "resin"}],
                "profiles": [
                    {
                        "printer_name": "Anycubic Photon Mono M7 Pro",
                        "resin_name": "Standard Resin",
                        "profile_name": "Official Settings",
                        "layer_height_mm": 0.05,
                        "exposure_s": 2.0,
                        "bottom_exposure_s": 25.0,
                        "is_default": True,
                        "is_active": True,
                    }
                ],
            },
            {
                "normalized_urls": urls,
                "scraped_urls": urls,
                "unsupported_urls": [],
                "notes": [],
            },
        )

    monkeypatch.setattr(main, "scrape_supported_catalog_pages", fake_scrape)

    response = client.post(
        "/sync/technical/scrape",
        json={
            "urls": [
                "https://store.anycubic.com/products/photon-mono-m7-pro",
                "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
            ],
            "source": "web_scrape",
            "replace_existing": False,
            "timeout_s": 15.0,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profiles_upserted"] == 1
    assert body["scraped_urls"] == [
        "https://store.anycubic.com/products/photon-mono-m7-pro",
        "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
    ]
    assert body["unsupported_urls"] == []


def test_sync_schedule_crud_and_tick(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    created = client.post(
        "/sync/schedules",
        json={
            "name": "fast_schedule",
            "source": "schedule_source",
            "interval_seconds": 1,
            "enabled": True,
            "replace_existing": False,
            "payload": {"printers": [{"name": "Scheduled Printer"}], "resins": [], "profiles": []},
        },
    )
    assert created.status_code == 200
    schedule_id = created.json()["id"]

    listed = client.get("/sync/schedules")
    assert listed.status_code == 200
    assert any(item["id"] == schedule_id for item in listed.json())

    health = client.get("/sync/scheduler/health")
    assert health.status_code == 200
    assert health.json()["schedules_total"] >= 1

    tick = client.post("/sync/scheduler/tick")
    assert tick.status_code == 200
    assert tick.json()["submitted"] >= 1

    # Wait for scheduled sync job to finish.
    for _ in range(80):
        jobs = client.get("/jobs?limit=20").json()
        if any(item["type"] == "sync_schedule" and item["status"] in {"succeeded", "failed"} for item in jobs):
            break
        time.sleep(0.02)

    printers = client.get("/catalog/printers").json()
    assert any(item["name"] == "Scheduled Printer" for item in printers)

    run_now = client.post(f"/sync/schedules/{schedule_id}/run-now")
    assert run_now.status_code == 200
    assert run_now.json()["job"]["type"] == "sync_schedule"


def test_sync_schedule_github_payload_run_now(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_fetch(*, owner: str, repo: str, path: str, ref: str = "main", timeout_s: float = 20.0):
        assert owner == "sync-org"
        assert repo == "sync-repo"
        assert path == "data/profiles.json"
        assert ref == "main"
        return (
            {
                "printers": [{"name": "Scheduled GitHub Printer"}],
                "resins": [{"name": "Scheduled GitHub Resin"}],
                "profiles": [
                    {
                        "printer_name": "Scheduled GitHub Printer",
                        "resin_name": "Scheduled GitHub Resin",
                        "profile_name": "Scheduled GitHub Profile",
                        "layer_height_mm": 0.05,
                        "exposure_s": 2.3,
                        "bottom_exposure_s": 30.0,
                        "is_default": True,
                        "is_active": True,
                    }
                ],
            },
            "https://raw.githubusercontent.com/sync-org/sync-repo/main/data/profiles.json",
        )

    monkeypatch.setattr(main, "fetch_technical_sync_from_github", fake_fetch)

    created = client.post(
        "/sync/schedules",
        json={
            "name": "github_schedule",
            "source": "github_schedule_source",
            "interval_seconds": 3600,
            "enabled": True,
            "replace_existing": False,
            "payload": {
                "github": {
                    "owner": "sync-org",
                    "repo": "sync-repo",
                    "path": "data/profiles.json",
                    "ref": "main",
                }
            },
        },
    )
    assert created.status_code == 200
    schedule_id = created.json()["id"]

    run_now = client.post(f"/sync/schedules/{schedule_id}/run-now")
    assert run_now.status_code == 200
    job_id = run_now.json()["job"]["id"]

    final = {}
    for _ in range(80):
        poll = client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        final = poll.json()
        if final["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    assert final["status"] == "succeeded"
    assert final["result"]["profiles_upserted"] == 1
    assert final["result"]["github_attempts_used"] == 1
    assert final["result"]["github_raw_url"].startswith("https://raw.githubusercontent.com/")

    printers = client.get("/catalog/printers")
    assert printers.status_code == 200
    assert any(item["name"] == "Scheduled GitHub Printer" for item in printers.json())


def test_sync_schedule_github_retry_then_success(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    calls = {"count": 0}

    def flaky_fetch(*, owner: str, repo: str, path: str, ref: str = "main", timeout_s: float = 20.0):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("temporary network issue")
        return (
            {
                "printers": [{"name": "Retry Printer"}],
                "resins": [{"name": "Retry Resin"}],
                "profiles": [
                    {
                        "printer_name": "Retry Printer",
                        "resin_name": "Retry Resin",
                        "profile_name": "Retry Profile",
                        "layer_height_mm": 0.05,
                        "exposure_s": 2.4,
                        "bottom_exposure_s": 31.0,
                        "is_default": True,
                        "is_active": True,
                    }
                ],
            },
            "https://raw.githubusercontent.com/sync-org/sync-repo/main/data/retry.json",
        )

    monkeypatch.setattr(main, "fetch_technical_sync_from_github", flaky_fetch)

    created = client.post(
        "/sync/schedules",
        json={
            "name": "github_retry_schedule",
            "source": "github_retry_source",
            "interval_seconds": 3600,
            "enabled": True,
            "replace_existing": False,
            "payload": {
                "github": {
                    "owner": "sync-org",
                    "repo": "sync-repo",
                    "path": "data/retry.json",
                    "ref": "main",
                    "retry_attempts": 3,
                    "retry_backoff_seconds": 0,
                }
            },
        },
    )
    assert created.status_code == 200
    schedule_id = created.json()["id"]

    run_now = client.post(f"/sync/schedules/{schedule_id}/run-now")
    assert run_now.status_code == 200
    job_id = run_now.json()["job"]["id"]

    final = {}
    for _ in range(80):
        poll = client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        final = poll.json()
        if final["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    assert final["status"] == "succeeded"
    assert calls["count"] == 3
    assert final["result"]["github_attempts_used"] == 3
    assert final["result"]["profiles_upserted"] == 1


def test_job_cancel_and_cleanup_endpoints(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    blocking = main.JOB_QUEUE.submit(job_type="blocking", actor="tester", fn=lambda: time.sleep(0.3))
    queued = main.JOB_QUEUE.submit(job_type="queued", actor="tester", fn=lambda: {"ok": True})

    cancel = client.post(f"/jobs/{queued['id']}/cancel")
    assert cancel.status_code == 200
    cancel_body = cancel.json()
    assert cancel_body["cancelled"] is True
    assert cancel_body["job"]["status"] == "cancelled"

    # Let blocking job finish so cleanup can remove terminal jobs only.
    for _ in range(80):
        block_state = client.get(f"/jobs/{blocking['id']}")
        if block_state.status_code == 200 and block_state.json()["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    cleanup = client.post("/jobs/cleanup", json={"max_age_days": 0, "keep_latest": 0})
    assert cleanup.status_code == 200
    assert cleanup.json()["deleted"] >= 1


def test_metrics_and_prometheus_output(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.get("/health")
    client.get("/catalog/health")

    metrics = client.get("/ops/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["requests_total"] >= 2

    prom = client.get("/ops/metrics/prometheus")
    assert prom.status_code == 200
    assert "resinlogic_requests_total" in prom.text

    jobs_health = client.get("/jobs/health")
    assert jobs_health.status_code == 200
    assert jobs_health.json()["status"] == "ok"
