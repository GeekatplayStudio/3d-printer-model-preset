from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.audit_store import (
    create_catalog_version,
    get_catalog_version,
    init_audit_store,
    list_audit_events,
    list_catalog_versions,
    log_audit_event,
    restore_catalog_version,
)
from app.auth import AuthContext, auth_enforced, require_role, standalone_mode
from app.camera_log import analyze_camera_log
from app.chitubox import render_chitubox_cfg
from app.catalog_store import (
    create_or_upsert_printer,
    create_or_upsert_profile,
    create_or_upsert_resin,
    delete_printer,
    delete_profile,
    delete_resin,
    export_catalog,
    get_printer,
    get_profile,
    get_resin,
    import_catalog,
    import_catalog_csv,
    init_catalog_store,
    list_printers,
    list_profiles,
    list_resins as list_catalog_resins,
    seed_catalog_from_legacy_json,
    update_printer,
    update_profile,
    update_resin,
)
from app.cura import render_cura_profile
from app.feedback import summarize_feedback
from app.feedback_adaptation import adapt_settings_from_feedback
from app.feedback_sources import (
    ingest_community_sheet_bytes,
    ingest_community_sheet_url,
    ingest_youtube_transcripts,
)
from app.feedback_store import (
    count_feedback_records,
    init_feedback_store,
    query_feedback_records,
    save_feedback_records,
)
from app.geometry import AnalysisCancelledError, mesh_health_actionable_issues, mesh_health_requires_fix, repair_mesh_file
from app.job_queue import InMemoryJobQueue
from app.job_store import cleanup_jobs, count_jobs, init_job_store
from app.monitoring import InMemoryMetrics
from app.models import (
    AnalysisLevel,
    AsyncJob,
    AuthWhoAmI,
    AuditEvent,
    CameraLogAnalysisResult,
    CatalogCsvImportResult,
    CatalogImportPayload,
    CatalogImportResult,
    CatalogPrinter,
    CatalogPrinterCreate,
    CatalogPrinterUpdate,
    CatalogProfile,
    CatalogProfileCreate,
    CatalogProfileUpdate,
    CatalogResin,
    CatalogResinCreate,
    CatalogResinUpdate,
    CatalogVersion,
    CatalogVersionCreateRequest,
    CatalogVersionDetail,
    CatalogVersionRestoreRequest,
    CommunitySheetURLRequest,
    JobCancelResponse,
    JobCleanupRequest,
    JobCleanupResponse,
    FeedbackBatchRequest,
    FeedbackHistoryQueryRequest,
    FeedbackHistoryQueryResult,
    FeedbackHistorySummaryRequest,
    FeedbackHistorySummaryResult,
    FeedbackIngestionResult,
    FeedbackSummary,
    GeometryAnalysis,
    GitHubTechnicalSyncRequest,
    GitHubTechnicalSyncResponse,
    HistoryAwareOptimizeRequest,
    HistoryAwareOptimizeResponse,
    MetricsResponse,
    OptimizeRequest,
    OptimalSettings,
    PipelineAsyncSubmitResponse,
    PipelineResponse,
    SyncSchedule,
    SyncScheduleCreate,
    SyncScheduleUpdate,
    SyncSchedulerHealth,
    SyncSchedulerTickResult,
    TechnicalSyncRequest,
    TechnicalSyncResponse,
    UseCase,
    WebScrapeTechnicalSyncRequest,
    WebScrapeTechnicalSyncResponse,
    WizardCatalogOptionsResponse,
    WizardAnalysisProgressResponse,
    WizardDatabaseGapSummary,
    WizardDatabaseSetupRequest,
    WizardDatabaseSetupResponse,
    WizardDatabaseStatus,
    WizardModelCheckResponse,
    WizardModelFixResponse,
    WizardModelRetopologyResponse,
    WizardRunUpdateNowRequest,
    WizardSettingsRequest,
    WizardSettingsResponse,
    WizardUpdateScheduleStatus,
    WizardUpdateStatusResponse,
    RetopologyMode,
    YouTubeIngestRequest,
)
from app.pipeline import ModularAgenticPipeline
from app.retopology import RetopologyError, run_blender_retopology
from app.resin_db import load_resin_database
from app.scheduler import TechnicalSyncScheduler
from app.official_catalog import load_official_sync_payload, official_sync_paths
from app.sync_service import (
    apply_technical_sync,
    fetch_technical_sync_from_github,
    fetch_technical_sync_from_url,
    parse_technical_sync_json,
)
from app.web_catalog_scraper import scrape_supported_catalog_pages
from app.sync_schedule_store import (
    create_or_upsert_sync_schedule,
    delete_sync_schedule,
    get_sync_schedule,
    init_sync_schedule_store,
    list_sync_schedules,
    mark_sync_schedule_run,
    update_sync_schedule,
)
from app.wizard_artifact_store import (
    cleanup_wizard_artifacts as cleanup_wizard_artifacts_store,
    create_wizard_artifact,
    delete_wizard_artifact,
    get_wizard_artifact,
    init_wizard_artifact_store,
)
from app.wizard_analysis_store import (
    cleanup_wizard_analysis_progress as cleanup_wizard_analysis_progress_store,
    get_wizard_analysis_progress,
    init_wizard_analysis_store,
    upsert_wizard_analysis_progress,
)

def _resolve_local_data_dir() -> Path:
    configured = os.getenv("RESINLOGIC_DATA_DIR")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    else:
        candidates.append(Path.home() / ".resinlogic")
    candidates.append(Path.cwd() / ".resinlogic")
    candidates.append(Path(tempfile.gettempdir()) / "resinlogic")

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except PermissionError:
            continue

    raise RuntimeError("Unable to initialize local data directory. Set RESINLOGIC_DATA_DIR to a writable path.")


DATA_DIR = _resolve_local_data_dir()
os.environ.setdefault("RESINLOGIC_DATA_DIR", str(DATA_DIR))

app = FastAPI(
    title="ResinLogic Local",
    version="0.1.0",
    description="Local-first resin printing workflow with per-user SQLite storage.",
)
pipeline = ModularAgenticPipeline()
STORAGE_PATH = init_feedback_store(DATA_DIR / "feedback_history.db")
CATALOG_PATH = init_catalog_store(DATA_DIR / "tech_catalog.db")
AUDIT_PATH = init_audit_store(DATA_DIR / "audit_log.db")
JOBS_PATH = init_job_store(DATA_DIR / "jobs.db")
SCHEDULES_PATH = init_sync_schedule_store(DATA_DIR / "sync_schedules.db")
WIZARD_ARTIFACTS_PATH = init_wizard_artifact_store(DATA_DIR / "wizard_artifacts.db")
WIZARD_ANALYSIS_PROGRESS_PATH = init_wizard_analysis_store(DATA_DIR / "wizard_analysis_progress.db")
seed_catalog_from_legacy_json(db_path=CATALOG_PATH)
STATIC_DIR = Path(__file__).resolve().parent / "static"
ADMIN_UI_PATH = STATIC_DIR / "catalog_admin.html"
APP_UI_PATH = STATIC_DIR / "app.html"
WIZARD_UI_PATH = STATIC_DIR / "wizard.html"
WIZARD_ARTIFACTS_DIR = DATA_DIR / "wizard_artifacts"
WIZARD_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
WIZARD_ARTIFACT_TTL_SECONDS = 7 * 86400
WIZARD_SLICERS_BY_TARGET = {
    "msla": "Chitubox Free",
    "fdm": "Ultimaker Cura",
}
WIZARD_ANALYSIS_PROGRESS_TTL_SECONDS = 3600
JOB_QUEUE = InMemoryJobQueue(max_workers=2, db_path=JOBS_PATH)
METRICS = InMemoryMetrics()
SCHEDULER: TechnicalSyncScheduler | None = None
WIZARD_ANALYSIS_STAGE_LABELS = {
    "save_upload": "upload staging",
    "load_prepare_mesh": "mesh loading and topology checks",
    "adapt_profile": "runtime profile adjustment",
    "cross_section": "cross-section analysis",
    "voxelize": "voxelization",
    "detect_suction_cups": "cavity detection",
    "detect_islands": "island detection",
    "curvature_proxy": "surface detail estimation",
    "derive_metrics": "final metric derivation",
    "finalize": "report finalization",
    "completed": "completed analysis",
}
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _cleanup_wizard_artifacts() -> None:
    stale_artifacts = cleanup_wizard_artifacts_store(
        max_age_seconds=WIZARD_ARTIFACT_TTL_SECONDS,
        db_path=WIZARD_ARTIFACTS_PATH,
    )
    for meta in stale_artifacts:
        _safe_unlink(Path(str(meta.get("path", ""))))


def _register_wizard_artifact(path: Path, *, download_name: str, media_type: str) -> str:
    _cleanup_wizard_artifacts()
    artifact_id = uuid4().hex
    create_wizard_artifact(
        artifact_id=artifact_id,
        path=path,
        download_name=download_name,
        media_type=media_type,
        db_path=WIZARD_ARTIFACTS_PATH,
    )
    return artifact_id


def _wizard_artifact(artifact_id: str) -> dict[str, object]:
    _cleanup_wizard_artifacts()
    meta = get_wizard_artifact(artifact_id, db_path=WIZARD_ARTIFACTS_PATH)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found or expired.")
    path = Path(str(meta.get("path", "")))
    if not path.exists():
        delete_wizard_artifact(artifact_id, db_path=WIZARD_ARTIFACTS_PATH)
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' is no longer available.")
    return meta


def _wizard_download_url(artifact_id: str) -> str:
    return f"/wizard/download/{artifact_id}"


def _normalize_wizard_target(value: str | None) -> str:
    normalized = str(value or "msla").strip().lower()
    return "fdm" if normalized == "fdm" else "msla"


def _wizard_target_from_catalog_item(item: dict[str, object] | None) -> str:
    if not item:
        return "msla"
    printer_technology = str(item.get("printer_technology") or item.get("technology") or "").strip().upper()
    material_type = str(item.get("material_type") or "").strip().lower()
    if material_type == "filament" or printer_technology in {"FDM", "FFF"}:
        return "fdm"
    return "msla"


def _wizard_target_from_settings(settings: OptimalSettings) -> str:
    if str(settings.process_technology).strip().upper() in {"FDM", "FFF"}:
        return "fdm"
    return "msla"


def _default_retopology_target_faces(analysis: GeometryAnalysis) -> int:
    base_faces = max(1, int(analysis.triangle_count))
    return max(200, min(int(round(base_faces * 0.5)), 120_000))


def _default_retopology_voxel_size_mm(analysis: GeometryAnalysis) -> float:
    dimensions = [float(value) for value in (analysis.bounding_box_mm or []) if value is not None]
    max_dimension = max(dimensions) if dimensions else 24.0
    return round(min(max(max_dimension / 180.0, 0.05), 0.85), 4)


def _normalize_wizard_analysis_job_id(job_id: str | None) -> str | None:
    if job_id is None:
        return None
    trimmed = job_id.strip()
    if not trimmed:
        return None
    return trimmed[:128]


def _cleanup_wizard_analysis_progress() -> None:
    cleanup_wizard_analysis_progress_store(
        max_age_seconds=WIZARD_ANALYSIS_PROGRESS_TTL_SECONDS,
        db_path=WIZARD_ANALYSIS_PROGRESS_PATH,
    )


def _wizard_analysis_progress_meta(job_id: str | None) -> dict[str, object]:
    if job_id is None:
        return {}
    return dict(get_wizard_analysis_progress(job_id, db_path=WIZARD_ANALYSIS_PROGRESS_PATH) or {})


def _wizard_analysis_status_is_terminal(status: str) -> bool:
    return status in {"completed", "failed", "cancelled"}


def _wizard_analysis_stage_timings(
    previous: dict[str, object],
    *,
    current_stage: str | None,
    status: str,
    now: float,
    performance_ms: dict[str, float] | None,
) -> dict[str, float]:
    timings = dict(previous.get("stage_timings_ms") or {})
    previous_stage = previous.get("stage")
    previous_stage_started_at = float(previous.get("stage_started_at_ts", now))
    if previous_stage and (current_stage != previous_stage or _wizard_analysis_status_is_terminal(status)):
        elapsed_ms = round(max(0.0, now - previous_stage_started_at) * 1000.0, 3)
        if elapsed_ms > 0.0 or previous_stage not in timings:
            timings[str(previous_stage)] = elapsed_ms
    for key, value in (performance_ms or {}).items():
        if key == "total":
            continue
        timings[str(key)] = float(value)
    return timings


def _set_wizard_analysis_progress(
    job_id: str | None,
    *,
    status: str,
    message: str,
    stage: str | None = None,
    cancel_requested: bool | None = None,
    error: str | None = None,
    performance_ms: dict[str, float] | None = None,
) -> None:
    if job_id is None:
        return
    _cleanup_wizard_analysis_progress()
    now = time.time()
    previous = _wizard_analysis_progress_meta(job_id)
    current_stage = stage if stage is not None else previous.get("stage")
    stage_started_at = float(previous.get("stage_started_at_ts", now))
    if current_stage != previous.get("stage"):
        stage_started_at = now
    stored_cancel_requested = cancel_requested
    if stored_cancel_requested is None and not previous:
        stored_cancel_requested = False
    upsert_wizard_analysis_progress(
        job_id=job_id,
        status=status,
        stage=current_stage if isinstance(current_stage, str) else None,
        message=message,
        cancel_requested=stored_cancel_requested,
        error=error,
        started_at_ts=float(previous.get("started_at_ts", now)),
        stage_started_at_ts=stage_started_at,
        updated_at_ts=now,
        performance_ms=dict(performance_ms or previous.get("performance_ms") or {}),
        stage_timings_ms=_wizard_analysis_stage_timings(
            previous,
            current_stage=current_stage if isinstance(current_stage, str) else None,
            status=status,
            now=now,
            performance_ms=performance_ms,
        ),
        db_path=WIZARD_ANALYSIS_PROGRESS_PATH,
    )


def _wizard_analysis_progress_response(job_id: str) -> WizardAnalysisProgressResponse:
    _cleanup_wizard_analysis_progress()
    now = time.time()
    meta = _wizard_analysis_progress_meta(job_id)
    if not meta:
        return WizardAnalysisProgressResponse(
            job_id=job_id,
            status="unknown",
            stage=None,
            message="Waiting for analysis worker to start.",
            elapsed_seconds=0,
            stage_elapsed_seconds=0,
            updated_at=_iso_timestamp(),
            cancel_requested=False,
            error=None,
            performance_ms={},
            stage_timings_ms={},
        )

    updated_at_ts = float(meta.get("updated_at_ts", now))
    started_at_ts = float(meta.get("started_at_ts", now))
    stage_started_at_ts = float(meta.get("stage_started_at_ts", started_at_ts))
    return WizardAnalysisProgressResponse(
        job_id=job_id,
        status=str(meta.get("status", "unknown")),
        stage=meta.get("stage"),
        message=str(meta.get("message", "Analysis is running.")),
        elapsed_seconds=max(0, int(now - started_at_ts)),
        stage_elapsed_seconds=max(0, int(now - stage_started_at_ts)),
        updated_at=datetime.fromtimestamp(updated_at_ts, tz=timezone.utc).replace(microsecond=0).isoformat(),
        cancel_requested=bool(meta.get("cancel_requested", False)),
        error=meta.get("error"),
        performance_ms=dict(meta.get("performance_ms") or {}),
        stage_timings_ms=dict(meta.get("stage_timings_ms") or {}),
    )


def _wizard_analysis_runtime_status(job_id: str | None) -> str:
    if job_id is None:
        return "running"
    meta = _wizard_analysis_progress_meta(job_id)
    if meta and bool(meta.get("cancel_requested", False)) and not _wizard_analysis_status_is_terminal(str(meta.get("status", ""))):
        return "cancelling"
    return "running"


def _request_wizard_analysis_cancel(job_id: str) -> WizardAnalysisProgressResponse:
    _cleanup_wizard_analysis_progress()
    meta = _wizard_analysis_progress_meta(job_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Analysis job '{job_id}' was not found.")
    status = str(meta.get("status", "unknown"))
    if _wizard_analysis_status_is_terminal(status):
        return _wizard_analysis_progress_response(job_id)
    current_stage = meta.get("stage")
    _set_wizard_analysis_progress(
        job_id,
        status="cancelling",
        stage=current_stage,
        message=(
            f"Cancellation requested during {_analysis_stage_label(current_stage)}. "
            "Waiting for the current checkpoint to stop."
        ),
        cancel_requested=True,
    )
    return _wizard_analysis_progress_response(job_id)


def _raise_if_wizard_analysis_cancelled(job_id: str | None) -> None:
    if job_id is None:
        return
    _cleanup_wizard_analysis_progress()
    meta = _wizard_analysis_progress_meta(job_id)
    if not meta or not bool(meta.get("cancel_requested", False)):
        return
    raise AnalysisCancelledError(
        f"Analysis cancelled by user during {_analysis_stage_label(meta.get('stage'))}."
    )


def _analysis_stage_label(stage: str | None) -> str:
    if not stage:
        return "analysis"
    return WIZARD_ANALYSIS_STAGE_LABELS.get(stage, stage.replace("_", " "))


def _analysis_completion_message(analysis: GeometryAnalysis) -> str:
    total_ms = float((analysis.performance_ms or {}).get("total", 0.0))
    slowest_step: str | None = None
    slowest_ms = 0.0
    for key, value in (analysis.performance_ms or {}).items():
        if key == "total":
            continue
        if value > slowest_ms:
            slowest_step = key
            slowest_ms = value
    if slowest_step is not None:
        return (
            f"Analysis finished. Slowest stage was {_analysis_stage_label(slowest_step)} "
            f"({round(slowest_ms, 1)} ms)."
        )
    if total_ms > 0.0:
        return f"Analysis finished in {round(total_ms, 1)} ms."
    return "Analysis finished."


def _analysis_failure_detail(job_id: str | None, exc: Exception) -> str:
    if job_id is None:
        return str(exc)
    progress = _wizard_analysis_progress_response(job_id)
    if progress.stage:
        return f"Analysis failed during {_analysis_stage_label(progress.stage)}: {exc}"
    return str(exc)


def _read_official_sync_payload() -> dict:
    return load_official_sync_payload()


def _has_source_urls(metadata: object) -> bool:
    if not isinstance(metadata, dict):
        return False
    source_urls = metadata.get("source_urls")
    return isinstance(source_urls, list) and any(str(url).strip() for url in source_urls)


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _catalog_provenance_summary(metadata: object) -> dict[str, object | None]:
    metadata_obj = metadata if isinstance(metadata, dict) else {}

    source_priority_tier: int | None = None
    tier_raw = metadata_obj.get("source_priority_tier")
    try:
        source_priority_tier = int(tier_raw) if tier_raw is not None else None
    except (TypeError, ValueError):
        source_priority_tier = None

    confidence_raw = metadata_obj.get("sync_confidence_score")
    if confidence_raw is None:
        confidence_raw = metadata_obj.get("confidence")

    confidence: float | None = None
    try:
        confidence = round(float(confidence_raw), 3) if confidence_raw is not None else None
    except (TypeError, ValueError):
        confidence = None

    return {
        "source_url": _clean_optional_text(metadata_obj.get("source_url")),
        "source_type": _clean_optional_text(metadata_obj.get("source_type")),
        "source_priority_tier": source_priority_tier,
        "confidence": confidence,
        "license_note": _clean_optional_text(metadata_obj.get("license_note")),
        "retrieved_at": _clean_optional_text(metadata_obj.get("retrieved_at")),
    }


def _wizard_catalog_entity_summary(
    item: dict,
    *,
    target: str,
    profile_count: int = 0,
    compatibility_count: int = 0,
) -> dict[str, object | None]:
    return {
        "name": str(item.get("name") or "").strip(),
        "brand": _clean_optional_text(item.get("brand")),
        "model": _clean_optional_text(item.get("model")),
        "material_family": _clean_optional_text(item.get("material_family")),
        "material_type": _clean_optional_text(item.get("material_type")),
        "technology": _clean_optional_text(item.get("technology")),
        "target": target if target in {"msla", "fdm"} else None,
        "notes": _clean_optional_text(item.get("notes")),
        "profile_count": max(0, int(profile_count)),
        "compatibility_count": max(0, int(compatibility_count)),
        "provenance": _catalog_provenance_summary(item.get("metadata")),
    }


def _wizard_catalog_compatibility_summary(item: dict) -> dict[str, object | None]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "printer_name": str(item.get("printer_name") or "").strip(),
        "material_name": str(item.get("resin_name") or "").strip(),
        "profile_name": _clean_optional_text(item.get("profile_name")),
        "layer_height_mm": item.get("layer_height_mm"),
        "process": _clean_optional_text(metadata.get("profile_process")),
        "notes": _clean_optional_text(item.get("notes")),
        "provenance": _catalog_provenance_summary(metadata),
    }


def _wizard_catalog_profile_rank(item: dict, index: int) -> tuple[int, float, int, int]:
    provenance = _catalog_provenance_summary(item.get("metadata"))
    tier = provenance.get("source_priority_tier")
    confidence = provenance.get("confidence")
    tier_rank = 5 - int(tier) if isinstance(tier, int) else 0
    confidence_rank = float(confidence) if isinstance(confidence, float) else 0.0
    default_rank = 1 if bool(item.get("is_default")) else 0
    return (tier_rank, confidence_rank, default_rank, index)


def _safe_slug(value: str, fallback: str) -> str:
    text = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    while "--" in text:
        text = text.replace("--", "-")
    text = text.strip("-")
    return text or fallback


def _require_stl_upload(file: UploadFile) -> None:
    name = str(file.filename or "").strip().lower()
    if not name.endswith(".stl"):
        raise HTTPException(status_code=400, detail="Only STL files are supported in the wizard upload flow.")


def _limited_sorted(values: list[str], limit: int = 20) -> list[str]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    return sorted(cleaned)[:limit]


def _wizard_sync_schedules() -> list[dict]:
    schedules = list_sync_schedules(enabled_only=False, limit=1000, offset=0, db_path=SCHEDULES_PATH)
    selected: list[dict] = []
    for schedule in schedules:
        name = str(schedule.get("name", "")).strip().lower()
        source = str(schedule.get("source", "")).strip().lower()
        if name.startswith("wizard_") or source.startswith("wizard_"):
            selected.append(schedule)
    return selected


def _wizard_schedule_status(schedule: dict) -> WizardUpdateScheduleStatus:
    github_owner: str | None = None
    github_repo: str | None = None
    github_path: str | None = None
    github_ref: str | None = None
    web_url: str | None = None
    scrape_urls: list[str] = []
    try:
        github = _github_schedule_config(schedule)
        if github:
            github_owner = str(github.get("owner") or "") or None
            github_repo = str(github.get("repo") or "") or None
            github_path = str(github.get("path") or "") or None
            github_ref = str(github.get("ref") or "") or None
    except Exception:
        github_owner = None
        github_repo = None
        github_path = None
        github_ref = None

    try:
        web = _web_schedule_config(schedule)
        if web:
            web_url = str(web.get("url") or "") or None
    except Exception:
        web_url = None

    try:
        scrape = _scrape_schedule_config(schedule)
        if scrape:
            scrape_urls = [str(item).strip() for item in scrape.get("urls", []) if str(item).strip()]
    except Exception:
        scrape_urls = []

    return WizardUpdateScheduleStatus(
        id=int(schedule["id"]),
        name=str(schedule["name"]),
        source=str(schedule["source"]),
        interval_seconds=int(schedule["interval_seconds"]),
        enabled=bool(schedule["enabled"]),
        replace_existing=bool(schedule.get("replace_existing", False)),
        last_run_at=schedule.get("last_run_at"),
        last_status=schedule.get("last_status"),
        last_error=schedule.get("last_error"),
        github_owner=github_owner,
        github_repo=github_repo,
        github_path=github_path,
        github_ref=github_ref,
        web_url=web_url,
        scrape_urls=scrape_urls,
    )


def _wizard_gap_summary() -> WizardDatabaseGapSummary:
    catalog = export_catalog(db_path=CATALOG_PATH)
    printers = catalog.get("printers", [])
    resins = catalog.get("resins", [])
    profiles = catalog.get("profiles", [])

    missing_printer_sources = _limited_sorted(
        [str(item.get("name", "")).strip() for item in printers if not _has_source_urls(item.get("metadata"))]
    )
    missing_resin_sources = _limited_sorted(
        [str(item.get("name", "")).strip() for item in resins if not _has_source_urls(item.get("metadata"))]
    )
    missing_profile_sources = _limited_sorted(
        [
            " / ".join(
                [
                    str(item.get("printer_name", "")).strip(),
                    str(item.get("resin_name", "")).strip(),
                    str(item.get("profile_name", "")).strip(),
                ]
            ).strip(" /")
            for item in profiles
            if not _has_source_urls(item.get("metadata"))
        ]
    )

    printer_names = {
        str(item.get("name", "")).strip().lower(): str(item.get("name", "")).strip()
        for item in printers
        if str(item.get("name", "")).strip()
    }
    resin_names = {
        str(item.get("name", "")).strip().lower(): str(item.get("name", "")).strip()
        for item in resins
        if str(item.get("name", "")).strip()
    }
    profile_printers = {
        str(item.get("printer_name", "")).strip().lower()
        for item in profiles
        if str(item.get("printer_name", "")).strip()
    }
    profile_resins = {
        str(item.get("resin_name", "")).strip().lower()
        for item in profiles
        if str(item.get("resin_name", "")).strip()
    }

    printers_without_profiles = _limited_sorted(
        [display for key, display in printer_names.items() if key not in profile_printers]
    )
    resins_without_profiles = _limited_sorted(
        [display for key, display in resin_names.items() if key not in profile_resins]
    )

    recommendations: list[str] = []
    if missing_printer_sources or missing_resin_sources or missing_profile_sources:
        recommendations.append("Run database setup/update from the official dataset, GitHub, or a curated web JSON feed to improve provenance coverage.")
    if printers_without_profiles or resins_without_profiles:
        recommendations.append("Add or sync profile presets for uncovered printer/resin combinations.")
    if not recommendations:
        recommendations.append("Coverage looks healthy. Keep auto-update enabled to stay current.")

    return WizardDatabaseGapSummary(
        missing_printer_sources_count=sum(1 for item in printers if not _has_source_urls(item.get("metadata"))),
        missing_resin_sources_count=sum(1 for item in resins if not _has_source_urls(item.get("metadata"))),
        missing_profile_sources_count=sum(1 for item in profiles if not _has_source_urls(item.get("metadata"))),
        printers_without_profiles_count=len([key for key in printer_names if key not in profile_printers]),
        resins_without_profiles_count=len([key for key in resin_names if key not in profile_resins]),
        missing_printer_sources=missing_printer_sources,
        missing_resin_sources=missing_resin_sources,
        missing_profile_sources=missing_profile_sources,
        printers_without_profiles=printers_without_profiles,
        resins_without_profiles=resins_without_profiles,
        recommendations=recommendations,
    )


def _env_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _to_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _to_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _github_schedule_config(schedule: dict) -> dict | None:
    payload = schedule.get("payload")
    if not isinstance(payload, dict):
        return None

    config_obj: object | None = payload.get("github")
    if not isinstance(config_obj, dict):
        has_flat = all(key in payload for key in ("owner", "repo", "path"))
        has_native_sync_shape = any(key in payload for key in ("printers", "resins", "profiles"))
        if not has_flat or has_native_sync_shape:
            return None
        config_obj = payload

    config = dict(config_obj)
    owner = str(config.get("owner", "")).strip()
    repo = str(config.get("repo", "")).strip()
    path = str(config.get("path", "")).strip()
    if not owner or not repo or not path:
        raise ValueError("GitHub schedule payload must include owner, repo, and path.")

    return {
        "owner": owner,
        "repo": repo,
        "path": path,
        "ref": str(config.get("ref") or "main").strip() or "main",
        "timeout_s": _to_float(
            config.get("timeout_s", os.getenv("RESINLOGIC_GITHUB_SYNC_TIMEOUT_SECONDS", 20.0)),
            default=20.0,
            minimum=1.0,
            maximum=120.0,
        ),
        "retry_attempts": _to_int(
            config.get("retry_attempts", os.getenv("RESINLOGIC_GITHUB_SYNC_RETRY_ATTEMPTS", 3)),
            default=3,
            minimum=1,
            maximum=10,
        ),
        "retry_backoff_seconds": _to_float(
            config.get("retry_backoff_seconds", os.getenv("RESINLOGIC_GITHUB_SYNC_RETRY_BACKOFF_SECONDS", 1.0)),
            default=1.0,
            minimum=0.0,
            maximum=60.0,
        ),
        "replace_existing": bool(config.get("replace_existing", schedule.get("replace_existing", False))),
    }


def _web_schedule_config(schedule: dict) -> dict | None:
    payload = schedule.get("payload")
    if not isinstance(payload, dict):
        return None

    config_obj: object | None = payload.get("web_json")
    if not isinstance(config_obj, dict):
        has_flat = "url" in payload and "github" not in payload
        has_native_sync_shape = any(key in payload for key in ("printers", "resins", "profiles"))
        if not has_flat or has_native_sync_shape:
            return None
        config_obj = payload

    config = dict(config_obj)
    url = str(config.get("url", "")).strip()
    if not url:
        raise ValueError("Web schedule payload must include a URL.")

    return {
        "url": url,
        "timeout_s": _to_float(
            config.get("timeout_s", os.getenv("RESINLOGIC_WEB_SYNC_TIMEOUT_SECONDS", 20.0)),
            default=20.0,
            minimum=1.0,
            maximum=120.0,
        ),
        "retry_attempts": _to_int(
            config.get("retry_attempts", os.getenv("RESINLOGIC_WEB_SYNC_RETRY_ATTEMPTS", 3)),
            default=3,
            minimum=1,
            maximum=10,
        ),
        "retry_backoff_seconds": _to_float(
            config.get("retry_backoff_seconds", os.getenv("RESINLOGIC_WEB_SYNC_RETRY_BACKOFF_SECONDS", 1.0)),
            default=1.0,
            minimum=0.0,
            maximum=60.0,
        ),
        "replace_existing": bool(config.get("replace_existing", schedule.get("replace_existing", False))),
    }


def _scrape_schedule_config(schedule: dict) -> dict | None:
    payload = schedule.get("payload")
    if not isinstance(payload, dict):
        return None

    config_obj: object | None = payload.get("web_scrape")
    if not isinstance(config_obj, dict):
        return None

    config = dict(config_obj)
    urls = [str(item).strip() for item in config.get("urls", []) if str(item).strip()]
    if not urls:
        raise ValueError("Web scrape schedule payload must include one or more URLs.")

    return {
        "urls": urls,
        "timeout_s": _to_float(
            config.get("timeout_s", os.getenv("RESINLOGIC_WEB_SCRAPE_TIMEOUT_SECONDS", 20.0)),
            default=20.0,
            minimum=1.0,
            maximum=120.0,
        ),
        "retry_attempts": _to_int(
            config.get("retry_attempts", os.getenv("RESINLOGIC_WEB_SCRAPE_RETRY_ATTEMPTS", 3)),
            default=3,
            minimum=1,
            maximum=10,
        ),
        "retry_backoff_seconds": _to_float(
            config.get("retry_backoff_seconds", os.getenv("RESINLOGIC_WEB_SCRAPE_RETRY_BACKOFF_SECONDS", 1.0)),
            default=1.0,
            minimum=0.0,
            maximum=60.0,
        ),
        "replace_existing": bool(config.get("replace_existing", schedule.get("replace_existing", False))),
    }


def _fetch_github_schedule_payload(config: dict) -> tuple[dict, str, int]:
    attempts = int(config["retry_attempts"])
    backoff = float(config["retry_backoff_seconds"])
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            payload, raw_url = fetch_technical_sync_from_github(
                owner=str(config["owner"]),
                repo=str(config["repo"]),
                path=str(config["path"]),
                ref=str(config["ref"]),
                timeout_s=float(config["timeout_s"]),
            )
            return payload, raw_url, attempt
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            sleep_seconds = backoff * (2 ** (attempt - 1))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    if last_error is not None:
        raise RuntimeError(f"GitHub sync failed after {attempts} attempts: {last_error}") from last_error
    raise RuntimeError("GitHub sync failed without error details.")


def _fetch_web_schedule_payload(config: dict) -> tuple[dict, str, int]:
    attempts = int(config["retry_attempts"])
    backoff = float(config["retry_backoff_seconds"])
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            payload, normalized_url = fetch_technical_sync_from_url(
                url=str(config["url"]),
                timeout_s=float(config["timeout_s"]),
            )
            return payload, normalized_url, attempt
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            sleep_seconds = backoff * (2 ** (attempt - 1))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    if last_error is not None:
        raise RuntimeError(f"Web sync failed after {attempts} attempts: {last_error}") from last_error
    raise RuntimeError("Web sync failed without error details.")


def _fetch_scrape_schedule_payload(config: dict) -> tuple[dict, dict, int]:
    attempts = int(config["retry_attempts"])
    backoff = float(config["retry_backoff_seconds"])
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            payload, summary = scrape_supported_catalog_pages(
                urls=list(config["urls"]),
                timeout_s=float(config["timeout_s"]),
            )
            return payload, summary, attempt
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            sleep_seconds = backoff * (2 ** (attempt - 1))
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    if last_error is not None:
        raise RuntimeError(f"Web scrape sync failed after {attempts} attempts: {last_error}") from last_error
    raise RuntimeError("Web scrape sync failed without error details.")


def _submit_sync_schedule_job(schedule: dict, actor: str = "scheduler") -> dict:
    schedule_id = int(schedule["id"])

    def _work() -> dict:
        try:
            github_config = _github_schedule_config(schedule)
            scrape_config = None if github_config is not None else _scrape_schedule_config(schedule)
            web_config = None if github_config is not None or scrape_config is not None else _web_schedule_config(schedule)
            source = str(schedule["source"])
            if github_config is None and scrape_config is None and web_config is None:
                result = apply_technical_sync(
                    payload=schedule["payload"],
                    source=source,
                    db_path=CATALOG_PATH,
                    replace_existing=bool(schedule.get("replace_existing", False)),
                )
                output = {
                    "source": source,
                    "schedule_id": schedule_id,
                    "printers_upserted": result["printers_upserted"],
                    "resins_upserted": result["resins_upserted"],
                    "profiles_upserted": result["profiles_upserted"],
                    "curation": result.get("curation") or {},
                    "notes": result.get("notes") or [],
                }
            elif scrape_config is not None:
                payload, summary, attempts_used = _fetch_scrape_schedule_payload(scrape_config)
                result = apply_technical_sync(
                    payload=payload,
                    source=source,
                    db_path=CATALOG_PATH,
                    replace_existing=bool(scrape_config["replace_existing"]),
                    source_metadata={
                        "kind": "web_scrape",
                        "supported_scraper": True,
                        "source_urls": summary.get("scraped_urls") or summary.get("normalized_urls") or [],
                    },
                )
                output = {
                    "source": source,
                    "schedule_id": schedule_id,
                    "printers_upserted": result["printers_upserted"],
                    "resins_upserted": result["resins_upserted"],
                    "profiles_upserted": result["profiles_upserted"],
                    "curation": result.get("curation") or {},
                    "notes": [*(result.get("notes") or []), *(summary.get("notes") or [])],
                    "scrape_urls": summary.get("scraped_urls") or [],
                    "unsupported_urls": summary.get("unsupported_urls") or [],
                    "scrape_attempts_used": attempts_used,
                }
            elif web_config is not None:
                payload, raw_url, attempts_used = _fetch_web_schedule_payload(web_config)
                result = apply_technical_sync(
                    payload=payload,
                    source=source,
                    db_path=CATALOG_PATH,
                    replace_existing=bool(web_config["replace_existing"]),
                    source_metadata={
                        "kind": "web_json",
                        "url": raw_url,
                    },
                )
                output = {
                    "source": source,
                    "schedule_id": schedule_id,
                    "printers_upserted": result["printers_upserted"],
                    "resins_upserted": result["resins_upserted"],
                    "profiles_upserted": result["profiles_upserted"],
                    "curation": result.get("curation") or {},
                    "notes": result.get("notes") or [],
                    "web_raw_url": raw_url,
                    "web_attempts_used": attempts_used,
                }
            else:
                payload, raw_url, attempts_used = _fetch_github_schedule_payload(github_config)
                result = apply_technical_sync(
                    payload=payload,
                    source=source,
                    db_path=CATALOG_PATH,
                    replace_existing=bool(github_config["replace_existing"]),
                    source_metadata={
                        "kind": "github",
                        "owner": github_config["owner"],
                        "repo": github_config["repo"],
                        "path": github_config["path"],
                        "ref": github_config["ref"],
                        "raw_url": raw_url,
                    },
                )
                output = {
                    "source": source,
                    "schedule_id": schedule_id,
                    "printers_upserted": result["printers_upserted"],
                    "resins_upserted": result["resins_upserted"],
                    "profiles_upserted": result["profiles_upserted"],
                    "curation": result.get("curation") or {},
                    "notes": result.get("notes") or [],
                    "github_raw_url": raw_url,
                    "github_owner": github_config["owner"],
                    "github_repo": github_config["repo"],
                    "github_path": github_config["path"],
                    "github_ref": github_config["ref"],
                    "github_attempts_used": attempts_used,
                }
            mark_sync_schedule_run(
                schedule_id,
                status="succeeded",
                error=None,
                db_path=SCHEDULES_PATH,
            )
            return output
        except Exception as exc:
            mark_sync_schedule_run(
                schedule_id,
                status="failed",
                error=str(exc),
                db_path=SCHEDULES_PATH,
            )
            raise

    job = JOB_QUEUE.submit(
        job_type="sync_schedule",
        actor=actor,
        metadata={"schedule_id": schedule_id, "name": schedule.get("name"), "source": schedule.get("source")},
        fn=_work,
    )
    return job


def startup_scheduler() -> None:
    global SCHEDULER
    if SCHEDULER is None:
        poll_seconds = float(os.getenv("RESINLOGIC_SCHEDULER_POLL_SECONDS", "5"))
        SCHEDULER = TechnicalSyncScheduler(
            schedule_db_path=SCHEDULES_PATH,
            submit_schedule_fn=lambda schedule: _submit_sync_schedule_job(schedule, actor="scheduler"),
            poll_seconds=poll_seconds,
        )
    if _env_enabled("RESINLOGIC_ENABLE_SCHEDULER", default=False):
        SCHEDULER.start()


def shutdown_scheduler() -> None:
    if SCHEDULER is not None:
        SCHEDULER.stop()


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    startup_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app.router.lifespan_context = _app_lifespan


@app.middleware("http")
async def metrics_middleware(request, call_next):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000.0
    METRICS.record(path=request.url.path, status_code=response.status_code, latency_ms=duration_ms)
    return response


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/wizard")


@app.get("/app", response_class=HTMLResponse, include_in_schema=False)
def app_ui() -> HTMLResponse:
    if not APP_UI_PATH.exists():
        raise HTTPException(status_code=404, detail="App UI not found.")
    return HTMLResponse(APP_UI_PATH.read_text(encoding="utf-8"))


@app.get("/wizard", response_class=HTMLResponse, include_in_schema=False)
def wizard_ui() -> HTMLResponse:
    if not WIZARD_UI_PATH.exists():
        raise HTTPException(status_code=404, detail="Wizard UI not found.")
    return HTMLResponse(WIZARD_UI_PATH.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/whoami", response_model=AuthWhoAmI)
def auth_whoami(auth: AuthContext = Depends(require_role("viewer"))) -> AuthWhoAmI:
    return AuthWhoAmI(
        auth_enforced=auth_enforced(),
        actor=auth.actor,
        role=auth.role,
        api_key_present=auth.api_key_present,
    )


@app.get("/ops/metrics", response_model=MetricsResponse)
def ops_metrics(auth: AuthContext = Depends(require_role("viewer"))) -> MetricsResponse:
    return MetricsResponse(**METRICS.snapshot())


@app.get("/ops/metrics/prometheus", response_class=PlainTextResponse)
def ops_metrics_prometheus(auth: AuthContext = Depends(require_role("viewer"))) -> PlainTextResponse:
    _ = auth
    return PlainTextResponse(METRICS.to_prometheus_text(), media_type="text/plain; version=0.0.4")


def _audit(
    *,
    auth: AuthContext,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    status: str = "success",
    details: dict | None = None,
) -> None:
    log_audit_event(
        actor=auth.actor,
        role=auth.role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        details=details or {},
        db_path=AUDIT_PATH,
    )


def _snapshot_catalog(auth: AuthContext, source_action: str, note: str | None = None) -> CatalogVersion:
    version = create_catalog_version(
        actor=auth.actor,
        note=note,
        source_action=source_action,
        catalog_db_path=CATALOG_PATH,
        audit_db_path=AUDIT_PATH,
    )
    _audit(
        auth=auth,
        action="catalog.snapshot",
        resource_type="catalog_version",
        resource_id=str(version["id"]),
        details={"source_action": source_action, "note": note},
    )
    return CatalogVersion(**version)


@app.get("/catalog/health")
def catalog_health() -> dict[str, object]:
    catalog = export_catalog(db_path=CATALOG_PATH)
    return {
        "status": "ok",
        "mode": "standalone" if standalone_mode() else "server",
        "data_dir": str(DATA_DIR),
        "storage_path": str(CATALOG_PATH),
        "printers": len(catalog.get("printers", [])),
        "resins": len(catalog.get("resins", [])),
        "profiles": len(catalog.get("profiles", [])),
    }


@app.get("/wizard/catalog/options", response_model=WizardCatalogOptionsResponse)
def wizard_catalog_options(auth: AuthContext = Depends(require_role("viewer"))) -> WizardCatalogOptionsResponse:
    _ = auth
    profiles = list_profiles(db_path=CATALOG_PATH, active_only=True, limit=5000)
    printer_rows = list_printers(db_path=CATALOG_PATH, limit=2000)
    material_rows = list_catalog_resins(db_path=CATALOG_PATH, limit=3000)

    printer_rows_by_name = {
        str(item.get("name") or "").strip(): item
        for item in printer_rows
        if str(item.get("name") or "").strip()
    }
    material_rows_by_name = {
        str(item.get("name") or "").strip(): item
        for item in material_rows
        if str(item.get("name") or "").strip()
    }

    compatibility_sets: dict[str, set[str]] = {}
    compatibility_by_target_sets: dict[str, dict[str, set[str]]] = {"msla": {}, "fdm": {}}
    profile_counts_by_target: dict[str, dict[str, int]] = {"msla": {}, "fdm": {}}
    material_profile_counts_by_target: dict[str, dict[str, int]] = {"msla": {}, "fdm": {}}
    best_profile_by_pair: dict[tuple[str, str, str], tuple[tuple[int, float, int, int], dict[str, object | None]]] = {}

    for index, item in enumerate(profiles):
        printer = str(item.get("printer_name", "")).strip()
        resin = str(item.get("resin_name", "")).strip()
        if not printer or not resin:
            continue
        compatibility_sets.setdefault(printer, set()).add(resin)
        target = _wizard_target_from_catalog_item(item)
        compatibility_by_target_sets.setdefault(target, {}).setdefault(printer, set()).add(resin)
        profile_counts_by_target.setdefault(target, {})[printer] = profile_counts_by_target.setdefault(target, {}).get(printer, 0) + 1
        material_profile_counts_by_target.setdefault(target, {})[resin] = (
            material_profile_counts_by_target.setdefault(target, {}).get(resin, 0) + 1
        )
        pair_key = (target, printer, resin)
        candidate = (_wizard_catalog_profile_rank(item, index), _wizard_catalog_compatibility_summary(item))
        existing = best_profile_by_pair.get(pair_key)
        if existing is None or candidate[0] > existing[0]:
            best_profile_by_pair[pair_key] = candidate

    compatibility = {key: sorted(values) for key, values in compatibility_sets.items()}
    compatibility_by_target = {
        target: {key: sorted(values) for key, values in printer_map.items()}
        for target, printer_map in compatibility_by_target_sets.items()
    }
    printers_by_target: dict[str, list[str]] = {
        target: sorted(printer_map.keys())
        for target, printer_map in compatibility_by_target.items()
    }
    materials_by_target: dict[str, list[str]] = {
        target: sorted({material for values in printer_map.values() for material in values})
        for target, printer_map in compatibility_by_target.items()
    }

    printers = sorted({printer for values in printers_by_target.values() for printer in values})
    resins = sorted({material for values in materials_by_target.values() for material in values})

    if not printers:
        printer_groups: dict[str, set[str]] = {"msla": set(), "fdm": set()}
        for item in printer_rows:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            printer_groups.setdefault(_wizard_target_from_catalog_item(item), set()).add(name)
        printers_by_target = {key: sorted(values) for key, values in printer_groups.items()}
        printers = sorted({name for values in printer_groups.values() for name in values})
    if not resins:
        material_groups: dict[str, set[str]] = {"msla": set(), "fdm": set()}
        for item in material_rows:
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            material_groups.setdefault(_wizard_target_from_catalog_item(item), set()).add(name)
        materials_by_target = {key: sorted(values) for key, values in material_groups.items()}
        resins = sorted({name for values in material_groups.values() for name in values})

    targets = [
        target
        for target in ("msla", "fdm")
        if printers_by_target.get(target) or materials_by_target.get(target) or compatibility_by_target.get(target)
    ]
    if not targets:
        targets = ["msla"]

    material_compatibility_counts_by_target: dict[str, dict[str, int]] = {target: {} for target in targets}
    for target, printer_map in compatibility_by_target.items():
        material_counts = material_compatibility_counts_by_target.setdefault(target, {})
        for printer_name, material_names in printer_map.items():
            _ = printer_name
            for material_name in material_names:
                material_counts[material_name] = material_counts.get(material_name, 0) + 1

    printer_details_by_target: dict[str, list[dict[str, object | None]]] = {}
    material_details_by_target: dict[str, list[dict[str, object | None]]] = {}
    compatibility_details_by_target: dict[str, dict[str, dict[str, dict[str, object | None]]]] = {
        target: {} for target in targets
    }

    for target in targets:
        printer_details: list[dict[str, object | None]] = []
        for name in printers_by_target.get(target, []):
            row = printer_rows_by_name.get(name, {"name": name})
            printer_details.append(
                _wizard_catalog_entity_summary(
                    row,
                    target=target,
                    profile_count=profile_counts_by_target.get(target, {}).get(name, 0),
                    compatibility_count=len(compatibility_by_target.get(target, {}).get(name, [])),
                )
            )
        printer_details_by_target[target] = printer_details

        material_details: list[dict[str, object | None]] = []
        for name in materials_by_target.get(target, []):
            row = material_rows_by_name.get(name, {"name": name})
            material_details.append(
                _wizard_catalog_entity_summary(
                    row,
                    target=target,
                    profile_count=material_profile_counts_by_target.get(target, {}).get(name, 0),
                    compatibility_count=material_compatibility_counts_by_target.get(target, {}).get(name, 0),
                )
            )
        material_details_by_target[target] = material_details

        target_pairs: dict[str, dict[str, dict[str, object | None]]] = {}
        for printer_name, material_names in compatibility_by_target.get(target, {}).items():
            pair_details: dict[str, dict[str, object | None]] = {}
            for material_name in material_names:
                candidate = best_profile_by_pair.get((target, printer_name, material_name))
                if candidate is not None:
                    pair_details[material_name] = candidate[1]
            if pair_details:
                target_pairs[printer_name] = pair_details
        compatibility_details_by_target[target] = target_pairs

    return WizardCatalogOptionsResponse(
        printers=printers,
        resins=resins,
        materials=resins,
        compatibility=compatibility,
        targets=targets,
        printers_by_target=printers_by_target,
        materials_by_target=materials_by_target,
        compatibility_by_target=compatibility_by_target,
        printer_details_by_target=printer_details_by_target,
        material_details_by_target=material_details_by_target,
        compatibility_details_by_target=compatibility_details_by_target,
        slicers_by_target={target: WIZARD_SLICERS_BY_TARGET[target] for target in targets},
    )


@app.get("/wizard/database/status", response_model=WizardDatabaseStatus)
def wizard_database_status(auth: AuthContext = Depends(require_role("viewer"))) -> WizardDatabaseStatus:
    _ = auth
    catalog = export_catalog(db_path=CATALOG_PATH)
    printers = catalog.get("printers", [])
    resins = catalog.get("resins", [])
    profiles = catalog.get("profiles", [])

    printers_total = len(printers)
    resins_total = len(resins)
    profiles_total = len(profiles)

    printers_with_sources = sum(1 for item in printers if _has_source_urls(item.get("metadata")))
    resins_with_sources = sum(1 for item in resins if _has_source_urls(item.get("metadata")))
    profiles_with_sources = sum(1 for item in profiles if _has_source_urls(item.get("metadata")))
    profiles_with_confidence = sum(
        1
        for item in profiles
        if isinstance(item.get("metadata"), dict) and item["metadata"].get("sync_confidence_score") is not None
    )

    manufacturers = sorted(
        {
            str(item.get("metadata", {}).get("manufacturer") or item.get("brand") or "").strip()
            for item in printers
            if str(item.get("metadata", {}).get("manufacturer") or item.get("brand") or "").strip()
        }
    )

    components: list[float] = []
    if printers_total:
        components.append(printers_with_sources / printers_total)
    if resins_total:
        components.append(resins_with_sources / resins_total)
    if profiles_total:
        components.append(profiles_with_sources / profiles_total)
        components.append(profiles_with_confidence / profiles_total)
    completeness = round((sum(components) / len(components)) * 100.0, 2) if components else 0.0

    official_updated_at: str | None = None
    try:
        official_payload = _read_official_sync_payload()
        official_updated_at = str(official_payload.get("updated_at")) if official_payload.get("updated_at") else None
    except Exception:
        official_updated_at = None

    schedules = list_sync_schedules(enabled_only=False, limit=1000, offset=0, db_path=SCHEDULES_PATH)
    schedules_enabled = sum(1 for item in schedules if bool(item.get("enabled")))

    return WizardDatabaseStatus(
        mode="standalone" if standalone_mode() else "server",
        data_dir=str(DATA_DIR),
        storage_path=str(CATALOG_PATH),
        official_dataset_file="; ".join(str(path) for path in official_sync_paths()),
        official_dataset_updated_at=official_updated_at,
        printers_total=printers_total,
        printers_with_sources=printers_with_sources,
        resins_total=resins_total,
        resins_with_sources=resins_with_sources,
        profiles_total=profiles_total,
        profiles_with_sources=profiles_with_sources,
        profiles_with_confidence=profiles_with_confidence,
        manufacturers=manufacturers,
        completeness_percent=completeness,
        ready_for_model_analysis=printers_total > 0 and resins_total > 0,
        ready_for_settings=printers_total > 0 and resins_total > 0 and profiles_total > 0,
        schedules_total=len(schedules),
        schedules_enabled=schedules_enabled,
    )


@app.get("/wizard/database/gaps", response_model=WizardDatabaseGapSummary)
def wizard_database_gaps(auth: AuthContext = Depends(require_role("viewer"))) -> WizardDatabaseGapSummary:
    _ = auth
    return _wizard_gap_summary()


@app.get("/wizard/updates/status", response_model=WizardUpdateStatusResponse)
def wizard_updates_status(auth: AuthContext = Depends(require_role("viewer"))) -> WizardUpdateStatusResponse:
    _ = auth
    schedules = _wizard_sync_schedules()
    statuses = [_wizard_schedule_status(item) for item in schedules]
    enabled = sum(1 for item in schedules if bool(item.get("enabled")))
    return WizardUpdateStatusResponse(
        scheduler_running=bool(SCHEDULER and SCHEDULER.is_running()),
        scheduler_poll_seconds=float(SCHEDULER.poll_seconds if SCHEDULER else 0.0),
        schedules_total=len(statuses),
        schedules_enabled=enabled,
        wizard_auto_update_present=any(item.name == "wizard_auto_update" for item in statuses),
        schedules=statuses,
    )


@app.post("/wizard/updates/run-now", response_model=PipelineAsyncSubmitResponse)
def wizard_updates_run_now(
    request: WizardRunUpdateNowRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> PipelineAsyncSubmitResponse:
    schedules = _wizard_sync_schedules()
    if not schedules:
        raise HTTPException(status_code=404, detail="No wizard update schedule found.")

    selected: dict | None = None
    if request.schedule_id is not None:
        selected = next((item for item in schedules if int(item["id"]) == int(request.schedule_id)), None)
        if selected is None:
            raise HTTPException(status_code=404, detail=f"Wizard schedule '{request.schedule_id}' not found.")
    else:
        selected = next((item for item in schedules if bool(item.get("enabled"))), schedules[0])

    schedule_id = int(selected["id"])
    try:
        mark_sync_schedule_run(schedule_id, status="queued", error=None, db_path=SCHEDULES_PATH)
        job = _submit_sync_schedule_job(selected, actor=auth.actor)
        _audit(
            auth=auth,
            action="wizard.updates.run_now",
            resource_type="sync_schedule",
            resource_id=str(schedule_id),
            details={"job_id": job["id"], "name": selected.get("name")},
        )
        return PipelineAsyncSubmitResponse(job=AsyncJob(**job))
    except Exception as exc:
        _audit(
            auth=auth,
            action="wizard.updates.run_now",
            resource_type="sync_schedule",
            resource_id=str(schedule_id),
            status="failed",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/wizard/updates/run-due", response_model=SyncSchedulerTickResult)
def wizard_updates_run_due(auth: AuthContext = Depends(require_role("operator"))) -> SyncSchedulerTickResult:
    _ = auth
    if SCHEDULER is None:
        raise HTTPException(status_code=500, detail="Scheduler is not initialized.")
    result = SCHEDULER.tick_once()
    return SyncSchedulerTickResult(**result)


@app.post("/wizard/database/setup", response_model=WizardDatabaseSetupResponse)
def wizard_database_setup(
    request: WizardDatabaseSetupRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> WizardDatabaseSetupResponse:
    mode = request.mode
    notes: list[str] = []
    raw_url: str | None = None
    source = request.source.strip() if request.source else ""
    owner = (request.owner or "").strip()
    repo = (request.repo or "").strip()
    path = (request.path or "").strip()
    url = (request.url or "").strip()
    scrape_urls = [str(item).strip() for item in request.scrape_urls if str(item).strip()]
    ref = (request.ref or "main").strip() or "main"

    try:
        if mode == "official_local":
            payload = _read_official_sync_payload()
            source = source or "wizard_official_local"
            source_metadata = {
                "kind": "official_docs",
                "verified": True,
                "trust_score": 0.92,
                "source_file": "; ".join(str(path) for path in official_sync_paths()),
                "retrieved_at": _iso_timestamp(),
            }
        elif mode == "github":
            if not owner or not repo or not path:
                raise HTTPException(
                    status_code=400,
                    detail="GitHub mode requires owner, repo, and path.",
                )
            payload, raw_url = fetch_technical_sync_from_github(
                owner=owner,
                repo=repo,
                path=path,
                ref=ref,
            )
            source = source or f"github:{owner}/{repo}@{ref}"
            source_metadata = {
                "kind": "github",
                "owner": owner,
                "repo": repo,
                "path": path,
                "ref": ref,
                "raw_url": raw_url,
                "retrieved_at": _iso_timestamp(),
            }
        elif mode == "web_json":
            if not url:
                raise HTTPException(
                    status_code=400,
                    detail="Web JSON mode requires a URL.",
                )
            payload, raw_url = fetch_technical_sync_from_url(url=url)
            source = source or f"web_json:{raw_url}"
            source_metadata = {
                "kind": "web_json",
                "url": raw_url,
                "retrieved_at": _iso_timestamp(),
            }
        else:
            if not scrape_urls:
                raise HTTPException(
                    status_code=400,
                    detail="Supported web scrape mode requires one or more URLs.",
                )
            payload, scrape_summary = scrape_supported_catalog_pages(urls=scrape_urls)
            raw_url = ", ".join(scrape_summary.get("scraped_urls") or scrape_summary.get("normalized_urls") or []) or None
            source = source or "wizard_setup_web_scrape"
            source_metadata = {
                "kind": "web_scrape",
                "supported_scraper": True,
                "source_urls": scrape_summary.get("scraped_urls") or scrape_summary.get("normalized_urls") or [],
                "retrieved_at": _iso_timestamp(),
            }
            notes.extend(scrape_summary.get("notes") or [])

        result = apply_technical_sync(
            payload=payload,
            source=source,
            db_path=CATALOG_PATH,
            replace_existing=request.replace_existing,
            source_metadata=source_metadata,
        )

        auto_update_schedule_id: int | None = None
        if request.auto_update:
            if mode == "github" and owner and repo and path:
                schedule = create_or_upsert_sync_schedule(
                    {
                        "name": "wizard_auto_update",
                        "source": "wizard_auto_update",
                        "interval_seconds": request.auto_update_interval_seconds,
                        "enabled": True,
                        "replace_existing": request.replace_existing,
                        "payload": {
                            "github": {
                                "owner": owner,
                                "repo": repo,
                                "path": path,
                                "ref": ref,
                                "replace_existing": request.replace_existing,
                            }
                        },
                    },
                    db_path=SCHEDULES_PATH,
                )
                auto_update_schedule_id = int(schedule["id"])
                notes.append(
                    f"Auto-update schedule enabled (every {request.auto_update_interval_seconds} seconds)."
                )
            elif mode == "web_json" and raw_url:
                schedule = create_or_upsert_sync_schedule(
                    {
                        "name": "wizard_auto_update",
                        "source": "wizard_auto_update",
                        "interval_seconds": request.auto_update_interval_seconds,
                        "enabled": True,
                        "replace_existing": request.replace_existing,
                        "payload": {
                            "web_json": {
                                "url": raw_url,
                                "replace_existing": request.replace_existing,
                            }
                        },
                    },
                    db_path=SCHEDULES_PATH,
                )
                auto_update_schedule_id = int(schedule["id"])
                notes.append(
                    f"Auto-update schedule enabled (every {request.auto_update_interval_seconds} seconds)."
                )
            elif mode == "web_scrape" and scrape_urls:
                schedule = create_or_upsert_sync_schedule(
                    {
                        "name": "wizard_auto_update",
                        "source": "wizard_auto_update",
                        "interval_seconds": request.auto_update_interval_seconds,
                        "enabled": True,
                        "replace_existing": request.replace_existing,
                        "payload": {
                            "web_scrape": {
                                "urls": scrape_urls,
                                "replace_existing": request.replace_existing,
                            }
                        },
                    },
                    db_path=SCHEDULES_PATH,
                )
                auto_update_schedule_id = int(schedule["id"])
                notes.append(
                    f"Auto-update schedule enabled (every {request.auto_update_interval_seconds} seconds)."
                )
            else:
                notes.append("Auto-update skipped: use GitHub, Web JSON, or supported web scrape mode for scheduled remote updates.")

        _audit(
            auth=auth,
            action="wizard.database.setup",
            resource_type="catalog",
            details={
                "mode": mode,
                "source": source,
                "raw_url": raw_url,
                "replace_existing": request.replace_existing,
                "auto_update": request.auto_update,
                "auto_update_schedule_id": auto_update_schedule_id,
                "result": result,
            },
        )
        _snapshot_catalog(auth, "wizard.database.setup", note=source)

        combined_notes = [*(result.get("notes") or []), *notes]
        return WizardDatabaseSetupResponse(
            mode=mode,
            source=source,
            raw_url=raw_url,
            printers_upserted=result["printers_upserted"],
            resins_upserted=result["resins_upserted"],
            profiles_upserted=result["profiles_upserted"],
            curation=result.get("curation") or {},
            auto_update_schedule_id=auto_update_schedule_id,
            notes=combined_notes,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _audit(
            auth=auth,
            action="wizard.database.setup",
            resource_type="catalog",
            status="failed",
            details={"mode": mode, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/catalog/admin", response_class=HTMLResponse)
def catalog_admin_ui() -> HTMLResponse:
    if not ADMIN_UI_PATH.exists():
        raise HTTPException(status_code=404, detail="Admin UI not found.")
    return HTMLResponse(ADMIN_UI_PATH.read_text(encoding="utf-8"))


@app.get("/catalog/printers", response_model=list[CatalogPrinter])
def catalog_list_printers(
    query: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[CatalogPrinter]:
    return list_printers(db_path=CATALOG_PATH, query=query, limit=limit, offset=offset)


@app.post("/catalog/printers", response_model=CatalogPrinter)
def catalog_create_printer(
    request: CatalogPrinterCreate,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogPrinter:
    try:
        item = create_or_upsert_printer(request.model_dump(), db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.printer.upsert",
            resource_type="printer",
            resource_id=str(item["id"]),
            details={"name": item["name"]},
        )
        _snapshot_catalog(auth, "catalog.printer.upsert", note=item["name"])
        return item
    except Exception as exc:
        _audit(auth=auth, action="catalog.printer.upsert", status="failed", details={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/catalog/printers/{printer_id}", response_model=CatalogPrinter)
def catalog_get_printer(printer_id: int) -> CatalogPrinter:
    try:
        return get_printer(printer_id, db_path=CATALOG_PATH)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/catalog/printers/{printer_id}", response_model=CatalogPrinter)
def catalog_update_printer(
    printer_id: int,
    request: CatalogPrinterUpdate,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogPrinter:
    try:
        item = update_printer(printer_id, request.model_dump(exclude_none=True), db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.printer.update",
            resource_type="printer",
            resource_id=str(printer_id),
            details={"name": item["name"]},
        )
        _snapshot_catalog(auth, "catalog.printer.update", note=item["name"])
        return item
    except Exception as exc:
        _audit(
            auth=auth,
            action="catalog.printer.update",
            resource_type="printer",
            resource_id=str(printer_id),
            status="failed",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/catalog/printers/{printer_id}")
def catalog_delete_printer(
    printer_id: int,
    auth: AuthContext = Depends(require_role("admin")),
) -> dict[str, object]:
    deleted = delete_printer(printer_id, db_path=CATALOG_PATH)
    _audit(
        auth=auth,
        action="catalog.printer.delete",
        resource_type="printer",
        resource_id=str(printer_id),
        details={"deleted": deleted},
    )
    if deleted:
        _snapshot_catalog(auth, "catalog.printer.delete", note=f"printer_id={printer_id}")
    return {"deleted": deleted, "printer_id": printer_id}


@app.get("/catalog/resins", response_model=list[CatalogResin])
def catalog_list_resins(
    query: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[CatalogResin]:
    return list_catalog_resins(db_path=CATALOG_PATH, query=query, limit=limit, offset=offset)


@app.post("/catalog/resins", response_model=CatalogResin)
def catalog_create_resin(
    request: CatalogResinCreate,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogResin:
    try:
        item = create_or_upsert_resin(request.model_dump(), db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.resin.upsert",
            resource_type="resin",
            resource_id=str(item["id"]),
            details={"name": item["name"]},
        )
        _snapshot_catalog(auth, "catalog.resin.upsert", note=item["name"])
        return item
    except Exception as exc:
        _audit(auth=auth, action="catalog.resin.upsert", status="failed", details={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/catalog/resins/{resin_id}", response_model=CatalogResin)
def catalog_get_resin(resin_id: int) -> CatalogResin:
    try:
        return get_resin(resin_id, db_path=CATALOG_PATH)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/catalog/resins/{resin_id}", response_model=CatalogResin)
def catalog_update_resin(
    resin_id: int,
    request: CatalogResinUpdate,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogResin:
    try:
        item = update_resin(resin_id, request.model_dump(exclude_none=True), db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.resin.update",
            resource_type="resin",
            resource_id=str(resin_id),
            details={"name": item["name"]},
        )
        _snapshot_catalog(auth, "catalog.resin.update", note=item["name"])
        return item
    except Exception as exc:
        _audit(
            auth=auth,
            action="catalog.resin.update",
            resource_type="resin",
            resource_id=str(resin_id),
            status="failed",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/catalog/resins/{resin_id}")
def catalog_delete_resin(
    resin_id: int,
    auth: AuthContext = Depends(require_role("admin")),
) -> dict[str, object]:
    deleted = delete_resin(resin_id, db_path=CATALOG_PATH)
    _audit(
        auth=auth,
        action="catalog.resin.delete",
        resource_type="resin",
        resource_id=str(resin_id),
        details={"deleted": deleted},
    )
    if deleted:
        _snapshot_catalog(auth, "catalog.resin.delete", note=f"resin_id={resin_id}")
    return {"deleted": deleted, "resin_id": resin_id}


@app.get("/catalog/profiles", response_model=list[CatalogProfile])
def catalog_list_profiles(
    printer_id: int | None = None,
    resin_id: int | None = None,
    query: str | None = None,
    active_only: bool = True,
    limit: int = 300,
    offset: int = 0,
) -> list[CatalogProfile]:
    return list_profiles(
        db_path=CATALOG_PATH,
        printer_id=printer_id,
        resin_id=resin_id,
        query=query,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )


@app.post("/catalog/profiles", response_model=CatalogProfile)
def catalog_create_profile(
    request: CatalogProfileCreate,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogProfile:
    try:
        item = create_or_upsert_profile(request.model_dump(), db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.profile.upsert",
            resource_type="profile",
            resource_id=str(item["id"]),
            details={"name": item["profile_name"]},
        )
        _snapshot_catalog(auth, "catalog.profile.upsert", note=item["profile_name"])
        return item
    except Exception as exc:
        _audit(auth=auth, action="catalog.profile.upsert", status="failed", details={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/catalog/profiles/{profile_id}", response_model=CatalogProfile)
def catalog_get_profile(profile_id: int) -> CatalogProfile:
    try:
        return get_profile(profile_id, db_path=CATALOG_PATH)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/catalog/profiles/{profile_id}", response_model=CatalogProfile)
def catalog_update_profile(
    profile_id: int,
    request: CatalogProfileUpdate,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogProfile:
    try:
        item = update_profile(profile_id, request.model_dump(exclude_none=True), db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.profile.update",
            resource_type="profile",
            resource_id=str(profile_id),
            details={"name": item["profile_name"]},
        )
        _snapshot_catalog(auth, "catalog.profile.update", note=item["profile_name"])
        return item
    except Exception as exc:
        _audit(
            auth=auth,
            action="catalog.profile.update",
            resource_type="profile",
            resource_id=str(profile_id),
            status="failed",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/catalog/profiles/{profile_id}")
def catalog_delete_profile(
    profile_id: int,
    auth: AuthContext = Depends(require_role("admin")),
) -> dict[str, object]:
    deleted = delete_profile(profile_id, db_path=CATALOG_PATH)
    _audit(
        auth=auth,
        action="catalog.profile.delete",
        resource_type="profile",
        resource_id=str(profile_id),
        details={"deleted": deleted},
    )
    if deleted:
        _snapshot_catalog(auth, "catalog.profile.delete", note=f"profile_id={profile_id}")
    return {"deleted": deleted, "profile_id": profile_id}


@app.get("/catalog/export")
def catalog_export() -> dict:
    return export_catalog(db_path=CATALOG_PATH)


@app.post("/catalog/import", response_model=CatalogImportResult)
def catalog_import(
    request: CatalogImportPayload,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogImportResult:
    try:
        result = import_catalog(request.model_dump(), db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.import",
            resource_type="catalog",
            details=result,
        )
        _snapshot_catalog(auth, "catalog.import", note="import payload")
        return CatalogImportResult(
            printers_upserted=result["printers_upserted"],
            resins_upserted=result["resins_upserted"],
            profiles_upserted=result["profiles_upserted"],
            notes=[],
        )
    except Exception as exc:
        _audit(auth=auth, action="catalog.import", status="failed", details={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/catalog/import/csv", response_model=CatalogCsvImportResult)
async def catalog_import_csv_endpoint(
    entity: str = Form(...),
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogCsvImportResult:
    try:
        content = (await file.read()).decode("utf-8-sig", errors="replace")
        result = import_catalog_csv(entity=entity, csv_text=content, db_path=CATALOG_PATH)
        _audit(
            auth=auth,
            action="catalog.import_csv",
            resource_type="catalog",
            details={"entity": entity, **result},
        )
        _snapshot_catalog(auth, "catalog.import_csv", note=f"entity={entity}")
        return CatalogCsvImportResult(
            entity=result["entity"],
            rows_processed=result["rows_processed"],
            upserted=result["upserted"],
            failed=result["failed"],
            notes=result["notes"],
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="catalog.import_csv",
            status="failed",
            details={"entity": entity, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/catalog/seed/legacy", response_model=CatalogImportResult)
def catalog_seed_legacy(auth: AuthContext = Depends(require_role("operator"))) -> CatalogImportResult:
    result = seed_catalog_from_legacy_json(db_path=CATALOG_PATH)
    _audit(auth=auth, action="catalog.seed_legacy", resource_type="catalog", details=result)
    _snapshot_catalog(auth, "catalog.seed_legacy", note="legacy seed")
    return CatalogImportResult(
        printers_upserted=result["printers_upserted"],
        resins_upserted=result["resins_upserted"],
        profiles_upserted=result["profiles_upserted"],
        notes=[],
    )


@app.get("/catalog/versions", response_model=list[CatalogVersion])
def catalog_versions(
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(require_role("viewer")),
) -> list[CatalogVersion]:
    items = list_catalog_versions(limit=limit, offset=offset, audit_db_path=AUDIT_PATH)
    return [CatalogVersion(**item) for item in items]


@app.post("/catalog/versions/snapshot", response_model=CatalogVersion)
def catalog_snapshot(
    request: CatalogVersionCreateRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> CatalogVersion:
    return _snapshot_catalog(auth, "catalog.manual_snapshot", note=request.note)


@app.get("/catalog/versions/{version_id}", response_model=CatalogVersionDetail)
def catalog_version_detail(
    version_id: int,
    auth: AuthContext = Depends(require_role("viewer")),
) -> CatalogVersionDetail:
    try:
        item = get_catalog_version(version_id, include_snapshot=True, audit_db_path=AUDIT_PATH)
        return CatalogVersionDetail(**item)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/catalog/versions/restore", response_model=CatalogImportResult)
def catalog_restore(
    request: CatalogVersionRestoreRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> CatalogImportResult:
    try:
        result = restore_catalog_version(
            request.version_id,
            actor=auth.actor,
            catalog_db_path=CATALOG_PATH,
            audit_db_path=AUDIT_PATH,
        )
        _snapshot_catalog(auth, "catalog.restore", note=f"from version {request.version_id}")
        return CatalogImportResult(
            printers_upserted=result["printers_upserted"],
            resins_upserted=result["resins_upserted"],
            profiles_upserted=result["profiles_upserted"],
            notes=[],
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="catalog.restore",
            status="failed",
            resource_type="catalog_version",
            resource_id=str(request.version_id),
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/ops/audit/events", response_model=list[AuditEvent])
def audit_events(
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 200,
    offset: int = 0,
    auth: AuthContext = Depends(require_role("admin")),
) -> list[AuditEvent]:
    rows = list_audit_events(
        action=action,
        resource_type=resource_type,
        limit=limit,
        offset=offset,
        db_path=AUDIT_PATH,
    )
    return [AuditEvent(**row) for row in rows]


@app.post("/sync/technical", response_model=TechnicalSyncResponse)
def sync_technical(
    request: TechnicalSyncRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> TechnicalSyncResponse:
    try:
        result = apply_technical_sync(
            payload=request.payload,
            source=request.source,
            db_path=CATALOG_PATH,
            replace_existing=request.replace_existing,
        )
        _audit(auth=auth, action="sync.technical", resource_type="catalog", details=result)
        _snapshot_catalog(auth, "sync.technical", note=request.source)
        return TechnicalSyncResponse(
            source=request.source,
            printers_upserted=result["printers_upserted"],
            resins_upserted=result["resins_upserted"],
            profiles_upserted=result["profiles_upserted"],
            curation=result.get("curation") or {},
            notes=result.get("notes") or [],
        )
    except Exception as exc:
        _audit(auth=auth, action="sync.technical", status="failed", details={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sync/technical/github", response_model=GitHubTechnicalSyncResponse)
def sync_technical_github(
    request: GitHubTechnicalSyncRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> GitHubTechnicalSyncResponse:
    source = request.source or f"github:{request.owner}/{request.repo}@{request.ref}"
    try:
        payload, raw_url = fetch_technical_sync_from_github(
            owner=request.owner,
            repo=request.repo,
            path=request.path,
            ref=request.ref,
        )
        result = apply_technical_sync(
            payload=payload,
            source=source,
            db_path=CATALOG_PATH,
            replace_existing=request.replace_existing,
            source_metadata={
                "kind": "github",
                "owner": request.owner,
                "repo": request.repo,
                "path": request.path,
                "ref": request.ref,
                "raw_url": raw_url,
            },
        )
        _audit(
            auth=auth,
            action="sync.technical.github",
            resource_type="catalog",
            details={
                "source": source,
                "owner": request.owner,
                "repo": request.repo,
                "path": request.path,
                "ref": request.ref,
                "raw_url": raw_url,
                **result,
            },
        )
        _snapshot_catalog(auth, "sync.technical.github", note=f"{request.owner}/{request.repo}:{request.path}@{request.ref}")
        return GitHubTechnicalSyncResponse(
            source=source,
            owner=request.owner,
            repo=request.repo,
            path=request.path,
            ref=request.ref,
            raw_url=raw_url,
            printers_upserted=result["printers_upserted"],
            resins_upserted=result["resins_upserted"],
            profiles_upserted=result["profiles_upserted"],
            curation=result.get("curation") or {},
            notes=result.get("notes") or [],
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="sync.technical.github",
            status="failed",
            details={
                "error": str(exc),
                "owner": request.owner,
                "repo": request.repo,
                "path": request.path,
                "ref": request.ref,
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sync/technical/scrape", response_model=WebScrapeTechnicalSyncResponse)
def sync_technical_scrape(
    request: WebScrapeTechnicalSyncRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> WebScrapeTechnicalSyncResponse:
    try:
        payload, summary = scrape_supported_catalog_pages(
            urls=request.urls,
            timeout_s=request.timeout_s,
        )
        result = apply_technical_sync(
            payload=payload,
            source=request.source,
            db_path=CATALOG_PATH,
            replace_existing=request.replace_existing,
            source_metadata={
                "kind": "web_scrape",
                "supported_scraper": True,
                "source_urls": summary.get("scraped_urls") or summary.get("normalized_urls") or [],
            },
        )
        notes = [*(result.get("notes") or []), *(summary.get("notes") or [])]
        _audit(
            auth=auth,
            action="sync.technical.scrape",
            resource_type="catalog",
            details={
                "source": request.source,
                "urls": request.urls,
                "scraped_urls": summary.get("scraped_urls") or [],
                "unsupported_urls": summary.get("unsupported_urls") or [],
                **result,
            },
        )
        _snapshot_catalog(auth, "sync.technical.scrape", note=request.source)
        return WebScrapeTechnicalSyncResponse(
            source=request.source,
            printers_upserted=result["printers_upserted"],
            resins_upserted=result["resins_upserted"],
            profiles_upserted=result["profiles_upserted"],
            curation=result.get("curation") or {},
            notes=notes,
            urls=summary.get("normalized_urls") or [],
            scraped_urls=summary.get("scraped_urls") or [],
            unsupported_urls=summary.get("unsupported_urls") or [],
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="sync.technical.scrape",
            status="failed",
            details={"error": str(exc), "urls": request.urls},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sync/technical/file", response_model=TechnicalSyncResponse)
async def sync_technical_file(
    file: UploadFile = File(...),
    source: str = Form("file_upload"),
    replace_existing: bool = Form(False),
    auth: AuthContext = Depends(require_role("operator")),
) -> TechnicalSyncResponse:
    try:
        text = (await file.read()).decode("utf-8-sig", errors="replace")
        payload = parse_technical_sync_json(text)
        result = apply_technical_sync(
            payload=payload,
            source=source,
            db_path=CATALOG_PATH,
            replace_existing=replace_existing,
        )
        _audit(
            auth=auth,
            action="sync.technical_file",
            resource_type="catalog",
            details={"source": source, **result},
        )
        _snapshot_catalog(auth, "sync.technical_file", note=source)
        return TechnicalSyncResponse(
            source=source,
            printers_upserted=result["printers_upserted"],
            resins_upserted=result["resins_upserted"],
            profiles_upserted=result["profiles_upserted"],
            curation=result.get("curation") or {},
            notes=result.get("notes") or [],
        )
    except Exception as exc:
        _audit(auth=auth, action="sync.technical_file", status="failed", details={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sync/technical/job", response_model=PipelineAsyncSubmitResponse)
def sync_technical_job(
    request: TechnicalSyncRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> PipelineAsyncSubmitResponse:
    def _work() -> dict:
        result = apply_technical_sync(
            payload=request.payload,
            source=request.source,
            db_path=CATALOG_PATH,
            replace_existing=request.replace_existing,
        )
        create_catalog_version(
            actor=auth.actor,
            note=f"job sync source={request.source}",
            source_action="sync.technical_job",
            catalog_db_path=CATALOG_PATH,
            audit_db_path=AUDIT_PATH,
        )
        return {
            "source": request.source,
            "printers_upserted": result["printers_upserted"],
            "resins_upserted": result["resins_upserted"],
            "profiles_upserted": result["profiles_upserted"],
            "curation": result.get("curation") or {},
            "notes": result.get("notes") or [],
        }

    job = JOB_QUEUE.submit(
        job_type="sync_technical",
        actor=auth.actor,
        metadata={"source": request.source},
        fn=_work,
    )
    _audit(auth=auth, action="sync.technical_job.submit", resource_type="job", resource_id=job["id"])
    return PipelineAsyncSubmitResponse(job=AsyncJob(**job))


@app.get("/sync/schedules", response_model=list[SyncSchedule])
def sync_schedule_list(
    enabled_only: bool = False,
    limit: int = 200,
    offset: int = 0,
    auth: AuthContext = Depends(require_role("viewer")),
) -> list[SyncSchedule]:
    _ = auth
    items = list_sync_schedules(
        enabled_only=enabled_only,
        limit=limit,
        offset=offset,
        db_path=SCHEDULES_PATH,
    )
    return [SyncSchedule(**item) for item in items]


@app.post("/sync/schedules", response_model=SyncSchedule)
def sync_schedule_create(
    request: SyncScheduleCreate,
    auth: AuthContext = Depends(require_role("operator")),
) -> SyncSchedule:
    try:
        item = create_or_upsert_sync_schedule(request.model_dump(), db_path=SCHEDULES_PATH)
        _audit(
            auth=auth,
            action="sync.schedule.upsert",
            resource_type="sync_schedule",
            resource_id=str(item["id"]),
            details={"name": item["name"], "interval_seconds": item["interval_seconds"]},
        )
        return SyncSchedule(**item)
    except Exception as exc:
        _audit(
            auth=auth,
            action="sync.schedule.upsert",
            resource_type="sync_schedule",
            status="failed",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sync/schedules/{schedule_id}", response_model=SyncSchedule)
def sync_schedule_get(
    schedule_id: int,
    auth: AuthContext = Depends(require_role("viewer")),
) -> SyncSchedule:
    _ = auth
    try:
        item = get_sync_schedule(schedule_id, db_path=SCHEDULES_PATH)
        return SyncSchedule(**item)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/sync/schedules/{schedule_id}", response_model=SyncSchedule)
def sync_schedule_patch(
    schedule_id: int,
    request: SyncScheduleUpdate,
    auth: AuthContext = Depends(require_role("operator")),
) -> SyncSchedule:
    try:
        item = update_sync_schedule(
            schedule_id,
            request.model_dump(exclude_none=True),
            db_path=SCHEDULES_PATH,
        )
        _audit(
            auth=auth,
            action="sync.schedule.update",
            resource_type="sync_schedule",
            resource_id=str(schedule_id),
            details={"name": item["name"]},
        )
        return SyncSchedule(**item)
    except Exception as exc:
        _audit(
            auth=auth,
            action="sync.schedule.update",
            resource_type="sync_schedule",
            resource_id=str(schedule_id),
            status="failed",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/sync/schedules/{schedule_id}")
def sync_schedule_remove(
    schedule_id: int,
    auth: AuthContext = Depends(require_role("admin")),
) -> dict[str, object]:
    deleted = delete_sync_schedule(schedule_id, db_path=SCHEDULES_PATH)
    _audit(
        auth=auth,
        action="sync.schedule.delete",
        resource_type="sync_schedule",
        resource_id=str(schedule_id),
        details={"deleted": deleted},
    )
    return {"deleted": deleted, "schedule_id": schedule_id}


@app.post("/sync/schedules/{schedule_id}/run-now", response_model=PipelineAsyncSubmitResponse)
def sync_schedule_run_now(
    schedule_id: int,
    auth: AuthContext = Depends(require_role("operator")),
) -> PipelineAsyncSubmitResponse:
    try:
        schedule = get_sync_schedule(schedule_id, db_path=SCHEDULES_PATH)
        mark_sync_schedule_run(schedule_id, status="queued", error=None, db_path=SCHEDULES_PATH)
        job = _submit_sync_schedule_job(schedule, actor=auth.actor)
        _audit(
            auth=auth,
            action="sync.schedule.run_now",
            resource_type="sync_schedule",
            resource_id=str(schedule_id),
            details={"job_id": job["id"]},
        )
        return PipelineAsyncSubmitResponse(job=AsyncJob(**job))
    except Exception as exc:
        _audit(
            auth=auth,
            action="sync.schedule.run_now",
            resource_type="sync_schedule",
            resource_id=str(schedule_id),
            status="failed",
            details={"error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sync/scheduler/health", response_model=SyncSchedulerHealth)
def sync_scheduler_health(auth: AuthContext = Depends(require_role("viewer"))) -> SyncSchedulerHealth:
    _ = auth
    schedules = list_sync_schedules(enabled_only=False, db_path=SCHEDULES_PATH, limit=2000, offset=0)
    enabled = sum(1 for item in schedules if item["enabled"])
    return SyncSchedulerHealth(
        running=bool(SCHEDULER and SCHEDULER.is_running()),
        poll_seconds=float(SCHEDULER.poll_seconds if SCHEDULER else 0.0),
        schedules_total=len(schedules),
        schedules_enabled=enabled,
    )


@app.post("/sync/scheduler/tick", response_model=SyncSchedulerTickResult)
def sync_scheduler_tick(auth: AuthContext = Depends(require_role("operator"))) -> SyncSchedulerTickResult:
    _ = auth
    if SCHEDULER is None:
        raise HTTPException(status_code=500, detail="Scheduler is not initialized.")
    result = SCHEDULER.tick_once()
    return SyncSchedulerTickResult(**result)


@app.get("/reference/resins")
def reference_resins(resin_type: str | None = None) -> dict:
    db = load_resin_database(db_path=CATALOG_PATH)
    profiles = db.get("profiles", [])
    if resin_type:
        key = resin_type.strip().lower()
        profiles = [p for p in profiles if str(p.get("resin_type", "")).strip().lower() == key]
    return {
        "schema_version": db.get("schema_version"),
        "updated_at": db.get("updated_at"),
        "count": len(profiles),
        "profiles": profiles,
    }


@app.get("/wizard/model/check/status/{job_id}", response_model=WizardAnalysisProgressResponse)
def wizard_model_check_status(
    job_id: str,
    auth: AuthContext = Depends(require_role("operator")),
) -> WizardAnalysisProgressResponse:
    _ = auth
    normalized_job_id = _normalize_wizard_analysis_job_id(job_id)
    if normalized_job_id is None:
        raise HTTPException(status_code=400, detail="A non-empty analysis job id is required.")
    return _wizard_analysis_progress_response(normalized_job_id)


@app.post("/wizard/model/check/status/{job_id}/cancel", response_model=WizardAnalysisProgressResponse)
def wizard_model_check_cancel(
    job_id: str,
    auth: AuthContext = Depends(require_role("operator")),
) -> WizardAnalysisProgressResponse:
    normalized_job_id = _normalize_wizard_analysis_job_id(job_id)
    if normalized_job_id is None:
        raise HTTPException(status_code=400, detail="A non-empty analysis job id is required.")
    progress = _request_wizard_analysis_cancel(normalized_job_id)
    _audit(
        auth=auth,
        action="wizard.model.check.cancel",
        resource_type="model",
        resource_id=normalized_job_id,
        details={"stage": progress.stage, "status": progress.status},
    )
    return progress


@app.post("/wizard/model/check", response_model=WizardModelCheckResponse)
async def wizard_model_check(
    file: UploadFile = File(...),
    slice_height_mm: float = Form(0.01),
    analysis_level: AnalysisLevel = Form("minimum"),
    progress_job_id: str | None = Form(default=None),
    auth: AuthContext = Depends(require_role("operator")),
) -> WizardModelCheckResponse:
    _require_stl_upload(file)
    job_id = _normalize_wizard_analysis_job_id(progress_job_id)
    _set_wizard_analysis_progress(
        job_id,
        status="queued",
        stage="save_upload",
        message="Saving uploaded STL to local storage.",
        cancel_requested=False,
    )
    temp_file = await asyncio.to_thread(_save_upload, file)
    try:
        progress_callback: Callable[[str, str], None] | None = None
        cancel_check: Callable[[], None] | None = None
        if job_id is not None:
            cancel_check = lambda: _raise_if_wizard_analysis_cancelled(job_id)
            progress_callback = lambda stage, message: _set_wizard_analysis_progress(
                job_id,
                status=_wizard_analysis_runtime_status(job_id),
                stage=stage,
                message=message,
            )
            cancel_check()
            progress_callback("load_prepare_mesh", "Upload saved. Loading mesh geometry and checking topology health.")

        analysis = await asyncio.to_thread(
            pipeline.run_phase_1_geometry,
            file_path=str(temp_file),
            slice_height_mm=slice_height_mm,
            auto_repair=False,
            analysis_level=analysis_level,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        health = analysis.mesh_health
        requires_fix = mesh_health_requires_fix(health)
        fix_reasons = mesh_health_actionable_issues(health) if requires_fix else []
        has_issues = bool(mesh_health_actionable_issues(health))
        _audit(
            auth=auth,
            action="wizard.model.check",
            resource_type="model",
            details={
                "file_name": file.filename,
                "slice_height_mm": slice_height_mm,
                "analysis_level": analysis_level,
                "requires_fix": requires_fix,
                "fix_reasons": fix_reasons,
            },
        )
        _set_wizard_analysis_progress(
            job_id,
            status="completed",
            stage="completed",
            message=_analysis_completion_message(analysis),
            cancel_requested=False,
            performance_ms=analysis.performance_ms,
        )
        return WizardModelCheckResponse(
            analysis=analysis,
            has_issues=has_issues,
            requires_fix=requires_fix,
            fix_reasons=fix_reasons,
        )
    except AnalysisCancelledError as exc:
        detail = str(exc)
        _set_wizard_analysis_progress(
            job_id,
            status="cancelled",
            message=detail,
            cancel_requested=True,
        )
        _audit(
            auth=auth,
            action="wizard.model.check",
            resource_type="model",
            status="cancelled",
            details={"file_name": file.filename, "analysis_level": analysis_level, "error": detail},
        )
        raise HTTPException(status_code=409, detail=detail) from exc
    except Exception as exc:
        detail = _analysis_failure_detail(job_id, exc)
        _set_wizard_analysis_progress(
            job_id,
            status="failed",
            message=detail,
            error=str(exc),
        )
        _audit(
            auth=auth,
            action="wizard.model.check",
            resource_type="model",
            status="failed",
            details={"file_name": file.filename, "analysis_level": analysis_level, "error": detail},
        )
        raise HTTPException(status_code=400, detail=detail) from exc
    finally:
        _safe_unlink(temp_file)


@app.post("/wizard/model/fix", response_model=WizardModelFixResponse)
async def wizard_model_fix(
    file: UploadFile = File(...),
    slice_height_mm: float = Form(0.01),
    analysis_level: AnalysisLevel = Form("minimum"),
    auth: AuthContext = Depends(require_role("operator")),
) -> WizardModelFixResponse:
    _require_stl_upload(file)
    temp_file = await asyncio.to_thread(_save_upload, file)
    original_name = Path(file.filename or "model.stl")
    safe_base = _safe_slug(original_name.stem, "model")
    out_name = f"{safe_base}_fixed_{uuid4().hex[:8]}.stl"
    output_path = WIZARD_ARTIFACTS_DIR / out_name
    try:
        repair_outcome = await asyncio.to_thread(
            repair_mesh_file,
            str(temp_file),
            str(output_path),
        )
        analysis = await asyncio.to_thread(
            pipeline.run_phase_1_geometry,
            file_path=str(output_path),
            slice_height_mm=slice_height_mm,
            auto_repair=False,
            analysis_level=analysis_level,
        )
        final_health = analysis.mesh_health or repair_outcome.after_fix
        after_fix = final_health.model_copy(
            update={
                "repaired": repair_outcome.repaired,
                "repair_actions": list(repair_outcome.repair_actions),
            }
        )
        before_issues = mesh_health_actionable_issues(repair_outcome.before_fix)
        after_issues = mesh_health_actionable_issues(after_fix)
        resolved_issues = [issue for issue in before_issues if issue not in after_issues]
        fully_repaired = not mesh_health_requires_fix(after_fix)
        recheck_summary = (
            "Auto-Fix saved the repaired STL and re-checked it. No blocking mesh defects remain."
            if fully_repaired
            else (
                "Auto-Fix saved the repaired STL and re-checked it. "
                f"{len(after_issues)} blocking issue type(s) remain after save/reload."
            )
        )
        download_name = f"{safe_base}_fixed.stl"
        artifact_id = _register_wizard_artifact(
            output_path,
            download_name=download_name,
            media_type="model/stl",
        )
        _audit(
            auth=auth,
            action="wizard.model.fix",
            resource_type="model",
            details={
                "file_name": file.filename,
                "analysis_level": analysis_level,
                "repaired": repair_outcome.repaired,
                "fully_repaired": fully_repaired,
                "repair_actions": repair_outcome.repair_actions,
                "remaining_issues": after_issues,
                "artifact_id": artifact_id,
            },
        )
        return WizardModelFixResponse(
            analysis=analysis,
            repaired=repair_outcome.repaired,
            fully_repaired=fully_repaired,
            rechecked_after_fix=True,
            recheck_summary=recheck_summary,
            repair_actions=repair_outcome.repair_actions,
            resolved_issues=resolved_issues,
            remaining_issues=after_issues,
            before_fix=repair_outcome.before_fix,
            after_fix=after_fix,
            download_id=artifact_id,
            download_url=_wizard_download_url(artifact_id),
            output_file_name=download_name,
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="wizard.model.fix",
            resource_type="model",
            status="failed",
            details={"file_name": file.filename, "analysis_level": analysis_level, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _safe_unlink(temp_file)


@app.post("/wizard/model/retopology", response_model=WizardModelRetopologyResponse)
async def wizard_model_retopology(
    file: UploadFile = File(...),
    slice_height_mm: float = Form(0.01),
    analysis_level: AnalysisLevel = Form("extreme"),
    mode: RetopologyMode = Form("quad"),
    target_faces: int | None = Form(default=None),
    voxel_size_mm: float | None = Form(default=None),
    preserve_sharp: bool = Form(True),
    preserve_boundary: bool = Form(True),
    auth: AuthContext = Depends(require_role("operator")),
) -> WizardModelRetopologyResponse:
    _require_stl_upload(file)
    if target_faces is not None and target_faces < 1:
        raise HTTPException(status_code=400, detail="target_faces must be at least 1 when provided.")
    if voxel_size_mm is not None and voxel_size_mm <= 0:
        raise HTTPException(status_code=400, detail="voxel_size_mm must be greater than 0 when provided.")

    temp_file = await asyncio.to_thread(_save_upload, file)
    original_name = Path(file.filename or "model.stl")
    safe_base = _safe_slug(original_name.stem, "model")
    output_name = f"{safe_base}_{mode}_retopo_{uuid4().hex[:8]}.stl"
    output_path = WIZARD_ARTIFACTS_DIR / output_name
    preprocessing_path: Path | None = None
    try:
        source_analysis = await asyncio.to_thread(
            pipeline.run_phase_1_geometry,
            file_path=str(temp_file),
            slice_height_mm=slice_height_mm,
            auto_repair=False,
            analysis_level=analysis_level,
        )

        preprocessing_fix = None
        retopology_source_path = Path(temp_file)
        notes: list[str] = []
        if mesh_health_requires_fix(source_analysis.mesh_health):
            preprocessing_path = temp_file.with_name(f"{temp_file.stem}_pre_retopo_{uuid4().hex[:8]}.stl")
            preprocessing_fix = await asyncio.to_thread(
                repair_mesh_file,
                str(temp_file),
                str(preprocessing_path),
            )
            retopology_source_path = preprocessing_path
            notes.append(
                "Input mesh needed a repair pre-pass before retopology, so the existing STL repair flow ran first."
            )
            if preprocessing_fix.remaining_issues:
                notes.append(
                    "Retopology continued from the repaired mesh even though some save/reload issues remained."
                )

        resolved_target_faces = target_faces if mode == "quad" else None
        if mode == "quad" and resolved_target_faces is None:
            resolved_target_faces = _default_retopology_target_faces(source_analysis)

        resolved_voxel_size_mm = voxel_size_mm if mode == "voxel" else None
        if mode == "voxel" and resolved_voxel_size_mm is None:
            resolved_voxel_size_mm = _default_retopology_voxel_size_mm(source_analysis)

        notes.extend(
            await asyncio.to_thread(
                run_blender_retopology,
                input_path=str(retopology_source_path),
                output_path=str(output_path),
                mode=mode,
                target_faces=resolved_target_faces,
                voxel_size_mm=resolved_voxel_size_mm,
                preserve_sharp=preserve_sharp,
                preserve_boundary=preserve_boundary,
            )
        )

        retopology_analysis = await asyncio.to_thread(
            pipeline.run_phase_1_geometry,
            file_path=str(output_path),
            slice_height_mm=slice_height_mm,
            auto_repair=False,
            analysis_level=analysis_level,
        )

        download_name = f"{safe_base}_{mode}_retopo.stl"
        artifact_id = _register_wizard_artifact(
            output_path,
            download_name=download_name,
            media_type="model/stl",
        )
        _audit(
            auth=auth,
            action="wizard.model.retopology",
            resource_type="model",
            details={
                "file_name": file.filename,
                "analysis_level": analysis_level,
                "mode": mode,
                "target_faces": resolved_target_faces,
                "voxel_size_mm": resolved_voxel_size_mm,
                "preserve_sharp": preserve_sharp,
                "preserve_boundary": preserve_boundary,
                "artifact_id": artifact_id,
            },
        )
        return WizardModelRetopologyResponse(
            source_analysis=source_analysis,
            retopology_analysis=retopology_analysis,
            mode=mode,
            backend_requested="blender",
            backend_used="blender",
            target_faces=resolved_target_faces,
            voxel_size_mm=resolved_voxel_size_mm,
            preserve_sharp=preserve_sharp,
            preserve_boundary=preserve_boundary,
            preprocessing_fix=preprocessing_fix,
            notes=notes,
            download_id=artifact_id,
            download_url=_wizard_download_url(artifact_id),
            output_file_name=download_name,
        )
    except RetopologyError as exc:
        _audit(
            auth=auth,
            action="wizard.model.retopology",
            resource_type="model",
            status="failed",
            details={"file_name": file.filename, "analysis_level": analysis_level, "mode": mode, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _audit(
            auth=auth,
            action="wizard.model.retopology",
            resource_type="model",
            status="failed",
            details={"file_name": file.filename, "analysis_level": analysis_level, "mode": mode, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _safe_unlink(temp_file)
        if preprocessing_path is not None:
            _safe_unlink(preprocessing_path)


@app.post("/wizard/settings/recommend", response_model=WizardSettingsResponse)
def wizard_settings_recommend(
    request: WizardSettingsRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> WizardSettingsResponse:
    try:
        settings = pipeline.run_phase_2_parameters(
            analysis=request.analysis,
            resin_type=request.resin_type,
            use_case=request.use_case,
            printer=request.printer,
            ambient_temp_c=request.ambient_temp_c,
            film_releases=request.film_releases,
            catalog_db_path=CATALOG_PATH,
        )
        target_process = _wizard_target_from_settings(settings)
        requested_target = _normalize_wizard_target(request.target_process)
        if request.target_process is not None and requested_target != target_process:
            raise ValueError(
                f"Selected target '{requested_target}' does not match the chosen printer/material profile ({target_process})."
            )

        slicer_name = WIZARD_SLICERS_BY_TARGET[target_process]
        if target_process == "fdm":
            slicer_profile_text = render_cura_profile(settings)
            slicer_profile_suffix = ".curaprofile"
            chitubox_cfg = None
        else:
            slicer_profile_text = render_chitubox_cfg(settings)
            slicer_profile_suffix = ".cfg"
            chitubox_cfg = slicer_profile_text

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prefix = (
            f"{_safe_slug(target_process, 'target')}_"
            f"{_safe_slug(request.printer, 'printer')}_"
            f"{_safe_slug(request.resin_type, 'material')}_{stamp}"
        )
        settings_path = WIZARD_ARTIFACTS_DIR / f"{prefix}_settings.json"
        slicer_profile_path = WIZARD_ARTIFACTS_DIR / f"{prefix}{slicer_profile_suffix}"
        slicer_profile_name = f"{prefix}{slicer_profile_suffix}"

        settings_payload = {
            "generated_at": _iso_timestamp(),
            "printer": request.printer,
            "resin_type": request.resin_type,
            "target_process": target_process,
            "slicer_name": slicer_name,
            "use_case": request.use_case,
            "settings": settings.model_dump(),
        }
        settings_path.write_text(json.dumps(settings_payload, ensure_ascii=True, indent=2), encoding="utf-8")
        slicer_profile_path.write_text(slicer_profile_text, encoding="utf-8")

        settings_artifact_id = _register_wizard_artifact(
            settings_path,
            download_name=f"{prefix}_settings.json",
            media_type="application/json",
        )
        slicer_profile_artifact_id = _register_wizard_artifact(
            slicer_profile_path,
            download_name=slicer_profile_name,
            media_type="text/plain",
        )

        _audit(
            auth=auth,
            action="wizard.settings.recommend",
            resource_type="settings",
            details={
                "printer": request.printer,
                "resin_type": request.resin_type,
                "target_process": target_process,
                "slicer_name": slicer_name,
                "use_case": request.use_case,
                "settings_artifact_id": settings_artifact_id,
                "cfg_artifact_id": slicer_profile_artifact_id,
            },
        )

        return WizardSettingsResponse(
            settings=settings,
            target_process=target_process,
            slicer_name=slicer_name,
            slicer_profile_text=slicer_profile_text,
            slicer_profile_file_name=slicer_profile_name,
            slicer_profile_download_id=slicer_profile_artifact_id,
            slicer_profile_download_url=_wizard_download_url(slicer_profile_artifact_id),
            chitubox_cfg=chitubox_cfg,
            settings_download_id=settings_artifact_id,
            settings_download_url=_wizard_download_url(settings_artifact_id),
            cfg_download_id=slicer_profile_artifact_id,
            cfg_download_url=_wizard_download_url(slicer_profile_artifact_id),
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="wizard.settings.recommend",
            resource_type="settings",
            status="failed",
            details={"printer": request.printer, "resin_type": request.resin_type, "error": str(exc)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/wizard/download/{artifact_id}")
def wizard_download_artifact(
    artifact_id: str,
    auth: AuthContext = Depends(require_role("viewer")),
) -> FileResponse:
    _ = auth
    meta = _wizard_artifact(artifact_id)
    path = Path(str(meta["path"]))
    return FileResponse(
        path=path,
        media_type=str(meta.get("media_type") or "application/octet-stream"),
        filename=str(meta.get("download_name") or path.name),
    )


@app.post("/phase1/analyze")
async def phase1_analyze(
    file: UploadFile = File(...),
    slice_height_mm: float = Form(0.01),
    auto_repair: bool = Form(True),
    analysis_level: AnalysisLevel = Form("balanced"),
) -> dict:
    temp_file = await asyncio.to_thread(_save_upload, file)
    try:
        analysis = await asyncio.to_thread(
            pipeline.run_phase_1_geometry,
            file_path=str(temp_file),
            slice_height_mm=slice_height_mm,
            auto_repair=auto_repair,
            analysis_level=analysis_level,
        )
        return analysis.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _safe_unlink(temp_file)


@app.post("/phase2/optimize", response_model=OptimalSettings)
def phase2_optimize(request: OptimizeRequest) -> OptimalSettings:
    return pipeline.run_phase_2_parameters(
        analysis=request.analysis,
        resin_type=request.resin_type,
        use_case=request.use_case,
        printer=request.printer,
        ambient_temp_c=request.ambient_temp_c,
        film_releases=request.film_releases,
        catalog_db_path=CATALOG_PATH,
    )


@app.post("/phase2/optimize/history-aware", response_model=HistoryAwareOptimizeResponse)
def phase2_optimize_history_aware(request: HistoryAwareOptimizeRequest) -> HistoryAwareOptimizeResponse:
    try:
        base_settings = pipeline.run_phase_2_parameters(
            analysis=request.analysis,
            resin_type=request.resin_type,
            use_case=request.use_case,
            printer=request.printer,
            ambient_temp_c=request.ambient_temp_c,
            film_releases=request.film_releases,
            catalog_db_path=CATALOG_PATH,
        )
        history_records = query_feedback_records(
            db_path=STORAGE_PATH,
            printer=request.printer,
            resin_type=request.resin_type,
            source=request.source,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.history_limit,
            offset=0,
        )
        history_summary = summarize_feedback(
            records=history_records,
            printer=request.printer,
            resin_type=request.resin_type,
        )
        adapted = adapt_settings_from_feedback(
            settings=base_settings,
            summary=history_summary,
            min_history_records=request.min_history_records,
        )
        return HistoryAwareOptimizeResponse(
            settings=adapted.settings,
            history_record_count=len(history_records),
            history_summary=history_summary,
            adjustments=adapted.adjustments,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/phase3/feedback", response_model=FeedbackSummary)
def phase3_feedback(request: FeedbackBatchRequest) -> FeedbackSummary:
    return summarize_feedback(
        records=request.records,
        printer=request.printer,
        resin_type=request.resin_type,
    )


@app.post("/phase3/camera-log/analyze", response_model=CameraLogAnalysisResult)
async def phase3_camera_log_analyze(file: UploadFile = File(...)) -> CameraLogAnalysisResult:
    try:
        content = await file.read()
        return analyze_camera_log(content.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/phase3/import/community-sheet/file", response_model=FeedbackIngestionResult)
async def phase3_import_community_sheet_file(
    file: UploadFile = File(...),
    printer: str = Form(...),
    resin_type: str = Form(...),
    source: str = Form("community_sheet_file"),
    auth: AuthContext = Depends(require_role("operator")),
) -> FeedbackIngestionResult:
    try:
        content = await file.read()
        records, notes = ingest_community_sheet_bytes(
            content=content,
            default_printer=printer,
            default_resin=resin_type,
            source=source,
        )
        persisted_count = save_feedback_records(records, db_path=STORAGE_PATH)
        summary = summarize_feedback(records=records, printer=printer, resin_type=resin_type)
        _audit(
            auth=auth,
            action="feedback.import.community_sheet_file",
            resource_type="feedback",
            details={"source": source, "imported_count": len(records), "persisted_count": persisted_count},
        )
        return FeedbackIngestionResult(
            imported_count=len(records),
            summary=summary,
            records=records,
            persisted_count=persisted_count,
            storage_path=str(STORAGE_PATH),
            notes=notes,
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="feedback.import.community_sheet_file",
            status="failed",
            details={"error": str(exc), "source": source},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/phase3/import/community-sheet/url", response_model=FeedbackIngestionResult)
def phase3_import_community_sheet_url(
    request: CommunitySheetURLRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> FeedbackIngestionResult:
    try:
        records, notes = ingest_community_sheet_url(
            url=request.url,
            default_printer=request.printer,
            default_resin=request.resin_type,
            source=request.source,
        )
        persisted_count = save_feedback_records(records, db_path=STORAGE_PATH)
        summary = summarize_feedback(
            records=records,
            printer=request.printer,
            resin_type=request.resin_type,
        )
        _audit(
            auth=auth,
            action="feedback.import.community_sheet_url",
            resource_type="feedback",
            details={"source": request.source, "imported_count": len(records), "persisted_count": persisted_count},
        )
        return FeedbackIngestionResult(
            imported_count=len(records),
            summary=summary,
            records=records,
            persisted_count=persisted_count,
            storage_path=str(STORAGE_PATH),
            notes=notes,
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="feedback.import.community_sheet_url",
            status="failed",
            details={"error": str(exc), "source": request.source},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/phase3/import/youtube", response_model=FeedbackIngestionResult)
def phase3_import_youtube(
    request: YouTubeIngestRequest,
    auth: AuthContext = Depends(require_role("operator")),
) -> FeedbackIngestionResult:
    try:
        records, notes = ingest_youtube_transcripts(
            video_urls=request.video_urls,
            printer=request.printer,
            resin_type=request.resin_type,
            languages=request.languages,
        )
        persisted_count = save_feedback_records(records, db_path=STORAGE_PATH)
        summary = summarize_feedback(
            records=records,
            printer=request.printer,
            resin_type=request.resin_type,
        )
        _audit(
            auth=auth,
            action="feedback.import.youtube",
            resource_type="feedback",
            details={"videos": len(request.video_urls), "imported_count": len(records), "persisted_count": persisted_count},
        )
        return FeedbackIngestionResult(
            imported_count=len(records),
            summary=summary,
            records=records,
            persisted_count=persisted_count,
            storage_path=str(STORAGE_PATH),
            notes=notes,
        )
    except Exception as exc:
        _audit(
            auth=auth,
            action="feedback.import.youtube",
            status="failed",
            details={"error": str(exc), "count": len(request.video_urls)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/phase3/history/health")
def phase3_history_health() -> dict[str, str]:
    path = init_feedback_store(STORAGE_PATH)
    return {"status": "ok", "storage_path": str(path)}


@app.post("/phase3/history/query", response_model=FeedbackHistoryQueryResult)
def phase3_history_query(request: FeedbackHistoryQueryRequest) -> FeedbackHistoryQueryResult:
    try:
        records = query_feedback_records(
            db_path=STORAGE_PATH,
            printer=request.printer,
            resin_type=request.resin_type,
            source=request.source,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
            offset=request.offset,
        )
        total = count_feedback_records(
            db_path=STORAGE_PATH,
            printer=request.printer,
            resin_type=request.resin_type,
            source=request.source,
            date_from=request.date_from,
            date_to=request.date_to,
        )
        return FeedbackHistoryQueryResult(total_count=total, records=records)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/phase3/history/summary", response_model=FeedbackHistorySummaryResult)
def phase3_history_summary(request: FeedbackHistorySummaryRequest) -> FeedbackHistorySummaryResult:
    try:
        records = query_feedback_records(
            db_path=STORAGE_PATH,
            printer=request.printer,
            resin_type=request.resin_type,
            source=request.source,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
            offset=0,
        )
        total = count_feedback_records(
            db_path=STORAGE_PATH,
            printer=request.printer,
            resin_type=request.resin_type,
            source=request.source,
            date_from=request.date_from,
            date_to=request.date_to,
        )
        summary = summarize_feedback(records=records, printer=request.printer, resin_type=request.resin_type)
        return FeedbackHistorySummaryResult(total_count=total, records_used=len(records), summary=summary)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/pipeline", response_model=PipelineResponse)
async def run_pipeline(
    file: UploadFile = File(...),
    resin_type: str = Form(...),
    use_case: UseCase = Form("miniature"),
    printer: str = Form("Elegoo Mars 5 Ultra"),
    ambient_temp_c: float | None = Form(default=None),
    film_releases: int = Form(default=0),
    slice_height_mm: float = Form(default=0.01),
    auto_repair: bool = Form(default=True),
    analysis_level: AnalysisLevel = Form(default="balanced"),
    auth: AuthContext = Depends(require_role("operator")),
) -> PipelineResponse:
    temp_file = await asyncio.to_thread(_save_upload, file)
    try:
        result = await asyncio.to_thread(
            pipeline.run_full_pipeline,
            file_path=str(temp_file),
            resin_type=resin_type,
            use_case=use_case,
            printer=printer,
            ambient_temp_c=ambient_temp_c,
            film_releases=film_releases,
            slice_height_mm=slice_height_mm,
            auto_repair=auto_repair,
            analysis_level=analysis_level,
            catalog_db_path=CATALOG_PATH,
        )
        _audit(
            auth=auth,
            action="pipeline.run",
            resource_type="pipeline",
            details={
                "printer": printer,
                "resin_type": resin_type,
                "use_case": use_case,
                "analysis_level": analysis_level,
            },
        )
        return result
    except Exception as exc:
        _audit(
            auth=auth,
            action="pipeline.run",
            resource_type="pipeline",
            status="failed",
            details={
                "error": str(exc),
                "printer": printer,
                "resin_type": resin_type,
                "analysis_level": analysis_level,
            },
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        _safe_unlink(temp_file)


@app.post("/jobs/pipeline", response_model=PipelineAsyncSubmitResponse)
async def run_pipeline_job(
    file: UploadFile = File(...),
    resin_type: str = Form(...),
    use_case: UseCase = Form("miniature"),
    printer: str = Form("Elegoo Mars 5 Ultra"),
    ambient_temp_c: float | None = Form(default=None),
    film_releases: int = Form(default=0),
    slice_height_mm: float = Form(default=0.01),
    auto_repair: bool = Form(default=True),
    analysis_level: AnalysisLevel = Form(default="balanced"),
    auth: AuthContext = Depends(require_role("operator")),
) -> PipelineAsyncSubmitResponse:
    temp_file = await asyncio.to_thread(_save_upload, file)

    def _work() -> dict:
        try:
            output = pipeline.run_full_pipeline(
                file_path=str(temp_file),
                resin_type=resin_type,
                use_case=use_case,
                printer=printer,
                ambient_temp_c=ambient_temp_c,
                film_releases=film_releases,
                slice_height_mm=slice_height_mm,
                auto_repair=auto_repair,
                analysis_level=analysis_level,
                catalog_db_path=CATALOG_PATH,
            )
            return output.model_dump()
        finally:
            _safe_unlink(temp_file)

    job = JOB_QUEUE.submit(
        job_type="pipeline",
        actor=auth.actor,
        metadata={
            "resin_type": resin_type,
            "use_case": use_case,
            "printer": printer,
            "analysis_level": analysis_level,
        },
        fn=_work,
    )
    _audit(auth=auth, action="job.pipeline.submit", resource_type="job", resource_id=job["id"])
    return PipelineAsyncSubmitResponse(job=AsyncJob(**job))


@app.get("/jobs", response_model=list[AsyncJob])
def list_jobs(
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(require_role("viewer")),
) -> list[AsyncJob]:
    _ = auth
    return [AsyncJob(**item) for item in JOB_QUEUE.list(limit=limit, offset=offset)]


@app.get("/jobs/health")
def jobs_health(auth: AuthContext = Depends(require_role("viewer"))) -> dict[str, object]:
    _ = auth
    counts = count_jobs(db_path=JOBS_PATH)
    return {"status": "ok", "storage_path": str(JOBS_PATH), "counts": counts}


@app.get("/jobs/{job_id}", response_model=AsyncJob)
def get_job(job_id: str, auth: AuthContext = Depends(require_role("viewer"))) -> AsyncJob:
    _ = auth
    try:
        return AsyncJob(**JOB_QUEUE.get(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.") from exc


@app.post("/jobs/{job_id}/cancel", response_model=JobCancelResponse)
def cancel_job(job_id: str, auth: AuthContext = Depends(require_role("operator"))) -> JobCancelResponse:
    try:
        cancelled, item = JOB_QUEUE.cancel(job_id)
        _audit(
            auth=auth,
            action="job.cancel",
            resource_type="job",
            resource_id=job_id,
            details={"cancelled": cancelled, "status": item.get("status")},
        )
        return JobCancelResponse(cancelled=cancelled, job=AsyncJob(**item))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.") from exc


@app.post("/jobs/cleanup", response_model=JobCleanupResponse)
def cleanup_job_history(
    request: JobCleanupRequest,
    auth: AuthContext = Depends(require_role("admin")),
) -> JobCleanupResponse:
    result = cleanup_jobs(
        max_age_seconds=int(request.max_age_days * 86400),
        keep_latest=request.keep_latest,
        db_path=JOBS_PATH,
    )
    _audit(
        auth=auth,
        action="job.cleanup",
        resource_type="job",
        details={"request": request.model_dump(), **result},
    )
    return JobCleanupResponse(**result)


@app.post("/ops/jobs/run/seed-legacy", response_model=PipelineAsyncSubmitResponse)
def run_seed_legacy_job(auth: AuthContext = Depends(require_role("operator"))) -> PipelineAsyncSubmitResponse:
    def _work() -> dict:
        result = seed_catalog_from_legacy_json(db_path=CATALOG_PATH)
        create_catalog_version(
            actor=auth.actor,
            note="scheduled seed legacy",
            source_action="ops.jobs.seed_legacy",
            catalog_db_path=CATALOG_PATH,
            audit_db_path=AUDIT_PATH,
        )
        return result

    job = JOB_QUEUE.submit(
        job_type="seed_legacy",
        actor=auth.actor,
        metadata={},
        fn=_work,
    )
    _audit(auth=auth, action="ops.jobs.seed_legacy.submit", resource_type="job", resource_id=job["id"])
    return PipelineAsyncSubmitResponse(job=AsyncJob(**job))


def _save_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "model.stl").suffix or ".stl"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        return Path(tmp.name)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
