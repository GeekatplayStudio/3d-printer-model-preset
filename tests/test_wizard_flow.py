from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
from app.wizard_artifact_store import init_wizard_artifact_store
from app.wizard_analysis_store import init_wizard_analysis_store


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
    wizard_artifacts_path = tmp_path / "wizard_artifacts.db"
    wizard_progress_path = tmp_path / "wizard_analysis_progress.db"
    init_catalog_store(db_path=catalog_path)
    init_audit_store(db_path=audit_path)
    init_job_store(db_path=jobs_path)
    init_sync_schedule_store(db_path=schedules_path)
    init_wizard_artifact_store(db_path=wizard_artifacts_path)
    init_wizard_analysis_store(db_path=wizard_progress_path)
    monkeypatch.setenv("RESINLOGIC_ENABLE_SCHEDULER", "0")
    monkeypatch.setattr(main, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(main, "AUDIT_PATH", audit_path)
    monkeypatch.setattr(main, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(main, "SCHEDULES_PATH", schedules_path)
    monkeypatch.setattr(main, "WIZARD_ARTIFACTS_PATH", wizard_artifacts_path)
    monkeypatch.setattr(main, "WIZARD_ANALYSIS_PROGRESS_PATH", wizard_progress_path)
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

    root = client.get("/", follow_redirects=False)
    assert root.status_code in {302, 307}
    assert root.headers["location"] == "/wizard"

    ui = client.get("/wizard")
    assert ui.status_code == 200
    assert "Geekatplay Studio Wizard" in ui.text
    assert "/static/geekatplay-mark.svg" in ui.text
    assert "Hover any" in ui.text
    assert "data-help-key=\"analysis_depth\"" in ui.text
    assert "Step 0: Select Analyzer Target" in ui.text
    assert "targetSel" in ui.text
    assert "Ultimaker Cura" in ui.text
    assert "dbSourceMode" in ui.text
    assert "GitHub sync feed" in ui.text
    assert "Web JSON feed" in ui.text
    assert "Supported vendor pages" in ui.text
    assert "dbScrapeUrls" in ui.text
    assert "runUpdateNowBtn" in ui.text
    assert "dbUpdateStatusMsg" in ui.text
    assert "Open Advanced Mode" in ui.text
    assert "downloadArtifact(" in ui.text
    assert "Show Catalog List" in ui.text
    assert "Analysis depth" in ui.text
    assert "/wizard/model/check/status/" in ui.text
    assert "progress_job_id" in ui.text
    assert "cancelAnalyzeBtn" in ui.text
    assert "analysisProgressPanel" in ui.text
    assert "checkApiReachability" in ui.text
    assert "/health" in ui.text
    assert "The wizard controls were unlocked locally." in ui.text
    assert "Large-model fallback is active" in ui.text
    assert "API missed a brief health check" in ui.text
    assert "Auto-Fix re-checked the saved repaired STL" in ui.text
    assert "Auto-Fix saved the repaired STL and re-checked it" in ui.text
    assert "select option," in ui.text
    assert "dbScanPanel" in ui.text
    assert "modelPreviewPanel" in ui.text
    assert "modelStatsPanel" in ui.text
    assert "modelRepairPanel" in ui.text
    assert "modelRetopologyPanel" in ui.text
    assert "Detected Issues" in ui.text
    assert "Retopology and Download STL" in ui.text
    assert "/wizard/model/retopology" in ui.text
    assert "/static/vendor/three/build/three.module.js" in ui.text
    assert "/static/vendor/three/build/three.core.js" in ui.text
    assert "3D preview runtime load failed" in ui.text
    assert "https://cdn.jsdelivr.net" not in ui.text
    assert "/wizard/database/gaps" in ui.text
    assert "/phase3/history/query" in ui.text
    assert "Smart check:" in ui.text
    assert "/phase3/history/summary" in ui.text
    assert "Catalog Provenance" in ui.text
    assert "catalogSelectionPanel" in ui.text
    assert "catalogSelectionDetails" in ui.text


def test_wizard_static_preview_assets_are_served(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    brand_mark = client.get("/static/geekatplay-mark.svg")
    assert brand_mark.status_code == 200
    assert "svg" in brand_mark.text

    three = client.get("/static/vendor/three/build/three.module.js")
    assert three.status_code == 200
    assert len(three.text) > 1000

    three_core = client.get("/static/vendor/three/build/three.core.js")
    assert three_core.status_code == 200
    assert len(three_core.text) > 1000

    controls = client.get("/static/vendor/three/examples/jsm/controls/OrbitControls.js")
    assert controls.status_code == 200
    assert "OrbitControls" in controls.text

    loader = client.get("/static/vendor/three/examples/jsm/loaders/STLLoader.js")
    assert loader.status_code == 200
    assert "STLLoader" in loader.text

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
    assert "materials" in options_body
    assert "compatibility" in options_body
    assert "targets" in options_body
    assert "msla" in options_body["targets"]
    assert "fdm" in options_body["targets"]
    assert options_body["slicers_by_target"]["msla"] == "Chitubox Free"
    assert options_body["slicers_by_target"]["fdm"] == "Ultimaker Cura"
    assert options_body["materials_by_target"]["msla"]
    assert "Elegoo Mars 4 Ultra" in options_body["printers_by_target"]["msla"]
    assert "Elegoo Mars 4" in options_body["printers_by_target"]["msla"]
    assert "Elegoo Mars 3" in options_body["printers_by_target"]["msla"]
    assert options_body["printers_by_target"]["fdm"]
    assert options_body["materials_by_target"]["fdm"]
    assert len(options_body["printers_by_target"]["fdm"]) >= 5
    assert len(options_body["materials_by_target"]["fdm"]) >= 13
    assert "Bambu Lab A1" in options_body["printers_by_target"]["fdm"]
    assert "Generic ABS" in options_body["materials_by_target"]["fdm"]
    assert "Anycubic ABS Filament" in options_body["materials_by_target"]["fdm"]
    assert isinstance(options_body["compatibility"], dict)
    assert options_body["printer_details_by_target"]["fdm"]
    assert options_body["material_details_by_target"]["fdm"]
    assert options_body["compatibility_details_by_target"]["fdm"]

    printer_details = {item["name"]: item for item in options_body["printer_details_by_target"]["fdm"]}
    msla_printer_details = {item["name"]: item for item in options_body["printer_details_by_target"]["msla"]}
    material_details = {item["name"]: item for item in options_body["material_details_by_target"]["fdm"]}

    assert printer_details["Bambu Lab A1"]["provenance"]["source_priority_tier"] == 2
    assert msla_printer_details["Elegoo Mars 4 Ultra"]["provenance"]["source_url"].startswith(
        "https://us.elegoo.com/products/elegoo-mars-4-ultra-msla-resin-3d-printer-with-9k-mono-lcd"
    )
    assert msla_printer_details["Elegoo Mars 4"]["provenance"]["source_url"].startswith(
        "https://us.elegoo.com/products/elegoo-mars-4-msla-resin-3d-printer-with-9k-mono-lcd"
    )
    assert material_details["Anycubic ABS Filament"]["provenance"]["source_priority_tier"] == 1
    assert material_details["Anycubic ABS Filament"]["provenance"]["source_url"].startswith(
        "https://store.anycubic.com/products/abs-filament"
    )
    assert material_details["Prusament PLA Galaxy Black"]["provenance"]["source_url"].startswith(
        "https://www.prusa3d.com/product/prusament-pla-prusa-galaxy-black-1kg-nfc/"
    )

    compatibility_detail = options_body["compatibility_details_by_target"]["fdm"]["Anycubic Kobra S1"][
        "Anycubic ABS Filament"
    ]
    assert compatibility_detail["profile_name"] == "Anycubic ABS Structural"
    assert compatibility_detail["provenance"]["source_priority_tier"] == 1
    assert compatibility_detail["provenance"]["confidence"] >= 0.8

    high_speed_detail = options_body["compatibility_details_by_target"]["fdm"]["Anycubic Kobra S1"][
        "Anycubic High Speed PLA"
    ]
    assert high_speed_detail["profile_name"] == "Anycubic High Speed PLA Rapid"
    assert high_speed_detail["provenance"]["source_priority_tier"] == 1


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


def test_wizard_web_json_setup_and_update_controls(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_fetch(*, url: str, timeout_s: float = 20.0):
        assert url == "https://catalog.example.com/sync.json"
        return (
            {
                "printers": [{"name": "Web Printer", "technology": "FDM"}],
                "resins": [{"name": "Web Filament", "material_type": "filament", "material_family": "PLA"}],
                "profiles": [
                    {
                        "printer_name": "Web Printer",
                        "resin_name": "Web Filament",
                        "profile_name": "Web PLA Profile",
                        "layer_height_mm": 0.2,
                        "nozzle_temp_c": 210.0,
                        "bed_temp_c": 60.0,
                        "print_speed_mm_s": 55.0,
                        "is_default": True,
                        "is_active": True,
                    }
                ],
            },
            "https://catalog.example.com/sync.json",
        )

    monkeypatch.setattr(main, "fetch_technical_sync_from_url", fake_fetch)

    setup = client.post(
        "/wizard/database/setup",
        json={
            "mode": "web_json",
            "url": "https://catalog.example.com/sync.json",
            "replace_existing": True,
            "auto_update": True,
            "auto_update_interval_seconds": 900,
            "source": "wizard_web_test",
        },
    )
    assert setup.status_code == 200
    setup_body = setup.json()
    assert setup_body["mode"] == "web_json"
    assert setup_body["raw_url"] == "https://catalog.example.com/sync.json"
    assert setup_body["auto_update_schedule_id"] is not None
    schedule_id = setup_body["auto_update_schedule_id"]

    status = client.get("/wizard/database/status")
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["ready_for_model_analysis"] is True
    assert status_body["ready_for_settings"] is True

    updates = client.get("/wizard/updates/status")
    assert updates.status_code == 200
    updates_body = updates.json()
    assert updates_body["wizard_auto_update_present"] is True
    schedule = next(item for item in updates_body["schedules"] if item["id"] == schedule_id)
    assert schedule["web_url"] == "https://catalog.example.com/sync.json"

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



def test_wizard_web_scrape_setup_and_update_controls(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_scrape(*, urls: list[str], timeout_s: float = 20.0, fetch_html=None):
        assert urls == [
            "https://store.anycubic.com/products/photon-mono-m7-pro",
            "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
        ]
        assert timeout_s == 20.0
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

    setup = client.post(
        "/wizard/database/setup",
        json={
            "mode": "web_scrape",
            "scrape_urls": [
                "https://store.anycubic.com/products/photon-mono-m7-pro",
                "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
            ],
            "replace_existing": True,
            "auto_update": True,
            "auto_update_interval_seconds": 1200,
            "source": "wizard_scrape_test",
        },
    )
    assert setup.status_code == 200
    setup_body = setup.json()
    assert setup_body["mode"] == "web_scrape"
    assert setup_body["auto_update_schedule_id"] is not None
    schedule_id = setup_body["auto_update_schedule_id"]

    updates = client.get("/wizard/updates/status")
    assert updates.status_code == 200
    schedule = next(item for item in updates.json()["schedules"] if item["id"] == schedule_id)
    assert schedule["scrape_urls"] == [
        "https://store.anycubic.com/products/photon-mono-m7-pro",
        "https://store.anycubic.com/blogs/news/resin-settings-for-anycubic-3d-printers",
    ]

    run_now = client.post("/wizard/updates/run-now", json={"schedule_id": schedule_id})
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
    assert any("Mesh is not watertight." == issue for issue in check_body["fix_reasons"])

    fix = client.post(
        "/wizard/model/fix",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2"},
    )
    assert fix.status_code == 200
    fix_body = fix.json()
    assert fix_body["download_url"].startswith("/wizard/download/")
    assert fix_body["repaired"] is True
    assert fix_body["fully_repaired"] is False
    assert fix_body["rechecked_after_fix"] is True
    assert "saved the repaired STL and re-checked it" in fix_body["recheck_summary"]
    assert fix_body["before_fix"]["duplicate_face_count"] >= 1
    assert fix_body["after_fix"]["duplicate_face_count"] == 0
    assert any("Mesh is not watertight." == issue for issue in fix_body["remaining_issues"])
    assert fix_body["resolved_issues"]

    repaired_download = client.get(fix_body["download_url"])
    assert repaired_download.status_code == 200
    assert len(repaired_download.content) > 100

    options = client.get("/wizard/catalog/options")
    assert options.status_code == 200
    options_body = options.json()
    printer = options_body["printers_by_target"]["msla"][0]
    compatibility = options_body.get("compatibility_by_target") or {}
    compatible_resins = compatibility.get("msla", {}).get(printer) or options_body["materials_by_target"]["msla"]
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
    assert settings_body["target_process"] == "msla"
    assert settings_body["slicer_name"] == "Chitubox Free"
    assert settings_body["slicer_profile_download_url"] == settings_body["cfg_download_url"]
    assert settings_body["slicer_profile_file_name"].endswith(".cfg")
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


def test_wizard_heavy_stl_endpoints_offload_blocking_work(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    recorded_calls: list[str] = []

    async def fake_to_thread(func, *args, **kwargs):
        recorded_calls.append(getattr(func, "__name__", repr(func)))
        return func(*args, **kwargs)

    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)
    def fake_run_full_pipeline(**kwargs):
        return {
            "analysis": _analysis_payload(),
            "settings": {
                "printer": kwargs["printer"],
                "resin_type": kwargs["resin_type"],
                "use_case": kwargs["use_case"],
                "intent_used": kwargs["use_case"],
                "layer_height_mm": 0.05,
                "exposure_s": 2.2,
                "bottom_exposure_s": 30.0,
                "tilt_speed_mm_min": 90.0,
                "tilt_speed_mm_h": 90.0,
                "tilt_angle_deg": -1.0,
                "rest_time_before_print_s": 2.0,
                "rest_time_after_retract_s": 0.5,
                "transition_layers": 5,
                "scale_compensation_percent": 100.0,
                "heater_required": False,
                "anti_aliasing": None,
                "grayscale_level": None,
                "xy_resolution_um": 18,
                "detail_tier": "medium_detail",
                "multi_parameter": {
                    "model_exposure_s": 2.1,
                    "support_exposure_s": 2.4,
                    "delicate_feature_exposure_s": 2.0,
                },
                "warnings": [],
                "recommendations": [],
                "source_profile": "Mock Profile",
            },
            "chitubox_cfg": "layer_height_mm=0.05\n",
        }

    fake_run_full_pipeline.__name__ = "run_full_pipeline"

    monkeypatch.setattr(main.pipeline, "run_full_pipeline", fake_run_full_pipeline)

    fix = client.post(
        "/wizard/model/fix",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2"},
    )
    assert fix.status_code == 200
    assert "_save_upload" in recorded_calls
    assert "repair_mesh_file" in recorded_calls
    assert "run_phase_1_geometry" in recorded_calls

    recorded_calls.clear()

    analyze = client.post(
        "/phase1/analyze",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2", "auto_repair": "true", "analysis_level": "balanced"},
    )
    assert analyze.status_code == 200
    assert recorded_calls.count("_save_upload") == 1
    assert "run_phase_1_geometry" in recorded_calls

    recorded_calls.clear()

    pipeline_response = client.post(
        "/pipeline",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={
            "resin_type": "Resin Mock",
            "use_case": "miniature",
            "printer": "Printer Mock",
            "slice_height_mm": "0.2",
            "analysis_level": "balanced",
        },
    )
    assert pipeline_response.status_code == 200
    assert recorded_calls.count("_save_upload") == 1
    assert "run_full_pipeline" in recorded_calls

    recorded_calls.clear()

    def fake_submit(*, job_type, actor, metadata, fn):
        return {
            "id": "job-pipeline-001",
            "type": job_type,
            "status": "queued",
            "actor": actor,
            "metadata": metadata,
            "created_at": "2026-04-25T00:00:00+00:00",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "result": None,
        }

    monkeypatch.setattr(main.JOB_QUEUE, "submit", fake_submit)

    queued_pipeline_response = client.post(
        "/jobs/pipeline",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={
            "resin_type": "Resin Mock",
            "use_case": "miniature",
            "printer": "Printer Mock",
            "slice_height_mm": "0.2",
            "analysis_level": "balanced",
        },
    )
    assert queued_pipeline_response.status_code == 200
    assert recorded_calls.count("_save_upload") == 1
    assert "run_full_pipeline" not in recorded_calls


def test_wizard_settings_recommend_returns_cura_profile_for_fdm(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    imported = client.post(
        "/catalog/import",
        json={
            "replace_existing": True,
            "printers": [
                {
                    "name": "Prusa MK4S",
                    "technology": "FDM",
                    "xy_resolution_um": 50,
                }
            ],
            "resins": [
                {
                    "name": "Prusament PLA Galaxy Black",
                    "material_type": "filament",
                    "material_family": "PLA",
                    "nozzle_temp_min_c": 205.0,
                    "nozzle_temp_max_c": 215.0,
                    "bed_temp_c": 60.0,
                }
            ],
            "profiles": [
                {
                    "printer_name": "Prusa MK4S",
                    "resin_name": "Prusament PLA Galaxy Black",
                    "profile_name": "PLA Quality",
                    "layer_height_mm": 0.2,
                    "nozzle_temp_c": 210.0,
                    "bed_temp_c": 60.0,
                    "print_speed_mm_s": 55.0,
                    "first_layer_speed_mm_s": 20.0,
                    "travel_speed_mm_s": 180.0,
                    "retraction_distance_mm": 0.8,
                    "retraction_speed_mm_s": 35.0,
                    "nozzle_diameter_mm": 0.4,
                    "fan_speed_percent": 100,
                    "infill_percent": 15.0,
                    "wall_count": 2,
                    "support_style": "tree",
                    "is_default": True,
                    "is_active": True,
                }
            ],
        },
    )
    assert imported.status_code == 200

    options = client.get("/wizard/catalog/options")
    assert options.status_code == 200
    options_body = options.json()
    assert "fdm" in options_body["targets"]
    assert options_body["slicers_by_target"]["fdm"] == "Ultimaker Cura"
    assert "Prusa MK4S" in options_body["printers_by_target"]["fdm"]

    settings = client.post(
        "/wizard/settings/recommend",
        json={
            "analysis": _analysis_payload(),
            "printer": "Prusa MK4S",
            "resin_type": "Prusament PLA Galaxy Black",
            "target_process": "fdm",
            "use_case": "collectible",
            "ambient_temp_c": 17.0,
            "film_releases": 0,
        },
    )
    assert settings.status_code == 200
    settings_body = settings.json()
    assert settings_body["target_process"] == "fdm"
    assert settings_body["slicer_name"] == "Ultimaker Cura"
    assert settings_body["slicer_profile_download_url"].startswith("/wizard/download/")
    assert settings_body["slicer_profile_file_name"].endswith(".curaprofile")
    assert settings_body["cfg_download_url"] == settings_body["slicer_profile_download_url"]
    assert settings_body["chitubox_cfg"] is None

    profile_download = client.get(settings_body["slicer_profile_download_url"])
    assert profile_download.status_code == 200
    assert "[values]" in profile_download.text
    assert "material_print_temperature" in profile_download.text


def test_wizard_model_retopology_runs_repair_prepass_and_downloads(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    def fake_blender_retopology(**kwargs):
        mesh = trimesh.creation.icosphere(subdivisions=1, radius=1.0)
        Path(kwargs["output_path"]).write_bytes(mesh.export(file_type="stl"))
        return [
            "Retopology backend: Blender (stubbed for tests).",
            f"Mode: {kwargs['mode']}",
        ]

    monkeypatch.setattr(main, "run_blender_retopology", fake_blender_retopology)

    response = client.post(
        "/wizard/model/retopology",
        files={"file": ("broken.stl", _broken_mesh_bytes(), "model/stl")},
        data={"slice_height_mm": "0.2", "analysis_level": "extreme", "mode": "quad"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "quad"
    assert body["backend_used"] == "blender"
    assert body["source_analysis"]["analysis_level"] == "extreme"
    assert body["retopology_analysis"]["analysis_level"] == "extreme"
    assert body["source_analysis"]["mesh_health"]["duplicate_face_count"] >= 1
    assert body["retopology_analysis"]["triangle_count"] > 0
    assert body["preprocessing_fix"] is not None
    assert body["target_faces"] >= 200
    assert body["download_url"].startswith("/wizard/download/")
    assert any("repair pre-pass" in note for note in body["notes"])

    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert len(download.content) > 100


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
