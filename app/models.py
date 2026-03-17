from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

UseCase = Literal["miniature", "collectible", "heavy_use"]


class SliceArea(BaseModel):
    z_mm: float
    area_mm2: float


class Cavity(BaseModel):
    id: int
    volume_mm3: float
    centroid_mm: list[float] | None = None


class Island(BaseModel):
    layer_index: int
    z_mm: float
    voxel_count: int


class GeometryAnalysis(BaseModel):
    file_name: str
    mesh_volume_mm3: float
    surface_area_mm2: float
    surface_area_ratio: float
    triangle_count: int
    detail_density: float
    curvature_proxy: float | None = None
    slice_height_mm: float
    build_plate_area_mm2: float
    max_cross_section_mm2: float
    max_cross_section_ratio: float
    slice_areas: list[SliceArea]
    suction_cups: list[Cavity]
    islands: list[Island]
    estimated_intent: UseCase | None = None
    intent_reasons: list[str] = Field(default_factory=list)
    structural_risk_score: float = Field(ge=0.0, le=100.0)
    notes: list[str] = Field(default_factory=list)


class MultiParameterSettings(BaseModel):
    model_exposure_s: float
    support_exposure_s: float
    delicate_feature_exposure_s: float


class OptimalSettings(BaseModel):
    printer: str = "Elegoo Mars 5 Ultra"
    resin_type: str
    use_case: UseCase
    intent_used: UseCase | None = None
    layer_height_mm: float
    exposure_s: float
    bottom_exposure_s: float
    tilt_speed_mm_min: float
    tilt_speed_mm_h: float | None = None
    tilt_angle_deg: float
    rest_time_before_print_s: float = 2.0
    rest_time_after_retract_s: float = 0.5
    transition_layers: int = 6
    scale_compensation_percent: float = 100.0
    heater_required: bool = False
    anti_aliasing: int | None = None
    grayscale_level: int | None = None
    xy_resolution_um: int | None = None
    detail_tier: str
    multi_parameter: MultiParameterSettings
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    source_profile: str | None = None


class OptimizeRequest(BaseModel):
    analysis: GeometryAnalysis
    resin_type: str
    use_case: UseCase
    printer: str = "Elegoo Mars 5 Ultra"
    ambient_temp_c: float | None = None
    film_releases: int = 0


class HistoryAwareOptimizeRequest(BaseModel):
    analysis: GeometryAnalysis
    resin_type: str
    use_case: UseCase
    printer: str = "Elegoo Mars 5 Ultra"
    source: str | None = None
    ambient_temp_c: float | None = None
    film_releases: int = 0
    date_from: date | None = None
    date_to: date | None = None
    history_limit: int = Field(default=500, ge=1, le=1000)
    min_history_records: int = Field(default=3, ge=1, le=100)


class HistoryAwareOptimizeResponse(BaseModel):
    settings: OptimalSettings
    history_record_count: int
    history_summary: FeedbackSummary
    adjustments: list[str] = Field(default_factory=list)


class PipelineResponse(BaseModel):
    analysis: GeometryAnalysis
    settings: OptimalSettings
    chitubox_cfg: str


class FeedbackRecord(BaseModel):
    source: str
    printer: str
    resin_type: str
    text: str
    layer_height_mm: float | None = None
    exposure_s: float | None = None
    ambient_temp_c: float | None = None
    sponsored: bool | None = None
    created_at: date | None = None


class FeedbackSummary(BaseModel):
    record_count: int
    blooming_mentions: int
    delamination_mentions: int
    real_world_ratio: float
    marketing_ratio: float
    recommendation: str
    notes: list[str] = Field(default_factory=list)


class FeedbackBatchRequest(BaseModel):
    printer: str
    resin_type: str
    records: list[FeedbackRecord]


class CommunitySheetURLRequest(BaseModel):
    url: str
    printer: str
    resin_type: str
    source: str = "community_sheet_url"


class YouTubeIngestRequest(BaseModel):
    video_urls: list[str]
    printer: str
    resin_type: str
    languages: list[str] = Field(default_factory=lambda: ["en"])


class FeedbackIngestionResult(BaseModel):
    imported_count: int
    summary: FeedbackSummary
    records: list[FeedbackRecord]
    persisted_count: int = 0
    storage_path: str | None = None
    notes: list[str] = Field(default_factory=list)


class FeedbackHistoryQueryRequest(BaseModel):
    printer: str | None = None
    resin_type: str | None = None
    source: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class FeedbackHistoryQueryResult(BaseModel):
    total_count: int
    records: list[FeedbackRecord]


class FeedbackHistorySummaryRequest(BaseModel):
    printer: str
    resin_type: str
    source: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(default=500, ge=1, le=1000)


class FeedbackHistorySummaryResult(BaseModel):
    total_count: int
    records_used: int
    summary: FeedbackSummary


class CameraLogAdjustment(BaseModel):
    parameter: str
    recommended_value: str
    reason: str


class CameraLogAnalysisResult(BaseModel):
    matched_events: list[str] = Field(default_factory=list)
    adjustments: list[CameraLogAdjustment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CatalogPrinterBase(BaseModel):
    name: str
    brand: str | None = None
    model: str | None = None
    technology: str | None = None
    xy_resolution_um: int | None = None
    build_volume_x_mm: float | None = None
    build_volume_y_mm: float | None = None
    build_volume_z_mm: float | None = None
    tilt_release_supported: bool | None = None
    wifi_supported: bool | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogPrinterCreate(CatalogPrinterBase):
    pass


class CatalogPrinterUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    technology: str | None = None
    xy_resolution_um: int | None = None
    build_volume_x_mm: float | None = None
    build_volume_y_mm: float | None = None
    build_volume_z_mm: float | None = None
    tilt_release_supported: bool | None = None
    wifi_supported: bool | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class CatalogPrinter(CatalogPrinterBase):
    id: int


class CatalogResinBase(BaseModel):
    name: str
    brand: str | None = None
    series: str | None = None
    technical_goal: str | None = None
    viscosity_cp: float | None = None
    shore_hardness: str | None = None
    shrinkage_percent: float | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogResinCreate(CatalogResinBase):
    pass


class CatalogResinUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    series: str | None = None
    technical_goal: str | None = None
    viscosity_cp: float | None = None
    shore_hardness: str | None = None
    shrinkage_percent: float | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class CatalogResin(CatalogResinBase):
    id: int


class CatalogProfileBase(BaseModel):
    printer_id: int | None = None
    resin_id: int | None = None
    printer_name: str | None = None
    resin_name: str | None = None
    profile_name: str
    layer_height_mm: float | None = None
    exposure_s: float | None = None
    bottom_exposure_s: float | None = None
    transition_layers: int | None = None
    tilt_speed_reference_mm_h: float | None = None
    rest_time_before_print_s: float | None = None
    rest_time_after_retract_s: float | None = None
    is_default: bool = False
    is_active: bool = True
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogProfileCreate(CatalogProfileBase):
    pass


class CatalogProfileUpdate(BaseModel):
    printer_id: int | None = None
    resin_id: int | None = None
    profile_name: str | None = None
    layer_height_mm: float | None = None
    exposure_s: float | None = None
    bottom_exposure_s: float | None = None
    transition_layers: int | None = None
    tilt_speed_reference_mm_h: float | None = None
    rest_time_before_print_s: float | None = None
    rest_time_after_retract_s: float | None = None
    is_default: bool | None = None
    is_active: bool | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class CatalogProfile(CatalogProfileBase):
    id: int
    printer_name: str
    resin_name: str


class CatalogImportPayload(BaseModel):
    printers: list[CatalogPrinterCreate] = Field(default_factory=list)
    resins: list[CatalogResinCreate] = Field(default_factory=list)
    profiles: list[CatalogProfileCreate] = Field(default_factory=list)
    replace_existing: bool = False


class CatalogImportResult(BaseModel):
    printers_upserted: int
    resins_upserted: int
    profiles_upserted: int
    notes: list[str] = Field(default_factory=list)


class CatalogCsvImportResult(BaseModel):
    entity: Literal["printers", "resins", "profiles"]
    rows_processed: int
    upserted: int
    failed: int
    notes: list[str] = Field(default_factory=list)


class AuthWhoAmI(BaseModel):
    auth_enforced: bool
    actor: str
    role: str
    api_key_present: bool


class AuditEvent(BaseModel):
    id: int
    created_at: str
    actor: str
    role: str
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    status: str
    details: dict[str, Any] = Field(default_factory=dict)


class CatalogVersion(BaseModel):
    id: int
    created_at: str
    actor: str
    note: str | None = None
    source_action: str | None = None


class CatalogVersionDetail(CatalogVersion):
    snapshot: dict[str, Any] = Field(default_factory=dict)


class CatalogVersionCreateRequest(BaseModel):
    note: str | None = None


class CatalogVersionRestoreRequest(BaseModel):
    version_id: int


class AsyncJob(BaseModel):
    id: str
    type: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    actor: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None


class PipelineAsyncSubmitResponse(BaseModel):
    job: AsyncJob


class JobCancelResponse(BaseModel):
    cancelled: bool
    job: AsyncJob


class JobCleanupRequest(BaseModel):
    max_age_days: int = Field(default=30, ge=0, le=3650)
    keep_latest: int = Field(default=1000, ge=0, le=50000)


class JobCleanupResponse(BaseModel):
    deleted: int
    remaining: int


class TechnicalSyncRequest(BaseModel):
    source: str = "manual"
    replace_existing: bool = False
    payload: dict[str, Any]


class TechnicalSyncResponse(BaseModel):
    source: str
    printers_upserted: int
    resins_upserted: int
    profiles_upserted: int


class MetricsResponse(BaseModel):
    uptime_s: float
    requests_total: int
    status_counts: dict[str, int]
    top_paths: list[dict[str, Any]]
    latency_ms_avg: float
    latency_ms_p95: float
    latency_samples: int


class SyncScheduleBase(BaseModel):
    name: str
    source: str
    interval_seconds: int = Field(ge=1, le=604800)
    enabled: bool = True
    replace_existing: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncScheduleCreate(SyncScheduleBase):
    pass


class SyncScheduleUpdate(BaseModel):
    name: str | None = None
    source: str | None = None
    interval_seconds: int | None = Field(default=None, ge=1, le=604800)
    enabled: bool | None = None
    replace_existing: bool | None = None
    payload: dict[str, Any] | None = None


class SyncSchedule(SyncScheduleBase):
    id: int
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str


class SyncSchedulerTickResult(BaseModel):
    due: int
    submitted: int
    failed: int


class SyncSchedulerHealth(BaseModel):
    running: bool
    poll_seconds: float
    schedules_total: int
    schedules_enabled: int
