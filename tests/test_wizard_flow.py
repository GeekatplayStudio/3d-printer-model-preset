from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor

import trimesh
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

import app.main as main
from app.audit_store import init_audit_store
from app.catalog_store import init_catalog_store
from app.job_queue import InMemoryJobQueue
from app.job_store import init_job_store
from app.models import GeometryAnalysis
from app.scheduler import TechnicalSyncScheduler
from app.sync_schedule_store import init_sync_schedule_store


def _auth_headers(token: str = "dev-operator-key", actor: str = "wizard-user") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Actor": actor,
    }


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


def _analysis_payload() -> dict:
    return {
        "file_name": "mock.stl",
        "mesh_volume_mm3": 125.0,
        "surface_area_mm2": 75.0,
        "surface_area_ratio": 0.6,
        "triangle_count": 256,
        "detail_density": 3.41,
        "curvature_proxy": 0.12,
        "slice_height_mm": 0.2,
        "build_plate_area_mm2": 12000.0,
        "max_cross_section_mm2": 250.0,
        "max_cross_section_ratio": 0.02,
        "slice_areas": [{"z_mm": 0.0, "area_mm2": 12.0}],
        "suction_cups": [],
        "islands": [],
        "mesh_health": {
            "watertight": True,
            "winding_consistent": True,
            "volume_consistent": True,
            "connected_components": 1,
            "boundary_edge_count": 0,
            "non_manifold_edge_count": 0,
            "degenerate_face_count": 0,
            "duplicate_face_count": 0,
            "repaired": False,
            "issues": [],
            "repair_actions": [],
        },
        "estimated_intent": "miniature",
        "intent_reasons": ["Mock intent"],
        "structural_risk_score": 12.5,
        "notes": ["Mock analysis"],
        "analysis_level": "balanced",
        "performance_ms": {"cross_section": 12.0, "voxelize": 5.0, "total": 25.0},
    }


def test_wizard_ui_and_status_route(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    ui = client.get("/wizard")
    assert ui.status_code == 200
    assert "Geekatplay Studio Wizard" in ui.text
    assert "downloadArtifact(" in ui.text
    assert "Show Catalog List" in ui.text
    assert "Analysis depth" in ui.text
    assert "/wizard/model/check/status/" in ui.text
    assert "progress_job_id" in ui.text
    assert "cancelAnalyzeBtn" in ui.text
    assert "analysisProgressPanel" in ui.text
    assert "dbScanPanel" in ui.text
    assert "/wizard/database/gaps" in ui.text
    assert "/phase3/history/query" in ui.text
    assert "Smart check:" in ui.text
    assert "/phase3/history/summary" in ui.text

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


def test_wizard_download_requires_auth_headers_when_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("RESINLOGIC_STANDALONE_MODE", "0")
    monkeypatch.setenv("RESINLOGIC_ENFORCE_AUTH", "1")
    monkeypatch.setenv("RESINLOGIC_ENABLE_DEFAULT_KEYS", "1")
    client = _client(tmp_path, monkeypatch)

    setup = client.post(
        "/wizard/database/setup",
        json={
            "mode": "official_local",
            "replace_existing": True,
            "auto_update": False,
            "source": "wizard_test",
        },
        headers=_auth_headers(),
    )
    assert setup.status_code == 200

    fix = client.post(
        "/wizard/model/fix",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2"},
        headers=_auth_headers(),
    )
    assert fix.status_code == 200
    download_url = fix.json()["download_url"]

    unauthenticated = client.get(download_url)
    assert unauthenticated.status_code == 401

    authenticated = client.get(download_url, headers=_auth_headers(token="dev-viewer-key"))
    assert authenticated.status_code == 200
    assert "attachment; filename=" in authenticated.headers.get("content-disposition", "")


def test_save_upload_streams_large_file_in_chunks(tmp_path, monkeypatch):
    class ChunkGuard(io.BytesIO):
        def __init__(self, data: bytes):
            super().__init__(data)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            if size < 0:
                raise AssertionError("_save_upload should not read the whole upload into memory at once")
            return super().read(size)

    payload = (b"0123456789abcdef" * 128_000)
    guarded = ChunkGuard(payload)
    upload = UploadFile(filename="large.stl", file=guarded)

    saved = main._save_upload(upload)
    try:
        assert saved.exists()
        assert saved.read_bytes() == payload
        assert guarded.read_sizes
        assert all(size == 1024 * 1024 for size in guarded.read_sizes[:-1])
    finally:
        saved.unlink(missing_ok=True)


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


def test_wizard_model_check_reports_live_progress_status(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    status_client = TestClient(main.app)

    def fake_run_phase_1_geometry(
        *,
        file_path: str,
        slice_height_mm: float = 0.01,
        auto_repair: bool = True,
        analysis_level: str = "balanced",
        progress_callback=None,
        cancel_check=None,
    ) -> GeometryAnalysis:
        assert file_path.endswith(".stl")
        assert slice_height_mm == 0.2
        assert auto_repair is False
        assert analysis_level == "balanced"
        if progress_callback is not None:
            progress_callback("cross_section", "Computing cross-sections through the model.")
        time.sleep(0.12)
        if progress_callback is not None:
            progress_callback("voxelize", "Voxelizing the mesh for cavity and island detection.")
        time.sleep(0.12)
        return GeometryAnalysis(**_analysis_payload())

    monkeypatch.setattr(main.pipeline, "run_phase_1_geometry", fake_run_phase_1_geometry)

    job_id = "progress-check-001"
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: client.post(
                "/wizard/model/check",
                files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
                data={"slice_height_mm": "0.2", "analysis_level": "balanced", "progress_job_id": job_id},
            )
        )

        running_progress = None
        for _ in range(50):
            status = status_client.get(f"/wizard/model/check/status/{job_id}")
            assert status.status_code == 200
            body = status.json()
            if body["status"] == "running":
                running_progress = body
                break
            time.sleep(0.02)

        response = future.result()

    assert running_progress is not None
    assert running_progress["stage"] in {"load_prepare_mesh", "cross_section", "voxelize"}
    assert running_progress["message"]
    assert response.status_code == 200

    final_status = status_client.get(f"/wizard/model/check/status/{job_id}")
    assert final_status.status_code == 200
    final_body = final_status.json()
    assert final_body["status"] == "completed"
    assert final_body["stage"] == "completed"
    assert final_body["performance_ms"]["total"] == 25.0
    assert final_body["stage_timings_ms"]["cross_section"] == 12.0
    assert final_body["stage_timings_ms"]["voxelize"] == 5.0


def test_wizard_model_check_failure_reports_stage_detail(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_run_phase_1_geometry(
        *,
        file_path: str,
        slice_height_mm: float = 0.01,
        auto_repair: bool = True,
        analysis_level: str = "balanced",
        progress_callback=None,
        cancel_check=None,
    ):
        assert file_path.endswith(".stl")
        assert slice_height_mm == 0.2
        assert auto_repair is False
        assert analysis_level == "balanced"
        if progress_callback is not None:
            progress_callback("voxelize", "Voxelizing the mesh for cavity and island detection.")
        raise RuntimeError("grid overflow")

    monkeypatch.setattr(main.pipeline, "run_phase_1_geometry", fake_run_phase_1_geometry)

    job_id = "progress-check-failure"
    response = client.post(
        "/wizard/model/check",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2", "analysis_level": "balanced", "progress_job_id": job_id},
    )

    assert response.status_code == 400
    assert "voxelization" in response.json()["detail"]
    assert "grid overflow" in response.json()["detail"]

    status = client.get(f"/wizard/model/check/status/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "failed"
    assert body["stage"] == "voxelize"
    assert "grid overflow" in body["message"]


def test_wizard_model_check_can_be_cancelled(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    status_client = TestClient(main.app)

    def fake_run_phase_1_geometry(
        *,
        file_path: str,
        slice_height_mm: float = 0.01,
        auto_repair: bool = True,
        analysis_level: str = "balanced",
        progress_callback=None,
        cancel_check=None,
    ) -> GeometryAnalysis:
        assert file_path.endswith(".stl")
        assert slice_height_mm == 0.2
        assert auto_repair is False
        assert analysis_level == "balanced"
        assert cancel_check is not None
        for index in range(40):
            if progress_callback is not None:
                progress_callback(
                    "detect_islands",
                    f"Checking sliced layers for unsupported islands. Processed {index + 1}/40 layers.",
                )
            cancel_check()
            time.sleep(0.02)
        return GeometryAnalysis(**_analysis_payload())

    monkeypatch.setattr(main.pipeline, "run_phase_1_geometry", fake_run_phase_1_geometry)

    job_id = "progress-check-cancel"
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            lambda: client.post(
                "/wizard/model/check",
                files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
                data={"slice_height_mm": "0.2", "analysis_level": "balanced", "progress_job_id": job_id},
            )
        )

        running_progress = None
        for _ in range(80):
            status = status_client.get(f"/wizard/model/check/status/{job_id}")
            assert status.status_code == 200
            body = status.json()
            if body["status"] == "running" and body["stage"] == "detect_islands":
                running_progress = body
                break
            time.sleep(0.02)

        assert running_progress is not None

        cancel = status_client.post(f"/wizard/model/check/status/{job_id}/cancel")
        assert cancel.status_code == 200
        cancel_body = cancel.json()
        assert cancel_body["status"] == "cancelling"
        assert cancel_body["cancel_requested"] is True

        response = future.result()

    assert response.status_code == 409
    assert "cancelled by user" in response.json()["detail"].lower()
    assert "island detection" in response.json()["detail"].lower()

    final_status = status_client.get(f"/wizard/model/check/status/{job_id}")
    assert final_status.status_code == 200
    final_body = final_status.json()
    assert final_body["status"] == "cancelled"
    assert final_body["stage"] == "detect_islands"
    assert final_body["cancel_requested"] is True
    assert "cancelled by user" in final_body["message"].lower()
