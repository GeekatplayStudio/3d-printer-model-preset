from app.feedback import summarize_feedback
from app.models import FeedbackRecord


def test_feedback_summary_flags_blooming():
    records = [
        FeedbackRecord(
            source="forum",
            printer="Elegoo Mars 5 Ultra",
            resin_type="Elegoo ABS-Like v3.0",
            text="Got blooming at 18um when exposure was too high.",
            layer_height_mm=0.02,
            exposure_s=2.0,
            ambient_temp_c=23.0,
            sponsored=False,
        ),
        FeedbackRecord(
            source="video",
            printer="Elegoo Mars 5 Ultra",
            resin_type="Elegoo ABS-Like v3.0",
            text="Sponsored video, best resin ever, perfect print.",
            sponsored=True,
        ),
    ]
    summary = summarize_feedback(
        records=records,
        printer="Elegoo Mars 5 Ultra",
        resin_type="Elegoo ABS-Like v3.0",
    )
    assert summary.blooming_mentions == 1
    assert summary.record_count == 2
    assert summary.marketing_ratio > 0

