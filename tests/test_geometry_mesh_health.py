from __future__ import annotations

import numpy as np
import trimesh

import app.geometry as geometry
from app.geometry import analyze_geometry
from app.models import MeshHealthReport


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


def _box_mesh_path(tmp_path):
    mesh = trimesh.creation.box(extents=(2.0, 3.0, 4.0))
    path = tmp_path / "box.stl"
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


def test_minimum_analysis_reports_unknown_topology_counts_when_skipped(tmp_path):
    path = _broken_mesh_path(tmp_path)

    analysis = analyze_geometry(
        str(path),
        slice_height_mm=0.2,
        auto_repair=False,
        analysis_level="minimum",
    )

    health = analysis.mesh_health
    assert health is not None
    assert health.connected_components is None
    assert health.boundary_edge_count is None
    assert health.non_manifold_edge_count is None
    assert any("detailed edge/connectivity scans were skipped" in issue for issue in health.issues)


def test_minimum_analysis_uses_exact_cross_sections_for_small_mesh(tmp_path, monkeypatch):
    path = _broken_mesh_path(tmp_path)
    observed: dict[str, object] = {"called": False, "slice_count": 0}

    def _fake_section_multiplane(self, plane_origin, plane_normal, heights):
        observed["called"] = True
        observed["slice_count"] = len(heights)
        return [None for _ in heights]

    monkeypatch.setattr(trimesh.Trimesh, "section_multiplane", _fake_section_multiplane, raising=True)

    analysis = analyze_geometry(
        str(path),
        slice_height_mm=0.05,
        auto_repair=False,
        analysis_level="minimum",
    )

    assert observed["called"] is True
    assert observed["slice_count"] > 0
    assert analysis.slice_height_mm >= 0.3
    assert any("exact multiplane slicing" in note for note in analysis.notes)


def test_minimum_analysis_fallback_spreads_cross_sections_across_vertical_span(tmp_path, monkeypatch):
    path = _box_mesh_path(tmp_path)

    def _missing_multiplane(self, plane_origin, plane_normal, heights):
        raise ModuleNotFoundError("scipy")

    monkeypatch.setattr(trimesh.Trimesh, "section_multiplane", _missing_multiplane, raising=True)

    analysis = analyze_geometry(
        str(path),
        slice_height_mm=0.5,
        auto_repair=False,
        analysis_level="minimum",
    )

    positive_slice_count = sum(slice_area.area_mm2 > 0 for slice_area in analysis.slice_areas)
    assert analysis.slice_areas[0].area_mm2 > 0
    assert analysis.slice_areas[-1].area_mm2 > 0
    assert positive_slice_count >= len(analysis.slice_areas) - 1
    assert any("surface-span weighted cross-section approximation" in note for note in analysis.notes)


def test_deep_analysis_adapts_large_mesh_profile(monkeypatch):
    class FakeMesh:
        def __init__(self):
            self.faces = np.zeros((800_000, 3), dtype=np.int64)
            self.vertices = np.zeros((3, 3), dtype=float)
            self.bounds = np.array([[0.0, 0.0, 0.0], [200.0, 200.0, 200.0]], dtype=float)
            self.extents = np.array([200.0, 200.0, 200.0], dtype=float)
            self.area = 240_000.0
            self.volume = 8_000_000.0

    mesh = FakeMesh()
    observed: dict[str, object] = {
        "cross_max_slices": None,
        "pitch": None,
        "curvature_called": False,
    }

    monkeypatch.setattr(
        geometry,
        "_load_and_prepare_mesh",
        lambda *args, **kwargs: (
            mesh,
            MeshHealthReport(
                watertight=False,
                winding_consistent=True,
                volume_consistent=False,
                connected_components=2,
                boundary_edge_count=8,
                non_manifold_edge_count=1,
                degenerate_face_count=0,
                duplicate_face_count=0,
                repaired=False,
                issues=["synthetic"],
                repair_actions=[],
            ),
        ),
    )
    monkeypatch.setattr(
        geometry,
        "_cross_section_areas",
        lambda mesh, slice_height_mm, max_slices, progress_callback=None, cancel_check=None: geometry._CrossSectionResult(
            slice_areas=observed.__setitem__("cross_max_slices", max_slices) or [],
            max_area_mm2=0.0,
            effective_slice_height_mm=slice_height_mm,
            notes=[f"cross max_slices={max_slices}"],
        ),
    )
    monkeypatch.setattr(
        geometry,
        "_voxelize_mesh",
        lambda mesh, pitch_mm, cancel_check=None: (None, np.zeros((1, 1, 1), dtype=bool)),
    )
    monkeypatch.setattr(
        geometry,
        "_detect_suction_cups",
        lambda mesh, pitch_mm, min_volume_mm3=0.5, voxel_data=None, progress_callback=None, cancel_check=None: observed.__setitem__("pitch", pitch_mm) or [],
    )
    monkeypatch.setattr(
        geometry,
        "_detect_islands",
        lambda mesh, pitch_mm, min_voxels=6, voxel_data=None, progress_callback=None, cancel_check=None: [],
    )

    def _fake_curvature(mesh, cancel_check=None):
        observed["curvature_called"] = True
        return 0.0

    monkeypatch.setattr(geometry, "_curvature_proxy", _fake_curvature)

    analysis = analyze_geometry(
        "synthetic.stl",
        slice_height_mm=0.01,
        auto_repair=False,
        analysis_level="deep",
    )

    assert observed["cross_max_slices"] == 7000
    assert observed["pitch"] is not None
    assert observed["pitch"] > 0.18
    assert observed["curvature_called"] is False
    assert any("raised voxel pitch" in note for note in analysis.notes)
    assert any("capped max slices" in note for note in analysis.notes)
    assert any("skipped curvature proxy" in note for note in analysis.notes)


def test_balanced_analysis_reuses_cross_section_voxels_and_emits_detail_notes(monkeypatch):
    class FaceCounter:
        def __len__(self):
            return 1_300_001

    class FakeVoxel:
        def __init__(self):
            self.matrix = np.zeros((3, 3, 3), dtype=bool)
            self.matrix[1, 1, 1] = True

        def indices_to_points(self, indices: np.ndarray) -> np.ndarray:
            return np.asarray(indices, dtype=float) * 0.1

    class FakeMesh:
        def __init__(self):
            self.faces = FaceCounter()
            self.vertices = np.zeros((3, 3), dtype=float)
            self.bounds = np.array([[0.0, 0.0, 0.0], [10.0, 10.0, 10.0]], dtype=float)
            self.extents = np.array([10.0, 10.0, 10.0], dtype=float)
            self.area = 600.0
            self.volume = 1000.0
            self.voxelized_calls: list[float] = []

        def voxelized(self, pitch: float):
            self.voxelized_calls.append(float(pitch))
            return FakeVoxel()

    mesh = FakeMesh()

    monkeypatch.setattr(
        geometry,
        "_load_and_prepare_mesh",
        lambda *args, **kwargs: (
            mesh,
            MeshHealthReport(
                watertight=True,
                winding_consistent=True,
                volume_consistent=True,
                connected_components=1,
                boundary_edge_count=0,
                non_manifold_edge_count=0,
                degenerate_face_count=0,
                duplicate_face_count=0,
                repaired=False,
                issues=[],
                repair_actions=[],
            ),
        ),
    )
    monkeypatch.setattr(
        geometry,
        "_detect_suction_cups",
        lambda mesh, pitch_mm, min_volume_mm3=0.5, voxel_data=None, progress_callback=None, cancel_check=None: [
            geometry.Cavity(id=1, volume_mm3=3.5, centroid_mm=[1.0, 2.0, 3.0]),
            geometry.Cavity(id=2, volume_mm3=1.25, centroid_mm=[4.0, 5.0, 6.0]),
        ] if voxel_data is not None else (_ for _ in ()).throw(AssertionError("expected reused voxel data")),
    )
    monkeypatch.setattr(
        geometry,
        "_detect_islands",
        lambda mesh, pitch_mm, min_voxels=6, voxel_data=None, progress_callback=None, cancel_check=None: [
            geometry.Island(layer_index=4, z_mm=0.4, voxel_count=12),
            geometry.Island(layer_index=7, z_mm=0.7, voxel_count=5),
        ] if voxel_data is not None else (_ for _ in ()).throw(AssertionError("expected reused voxel data")),
    )

    analysis = analyze_geometry(
        "synthetic.stl",
        slice_height_mm=0.1,
        auto_repair=False,
        analysis_level="balanced",
    )

    assert len(mesh.voxelized_calls) == 1
    assert abs(mesh.voxelized_calls[0] - 0.15) < 1e-9
    assert "voxelize" not in analysis.performance_ms
    assert any("Reused 0.15mm voxel grid" in note for note in analysis.notes)
    assert any("Peak cross-section measured" in note for note in analysis.notes)
    assert any("trapped-resin cavity candidates" in note for note in analysis.notes)
    assert any("unsupported island regions" in note for note in analysis.notes)


def test_detect_islands_clusters_adjacent_layers_into_regions():
    class FakeVoxel:
        def __init__(self, matrix: np.ndarray):
            self.matrix = matrix

        def indices_to_points(self, indices: np.ndarray) -> np.ndarray:
            return np.asarray(indices, dtype=float) * 0.2

    occupied = np.zeros((12, 8, 4), dtype=bool)
    occupied[2:6, 2:4, 1] = True
    occupied[2:9, 2:4, 2] = True

    voxel = FakeVoxel(occupied)

    islands = geometry._detect_islands(
        mesh=None,
        pitch_mm=0.2,
        min_voxels=1,
        voxel_data=(voxel, occupied),
    )

    assert len(islands) == 1
    island = islands[0]
    assert island.layer_index == 1
    assert island.end_layer_index == 2
    assert island.layer_span == 2
    assert island.voxel_count == 8
    assert island.total_voxel_count == 12
    assert island.xy_centroid_mm is not None
