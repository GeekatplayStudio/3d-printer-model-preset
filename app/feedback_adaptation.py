from __future__ import annotations

from dataclasses import dataclass

from app.models import FeedbackSummary, OptimalSettings


@dataclass
class AdaptationResult:
    settings: OptimalSettings
    adjustments: list[str]


def adapt_settings_from_feedback(
    settings: OptimalSettings,
    summary: FeedbackSummary,
    min_history_records: int = 3,
) -> AdaptationResult:
    adjustments: list[str] = []
    record_count = max(0, summary.record_count)
    if record_count < min_history_records:
        adjustments.append(
            f"History-aware mode skipped: only {record_count} records (min {min_history_records})."
        )
        return AdaptationResult(settings=settings, adjustments=adjustments)

    bloom_rate = summary.blooming_mentions / record_count
    delam_rate = summary.delamination_mentions / record_count
    confidence = _confidence_weight(summary)

    exposure_delta = _exposure_delta_s(bloom_rate=bloom_rate, delam_rate=delam_rate, confidence=confidence)
    bottom_delta = _bottom_exposure_delta_s(bloom_rate=bloom_rate, delam_rate=delam_rate, confidence=confidence)

    tuned = settings.model_copy(deep=True)
    if exposure_delta != 0.0:
        tuned.exposure_s = round(max(0.5, tuned.exposure_s + exposure_delta), 3)
        tuned.multi_parameter.model_exposure_s = round(
            max(0.5, tuned.multi_parameter.model_exposure_s + exposure_delta),
            3,
        )
        tuned.multi_parameter.support_exposure_s = round(
            max(0.5, tuned.multi_parameter.support_exposure_s + exposure_delta),
            3,
        )
        tuned.multi_parameter.delicate_feature_exposure_s = round(
            max(0.5, tuned.multi_parameter.delicate_feature_exposure_s + exposure_delta),
            3,
        )
        direction = "increased" if exposure_delta > 0 else "decreased"
        adjustments.append(
            f"Normal exposure {direction} by {abs(exposure_delta):.3f}s from feedback trend "
            f"(delam={delam_rate:.2f}, bloom={bloom_rate:.2f}, confidence={confidence:.2f})."
        )

    if bottom_delta != 0.0:
        tuned.bottom_exposure_s = round(max(5.0, tuned.bottom_exposure_s + bottom_delta), 3)
        direction = "increased" if bottom_delta > 0 else "decreased"
        adjustments.append(
            f"Bottom exposure {direction} by {abs(bottom_delta):.3f}s using historical adhesion trend."
        )

    if delam_rate >= 0.2:
        old_speed = tuned.tilt_speed_mm_min
        tuned.tilt_speed_mm_min = round(max(35.0, tuned.tilt_speed_mm_min * (1.0 - 0.12 * confidence)), 2)
        if tuned.tilt_speed_mm_min != old_speed:
            adjustments.append(
                f"Tilt speed reduced from {old_speed} to {tuned.tilt_speed_mm_min} mm/min to lower peel stress."
            )

    if bloom_rate >= 0.25:
        tuned.warnings.append("History trend: blooming reported frequently for this resin profile.")
    if delam_rate >= 0.2:
        tuned.warnings.append("History trend: delamination reported; support and peel strategy should be reviewed.")
    if adjustments:
        tuned.recommendations.append("History-aware adaptation applied from stored community feedback.")

    return AdaptationResult(settings=tuned, adjustments=adjustments)


def _confidence_weight(summary: FeedbackSummary) -> float:
    signal = summary.real_world_ratio - (summary.marketing_ratio * 0.6)
    return max(0.25, min(1.0, signal + 0.35))


def _exposure_delta_s(bloom_rate: float, delam_rate: float, confidence: float) -> float:
    raw = 0.0
    if delam_rate >= 0.2:
        raw += 0.10
    if delam_rate >= 0.4:
        raw += 0.08
    if bloom_rate >= 0.2:
        raw -= 0.09
    if bloom_rate >= 0.4:
        raw -= 0.07
    adjusted = raw * confidence
    return round(max(-0.25, min(0.25, adjusted)), 3)


def _bottom_exposure_delta_s(bloom_rate: float, delam_rate: float, confidence: float) -> float:
    raw = 0.0
    if delam_rate >= 0.25:
        raw += 1.2
    if bloom_rate >= 0.3:
        raw -= 0.6
    adjusted = raw * confidence
    return round(max(-2.0, min(2.0, adjusted)), 3)

