from app.feedback_adaptation import adapt_settings_from_feedback
from app.models import FeedbackSummary, MultiParameterSettings, OptimalSettings


def _settings_stub(exposure_s: float = 1.8, bottom_exposure_s: float = 28.0) -> OptimalSettings:
    return OptimalSettings(
        printer="Elegoo Mars 5 Ultra",
        resin_type="Elegoo ABS-Like v3.0",
        use_case="miniature",
        layer_height_mm=0.02,
        exposure_s=exposure_s,
        bottom_exposure_s=bottom_exposure_s,
        tilt_speed_mm_min=120.0,
        tilt_angle_deg=-1.0,
        xy_resolution_um=18,
        detail_tier="high_detail",
        multi_parameter=MultiParameterSettings(
            model_exposure_s=exposure_s - 0.15,
            support_exposure_s=exposure_s + 0.25,
            delicate_feature_exposure_s=exposure_s - 0.2,
        ),
        warnings=[],
        recommendations=[],
        source_profile="stub",
    )


def test_adaptation_increases_exposure_for_delamination_trend():
    base = _settings_stub(exposure_s=1.75)
    summary = FeedbackSummary(
        record_count=10,
        blooming_mentions=1,
        delamination_mentions=4,
        real_world_ratio=0.8,
        marketing_ratio=0.1,
        recommendation="Potential instability detected",
        notes=[],
    )
    result = adapt_settings_from_feedback(base, summary, min_history_records=3)
    assert result.settings.exposure_s > base.exposure_s
    assert result.settings.bottom_exposure_s >= base.bottom_exposure_s
    assert any("increased" in msg for msg in result.adjustments)


def test_adaptation_decreases_exposure_for_blooming_trend():
    base = _settings_stub(exposure_s=1.95)
    summary = FeedbackSummary(
        record_count=12,
        blooming_mentions=6,
        delamination_mentions=1,
        real_world_ratio=0.9,
        marketing_ratio=0.05,
        recommendation="Potential instability detected",
        notes=[],
    )
    result = adapt_settings_from_feedback(base, summary, min_history_records=3)
    assert result.settings.exposure_s < base.exposure_s
    assert any("decreased" in msg for msg in result.adjustments)


def test_adaptation_skips_when_history_is_too_small():
    base = _settings_stub(exposure_s=1.85)
    summary = FeedbackSummary(
        record_count=2,
        blooming_mentions=1,
        delamination_mentions=1,
        real_world_ratio=1.0,
        marketing_ratio=0.0,
        recommendation="n/a",
        notes=[],
    )
    result = adapt_settings_from_feedback(base, summary, min_history_records=3)
    assert result.settings.exposure_s == base.exposure_s
    assert any("skipped" in msg.lower() for msg in result.adjustments)
