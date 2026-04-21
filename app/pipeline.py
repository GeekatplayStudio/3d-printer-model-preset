from __future__ import annotations

from pathlib import Path

from app.camera_log import analyze_camera_log
from app.chitubox import render_chitubox_cfg
from app.feedback import summarize_feedback
from app.feedback_adaptation import adapt_settings_from_feedback
from app.feedback_sources import ingest_community_sheet_text, ingest_youtube_transcripts
from app.geometry import analyze_geometry
from app.logic import PRINTER_NAME, get_optimal_settings
from app.models import (
    AnalysisLevel,
    FeedbackIngestionResult,
    FeedbackRecord,
    FeedbackSummary,
    GeometryAnalysis,
    OptimalSettings,
    PipelineResponse,
    UseCase,
    CameraLogAnalysisResult,
)


class ModularAgenticPipeline:
    def run_phase_1_geometry(
        self,
        file_path: str,
        slice_height_mm: float = 0.01,
        auto_repair: bool = True,
        analysis_level: AnalysisLevel = "balanced",
    ) -> GeometryAnalysis:
        return analyze_geometry(
            file_path=file_path,
            slice_height_mm=slice_height_mm,
            auto_repair=auto_repair,
            analysis_level=analysis_level,
        )

    def run_phase_2_parameters(
        self,
        analysis: GeometryAnalysis,
        resin_type: str,
        use_case: UseCase,
        printer: str = PRINTER_NAME,
        ambient_temp_c: float | None = None,
        film_releases: int = 0,
        catalog_db_path: str | Path | None = None,
    ) -> OptimalSettings:
        return get_optimal_settings(
            analysis_data=analysis,
            resin_type=resin_type,
            use_case=use_case,
            printer=printer,
            ambient_temp_c=ambient_temp_c,
            film_releases=film_releases,
            catalog_db_path=catalog_db_path,
        )

    def run_phase_2_history_aware(
        self,
        analysis: GeometryAnalysis,
        resin_type: str,
        use_case: UseCase,
        history_summary: FeedbackSummary,
        printer: str = PRINTER_NAME,
        ambient_temp_c: float | None = None,
        film_releases: int = 0,
        min_history_records: int = 3,
        catalog_db_path: str | Path | None = None,
    ) -> tuple[OptimalSettings, list[str]]:
        base = self.run_phase_2_parameters(
            analysis=analysis,
            resin_type=resin_type,
            use_case=use_case,
            printer=printer,
            ambient_temp_c=ambient_temp_c,
            film_releases=film_releases,
            catalog_db_path=catalog_db_path,
        )
        adapted = adapt_settings_from_feedback(
            settings=base,
            summary=history_summary,
            min_history_records=min_history_records,
        )
        return adapted.settings, adapted.adjustments

    def run_phase_3_feedback(
        self,
        records: list[FeedbackRecord],
        printer: str,
        resin_type: str,
    ) -> FeedbackSummary:
        return summarize_feedback(records=records, printer=printer, resin_type=resin_type)

    def run_phase_3_community_sheet(
        self,
        text: str,
        printer: str,
        resin_type: str,
        source: str = "community_sheet_text",
    ) -> FeedbackIngestionResult:
        records, notes = ingest_community_sheet_text(
            text=text,
            default_printer=printer,
            default_resin=resin_type,
            source=source,
        )
        summary = self.run_phase_3_feedback(records=records, printer=printer, resin_type=resin_type)
        return FeedbackIngestionResult(
            imported_count=len(records),
            summary=summary,
            records=records,
            notes=notes,
        )

    def run_phase_3_youtube(
        self,
        video_urls: list[str],
        printer: str,
        resin_type: str,
        languages: list[str] | None = None,
    ) -> FeedbackIngestionResult:
        records, notes = ingest_youtube_transcripts(
            video_urls=video_urls,
            printer=printer,
            resin_type=resin_type,
            languages=languages,
        )
        summary = self.run_phase_3_feedback(records=records, printer=printer, resin_type=resin_type)
        return FeedbackIngestionResult(
            imported_count=len(records),
            summary=summary,
            records=records,
            notes=notes,
        )

    def run_phase_3_camera_log(self, log_text: str) -> CameraLogAnalysisResult:
        return analyze_camera_log(log_text)

    def run_full_pipeline(
        self,
        file_path: str,
        resin_type: str,
        use_case: UseCase,
        printer: str = PRINTER_NAME,
        ambient_temp_c: float | None = None,
        film_releases: int = 0,
        slice_height_mm: float = 0.01,
        auto_repair: bool = True,
        analysis_level: AnalysisLevel = "balanced",
        catalog_db_path: str | Path | None = None,
    ) -> PipelineResponse:
        analysis = self.run_phase_1_geometry(
            file_path=file_path,
            slice_height_mm=slice_height_mm,
            auto_repair=auto_repair,
            analysis_level=analysis_level,
        )
        settings = self.run_phase_2_parameters(
            analysis=analysis,
            resin_type=resin_type,
            use_case=use_case,
            printer=printer,
            ambient_temp_c=ambient_temp_c,
            film_releases=film_releases,
            catalog_db_path=catalog_db_path,
        )
        cfg_text = render_chitubox_cfg(settings)
        return PipelineResponse(analysis=analysis, settings=settings, chitubox_cfg=cfg_text)
