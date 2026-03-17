from __future__ import annotations

import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.audit_store import (
    create_catalog_version,
    get_catalog_version,
    init_audit_store,
    list_audit_events,
    list_catalog_versions,
    log_audit_event,
    restore_catalog_version,
)
from app.auth import AuthContext, auth_enforced, require_role
from app.camera_log import analyze_camera_log
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
from app.job_queue import InMemoryJobQueue
from app.job_store import cleanup_jobs, count_jobs, init_job_store
from app.monitoring import InMemoryMetrics
from app.models import (
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
    YouTubeIngestRequest,
)
from app.pipeline import ModularAgenticPipeline
from app.resin_db import load_resin_database
from app.scheduler import TechnicalSyncScheduler
from app.sync_service import apply_technical_sync, parse_technical_sync_json
from app.sync_schedule_store import (
    create_or_upsert_sync_schedule,
    delete_sync_schedule,
    get_sync_schedule,
    init_sync_schedule_store,
    list_sync_schedules,
    mark_sync_schedule_run,
    update_sync_schedule,
)

app = FastAPI(
    title="Mars 5 Ultra Agentic Slicer",
    version="0.1.0",
    description="Modular agent workflow for geometry analysis, parameter injection, and resin intelligence.",
)
pipeline = ModularAgenticPipeline()
STORAGE_PATH = init_feedback_store()
CATALOG_PATH = init_catalog_store()
AUDIT_PATH = init_audit_store()
JOBS_PATH = init_job_store()
SCHEDULES_PATH = init_sync_schedule_store()
seed_catalog_from_legacy_json(db_path=CATALOG_PATH)
ADMIN_UI_PATH = Path(__file__).resolve().parent / "static" / "catalog_admin.html"
APP_UI_PATH = Path(__file__).resolve().parent / "static" / "app.html"
JOB_QUEUE = InMemoryJobQueue(max_workers=2, db_path=JOBS_PATH)
METRICS = InMemoryMetrics()
SCHEDULER: TechnicalSyncScheduler | None = None


def _env_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _submit_sync_schedule_job(schedule: dict, actor: str = "scheduler") -> dict:
    schedule_id = int(schedule["id"])

    def _work() -> dict:
        try:
            result = apply_technical_sync(
                payload=schedule["payload"],
                source=schedule["source"],
                db_path=CATALOG_PATH,
                replace_existing=bool(schedule.get("replace_existing", False)),
            )
            mark_sync_schedule_run(
                schedule_id,
                status="succeeded",
                error=None,
                db_path=SCHEDULES_PATH,
            )
            return {
                "source": schedule["source"],
                "schedule_id": schedule_id,
                "printers_upserted": result["printers_upserted"],
                "resins_upserted": result["resins_upserted"],
                "profiles_upserted": result["profiles_upserted"],
            }
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
    return RedirectResponse(url="/app")


@app.get("/app", response_class=HTMLResponse, include_in_schema=False)
def app_ui() -> HTMLResponse:
    if not APP_UI_PATH.exists():
        raise HTTPException(status_code=404, detail="App UI not found.")
    return HTMLResponse(APP_UI_PATH.read_text(encoding="utf-8"))


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
        "storage_path": str(CATALOG_PATH),
        "printers": len(catalog.get("printers", [])),
        "resins": len(catalog.get("resins", [])),
        "profiles": len(catalog.get("profiles", [])),
    }


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
        )
    except Exception as exc:
        _audit(auth=auth, action="sync.technical", status="failed", details={"error": str(exc)})
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
    db = load_resin_database()
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


@app.post("/phase1/analyze")
async def phase1_analyze(
    file: UploadFile = File(...),
    slice_height_mm: float = Form(0.01),
) -> dict:
    temp_file = _save_upload(file)
    try:
        analysis = pipeline.run_phase_1_geometry(
            file_path=str(temp_file),
            slice_height_mm=slice_height_mm,
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
        persisted_count = save_feedback_records(records)
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
        persisted_count = save_feedback_records(records)
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
        persisted_count = save_feedback_records(records)
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
    path = init_feedback_store()
    return {"status": "ok", "storage_path": str(path)}


@app.post("/phase3/history/query", response_model=FeedbackHistoryQueryResult)
def phase3_history_query(request: FeedbackHistoryQueryRequest) -> FeedbackHistoryQueryResult:
    try:
        records = query_feedback_records(
            printer=request.printer,
            resin_type=request.resin_type,
            source=request.source,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
            offset=request.offset,
        )
        total = count_feedback_records(
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
            printer=request.printer,
            resin_type=request.resin_type,
            source=request.source,
            date_from=request.date_from,
            date_to=request.date_to,
            limit=request.limit,
            offset=0,
        )
        total = count_feedback_records(
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
    auth: AuthContext = Depends(require_role("operator")),
) -> PipelineResponse:
    temp_file = _save_upload(file)
    try:
        result = pipeline.run_full_pipeline(
            file_path=str(temp_file),
            resin_type=resin_type,
            use_case=use_case,
            printer=printer,
            ambient_temp_c=ambient_temp_c,
            film_releases=film_releases,
            slice_height_mm=slice_height_mm,
            catalog_db_path=CATALOG_PATH,
        )
        _audit(
            auth=auth,
            action="pipeline.run",
            resource_type="pipeline",
            details={"printer": printer, "resin_type": resin_type, "use_case": use_case},
        )
        return result
    except Exception as exc:
        _audit(
            auth=auth,
            action="pipeline.run",
            resource_type="pipeline",
            status="failed",
            details={"error": str(exc), "printer": printer, "resin_type": resin_type},
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
    auth: AuthContext = Depends(require_role("operator")),
) -> PipelineAsyncSubmitResponse:
    temp_file = _save_upload(file)

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
                catalog_db_path=CATALOG_PATH,
            )
            return output.model_dump()
        finally:
            _safe_unlink(temp_file)

    job = JOB_QUEUE.submit(
        job_type="pipeline",
        actor=auth.actor,
        metadata={"resin_type": resin_type, "use_case": use_case, "printer": printer},
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
        tmp.write(file.file.read())
        return Path(tmp.name)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
