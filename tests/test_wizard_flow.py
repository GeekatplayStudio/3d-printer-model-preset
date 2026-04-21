from __future__ import annotations

import time

import trimesh
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


def _broken_mesh_bytes() -> bytes:
    vertices = [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [5.0, 0.0, 0.0],
        [6.0, 0.0, 0.0],
        [5.0, 1.0, 0.0],
    ]
    faces = [
        [0, 1, 2],
        [0, 1, 2],
        [0, 1, 1],
        [0, 2, 3],
        [0, 3, 1],
        [1, 3, 2],
        [4, 5, 6],
    ]
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    return mesh.export(file_type="stl")


def test_wizard_ui_and_status_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    ui = client.get("/wizard")
    assert ui.status_code == 200
    assert "ResinLogic Wizard" in ui.text
    assert "Show Catalog List" in ui.text
    assert "Analysis depth" in ui.text

    status = client.get("/wizard/database/status")
    assert status.status_code == 200
    assert "completeness_percent" in status.json()


def test_wizard_setup_local_dataset_and_catalog_options(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    setup = client.post(
        "/wizard/database/setup",
        json={
            "mode": "official_local",
            "replace_existing": True,
            "auto_update": False,
            "source": "wizard_test",
        },
    )
    assert setup.status_code == 200
    setup_body = setup.json()
    assert setup_body["printers_upserted"] >= 1
    assert setup_body["resins_upserted"] >= 1
    assert setup_body["profiles_upserted"] >= 1

    status = client.get("/wizard/database/status")
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["ready_for_model_analysis"] is True
    assert status_body["ready_for_settings"] is True

    options = client.get("/wizard/catalog/options")
    assert options.status_code == 200
    options_body = options.json()
    assert len(options_body["printers"]) >= 1
    assert len(options_body["resins"]) >= 1
    assert "compatibility" in options_body
    assert isinstance(options_body["compatibility"], dict)


def test_wizard_gap_report_and_update_controls(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_fetch(*, owner: str, repo: str, path: str, ref: str = "main", timeout_s: float = 20.0):
        assert owner == "wizard-org"
        assert repo == "wizard-repo"
        assert path == "data/sync.json"
        assert ref == "main"
        return (
            {
                "printers": [{"name": "Wizard Printer"}],
                "resins": [{"name": "Wizard Resin"}],
                "profiles": [
                    {
                        "printer_name": "Wizard Printer",
                        "resin_name": "Wizard Resin",
                        "profile_name": "Wizard Profile",
                        "layer_height_mm": 0.05,
                        "exposure_s": 2.5,
                        "bottom_exposure_s": 30.0,
                        "is_default": True,
                        "is_active": True,
                    }
                ],
            },
            "https://raw.githubusercontent.com/wizard-org/wizard-repo/main/data/sync.json",
        )

    monkeypatch.setattr(main, "fetch_technical_sync_from_github", fake_fetch)

    setup = client.post(
        "/wizard/database/setup",
        json={
            "mode": "github",
            "owner": "wizard-org",
            "repo": "wizard-repo",
            "path": "data/sync.json",
            "ref": "main",
            "replace_existing": False,
            "auto_update": True,
            "auto_update_interval_seconds": 600,
            "source": "wizard_github_test",
        },
    )
    assert setup.status_code == 200
    setup_body = setup.json()
    assert setup_body["auto_update_schedule_id"] is not None
    schedule_id = setup_body["auto_update_schedule_id"]

    gaps = client.get("/wizard/database/gaps")
    assert gaps.status_code == 200
    gaps_body = gaps.json()
    assert "recommendations" in gaps_body
    assert isinstance(gaps_body["recommendations"], list)

    updates = client.get("/wizard/updates/status")
    assert updates.status_code == 200
    updates_body = updates.json()
    assert updates_body["wizard_auto_update_present"] is True
    assert updates_body["schedules_total"] >= 1

    run_now = client.post("/wizard/updates/run-now", json={"schedule_id": schedule_id})
    assert run_now.status_code == 200
    run_now_body = run_now.json()
    assert run_now_body["job"]["type"] == "sync_schedule"
    job_id = run_now_body["job"]["id"]

    final = {}
    for _ in range(80):
        poll = client.get(f"/jobs/{job_id}")
        assert poll.status_code == 200
        final = poll.json()
        if final["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)
    assert final["status"] == "succeeded"

    due = client.post("/wizard/updates/run-due")
    assert due.status_code == 200
    due_body = due.json()
    assert "due" in due_body
    assert "submitted" in due_body


def test_wizard_model_fix_and_settings_downloads(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    setup = client.post(
        "/wizard/database/setup",
        json={
            "mode": "official_local",
            "replace_existing": True,
            "auto_update": False,
            "source": "wizard_test",
        },
    )
    assert setup.status_code == 200

    check = client.post(
        "/wizard/model/check",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2"},
    )
    assert check.status_code == 200
    check_body = check.json()
    assert "analysis" in check_body
    assert check_body["analysis"]["mesh_health"]["duplicate_face_count"] >= 1

    fix = client.post(
        "/wizard/model/fix",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2"},
    )
    assert fix.status_code == 200
    fix_body = fix.json()
    assert fix_body["download_url"].startswith("/wizard/download/")

    repaired_download = client.get(fix_body["download_url"])
    assert repaired_download.status_code == 200
    assert len(repaired_download.content) > 100

    options = client.get("/wizard/catalog/options")
    assert options.status_code == 200
    options_body = options.json()
    printer = options_body["printers"][0]
    compatibility = options_body.get("compatibility") or {}
    compatible_resins = compatibility.get(printer) or options_body["resins"]
    resin = compatible_resins[0]

    settings = client.post(
        "/wizard/settings/recommend",
        json={
            "analysis": fix_body["analysis"],
            "printer": printer,
            "resin_type": resin,
            "use_case": "miniature",
            "ambient_temp_c": 22.0,
            "film_releases": 100,
        },
    )
    assert settings.status_code == 200
    settings_body = settings.json()
    assert settings_body["cfg_download_url"].startswith("/wizard/download/")
    assert settings_body["settings_download_url"].startswith("/wizard/download/")
    assert settings_body["settings"]["provenance"]["data_quality"] == "verified_sources"
    assert settings_body["settings"]["provenance"]["source_count"] >= 1

    cfg_download = client.get(settings_body["cfg_download_url"])
    json_download = client.get(settings_body["settings_download_url"])
    assert cfg_download.status_code == 200
    assert json_download.status_code == 200
    assert "layer_height_mm" in settings_body["settings"]

    # Ensure artifacts survive a short delay and remain downloadable.
    time.sleep(0.01)
    assert client.get(settings_body["cfg_download_url"]).status_code == 200


def test_wizard_rejects_non_stl_upload(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    setup = client.post(
        "/wizard/database/setup",
        json={
            "mode": "official_local",
            "replace_existing": True,
            "auto_update": False,
            "source": "wizard_test",
        },
    )
    assert setup.status_code == 200

    bad_check = client.post(
        "/wizard/model/check",
        files={"file": ("bad.obj", b"o cube\\nv 0 0 0\\n", "text/plain")},
        data={"slice_height_mm": "0.2"},
    )
    assert bad_check.status_code == 400
    assert "Only STL files are supported" in bad_check.json()["detail"]

    bad_fix = client.post(
        "/wizard/model/fix",
        files={"file": ("bad.3mf", b"dummy", "application/octet-stream")},
        data={"slice_height_mm": "0.2"},
    )
    assert bad_fix.status_code == 400
    assert "Only STL files are supported" in bad_fix.json()["detail"]
