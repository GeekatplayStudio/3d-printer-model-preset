from __future__ import annotations

import csv
import json
from pathlib import Path

from app.models import FeedbackRecord, FeedbackSummary

_BLOOMING_KEYWORDS = ("blooming", "bloom", "light bleed", "overcure")
_DELAMINATION_KEYWORDS = ("delamination", "split layer", "layer separation")
_MARKETING_KEYWORDS = ("sponsored", "affiliate", "coupon", "perfect print", "best resin ever")


def load_feedback_records(path: str | Path) -> list[FeedbackRecord]:
    file_path = Path(path)
    if file_path.suffix.lower() == ".json":
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        return [FeedbackRecord.model_validate(item) for item in raw]

    if file_path.suffix.lower() == ".csv":
        with file_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            return [FeedbackRecord.model_validate(row) for row in reader]

    raise ValueError("Unsupported feedback file format. Use JSON or CSV.")


def summarize_feedback(
    records: list[FeedbackRecord],
    printer: str,
    resin_type: str,
) -> FeedbackSummary:
    printer_key = printer.lower().strip()
    resin_key = resin_type.lower().strip()

    filtered = [
        r
        for r in records
        if r.printer.lower().strip() == printer_key and r.resin_type.lower().strip() == resin_key
    ]

    if not filtered:
        return FeedbackSummary(
            record_count=0,
            blooming_mentions=0,
            delamination_mentions=0,
            real_world_ratio=0.0,
            marketing_ratio=0.0,
            recommendation="No matching feedback records.",
            notes=["Provide data from Liqcreate/Elegoo community sheets or user logs."],
        )

    blooming = 0
    delamination = 0
    marketing_hits = 0
    real_world_hits = 0

    for record in filtered:
        text = record.text.lower()
        if any(k in text for k in _BLOOMING_KEYWORDS):
            blooming += 1
        if any(k in text for k in _DELAMINATION_KEYWORDS):
            delamination += 1
        if _is_marketing_like(record):
            marketing_hits += 1
        if _is_real_world_like(record):
            real_world_hits += 1

    count = len(filtered)
    real_world_ratio = round(real_world_hits / count, 3)
    marketing_ratio = round(marketing_hits / count, 3)

    recommendation = "Stable community signal."
    if blooming >= 2 or delamination >= 2:
        recommendation = "Potential instability detected; raise exposure and review support strategy."
    if marketing_ratio > 0.5 and real_world_ratio < 0.35:
        recommendation = "Data appears marketing-heavy; trust only parameter-backed reports."

    notes: list[str] = []
    if blooming:
        notes.append("Blooming reports found for this resin/printer combination.")
    if delamination:
        notes.append("Delamination reports found for this resin/printer combination.")
    if marketing_ratio > real_world_ratio:
        notes.append("Marketing-like entries outnumber practical test logs.")

    return FeedbackSummary(
        record_count=count,
        blooming_mentions=blooming,
        delamination_mentions=delamination,
        real_world_ratio=real_world_ratio,
        marketing_ratio=marketing_ratio,
        recommendation=recommendation,
        notes=notes,
    )


def _is_marketing_like(record: FeedbackRecord) -> bool:
    text = record.text.lower()
    keyword_hit = any(k in text for k in _MARKETING_KEYWORDS)
    no_parameters = record.exposure_s is None and record.layer_height_mm is None
    return bool(record.sponsored) or (keyword_hit and no_parameters)


def _is_real_world_like(record: FeedbackRecord) -> bool:
    has_parameters = record.exposure_s is not None or record.layer_height_mm is not None
    text = record.text.lower()
    has_failure_context = "failed" in text or "issue" in text or "retry" in text
    has_environment = record.ambient_temp_c is not None or "temp" in text or "c" in text
    return has_parameters and (has_failure_context or has_environment or not record.sponsored)

