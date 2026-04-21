from __future__ import annotations

import trimesh

from app.geometry import analyze_geometry


def _broken_mesh_path(tmp_path):
    vertices = [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [5.0, 0.0, 0.0],
        [6.0, 0.0, 0.0],
        [5.0, 1.0, 0.0],
    ]
    faces = [
        [0, 1, 2],  # base
        [0, 1, 2],  # duplicate
        [0, 1, 1],  # degenerate
        [0, 2, 3],
        [0, 3, 1],
        [1, 3, 2],
        [4, 5, 6],  # disconnected shell
    ]
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    path = tmp_path / "broken.stl"
    mesh.export(path)
    return path


def test_mesh_health_report_detects_common_issues(tmp_path):
    path = _broken_mesh_path(tmp_path)

    analysis = analyze_geometry(str(path), slice_height_mm=0.2, auto_repair=False)
    health = analysis.mesh_health

    assert health is not None
    assert health.duplicate_face_count >= 1
    assert health.degenerate_face_count >= 1
    assert health.connected_components >= 2
    assert any("Duplicate triangles" in issue for issue in health.issues)
    assert any("Degenerate triangles" in issue for issue in health.issues)


def test_mesh_auto_repair_reduces_topology_noise(tmp_path):
    path = _broken_mesh_path(tmp_path)

    analysis = analyze_geometry(str(path), slice_height_mm=0.2, auto_repair=True)
    health = analysis.mesh_health

    assert health is not None
    assert health.repaired is True
    assert health.duplicate_face_count == 0
    assert health.degenerate_face_count == 0
    assert health.repair_actions
    assert any("Automatic STL repair applied" in note for note in analysis.notes)


def test_minimum_analysis_level_emits_performance_metrics(tmp_path):
    path = _broken_mesh_path(tmp_path)

    analysis = analyze_geometry(
        str(path),
        slice_height_mm=0.2,
        auto_repair=False,
        analysis_level="minimum",
    )

    assert analysis.analysis_level == "minimum"
    assert isinstance(analysis.performance_ms, dict)
    assert analysis.performance_ms.get("total", 0) > 0
    assert analysis.suction_cups == []
    assert analysis.islands == []
    assert any("Minimum analysis" in note for note in analysis.notes)


def test_minimum_analysis_avoids_voxelization(tmp_path, monkeypatch):
    path = _broken_mesh_path(tmp_path)

    def _fail_voxelized(*args, **kwargs):
        raise AssertionError("minimum analysis should not call voxelized")

    monkeypatch.setattr(trimesh.Trimesh, "voxelized", _fail_voxelized, raising=True)

    analysis = analyze_geometry(
        str(path),
        slice_height_mm=0.1,
        auto_repair=False,
        analysis_level="minimum",
    )

    assert analysis.analysis_level == "minimum"
    assert analysis.max_cross_section_mm2 >= 0
