from app.feedback_sources import (
    extract_youtube_video_id,
    ingest_community_sheet_text,
)


def test_ingest_community_sheet_text_csv():
    csv_text = """printer,resin_type,layer_height_mm,exposure_s,ambient_temp_c,comments,sponsored,date
Elegoo Mars 5 Ultra,Elegoo ABS-Like v3.0,0.02,1.8,21.5,Blooming appeared at high exposure,false,2026-02-01
Elegoo Mars 5 Ultra,Elegoo ABS-Like v3.0,0.02,1.7,23.0,No issue on retry,false,2026-02-02
"""
    records, notes = ingest_community_sheet_text(
        text=csv_text,
        default_printer="Elegoo Mars 5 Ultra",
        default_resin="Elegoo ABS-Like v3.0",
        source="sheet",
    )
    assert len(records) == 2
    assert records[0].exposure_s == 1.8
    assert records[1].ambient_temp_c == 23.0
    assert records[0].sponsored is False
    assert any("Parsed 2 rows" in note for note in notes)


def test_extract_youtube_video_id_variants():
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=abc123XYZ09") == "abc123XYZ09"
    assert extract_youtube_video_id("https://youtu.be/abc123XYZ09") == "abc123XYZ09"
    assert extract_youtube_video_id("https://www.youtube.com/shorts/abc123XYZ09") == "abc123XYZ09"
    assert extract_youtube_video_id("abc123XYZ09") == "abc123XYZ09"
