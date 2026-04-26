from __future__ import annotations

import time
from pathlib import Path

from app.wizard_artifact_store import (
    cleanup_wizard_artifacts,
    create_wizard_artifact,
    delete_wizard_artifact,
    get_wizard_artifact,
    init_wizard_artifact_store,
)


def test_wizard_artifact_store_round_trips_records(tmp_path):
    db_path = init_wizard_artifact_store(tmp_path / "wizard_artifacts.db")
    artifact_path = tmp_path / "artifact.stl"
    artifact_path.write_bytes(b"mesh")

    create_wizard_artifact(
        artifact_id="artifact-1",
        path=artifact_path,
        download_name="fixed.stl",
        media_type="model/stl",
        created_at_ts=10.0,
        db_path=db_path,
    )

    artifact = get_wizard_artifact("artifact-1", db_path=db_path)

    assert artifact is not None
    assert artifact["path"] == str(artifact_path)
    assert artifact["download_name"] == "fixed.stl"
    assert artifact["media_type"] == "model/stl"
    assert artifact["created_at"] == 10.0


def test_wizard_artifact_store_cleanup_and_delete(tmp_path):
    db_path = init_wizard_artifact_store(tmp_path / "wizard_artifacts.db")
    stale_path = tmp_path / "stale.json"
    fresh_path = tmp_path / "fresh.json"
    stale_path.write_text("{}", encoding="utf-8")
    fresh_path.write_text("{}", encoding="utf-8")
    now = time.time()

    create_wizard_artifact(
        artifact_id="stale",
        path=stale_path,
        download_name="stale.json",
        media_type="application/json",
        created_at_ts=now - 120.0,
        db_path=db_path,
    )
    create_wizard_artifact(
        artifact_id="fresh",
        path=fresh_path,
        download_name="fresh.json",
        media_type="application/json",
        created_at_ts=now,
        db_path=db_path,
    )

    cleaned = cleanup_wizard_artifacts(max_age_seconds=60, db_path=db_path)

    assert [artifact["artifact_id"] for artifact in cleaned] == ["stale"]
    assert get_wizard_artifact("stale", db_path=db_path) is None
    assert get_wizard_artifact("fresh", db_path=db_path) is not None

    assert delete_wizard_artifact("fresh", db_path=db_path) is True
    assert delete_wizard_artifact("fresh", db_path=db_path) is False
    assert get_wizard_artifact("fresh", db_path=db_path) is None