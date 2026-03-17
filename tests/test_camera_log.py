from app.camera_log import analyze_camera_log


def test_camera_log_warp_and_empty_plate():
    log_text = "AI camera: Warp detected near corner. Next frame: Empty plate warning."
    result = analyze_camera_log(log_text)
    assert "warp_detected" in result.matched_events
    assert "empty_plate_detected" in result.matched_events
    assert any(adj.parameter == "bottom_exposure_s" for adj in result.adjustments)


def test_camera_log_no_known_events():
    result = analyze_camera_log("print completed successfully without warnings")
    assert result.matched_events == []
    assert any("No known" in note for note in result.notes)
