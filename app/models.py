from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

UseCase = Literal["miniature", "collectible", "heavy_use"]
AnalysisLevel = Literal["minimum", "balanced", "deep", "extreme"]
CavityConfidenceLevel = Literal["low", "medium", "high"]
RetopologyBackend = Literal["blender"]
RetopologyMode = Literal["quad", "voxel"]


class SliceArea(BaseModel):
    z_mm: float
    area_mm2: float


class Cavity(BaseModel):
    id: int
    volume_mm3: float
    centroid_mm: list[float] | None = None
    xy_footprint_mm2: float | None = Field(default=None, ge=0.0)
    z_span_mm: float | None = Field(default=None, ge=0.0)
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_level: CavityConfidenceLevel | None = None


class Island(BaseModel):
    layer_index: int
    z_mm: float
    voxel_count: int
    end_layer_index: int | None = None
    z_end_mm: float | None = None
    layer_span: int = Field(default=1, ge=1)
    total_voxel_count: int | None = Field(default=None, ge=0)
    xy_centroid_mm: list[float] | None = None


class MeshHealthReport(BaseModel):
    watertight: bool
    winding_consistent: bool
    volume_consistent: bool
    connected_components: int | None = Field(default=None, ge=0)
    boundary_edge_count: int | None = Field(default=None, ge=0)
    non_manifold_edge_count: int | None = Field(default=None, ge=0)
    degenerate_face_count: int = Field(ge=0)
    duplicate_face_count: int = Field(ge=0)
    repaired: bool = False
    issues: list[str] = Field(default_factory=list)
    repair_actions: list[str] = Field(default_factory=list)


class MeshRepairOutcome(BaseModel):
    repaired: bool = False
    fully_repaired: bool = False
    before_fix: MeshHealthReport
    after_fix: MeshHealthReport
    resolved_issues: list[str] = Field(default_factory=list)
    remaining_issues: list[str] = Field(default_factory=list)
    repair_actions: list[str] = Field(default_factory=list)


class GeometryAnalysis(BaseModel):
    file_name: str
    mesh_volume_mm3: float
    surface_area_mm2: float
    surface_area_ratio: float
    triangle_count: int
    vertex_count: int | None = Field(default=None, ge=0)
    detail_density: float
    curvature_proxy: float | None = None
    slice_height_mm: float
    build_plate_area_mm2: float
    max_cross_section_mm2: float
    max_cross_section_ratio: float
    bounding_box_mm: list[float] = Field(default_factory=list)
    bounding_box_diagonal_mm: float | None = Field(default=None, ge=0.0)
    center_of_mass_mm: list[float] | None = None
    euler_number: int | None = None
    slice_areas: list[SliceArea]
    suction_cups: list[Cavity]
    islands: list[Island]
    mesh_health: MeshHealthReport | None = None
    estimated_intent: UseCase | None = None
    intent_reasons: list[str] = Field(default_factory=list)
    structural_risk_score: float = Field(ge=0.0, le=100.0)
    notes: list[str] = Field(default_factory=list)
    analysis_level: AnalysisLevel = "balanced"
    performance_ms: dict[str, float] = Field(default_factory=dict)


class MultiParameterSettings(BaseModel):
    model_exposure_s: float
    support_exposure_s: float
    delicate_feature_exposure_s: float


SettingsDataQuality = Literal["verified_sources", "catalog_unverified", "fallback_defaults"]
WizardAnalysisProgressStatus = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled", "unknown"]


class SettingReference(BaseModel):
    title: str
    source_type: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    applies_to: list[str] = Field(default_factory=list)


class SettingsProvenance(BaseModel):
    data_quality: SettingsDataQuality = "fallback_defaults"
    real_data_backed: bool = False
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    source_count: int = Field(default=0, ge=0)
    references: list[SettingReference] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
    provenance: SettingsProvenance = Field(default_factory=SettingsProvenance)


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
    curation: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class GitHubTechnicalSyncRequest(BaseModel):
    owner: str
    repo: str
    path: str
    ref: str = "main"
    source: str = "github_repo"
    replace_existing: bool = False


class GitHubTechnicalSyncResponse(TechnicalSyncResponse):
    owner: str
    repo: str
    path: str
    ref: str
    raw_url: str


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


class WizardDatabaseSetupRequest(BaseModel):
    mode: Literal["official_local", "github"] = "official_local"
    replace_existing: bool = False
    owner: str | None = None
    repo: str | None = None
    path: str | None = None
    ref: str = "main"
    auto_update: bool = False
    auto_update_interval_seconds: int = Field(default=86400, ge=300, le=604800)
    source: str = "wizard_setup"


class WizardDatabaseSetupResponse(BaseModel):
    mode: Literal["official_local", "github"]
    source: str
    raw_url: str | None = None
    printers_upserted: int
    resins_upserted: int
    profiles_upserted: int
    curation: dict[str, Any] = Field(default_factory=dict)
    auto_update_schedule_id: int | None = None
    notes: list[str] = Field(default_factory=list)


class WizardDatabaseStatus(BaseModel):
    mode: str
    data_dir: str
    storage_path: str
    official_dataset_file: str
    official_dataset_updated_at: str | None = None
    printers_total: int
    printers_with_sources: int
    resins_total: int
    resins_with_sources: int
    profiles_total: int
    profiles_with_sources: int
    profiles_with_confidence: int
    manufacturers: list[str] = Field(default_factory=list)
    completeness_percent: float = Field(ge=0.0, le=100.0)
    ready_for_model_analysis: bool
    ready_for_settings: bool
    schedules_total: int
    schedules_enabled: int


class WizardDatabaseGapSummary(BaseModel):
    missing_printer_sources_count: int
    missing_resin_sources_count: int
    missing_profile_sources_count: int
    printers_without_profiles_count: int
    resins_without_profiles_count: int
    missing_printer_sources: list[str] = Field(default_factory=list)
    missing_resin_sources: list[str] = Field(default_factory=list)
    missing_profile_sources: list[str] = Field(default_factory=list)
    printers_without_profiles: list[str] = Field(default_factory=list)
    resins_without_profiles: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class WizardUpdateScheduleStatus(BaseModel):
    id: int
    name: str
    source: str
    interval_seconds: int
    enabled: bool
    replace_existing: bool
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    github_path: str | None = None
    github_ref: str | None = None


class WizardUpdateStatusResponse(BaseModel):
    scheduler_running: bool
    scheduler_poll_seconds: float
    schedules_total: int
    schedules_enabled: int
    wizard_auto_update_present: bool
    schedules: list[WizardUpdateScheduleStatus] = Field(default_factory=list)


class WizardRunUpdateNowRequest(BaseModel):
    schedule_id: int | None = None


class WizardCatalogOptionsResponse(BaseModel):
    printers: list[str] = Field(default_factory=list)
    resins: list[str] = Field(default_factory=list)
    compatibility: dict[str, list[str]] = Field(default_factory=dict)


class WizardAnalysisProgressResponse(BaseModel):
    job_id: str
    status: WizardAnalysisProgressStatus
    stage: str | None = None
    message: str
    elapsed_seconds: int = Field(default=0, ge=0)
    stage_elapsed_seconds: int = Field(default=0, ge=0)
    updated_at: str
    cancel_requested: bool = False
    error: str | None = None
    performance_ms: dict[str, float] = Field(default_factory=dict)
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)


class WizardModelCheckResponse(BaseModel):
    analysis: GeometryAnalysis
    has_issues: bool
    requires_fix: bool
    fix_reasons: list[str] = Field(default_factory=list)


class WizardModelFixResponse(BaseModel):
    analysis: GeometryAnalysis
    repaired: bool
    fully_repaired: bool
    repair_actions: list[str] = Field(default_factory=list)
    resolved_issues: list[str] = Field(default_factory=list)
    remaining_issues: list[str] = Field(default_factory=list)
    before_fix: MeshHealthReport
    after_fix: MeshHealthReport
    download_id: str
    download_url: str
    output_file_name: str


class WizardModelRetopologyResponse(BaseModel):
    source_analysis: GeometryAnalysis
    retopology_analysis: GeometryAnalysis
    mode: RetopologyMode
    backend_requested: RetopologyBackend
    backend_used: RetopologyBackend
    target_faces: int | None = Field(default=None, ge=1)
    voxel_size_mm: float | None = Field(default=None, gt=0.0)
    preserve_sharp: bool = True
    preserve_boundary: bool = True
    preprocessing_fix: MeshRepairOutcome | None = None
    notes: list[str] = Field(default_factory=list)
    download_id: str
    download_url: str
    output_file_name: str


class WizardSettingsRequest(BaseModel):
    analysis: GeometryAnalysis
    resin_type: str
    printer: str
    use_case: UseCase = "miniature"
    ambient_temp_c: float | None = None
    film_releases: int = 0


class WizardSettingsResponse(BaseModel):
    settings: OptimalSettings
    chitubox_cfg: str
    settings_download_id: str
    settings_download_url: str
    cfg_download_id: str
    cfg_download_url: str
