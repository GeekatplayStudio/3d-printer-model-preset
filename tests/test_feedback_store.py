from datetime import date

from app.feedback_store import count_feedback_records, query_feedback_records, save_feedback_records
from app.models import FeedbackRecord


def test_feedback_store_insert_and_query(tmp_path):
    db_path = tmp_path / "history.db"
    records = [
        FeedbackRecord(
            source="sheet",
            printer="Elegoo Mars 5 Ultra",
            resin_type="Elegoo ABS-Like v3.0",
            text="Blooming on thin details",
            layer_height_mm=0.02,
            exposure_s=1.9,
            sponsored=False,
            created_at=date(2026, 2, 1),
        ),
        FeedbackRecord(
            source="youtube:abc123XYZ09",
            printer="Elegoo Mars 5 Ultra",
            resin_type="Elegoo ABS-Like v3.0",
            text="No issue after support tweak",
            layer_height_mm=0.02,
            exposure_s=1.8,
            sponsored=True,
            created_at=date(2026, 2, 3),
        ),
    ]

    inserted = save_feedback_records(records, db_path=db_path)
    assert inserted == 2

    queried = query_feedback_records(
        db_path=db_path,
        printer="Elegoo Mars 5 Ultra",
        resin_type="Elegoo ABS-Like v3.0",
        limit=10,
    )
    assert len(queried) == 2

    filtered = query_feedback_records(
        db_path=db_path,
        printer="Elegoo Mars 5 Ultra",
        resin_type="Elegoo ABS-Like v3.0",
        date_from=date(2026, 2, 2),
        limit=10,
    )
    assert len(filtered) == 1
    assert filtered[0].source.startswith("youtube:")

    total = count_feedback_records(
        db_path=db_path,
        printer="Elegoo Mars 5 Ultra",
        resin_type="Elegoo ABS-Like v3.0",
    )
    assert total == 2
