from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from app.models import FeedbackRecord

_TEXT_KEYS = (
    "text",
    "notes",
    "comment",
    "comments",
    "result",
    "issue",
    "issues",
    "observations",
    "feedback",
)
_PRINTER_KEYS = ("printer", "printer_model", "machine")
_RESIN_KEYS = ("resin", "resin_type", "material")
_LAYER_KEYS = ("layer_height_mm", "layer_height", "layer", "layer_mm")
_EXPOSURE_KEYS = ("exposure_s", "exposure", "normal_exposure")
_TEMP_KEYS = ("ambient_temp_c", "temperature_c", "temp_c", "room_temp_c")
_SPONSORED_KEYS = ("sponsored", "affiliate", "ad")
_DATE_KEYS = ("date", "created_at", "timestamp", "posted_at")
_SPONSOR_WORDS = ("sponsored", "affiliate", "promo code", "coupon")


def ingest_community_sheet_url(
    url: str,
    default_printer: str,
    default_resin: str,
    source: str = "community_sheet_url",
    timeout_s: float = 20.0,
) -> tuple[list[FeedbackRecord], list[str]]:
    request = Request(url, headers={"User-Agent": "Mars5-Agentic/0.1"})
    with urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        charset = response.headers.get_content_charset() or "utf-8"
        body = response.read().decode(charset, errors="replace")
    records, notes = ingest_community_sheet_text(
        body,
        default_printer=default_printer,
        default_resin=default_resin,
        source=source,
    )
    notes.append(f"Loaded community sheet from URL: {url}")
    return records, notes


def ingest_community_sheet_bytes(
    content: bytes,
    default_printer: str,
    default_resin: str,
    source: str,
) -> tuple[list[FeedbackRecord], list[str]]:
    text = content.decode("utf-8-sig", errors="replace")
    return ingest_community_sheet_text(
        text,
        default_printer=default_printer,
        default_resin=default_resin,
        source=source,
    )


def ingest_community_sheet_text(
    text: str,
    default_printer: str,
    default_resin: str,
    source: str,
) -> tuple[list[FeedbackRecord], list[str]]:
    stripped = text.strip()
    if not stripped:
        return [], ["Sheet content was empty."]

    notes: list[str] = []
    rows: list[dict[str, str]]

    if stripped[0] in "[{":
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            values = parsed.get("rows", [])
            if not isinstance(values, list):
                raise ValueError("JSON sheet must be a list or {'rows': [...]} format.")
            rows = [dict(v) for v in values if isinstance(v, dict)]
        elif isinstance(parsed, list):
            rows = [dict(v) for v in parsed if isinstance(v, dict)]
        else:
            raise ValueError("JSON sheet must be a list of row objects.")
    else:
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader]

    records: list[FeedbackRecord] = []
    dropped = 0
    for row in rows:
        record = _row_to_feedback_record(
            row=row,
            default_printer=default_printer,
            default_resin=default_resin,
            source=source,
        )
        if record is None:
            dropped += 1
            continue
        records.append(record)

    notes.append(f"Parsed {len(rows)} rows from community sheet.")
    if dropped:
        notes.append(f"Dropped {dropped} rows without enough signal.")
    return records, notes


def ingest_youtube_transcripts(
    video_urls: list[str],
    printer: str,
    resin_type: str,
    languages: list[str] | None = None,
) -> tuple[list[FeedbackRecord], list[str]]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "youtube-transcript-api is required. Install with: pip install youtube-transcript-api"
        ) from exc

    records: list[FeedbackRecord] = []
    notes: list[str] = []
    requested_languages = languages or ["en"]

    for url in video_urls:
        video_id = extract_youtube_video_id(url)
        if not video_id:
            notes.append(f"Skipped invalid YouTube URL: {url}")
            continue

        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=requested_languages)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Failed transcript for {video_id}: {exc}")
            continue

        text = " ".join(chunk.get("text", "").strip() for chunk in transcript if chunk.get("text"))
        if not text:
            notes.append(f"Empty transcript for {video_id}.")
            continue

        records.append(
            FeedbackRecord(
                source=f"youtube:{video_id}",
                printer=printer,
                resin_type=resin_type,
                text=text[:12000],
                sponsored=_infer_sponsored_from_text(text),
            )
        )

    notes.append(f"Ingested {len(records)} transcript-backed records from YouTube.")
    return records, notes


def extract_youtube_video_id(url: str) -> str | None:
    candidate = url.strip()
    if len(candidate) == 11 and "/" not in candidate and " " not in candidate:
        return candidate

    parsed = urlparse(candidate)
    host = parsed.netloc.lower()

    if "youtu.be" in host:
        video_id = parsed.path.strip("/").split("/")[0]
        return video_id or None

    if "youtube.com" in host:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2:
                return parts[1]

    return None


def _row_to_feedback_record(
    row: dict[str, str],
    default_printer: str,
    default_resin: str,
    source: str,
) -> FeedbackRecord | None:
    normalized = {str(k).strip().lower(): ("" if v is None else str(v).strip()) for k, v in row.items()}

    text_parts = [normalized[k] for k in _TEXT_KEYS if normalized.get(k)]
    text = " | ".join(dict.fromkeys(text_parts))
    if not text:
        text = _fallback_text_blob(normalized)
    if not text:
        return None

    printer = _pick_first(normalized, _PRINTER_KEYS) or default_printer
    resin_type = _pick_first(normalized, _RESIN_KEYS) or default_resin
    layer = _to_float(_pick_first(normalized, _LAYER_KEYS))
    exposure = _to_float(_pick_first(normalized, _EXPOSURE_KEYS))
    ambient_temp = _to_float(_pick_first(normalized, _TEMP_KEYS))
    sponsored = _to_bool(_pick_first(normalized, _SPONSORED_KEYS))
    created_at = _to_date(_pick_first(normalized, _DATE_KEYS))

    if sponsored is None:
        sponsored = _infer_sponsored_from_text(text)

    return FeedbackRecord(
        source=source,
        printer=printer,
        resin_type=resin_type,
        text=text,
        layer_height_mm=layer,
        exposure_s=exposure,
        ambient_temp_c=ambient_temp,
        sponsored=sponsored,
        created_at=created_at,
    )


def _fallback_text_blob(row: dict[str, str]) -> str:
    skip = set(_PRINTER_KEYS + _RESIN_KEYS + _LAYER_KEYS + _EXPOSURE_KEYS + _TEMP_KEYS + _SPONSORED_KEYS + _DATE_KEYS)
    fragments = [value for key, value in row.items() if key not in skip and value]
    return " | ".join(fragments)


def _pick_first(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return value
    return None


def _to_float(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.lower().replace("mm", "").replace("s", "").replace("c", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_bool(value: str | None) -> bool | None:
    if not value:
        return None
    truthy = {"1", "true", "yes", "y", "sponsored"}
    falsy = {"0", "false", "no", "n", "organic"}
    lowered = value.strip().lower()
    if lowered in truthy:
        return True
    if lowered in falsy:
        return False
    return None


def _to_date(value: str | None) -> date | None:
    if not value:
        return None
    raw = value.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _infer_sponsored_from_text(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in _SPONSOR_WORDS)

