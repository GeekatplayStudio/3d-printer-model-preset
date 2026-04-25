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


def test_analysis_reports_fdm_support_metrics_for_box_mesh(tmp_path):
    path = _box_mesh_path(tmp_path)

    analysis = analyze_geometry(str(path), slice_height_mm=0.25, auto_repair=False)

    assert analysis.downskin_area_ratio is not None
    assert 0.10 <= analysis.downskin_area_ratio <= 0.13
    assert analysis.fdm_support_risk_score is not None
    assert analysis.fdm_support_risk_score > 0.0
    assert any("FDM heuristic" in note for note in analysis.notes)


def test_repair_mesh_file_reports_before_after_fix_state(tmp_path):
    source_path = _broken_mesh_path(tmp_path)
    repaired_path = tmp_path / "broken_fixed.stl"

    outcome = geometry.repair_mesh_file(str(source_path), str(repaired_path))

    assert outcome.repaired is True
    assert outcome.before_fix.duplicate_face_count >= 1
    assert outcome.before_fix.degenerate_face_count >= 1
    assert outcome.after_fix.duplicate_face_count == 0
    assert outcome.after_fix.degenerate_face_count == 0
    assert outcome.after_fix.boundary_edge_count == 3
    assert outcome.after_fix.connected_components == 2
    assert outcome.fully_repaired is False
    assert any("Duplicate triangles detected" in issue for issue in outcome.resolved_issues)
    assert any("Mesh is not watertight." == issue for issue in outcome.remaining_issues)
    assert repaired_path.exists()


def test_requested_pymeshlab_backend_falls_back_to_trimesh_when_unavailable(monkeypatch):
    observed = {"trimesh_called": False}

    def _missing_pymeshlab(mesh):
        raise ModuleNotFoundError("pymeshlab")

    def _fake_trimesh(mesh):
        observed["trimesh_called"] = True
        return True, ["Trimesh repair applied."]

    monkeypatch.setattr(geometry, "_repair_mesh_with_pymeshlab", _missing_pymeshlab)
    monkeypatch.setattr(geometry, "_repair_mesh_with_trimesh", _fake_trimesh)

    repaired, actions = geometry._repair_mesh(trimesh.creation.box(), backend="pymeshlab")

    assert repaired is True
    assert observed["trimesh_called"] is True
    assert any("fell back to Trimesh repair" in action for action in actions)


def test_requested_pymeshlab_backend_uses_pymeshlab_pipeline(monkeypatch):
    def _fake_pymeshlab(mesh):
        return True, ["Applied PyMeshLab prototype repair pipeline."]

    def _unexpected_trimesh(mesh):
        raise AssertionError("Trimesh fallback should not be used when PyMeshLab succeeds")

    monkeypatch.setattr(geometry, "_repair_mesh_with_pymeshlab", _fake_pymeshlab)
    monkeypatch.setattr(geometry, "_repair_mesh_with_trimesh", _unexpected_trimesh)

    repaired, actions = geometry._repair_mesh(trimesh.creation.box(), backend="pymeshlab")

    assert repaired is True
    assert actions == ["Applied PyMeshLab prototype repair pipeline."]


def test_requested_open3d_voxel_backend_falls_back_to_trimesh_when_unavailable(monkeypatch):
    observed = {"trimesh_called": False}

    class FakeVoxel:
        def __init__(self):
            self.matrix = np.ones((1, 1, 1), dtype=bool)

        def indices_to_points(self, indices: np.ndarray) -> np.ndarray:
            return np.asarray(indices, dtype=float)

    def _missing_open3d(mesh, pitch_mm, cancel_check=None):
        raise ModuleNotFoundError("open3d")

    def _fake_trimesh(mesh, pitch_mm, cancel_check=None):
        observed["trimesh_called"] = True
        voxel = FakeVoxel()
        return voxel, voxel.matrix

    monkeypatch.setenv("RESINLOGIC_GEOMETRY_VOXEL_BACKEND", "open3d")
    monkeypatch.setattr(geometry, "_voxelize_mesh_with_open3d", _missing_open3d)
    monkeypatch.setattr(geometry, "_voxelize_mesh_with_trimesh", _fake_trimesh)

    voxel, occupied = geometry._voxelize_mesh(trimesh.creation.box(), pitch_mm=0.25)

    assert observed["trimesh_called"] is True
    assert voxel is not None
    assert occupied.shape == (1, 1, 1)


def test_requested_open3d_voxel_backend_uses_open3d_pipeline(monkeypatch):
    sentinel_voxel = object()
    occupied = np.zeros((2, 2, 2), dtype=bool)
    occupied[1, 1, 1] = True

    def _fake_open3d(mesh, pitch_mm, cancel_check=None):
        return sentinel_voxel, occupied

    def _unexpected_trimesh(mesh, pitch_mm, cancel_check=None):
        raise AssertionError("Trimesh fallback should not be used when Open3D succeeds")

    monkeypatch.setenv("RESINLOGIC_GEOMETRY_VOXEL_BACKEND", "open3d")
    monkeypatch.setattr(geometry, "_voxelize_mesh_with_open3d", _fake_open3d)
    monkeypatch.setattr(geometry, "_voxelize_mesh_with_trimesh", _unexpected_trimesh)

    voxel, voxel_occupied = geometry._voxelize_mesh(trimesh.creation.box(), pitch_mm=0.25)

    assert voxel is sentinel_voxel
    assert voxel_occupied is occupied


def test_open3d_voxel_backend_fills_interior_occupancy(monkeypatch):
    class FakeTensor:
        def __init__(self, value):
            self._value = np.asarray(value)

        def numpy(self):
            return np.asarray(self._value)

    class FakeVoxel:
        def __init__(self, grid_index):
            self.grid_index = np.asarray(grid_index, dtype=np.int32)

    class FakeVoxelGrid:
        def __init__(self):
            self.origin = np.zeros(3, dtype=float)
            self._voxels = [FakeVoxel([0, 0, 0]), FakeVoxel([2, 0, 0])]

        def get_voxels(self):
            return list(self._voxels)

    class FakeLegacyTriangleMesh:
        def __init__(self):
            self.vertices = None
            self.triangles = None

    class FakeGeometryModule:
        TriangleMesh = FakeLegacyTriangleMesh

        class VoxelGrid:
            @staticmethod
            def create_from_triangle_mesh(mesh, voxel_size):
                return FakeVoxelGrid()

    class FakeUtilityModule:
        @staticmethod
        def Vector3dVector(value):
            return value

        @staticmethod
        def Vector3iVector(value):
            return value

    class FakeTensorTriangleMesh:
        @staticmethod
        def from_legacy(mesh):
            return mesh

    class FakeRaycastingScene:
        def add_triangles(self, mesh):
            return 1

        def compute_occupancy(self, query_points, nsamples=1):
            assert nsamples == 3
            grid = np.asarray(query_points._value)
            assert grid.shape == (3, 1, 1, 3)
            return FakeTensor(np.array([[[0]], [[1]], [[0]]], dtype=np.float32))

    class FakeTensorGeometryModule:
        TriangleMesh = FakeTensorTriangleMesh
        RaycastingScene = FakeRaycastingScene

    class FakeTensorModule:
        geometry = FakeTensorGeometryModule

    class FakeCoreModule:
        @staticmethod
        def Tensor(value):
            return FakeTensor(value)

    class FakeOpen3D:
        geometry = FakeGeometryModule
        utility = FakeUtilityModule
        t = FakeTensorModule
        core = FakeCoreModule

    monkeypatch.setattr(geometry, "_load_open3d_module", lambda: FakeOpen3D())

    voxel, occupied = geometry._voxelize_mesh_with_open3d(trimesh.creation.box(), pitch_mm=1.0)

    assert occupied.shape == (3, 1, 1)
    assert occupied[:, 0, 0].tolist() == [True, True, True]
    points = voxel.indices_to_points(np.asarray([[1, 0, 0]], dtype=float))
    assert points.tolist() == [[1.5, 0.5, 0.5]]


def test_cross_section_voxel_fallback_uses_shared_voxel_backend(monkeypatch):
    class FakeVoxel:
        def indices_to_points(self, indices: np.ndarray) -> np.ndarray:
            return np.asarray(indices, dtype=float) * 0.2

    occupied = np.zeros((3, 3, 3), dtype=bool)
    occupied[1, 1, :] = True
    observed: dict[str, float] = {}

    def _fake_voxelize(mesh, pitch_mm, cancel_check=None):
        observed["pitch_mm"] = float(pitch_mm)
        return FakeVoxel(), occupied

    monkeypatch.setattr(geometry, "_voxelize_mesh", _fake_voxelize)

    result = geometry._cross_section_areas_voxel(
        trimesh.creation.box(extents=(1.0, 1.0, 1.0)),
        z_min=0.0,
        heights=np.array([0.0, 0.2, 0.4], dtype=float),
        requested_slice_mm=0.2,
        notes=[],
    )

    assert observed["pitch_mm"] == 0.2
    assert result.voxel_data is not None
    assert result.voxel_data[1] is occupied


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
            geometry.Cavity(
                id=1,
                volume_mm3=3.5,
                centroid_mm=[1.0, 2.0, 3.0],
                xy_footprint_mm2=1.8,
                z_span_mm=0.3,
                confidence_score=0.91,
                confidence_level="high",
            ),
            geometry.Cavity(
                id=2,
                volume_mm3=1.25,
                centroid_mm=[4.0, 5.0, 6.0],
                xy_footprint_mm2=0.6,
                z_span_mm=0.15,
                confidence_score=0.53,
                confidence_level="medium",
            ),
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
    assert any("1 high-confidence" in note for note in analysis.notes)
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


def test_detect_suction_cups_filters_tall_narrow_sealed_voids():
    class FakeVoxel:
        def __init__(self, matrix: np.ndarray):
            self.matrix = matrix

        def indices_to_points(self, indices: np.ndarray) -> np.ndarray:
            return np.asarray(indices, dtype=float)

    occupied = np.ones((10, 10, 8), dtype=bool)
    occupied[2:5, 2:5, 3] = False
    occupied[7, 7, 1:5] = False

    voxel = FakeVoxel(occupied)

    cavities = geometry._detect_suction_cups(
        mesh=None,
        pitch_mm=1.0,
        min_volume_mm3=0.5,
        voxel_data=(voxel, occupied),
    )

    assert len(cavities) == 1
    assert cavities[0].volume_mm3 == 9.0
    assert cavities[0].centroid_mm is not None
    assert cavities[0].xy_footprint_mm2 == 9.0
    assert cavities[0].z_span_mm == 1.0
    assert cavities[0].confidence_level == "high"
    assert cavities[0].confidence_score is not None
    assert cavities[0].confidence_score >= 0.75


def test_suction_cup_shape_filter_keeps_broad_multilayer_cavity():
    component = np.array(
        [
            [x, y, z]
            for x in range(3)
            for y in range(3)
            for z in range(3)
        ],
        dtype=float,
    )

    assert geometry._is_plausible_suction_cup_component(component) is True


def test_suction_cup_shape_filter_keeps_shallow_compact_pocket():
    component = np.array(
        [
            [4, 4, 2],
            [5, 4, 2],
        ],
        dtype=float,
    )

    assert geometry._is_plausible_suction_cup_component(component) is True
