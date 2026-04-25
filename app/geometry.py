from __future__ import annotations

from dataclasses import dataclass
import math
import os
from collections.abc import Callable
from collections import deque
from pathlib import Path
from time import perf_counter
from tempfile import TemporaryDirectory

import numpy as np
import trimesh

from app.models import AnalysisLevel, Cavity, CavityConfidenceLevel, GeometryAnalysis, Island, MeshHealthReport, MeshRepairOutcome, SliceArea, UseCase

DEFAULT_BUILD_PLATE_MM = (153.36, 77.76)
_DEFAULT_MESH_REPAIR_BACKEND = "trimesh"
_SUPPORTED_MESH_REPAIR_BACKENDS = {"trimesh", "pymeshlab"}
_DEFAULT_VOXEL_BACKEND = "trimesh"
_SUPPORTED_VOXEL_BACKENDS = {"trimesh", "open3d"}
_NEIGHBORS_3D = (
    (-1, 0, 0),
    (1, 0, 0),
    (0, -1, 0),
    (0, 1, 0),
    (0, 0, -1),
    (0, 0, 1),
)
_NEIGHBORS_2D = ((-1, 0), (1, 0), (0, -1), (0, 1))
AnalysisProgressCallback = Callable[[str, str], None]
AnalysisCancelCheck = Callable[[], None]


class AnalysisCancelledError(RuntimeError):
    pass


@dataclass(slots=True)
class _CrossSectionResult:
    slice_areas: list[SliceArea]
    max_area_mm2: float
    effective_slice_height_mm: float
    notes: list[str]
    voxel_data: tuple[object, np.ndarray] | None = None
    voxel_pitch_mm: float | None = None


@dataclass(slots=True)
class _IslandComponent:
    layer_index: int
    z_mm: float
    voxel_count: int
    bbox_xy_idx: tuple[int, int, int, int]
    xy_centroid_mm: list[float]


@dataclass(slots=True)
class _IslandRegionState:
    start_layer_index: int
    end_layer_index: int
    start_z_mm: float
    end_z_mm: float
    peak_voxel_count: int
    total_voxel_count: int
    weighted_x_mm: float
    weighted_y_mm: float
    weight_sum: int
    last_bbox_xy_idx: tuple[int, int, int, int]

    @classmethod
    def from_component(cls, component: _IslandComponent) -> "_IslandRegionState":
        return cls(
            start_layer_index=component.layer_index,
            end_layer_index=component.layer_index,
            start_z_mm=component.z_mm,
            end_z_mm=component.z_mm,
            peak_voxel_count=component.voxel_count,
            total_voxel_count=component.voxel_count,
            weighted_x_mm=float(component.xy_centroid_mm[0]) * component.voxel_count,
            weighted_y_mm=float(component.xy_centroid_mm[1]) * component.voxel_count,
            weight_sum=component.voxel_count,
            last_bbox_xy_idx=component.bbox_xy_idx,
        )

    def absorb(self, component: _IslandComponent) -> None:
        self.end_layer_index = component.layer_index
        self.end_z_mm = component.z_mm
        self.peak_voxel_count = max(self.peak_voxel_count, component.voxel_count)
        self.total_voxel_count += component.voxel_count
        self.weighted_x_mm += float(component.xy_centroid_mm[0]) * component.voxel_count
        self.weighted_y_mm += float(component.xy_centroid_mm[1]) * component.voxel_count
        self.weight_sum += component.voxel_count
        self.last_bbox_xy_idx = component.bbox_xy_idx

    def absorb_region(self, other: "_IslandRegionState") -> None:
        self.start_layer_index = min(self.start_layer_index, other.start_layer_index)
        self.end_layer_index = max(self.end_layer_index, other.end_layer_index)
        self.start_z_mm = min(self.start_z_mm, other.start_z_mm)
        self.end_z_mm = max(self.end_z_mm, other.end_z_mm)
        self.peak_voxel_count = max(self.peak_voxel_count, other.peak_voxel_count)
        self.total_voxel_count += other.total_voxel_count
        self.weighted_x_mm += other.weighted_x_mm
        self.weighted_y_mm += other.weighted_y_mm
        self.weight_sum += other.weight_sum

    def to_island(self) -> Island:
        centroid = None
        if self.weight_sum > 0:
            centroid = [
                float(self.weighted_x_mm / self.weight_sum),
                float(self.weighted_y_mm / self.weight_sum),
            ]
        return Island(
            layer_index=self.start_layer_index,
            z_mm=float(self.start_z_mm),
            voxel_count=int(self.peak_voxel_count),
            end_layer_index=int(self.end_layer_index),
            z_end_mm=float(self.end_z_mm),
            layer_span=int(self.end_layer_index - self.start_layer_index + 1),
            total_voxel_count=int(self.total_voxel_count),
            xy_centroid_mm=centroid,
        )


@dataclass(slots=True)
class _SuctionCupComponentMetrics:
    x_span_voxels: int
    y_span_voxels: int
    z_span_voxels: int
    footprint_voxel_count: int
    widest_xy_span_voxels: int
    footprint_to_depth_ratio: float


@dataclass(slots=True)
class _Open3DVoxelGridAdapter:
    origin_mm: np.ndarray
    index_offset: np.ndarray
    voxel_size_mm: float

    def indices_to_points(self, indices: np.ndarray) -> np.ndarray:
        if len(indices) == 0:
            return np.zeros((0, 3), dtype=float)

        open3d_indices = np.asarray(indices, dtype=float) + self.index_offset.reshape(1, 3)
        return self.origin_mm.reshape(1, 3) + ((open3d_indices + 0.5) * float(self.voxel_size_mm))


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 3)


def _report_progress(
    progress_callback: AnalysisProgressCallback | None,
    stage: str,
    detail: str,
) -> None:
    if progress_callback is not None:
        progress_callback(stage, detail)


def _check_cancel(cancel_check: AnalysisCancelCheck | None) -> None:
    if cancel_check is not None:
        cancel_check()


def _analysis_profile_config(
    *,
    analysis_level: AnalysisLevel,
    max_slices: int,
    voxel_pitch_mm: float,
) -> dict[str, object]:
    if analysis_level == "minimum":
        return {
            "max_slices": min(max_slices, 600),
            "voxel_pitch_mm": max(voxel_pitch_mm, 0.6),
            "include_suction_cups": False,
            "include_islands": False,
            "include_curvature": False,
        }
    if analysis_level == "extreme":
        return {
            "max_slices": max(min(max_slices, 30000), 18000),
            "voxel_pitch_mm": min(max(voxel_pitch_mm, 0.04), 0.08),
            "include_suction_cups": True,
            "include_islands": True,
            "include_curvature": True,
        }
    if analysis_level == "deep":
        return {
            "max_slices": max(min(max_slices, 22000), 12000),
            "voxel_pitch_mm": min(max(voxel_pitch_mm, 0.08), 0.18),
            "include_suction_cups": True,
            "include_islands": True,
            "include_curvature": True,
        }
    return {
        "max_slices": min(max_slices, 6000),
        "voxel_pitch_mm": max(voxel_pitch_mm, 0.3),
        "include_suction_cups": True,
        "include_islands": True,
        "include_curvature": True,
    }


def _estimated_voxel_cells(mesh: trimesh.Trimesh, pitch_mm: float) -> float:
    extents = np.maximum(np.asarray(mesh.extents, dtype=float), 0.0)
    if pitch_mm <= 0.0:
        return 0.0
    grid_shape = np.ceil(extents / pitch_mm) + 3.0
    return float(np.prod(np.maximum(grid_shape, 1.0)))


def _adaptive_profile_overrides(
    mesh: trimesh.Trimesh,
    *,
    analysis_level: AnalysisLevel,
    slice_height_mm: float,
    profile: dict[str, object],
) -> tuple[dict[str, object], list[str]]:
    adjusted = dict(profile)
    notes: list[str] = []
    face_count = int(len(mesh.faces))
    requested_slices = int(math.floor(max(float(mesh.extents[2]), 0.0) / max(slice_height_mm, 1e-6))) + 1

    if analysis_level == "minimum":
        return adjusted, notes

    voxel_pitch = float(adjusted["voxel_pitch_mm"])
    if analysis_level == "balanced":
        max_voxel_cells = 12_000_000
    elif analysis_level == "deep":
        max_voxel_cells = 18_000_000
    else:
        max_voxel_cells = 32_000_000
    estimated_cells = _estimated_voxel_cells(mesh, voxel_pitch)
    if estimated_cells > max_voxel_cells:
        extent_volume = float(np.prod(np.maximum(np.asarray(mesh.extents, dtype=float), 1e-6)))
        adjusted_pitch = max(voxel_pitch, (extent_volume / max_voxel_cells) ** (1.0 / 3.0))
        adjusted["voxel_pitch_mm"] = round(adjusted_pitch, 4)
        notes.append(
            f"Analysis level '{analysis_level}' raised voxel pitch to {adjusted['voxel_pitch_mm']}mm "
            f"to keep voxel analysis within runtime limits."
        )

    if face_count >= 350_000 and requested_slices > 4000:
        if analysis_level == "balanced":
            capped_slices = 4000
        elif analysis_level == "deep":
            capped_slices = 7000
        else:
            capped_slices = 12000
        if int(adjusted["max_slices"]) > capped_slices:
            adjusted["max_slices"] = capped_slices
            notes.append(
                f"Analysis level '{analysis_level}' capped max slices at {capped_slices} for a high-complexity mesh."
            )

    if analysis_level == "balanced":
        curvature_face_limit = 400_000
    elif analysis_level == "deep":
        curvature_face_limit = 750_000
    else:
        curvature_face_limit = 1_250_000
    if face_count >= curvature_face_limit and bool(adjusted["include_curvature"]):
        adjusted["include_curvature"] = False
        notes.append(
            f"Analysis level '{analysis_level}' skipped curvature proxy on a very large mesh to avoid timeout."
        )

    return adjusted, notes


def analyze_geometry(
    file_path: str,
    slice_height_mm: float = 0.01,
    build_plate_mm: tuple[float, float] = DEFAULT_BUILD_PLATE_MM,
    voxel_pitch_mm: float = 0.2,
    max_slices: int = 20000,
    auto_repair: bool = True,
    analysis_level: AnalysisLevel = "balanced",
    progress_callback: AnalysisProgressCallback | None = None,
    cancel_check: AnalysisCancelCheck | None = None,
) -> GeometryAnalysis:
    total_started = perf_counter()
    timings_ms: dict[str, float] = {}

    _check_cancel(cancel_check)
    _report_progress(progress_callback, "load_prepare_mesh", "Loading mesh geometry and checking topology health.")
    started = perf_counter()
    mesh, mesh_health = _load_and_prepare_mesh(
        file_path,
        auto_repair=auto_repair,
        lightweight_health=analysis_level == "minimum",
    )
    timings_ms["load_prepare_mesh"] = _elapsed_ms(started)
    notes: list[str] = []
    if mesh_health.repaired:
        notes.append("Automatic STL repair applied before geometry analysis.")
    notes.extend(mesh_health.issues)
    if analysis_level == "extreme":
        notes.append(
            "Extreme analysis enabled the highest slice density, smallest voxel pitch, and full curvature estimation."
        )

    profile = _analysis_profile_config(
        analysis_level=analysis_level,
        max_slices=max_slices,
        voxel_pitch_mm=voxel_pitch_mm,
    )
    _check_cancel(cancel_check)
    _report_progress(progress_callback, "adapt_profile", "Adapting slice and voxel settings to match mesh size.")
    profile, profile_notes = _adaptive_profile_overrides(
        mesh,
        analysis_level=analysis_level,
        slice_height_mm=slice_height_mm,
        profile=profile,
    )
    notes.extend(profile_notes)
    if profile["max_slices"] != max_slices:
        notes.append(
            f"Analysis level '{analysis_level}' adjusted max slices to {profile['max_slices']} "
            f"(requested {max_slices})."
        )
    if profile["voxel_pitch_mm"] != voxel_pitch_mm:
        notes.append(
            f"Analysis level '{analysis_level}' adjusted voxel pitch to {profile['voxel_pitch_mm']}mm "
            f"(requested {voxel_pitch_mm}mm)."
        )

    _check_cancel(cancel_check)
    _report_progress(progress_callback, "cross_section", "Computing cross-sections through the model.")
    started = perf_counter()
    if analysis_level == "minimum":
        cross_section = _cross_section_areas_fast(
            mesh=mesh,
            slice_height_mm=slice_height_mm,
            max_slices=int(profile["max_slices"]),
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    else:
        cross_section = _cross_section_areas(
            mesh=mesh,
            slice_height_mm=slice_height_mm,
            max_slices=int(profile["max_slices"]),
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    timings_ms["cross_section"] = _elapsed_ms(started)
    slice_areas = cross_section.slice_areas
    max_cross_section_mm2 = cross_section.max_area_mm2
    effective_slice_height_mm = cross_section.effective_slice_height_mm
    notes.extend(cross_section.notes)

    suction_cups: list[Cavity] = []
    islands: list[Island] = []
    curvature_proxy: float | None = None

    voxel_data = cross_section.voxel_data
    cross_section_voxel_pitch_mm = cross_section.voxel_pitch_mm
    should_voxelize = bool(profile["include_suction_cups"] or profile["include_islands"])
    if should_voxelize:
        target_voxel_pitch_mm = float(profile["voxel_pitch_mm"])
        can_reuse_cross_section_voxels = (
            voxel_data is not None
            and cross_section_voxel_pitch_mm is not None
            and cross_section_voxel_pitch_mm <= target_voxel_pitch_mm + 1e-9
        )
        if can_reuse_cross_section_voxels:
            notes.append(
                f"Reused {round(float(cross_section_voxel_pitch_mm), 4)}mm voxel grid from cross-section fallback "
                "for cavity and island detection."
            )
        else:
            if voxel_data is not None and cross_section_voxel_pitch_mm is not None:
                notes.append(
                    f"Cross-section fallback used {round(float(cross_section_voxel_pitch_mm), 4)}mm voxels, "
                    f"so cavity and island detection re-voxelized at {round(target_voxel_pitch_mm, 4)}mm for higher detail."
                )
            _check_cancel(cancel_check)
            _report_progress(progress_callback, "voxelize", "Voxelizing the mesh for cavity and island detection.")
            started = perf_counter()
            voxel_data = _voxelize_mesh(
                mesh,
                pitch_mm=target_voxel_pitch_mm,
                cancel_check=cancel_check,
            )
            timings_ms["voxelize"] = _elapsed_ms(started)

    if bool(profile["include_suction_cups"]):
        _check_cancel(cancel_check)
        _report_progress(progress_callback, "detect_suction_cups", "Scanning the voxel grid for suction cups and trapped resin cavities.")
        started = perf_counter()
        suction_cups = _detect_suction_cups(
            mesh,
            pitch_mm=float(profile["voxel_pitch_mm"]),
            voxel_data=voxel_data,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        timings_ms["detect_suction_cups"] = _elapsed_ms(started)
    else:
        notes.append("Minimum analysis: suction cup detection skipped for faster processing.")

    if bool(profile["include_islands"]):
        _check_cancel(cancel_check)
        _report_progress(progress_callback, "detect_islands", "Checking sliced layers for unsupported islands.")
        started = perf_counter()
        islands = _detect_islands(
            mesh,
            pitch_mm=float(profile["voxel_pitch_mm"]),
            voxel_data=voxel_data,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        timings_ms["detect_islands"] = _elapsed_ms(started)
    else:
        notes.append("Minimum analysis: island detection skipped for faster processing.")

    if bool(profile["include_curvature"]):
        _check_cancel(cancel_check)
        _report_progress(progress_callback, "curvature_proxy", "Estimating surface detail density from mesh curvature.")
        started = perf_counter()
        curvature_proxy = _curvature_proxy(mesh, cancel_check=cancel_check)
        timings_ms["curvature_proxy"] = _elapsed_ms(started)
    else:
        if analysis_level == "minimum":
            notes.append("Minimum analysis: curvature proxy skipped for faster processing.")
        else:
            notes.append("Curvature proxy skipped to stay within runtime limits for this mesh.")

    _check_cancel(cancel_check)
    _report_progress(progress_callback, "derive_metrics", "Deriving printability metrics and intent hints.")
    started = perf_counter()
    surface_area = float(mesh.area)
    volume_mm3 = float(abs(mesh.volume))
    triangle_count = int(len(mesh.faces))
    vertex_count = int(len(mesh.vertices))
    detail_density = float(triangle_count / surface_area) if surface_area > 0 else 0.0
    surface_area_ratio = float(surface_area / volume_mm3) if volume_mm3 > 0 else 0.0
    build_plate_area_mm2 = float(build_plate_mm[0] * build_plate_mm[1])
    max_ratio = max_cross_section_mm2 / build_plate_area_mm2 if build_plate_area_mm2 > 0 else 0.0
    bounding_box = np.maximum(np.asarray(mesh.extents, dtype=float), 0.0)
    bounding_box_mm = [float(value) for value in bounding_box.tolist()]
    bounding_box_diagonal_mm = float(np.linalg.norm(bounding_box)) if bounding_box.size else 0.0
    center_of_mass_mm: list[float] | None = None
    try:
        center_of_mass = np.asarray(mesh.center_mass, dtype=float)
        if np.all(np.isfinite(center_of_mass)):
            center_of_mass_mm = [float(value) for value in center_of_mass.tolist()]
    except Exception:
        center_of_mass_mm = None
    try:
        euler_number = int(mesh.euler_number)
    except Exception:
        euler_number = None
    estimated_intent, intent_reasons = _estimate_model_intent(
        surface_area_ratio=surface_area_ratio,
        volume_mm3=volume_mm3,
        detail_density=detail_density,
        max_cross_section_ratio=max_ratio,
        suction_cup_count=len(suction_cups),
        island_count=len(islands),
    )
    structural_risk = _structural_risk(
        max_ratio=max_ratio,
        cavity_count=len(suction_cups),
        island_count=len(islands),
        detail_density=detail_density,
    )
    notes.extend(
        _analysis_detail_notes(
            slice_areas=slice_areas,
            build_plate_area_mm2=build_plate_area_mm2,
            suction_cups=suction_cups,
            islands=islands,
            curvature_proxy=curvature_proxy,
        )
    )
    timings_ms["derive_metrics"] = _elapsed_ms(started)

    timings_ms["total"] = _elapsed_ms(total_started)
    slowest_step = None
    slowest_ms = 0.0
    for key, value in timings_ms.items():
        if key == "total":
            continue
        if value > slowest_ms:
            slowest_step = key
            slowest_ms = value
    if slowest_step is not None:
        notes.append(
            f"Performance: slowest step was '{slowest_step}' ({round(slowest_ms, 1)} ms)."
        )

    _check_cancel(cancel_check)
    _report_progress(progress_callback, "finalize", "Finalizing analysis report.")

    return GeometryAnalysis(
        file_name=Path(file_path).name,
        mesh_volume_mm3=volume_mm3,
        surface_area_mm2=surface_area,
        surface_area_ratio=surface_area_ratio,
        triangle_count=triangle_count,
        vertex_count=vertex_count,
        detail_density=detail_density,
        curvature_proxy=curvature_proxy,
        slice_height_mm=effective_slice_height_mm,
        build_plate_area_mm2=build_plate_area_mm2,
        max_cross_section_mm2=float(max_cross_section_mm2),
        max_cross_section_ratio=float(max_ratio),
        bounding_box_mm=bounding_box_mm,
        bounding_box_diagonal_mm=bounding_box_diagonal_mm,
        center_of_mass_mm=center_of_mass_mm,
        euler_number=euler_number,
        slice_areas=slice_areas,
        suction_cups=suction_cups,
        islands=islands,
        mesh_health=mesh_health,
        estimated_intent=estimated_intent,
        intent_reasons=intent_reasons,
        structural_risk_score=structural_risk,
        notes=notes,
        analysis_level=analysis_level,
        performance_ms=timings_ms,
    )


def mesh_health_actionable_issues(health: MeshHealthReport | None) -> list[str]:
    if health is None:
        return []
    issues: list[str] = []
    if (health.boundary_edge_count or 0) > 0:
        issues.append(f"Open boundaries detected ({health.boundary_edge_count} boundary edges).")
    if (health.non_manifold_edge_count or 0) > 0:
        issues.append(f"Non-manifold topology detected ({health.non_manifold_edge_count} non-manifold edges).")
    if (health.connected_components or 1) > 1:
        issues.append(f"Multiple disconnected shells detected ({health.connected_components} components).")
    if health.degenerate_face_count > 0:
        issues.append(f"Degenerate triangles detected ({health.degenerate_face_count} faces).")
    if health.duplicate_face_count > 0:
        issues.append(f"Duplicate triangles detected ({health.duplicate_face_count} faces).")
    if not health.winding_consistent:
        issues.append("Face winding is inconsistent.")
    if not health.watertight:
        issues.append("Mesh is not watertight.")
    return issues


def mesh_health_requires_fix(health: MeshHealthReport | None) -> bool:
    if health is None:
        return False
    return bool(
        (not health.watertight)
        or (not health.winding_consistent)
        or ((health.boundary_edge_count or 0) > 0)
        or ((health.non_manifold_edge_count or 0) > 0)
        or (health.degenerate_face_count > 0)
        or (health.duplicate_face_count > 0)
        or ((health.connected_components or 1) > 1)
    )


def repair_mesh_file(file_path: str, output_path: str) -> MeshRepairOutcome:
    mesh = _load_mesh(file_path)
    before_fix = _mesh_health_report(mesh=mesh, repaired=False, repair_actions=[])
    repaired, repair_actions = _repair_mesh(mesh)
    after_fix = _mesh_health_report(mesh=mesh, repaired=repaired, repair_actions=repair_actions)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(output, file_type="stl")
    before_issues = mesh_health_actionable_issues(before_fix)
    after_issues = mesh_health_actionable_issues(after_fix)
    return MeshRepairOutcome(
        repaired=repaired,
        fully_repaired=not mesh_health_requires_fix(after_fix),
        before_fix=before_fix,
        after_fix=after_fix,
        resolved_issues=[issue for issue in before_issues if issue not in after_issues],
        remaining_issues=after_issues,
        repair_actions=list(repair_actions),
    )


def _load_mesh(file_path: str) -> trimesh.Trimesh:
    loaded = trimesh.load(file_path, force="mesh")
    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise ValueError("No mesh geometry found in file.")
        mesh = trimesh.util.concatenate(meshes)
    elif isinstance(loaded, trimesh.Trimesh):
        mesh = loaded
    else:
        raise ValueError("Unsupported geometry format.")

    if mesh.is_empty:
        raise ValueError("Loaded mesh is empty.")

    return mesh


def _load_and_prepare_mesh(
    file_path: str,
    auto_repair: bool,
    lightweight_health: bool = False,
) -> tuple[trimesh.Trimesh, MeshHealthReport]:
    mesh = _load_mesh(file_path)
    repair_actions: list[str] = []
    repaired = False
    if auto_repair:
        repaired, repair_actions = _repair_mesh(mesh)
    if lightweight_health:
        health = _mesh_health_report_fast(
            mesh=mesh,
            repaired=repaired,
            repair_actions=repair_actions,
        )
    else:
        health = _mesh_health_report(
            mesh=mesh,
            repaired=repaired,
            repair_actions=repair_actions,
        )
    return mesh, health


def _requested_mesh_repair_backend(backend: str | None = None) -> str:
    candidate = str(backend or os.getenv("RESINLOGIC_MESH_REPAIR_BACKEND", _DEFAULT_MESH_REPAIR_BACKEND)).strip().lower()
    if candidate in _SUPPORTED_MESH_REPAIR_BACKENDS:
        return candidate
    return _DEFAULT_MESH_REPAIR_BACKEND


def _load_pymeshlab_module():
    import pymeshlab

    return pymeshlab


def _load_open3d_module():
    import open3d as o3d

    return o3d


def _replace_mesh_geometry(target: trimesh.Trimesh, source: trimesh.Trimesh) -> None:
    target.vertices = np.asarray(source.vertices, dtype=float).copy()
    target.faces = np.asarray(source.faces, dtype=np.int64).copy()


def _requested_voxel_backend(backend: str | None = None) -> str:
    candidate = str(backend or os.getenv("RESINLOGIC_GEOMETRY_VOXEL_BACKEND", _DEFAULT_VOXEL_BACKEND)).strip().lower()
    if candidate in _SUPPORTED_VOXEL_BACKENDS:
        return candidate
    return _DEFAULT_VOXEL_BACKEND


def _mesh_health_report(
    mesh: trimesh.Trimesh,
    *,
    repaired: bool,
    repair_actions: list[str],
) -> MeshHealthReport:
    boundary_edges, non_manifold_edges = _edge_health(mesh)
    degenerate_faces = _degenerate_face_count(mesh)
    duplicate_faces = _duplicate_face_count(mesh)
    components = _component_count(mesh)

    issues: list[str] = []
    if boundary_edges > 0:
        issues.append(f"Open boundaries detected ({boundary_edges} boundary edges).")
    if non_manifold_edges > 0:
        issues.append(f"Non-manifold topology detected ({non_manifold_edges} non-manifold edges).")
    if components > 1:
        issues.append(f"Multiple disconnected shells detected ({components} components).")
    if degenerate_faces > 0:
        issues.append(f"Degenerate triangles detected ({degenerate_faces} faces).")
    if duplicate_faces > 0:
        issues.append(f"Duplicate triangles detected ({duplicate_faces} faces).")
    if not mesh.is_winding_consistent:
        issues.append("Face winding is inconsistent.")
    if not mesh.is_watertight:
        issues.append("Mesh is not watertight.")
    if not issues:
        issues.append("No major mesh topology issues detected.")

    return MeshHealthReport(
        watertight=bool(mesh.is_watertight),
        winding_consistent=bool(mesh.is_winding_consistent),
        volume_consistent=bool(mesh.is_volume),
        connected_components=components,
        boundary_edge_count=boundary_edges,
        non_manifold_edge_count=non_manifold_edges,
        degenerate_face_count=degenerate_faces,
        duplicate_face_count=duplicate_faces,
        repaired=repaired,
        issues=issues,
        repair_actions=repair_actions,
    )


def _mesh_health_report_fast(
    mesh: trimesh.Trimesh,
    *,
    repaired: bool,
    repair_actions: list[str],
) -> MeshHealthReport:
    # Fast path for very large models: avoid expensive per-edge topology scans.
    degenerate_faces = _degenerate_face_count(mesh)
    duplicate_faces = _duplicate_face_count(mesh)

    try:
        watertight = bool(mesh.is_watertight)
    except Exception:
        watertight = False
    try:
        winding_consistent = bool(mesh.is_winding_consistent)
    except Exception:
        winding_consistent = True
    try:
        volume_consistent = bool(mesh.is_volume) if watertight else False
    except Exception:
        volume_consistent = False

    issues: list[str] = []
    if not watertight:
        issues.append("Mesh may not be watertight (fast check).")
    if not winding_consistent:
        issues.append("Face winding may be inconsistent (fast check).")
    if degenerate_faces > 0:
        issues.append(f"Degenerate triangles detected ({degenerate_faces} faces).")
    if duplicate_faces > 0:
        issues.append(f"Duplicate triangles detected ({duplicate_faces} faces).")
    if not issues:
        issues.append("No major mesh topology issues detected (fast mode).")
    issues.append("Minimum analysis mode: detailed edge/connectivity scans were skipped for speed.")

    return MeshHealthReport(
        watertight=watertight,
        winding_consistent=winding_consistent,
        volume_consistent=volume_consistent,
        connected_components=None,
        boundary_edge_count=None,
        non_manifold_edge_count=None,
        degenerate_face_count=degenerate_faces,
        duplicate_face_count=duplicate_faces,
        repaired=repaired,
        issues=issues,
        repair_actions=repair_actions,
    )


def _repair_mesh(mesh: trimesh.Trimesh, backend: str | None = None) -> tuple[bool, list[str]]:
    selected_backend = _requested_mesh_repair_backend(backend)
    if selected_backend == "pymeshlab":
        try:
            return _repair_mesh_with_pymeshlab(mesh)
        except ModuleNotFoundError:
            repaired, actions = _repair_mesh_with_trimesh(mesh)
            actions.append("PyMeshLab repair backend was requested but is not installed; fell back to Trimesh repair.")
            return repaired, actions
        except Exception as exc:  # noqa: BLE001
            repaired, actions = _repair_mesh_with_trimesh(mesh)
            actions.append(f"PyMeshLab repair backend failed and fell back to Trimesh repair: {exc}")
            return repaired, actions
    return _repair_mesh_with_trimesh(mesh)


def _repair_mesh_with_pymeshlab(mesh: trimesh.Trimesh) -> tuple[bool, list[str]]:
    pymeshlab = _load_pymeshlab_module()
    actions: list[str] = []
    before_faces = int(len(mesh.faces))
    before_vertices = int(len(mesh.vertices))
    before_watertight = bool(mesh.is_watertight)

    with TemporaryDirectory(prefix="resinlogic-mesh-repair-") as temp_dir:
        input_path = Path(temp_dir) / "repair_input.stl"
        output_path = Path(temp_dir) / "repair_output.stl"
        mesh.export(input_path, file_type="stl")

        meshset = pymeshlab.MeshSet()
        meshset.load_new_mesh(str(input_path))
        filter_pipeline = (
            ("meshing_remove_duplicate_vertices", {}),
            ("meshing_remove_duplicate_faces", {}),
            ("meshing_remove_null_faces", {}),
            ("meshing_repair_non_manifold_edges", {}),
            ("meshing_repair_non_manifold_vertices", {}),
            ("meshing_close_holes", {"maxholesize": 400, "selfintersection": True}),
            ("meshing_remove_unreferenced_vertices", {}),
            ("meshing_re_orient_faces_coherently", {}),
            ("compute_normal_per_vertex", {}),
        )
        applied_filters: list[str] = []
        for filter_name, kwargs in filter_pipeline:
            try:
                meshset.apply_filter(filter_name, **kwargs)
                applied_filters.append(filter_name)
            except Exception:
                continue

        meshset.save_current_mesh(str(output_path))
        repaired_mesh = _load_mesh(str(output_path))

    _replace_mesh_geometry(mesh, repaired_mesh)

    duplicate_removed = _remove_duplicate_faces(mesh)
    if duplicate_removed:
        actions.append(f"Removed {duplicate_removed} duplicate triangles after PyMeshLab repair.")

    degenerate_removed = _remove_degenerate_faces(mesh)
    if degenerate_removed:
        actions.append(f"Removed {degenerate_removed} degenerate triangles after PyMeshLab repair.")

    try:
        mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        mesh.merge_vertices()
    except Exception:
        pass
    try:
        trimesh.repair.fix_normals(mesh, multibody=True)
    except Exception:
        pass
    try:
        trimesh.repair.fix_inversion(mesh, multibody=True)
    except TypeError:
        try:
            trimesh.repair.fix_inversion(mesh)
        except Exception:
            pass
    except Exception:
        pass

    after_faces = int(len(mesh.faces))
    after_vertices = int(len(mesh.vertices))
    after_watertight = bool(mesh.is_watertight)
    repaired = (
        duplicate_removed > 0
        or degenerate_removed > 0
        or after_faces != before_faces
        or after_vertices != before_vertices
        or after_watertight != before_watertight
    )
    if repaired:
        actions.insert(0, "Applied PyMeshLab prototype repair pipeline.")
        if applied_filters:
            actions.append(f"PyMeshLab filters: {', '.join(applied_filters)}.")
    return repaired, actions


def _repair_mesh_with_trimesh(mesh: trimesh.Trimesh) -> tuple[bool, list[str]]:
    actions: list[str] = []
    before_faces = int(len(mesh.faces))
    before_vertices = int(len(mesh.vertices))
    before_watertight = bool(mesh.is_watertight)

    duplicate_removed = _remove_duplicate_faces(mesh)
    if duplicate_removed:
        actions.append(f"Removed {duplicate_removed} duplicate triangles.")

    degenerate_removed = _remove_degenerate_faces(mesh)
    if degenerate_removed:
        actions.append(f"Removed {degenerate_removed} degenerate triangles.")

    try:
        holes_filled = bool(trimesh.repair.fill_holes(mesh))
    except Exception:
        holes_filled = False
    if holes_filled:
        actions.append("Filled one or more mesh holes.")

    try:
        trimesh.repair.fix_normals(mesh, multibody=True)
    except Exception:
        pass
    try:
        trimesh.repair.fix_inversion(mesh, multibody=True)
    except TypeError:
        try:
            trimesh.repair.fix_inversion(mesh)
        except Exception:
            pass
    except Exception:
        pass

    # Some repair operations can introduce or expose duplicate or degenerate faces again.
    duplicate_removed_after_fill = _remove_duplicate_faces(mesh)
    if duplicate_removed_after_fill:
        actions.append(f"Removed {duplicate_removed_after_fill} duplicate triangles after hole repair.")

    degenerate_removed_after_fill = _remove_degenerate_faces(mesh)
    if degenerate_removed_after_fill:
        actions.append(f"Removed {degenerate_removed_after_fill} degenerate triangles after hole repair.")

    try:
        mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        mesh.merge_vertices()
    except Exception:
        pass

    after_faces = int(len(mesh.faces))
    after_vertices = int(len(mesh.vertices))
    after_watertight = bool(mesh.is_watertight)
    repaired = (
        duplicate_removed > 0
        or degenerate_removed > 0
        or duplicate_removed_after_fill > 0
        or degenerate_removed_after_fill > 0
        or holes_filled
        or after_faces != before_faces
        or after_vertices != before_vertices
        or after_watertight != before_watertight
    )
    return repaired, actions


def _component_count(mesh: trimesh.Trimesh) -> int:
    try:
        return len(mesh.split(only_watertight=False))
    except Exception:
        return 1 if len(mesh.faces) else 0


def _edge_health(mesh: trimesh.Trimesh) -> tuple[int, int]:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.size == 0:
        return 0, 0
    edges = np.vstack(
        (
            faces[:, [0, 1]],
            faces[:, [1, 2]],
            faces[:, [2, 0]],
        )
    )
    edges = np.sort(edges, axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = int(np.sum(counts == 1))
    non_manifold_edges = int(np.sum(counts > 2))
    return boundary_edges, non_manifold_edges


def _duplicate_face_count(mesh: trimesh.Trimesh) -> int:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.size == 0:
        return 0
    canonical = np.sort(faces, axis=1)
    unique_count = int(np.unique(canonical, axis=0).shape[0])
    return int(len(canonical) - unique_count)


def _degenerate_face_count(mesh: trimesh.Trimesh) -> int:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.size == 0:
        return 0
    valid_mask = (
        (faces[:, 0] != faces[:, 1])
        & (faces[:, 1] != faces[:, 2])
        & (faces[:, 0] != faces[:, 2])
    )
    return int(len(faces) - int(np.sum(valid_mask)))


def _remove_duplicate_faces(mesh: trimesh.Trimesh) -> int:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.size == 0:
        return 0
    canonical = np.sort(faces, axis=1)
    _, unique_indices = np.unique(canonical, axis=0, return_index=True)
    if len(unique_indices) == len(faces):
        return 0
    keep = np.zeros(len(faces), dtype=bool)
    keep[np.sort(unique_indices)] = True
    mesh.update_faces(keep)
    return int(len(faces) - len(unique_indices))


def _remove_degenerate_faces(mesh: trimesh.Trimesh) -> int:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.size == 0:
        return 0
    keep = (
        (faces[:, 0] != faces[:, 1])
        & (faces[:, 1] != faces[:, 2])
        & (faces[:, 0] != faces[:, 2])
    )
    removed = int(len(faces) - int(np.sum(keep)))
    if removed > 0:
        mesh.update_faces(keep)
    return removed


def _analysis_detail_notes(
    *,
    slice_areas: list[SliceArea],
    build_plate_area_mm2: float,
    suction_cups: list[Cavity],
    islands: list[Island],
    curvature_proxy: float | None,
) -> list[str]:
    notes: list[str] = []

    if slice_areas:
        peak_slice = max(slice_areas, key=lambda area: area.area_mm2)
        plate_ratio = peak_slice.area_mm2 / build_plate_area_mm2 if build_plate_area_mm2 > 0 else 0.0
        notes.append(
            f"Peak cross-section measured {round(peak_slice.area_mm2, 2)} mm^2 at z={round(peak_slice.z_mm, 3)}mm "
            f"({round(plate_ratio * 100.0, 1)}% of the build plate)."
        )

    if suction_cups:
        total_cavity_volume = float(sum(cavity.volume_mm3 for cavity in suction_cups))
        largest_cavity = max(suction_cups, key=lambda cavity: cavity.volume_mm3)
        confidence_counts = {
            level: sum(1 for cavity in suction_cups if cavity.confidence_level == level)
            for level in ("high", "medium", "low")
        }
        confidence_parts = [
            f"{count} {level}-confidence"
            for level, count in confidence_counts.items()
            if count > 0
        ]
        confidence_text = f" ({', '.join(confidence_parts)})" if confidence_parts else ""
        centroid = largest_cavity.centroid_mm or []
        centroid_text = ""
        if len(centroid) == 3:
            centroid_text = (
                f" near [{round(float(centroid[0]), 2)}, {round(float(centroid[1]), 2)}, {round(float(centroid[2]), 2)}]"
            )
        notes.append(
            f"Detected {len(suction_cups)} trapped-resin cavity candidates{confidence_text} totaling {round(total_cavity_volume, 2)} mm^3; "
            f"largest cavity was {round(largest_cavity.volume_mm3, 2)} mm^3{centroid_text}."
        )

    if islands:
        total_unsupported_voxels = int(
            sum((island.total_voxel_count or island.voxel_count) for island in islands)
        )
        layers_with_islands = len(
            {
                layer_index
                for island in islands
                for layer_index in range(
                    island.layer_index,
                    (island.end_layer_index if island.end_layer_index is not None else island.layer_index) + 1,
                )
            }
        )
        largest_island = max(islands, key=lambda island: island.total_voxel_count or island.voxel_count)
        span_text = ""
        if (largest_island.layer_span or 1) > 1 and largest_island.z_end_mm is not None:
            span_text = (
                f" spanning {largest_island.layer_span} layers from z={round(largest_island.z_mm, 3)}mm "
                f"to z={round(largest_island.z_end_mm, 3)}mm"
            )
        notes.append(
            f"Detected {len(islands)} unsupported island regions across {layers_with_islands} layers; "
            f"largest island region accumulated {largest_island.total_voxel_count or largest_island.voxel_count} voxels{span_text}; "
            f"({total_unsupported_voxels} unsupported voxels total)."
        )

    if curvature_proxy is not None:
        notes.append(
            f"Surface detail proxy measured {round(float(curvature_proxy), 4)} mean curvature magnitude."
        )

    return notes


def _approximate_surface_span_slice_bins(
    *,
    vertices: np.ndarray,
    faces: np.ndarray,
    z_min: float,
    effective_slice_mm: float,
    bin_count: int,
    stride: int,
    cancel_check: AnalysisCancelCheck | None = None,
) -> np.ndarray:
    if bin_count <= 0 or faces.size == 0:
        return np.zeros(max(bin_count, 0), dtype=float)

    sampled_faces = faces[::stride]
    if sampled_faces.size == 0:
        return np.zeros(bin_count, dtype=float)

    span_bins = np.zeros(bin_count, dtype=float)
    centroid_bins = np.zeros(bin_count, dtype=float)
    batch_size = 200_000

    for start_index in range(0, len(sampled_faces), batch_size):
        _check_cancel(cancel_check)
        batch_faces = sampled_faces[start_index : start_index + batch_size]
        triangle_vertices = vertices[batch_faces]
        edge_a = triangle_vertices[:, 1] - triangle_vertices[:, 0]
        edge_b = triangle_vertices[:, 2] - triangle_vertices[:, 0]
        normals = np.cross(edge_a, edge_b)
        double_area = np.linalg.norm(normals, axis=1)
        valid = double_area > 1e-12
        if not np.any(valid):
            continue

        triangle_vertices = triangle_vertices[valid]
        normals = normals[valid]
        double_area = double_area[valid]
        face_area = 0.5 * double_area
        face_verticality = np.sqrt(
            np.clip(1.0 - np.square(np.abs(normals[:, 2]) / double_area), 0.0, 1.0)
        )
        weighted_area = face_area * (0.18 + (0.82 * face_verticality)) * float(stride)

        z_face_min = triangle_vertices[:, :, 2].min(axis=1)
        z_face_max = triangle_vertices[:, :, 2].max(axis=1)
        start_bin = np.floor((z_face_min - z_min) / effective_slice_mm).astype(np.int64)
        end_bin = np.floor((z_face_max - z_min) / effective_slice_mm).astype(np.int64)
        start_bin = np.clip(start_bin, 0, bin_count - 1)
        end_bin = np.clip(end_bin, 0, bin_count - 1)

        cover_count = np.maximum(end_bin - start_bin + 1, 1)
        per_bin_weight = weighted_area / cover_count
        diff = np.zeros(bin_count + 1, dtype=float)
        np.add.at(diff, start_bin, per_bin_weight)
        np.add.at(diff, np.minimum(end_bin + 1, bin_count), -per_bin_weight)
        span_bins += np.cumsum(diff[:-1])

        z_centers = triangle_vertices[:, :, 2].mean(axis=1)
        centroid_index = np.floor((z_centers - z_min) / effective_slice_mm).astype(np.int64)
        centroid_index = np.clip(centroid_index, 0, bin_count - 1)
        np.add.at(centroid_bins, centroid_index, weighted_area)

    if bin_count >= 3:
        kernel = np.array([0.15, 0.7, 0.15], dtype=float)
        span_bins = np.convolve(span_bins, kernel, mode="same")
        centroid_bins = np.convolve(centroid_bins, kernel, mode="same")

    span_peak = float(np.max(span_bins)) if span_bins.size else 0.0
    centroid_peak = float(np.max(centroid_bins)) if centroid_bins.size else 0.0
    if span_peak > 0.0:
        span_bins = span_bins / span_peak
    if centroid_peak > 0.0:
        centroid_bins = centroid_bins / centroid_peak

    if span_peak > 0.0 and centroid_peak > 0.0:
        return np.clip((0.65 * span_bins) + (0.35 * centroid_bins), 0.0, 1.0)
    if span_peak > 0.0:
        return np.clip(span_bins, 0.0, 1.0)
    if centroid_peak > 0.0:
        return np.clip(centroid_bins, 0.0, 1.0)
    return np.zeros(bin_count, dtype=float)


def _shoelace_area(points: np.ndarray) -> float:
    if points.shape[0] < 3:
        return 0.0
    x = points[:, 0]
    y = points[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _section_area(section: object | None) -> float:
    if section is None:
        return 0.0

    if hasattr(section, "to_planar"):
        planar, _ = section.to_planar()
    else:
        planar = section
    try:
        polygons_full = getattr(planar, "polygons_full", None)
        if polygons_full:
            return float(sum(poly.area for poly in polygons_full))

        area = 0.0
        for loop in planar.discrete:
            area += _shoelace_area(np.asarray(loop, dtype=float))
        return float(area)
    except ModuleNotFoundError:
        return _convex_area_from_vertices(planar)


def _convex_area_from_vertices(planar: object) -> float:
    vertices = np.asarray(getattr(planar, "vertices", []), dtype=float)
    if vertices.shape[0] < 3:
        return 0.0

    points_2d = vertices[:, :2]
    try:
        from scipy.spatial import ConvexHull

        hull = ConvexHull(points_2d)
        hull_points = points_2d[hull.vertices]
        return float(_shoelace_area(hull_points))
    except Exception:
        return 0.0


def _cross_section_areas(
    mesh: trimesh.Trimesh,
    slice_height_mm: float,
    max_slices: int,
    progress_callback: AnalysisProgressCallback | None = None,
    cancel_check: AnalysisCancelCheck | None = None,
) -> _CrossSectionResult:
    z_min, z_max = mesh.bounds[:, 2]
    height = max(0.0, float(z_max - z_min))
    requested_slices = int(math.floor(height / slice_height_mm)) + 1
    effective_slice = slice_height_mm
    notes: list[str] = []

    if requested_slices > max_slices:
        factor = math.ceil(requested_slices / max_slices)
        effective_slice = slice_height_mm * factor
        notes.append(
            f"Requested {requested_slices} slices at {slice_height_mm}mm; "
            f"auto-raised slice to {effective_slice}mm to cap runtime."
        )

    heights = np.arange(0.0, height + effective_slice, effective_slice)
    face_count = int(len(mesh.faces))
    if face_count > 1_200_000 or len(heights) > 9000:
        notes.append(
            "Large model detected; switched to voxel-based cross-section estimation for faster runtime."
        )
        return _cross_section_areas_voxel(
            mesh=mesh,
            z_min=float(z_min),
            heights=heights,
            requested_slice_mm=effective_slice,
            notes=notes,
            min_voxel_pitch_mm=max(effective_slice * 1.5, 0.15),
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

    try:
        return _cross_section_areas_multiplane(
            mesh=mesh,
            z_min=float(z_min),
            heights=heights,
            effective_slice_mm=effective_slice,
            notes=notes,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
    except ModuleNotFoundError as exc:
        missing_hint = f"{exc.name or ''} {exc}".lower()
        if "scipy" not in missing_hint:
            raise
        notes.append("scipy not available; switched to voxel-based cross-section estimation.")
        return _cross_section_areas_voxel(
            mesh=mesh,
            z_min=float(z_min),
            heights=heights,
            requested_slice_mm=effective_slice,
            notes=notes,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )


def _cross_section_areas_multiplane(
    mesh: trimesh.Trimesh,
    z_min: float,
    heights: np.ndarray,
    effective_slice_mm: float,
    notes: list[str],
    progress_callback: AnalysisProgressCallback | None = None,
    cancel_check: AnalysisCancelCheck | None = None,
) -> _CrossSectionResult:
    slice_areas: list[SliceArea] = []
    max_area = 0.0
    total_slices = len(heights)
    batch_size = max(1, min(256, total_slices))

    for start_index in range(0, total_slices, batch_size):
        _check_cancel(cancel_check)
        end_index = min(start_index + batch_size, total_slices)
        batch_heights = heights[start_index:end_index]
        if total_slices > batch_size:
            _report_progress(
                progress_callback,
                "cross_section",
                f"Computing cross-sections through the model. Processed {start_index}/{total_slices} slice planes.",
            )
        sections = mesh.section_multiplane(
            plane_origin=[0.0, 0.0, float(z_min)],
            plane_normal=[0.0, 0.0, 1.0],
            heights=batch_heights,
        )
        for offset, section in zip(batch_heights, sections):
            area = _section_area(section)
            max_area = max(max_area, area)
            slice_areas.append(
                SliceArea(
                    z_mm=float(round(float(z_min + offset), 5)),
                    area_mm2=float(area),
                )
            )
        if total_slices > batch_size:
            _report_progress(
                progress_callback,
                "cross_section",
                f"Computing cross-sections through the model. Processed {end_index}/{total_slices} slice planes.",
            )

    return _CrossSectionResult(
        slice_areas=slice_areas,
        max_area_mm2=max_area,
        effective_slice_height_mm=effective_slice_mm,
        notes=notes,
    )


def _cross_section_areas_fast(
    mesh: trimesh.Trimesh,
    slice_height_mm: float,
    max_slices: int,
    progress_callback: AnalysisProgressCallback | None = None,
    cancel_check: AnalysisCancelCheck | None = None,
) -> _CrossSectionResult:
    z_min, z_max = mesh.bounds[:, 2]
    height = max(0.0, float(z_max - z_min))
    if height <= 0.0:
        return _CrossSectionResult(
            slice_areas=[SliceArea(z_mm=float(round(float(z_min), 5)), area_mm2=0.0)],
            max_area_mm2=0.0,
            effective_slice_height_mm=slice_height_mm,
            notes=["Model has zero Z height; cross-section area is zero."],
        )

    safe_slice = max(float(slice_height_mm), 1e-6)
    requested_slices = int(math.floor(height / safe_slice)) + 1
    effective_slice = safe_slice
    notes: list[str] = []

    if requested_slices > max_slices:
        factor = math.ceil(requested_slices / max_slices)
        effective_slice = safe_slice * factor
        notes.append(
            f"Requested {requested_slices} slices at {slice_height_mm}mm; "
            f"auto-raised slice to {effective_slice}mm to cap runtime."
        )

    fast_floor = 0.3
    if effective_slice < fast_floor:
        effective_slice = fast_floor
        notes.append(
            f"Minimum analysis raised slice spacing to {effective_slice}mm for faster processing."
        )

    heights = np.arange(0.0, height + effective_slice, effective_slice)
    if len(heights) > max_slices:
        heights = heights[:max_slices]
        notes.append(
            f"Minimum analysis truncated slice sampling to {len(heights)} slices."
        )

    face_count = int(len(mesh.faces))
    if face_count <= 80_000 and len(heights) <= 240:
        notes.append("Minimum analysis used exact multiplane slicing because the mesh is small enough.")
        try:
            return _cross_section_areas_multiplane(
                mesh=mesh,
                z_min=float(z_min),
                heights=heights,
                effective_slice_mm=effective_slice,
                notes=notes,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
        except ModuleNotFoundError as exc:
            missing_hint = f"{exc.name or ''} {exc}".lower()
            if "scipy" not in missing_hint:
                raise
            notes.append("scipy not available; minimum analysis kept the lightweight cross-section approximation.")

    _check_cancel(cancel_check)
    notes.append("Minimum analysis: using lightweight surface-span weighted cross-section approximation.")
    _report_progress(
        progress_callback,
        "cross_section",
        f"Approximating cross-sections from surface span coverage across {len(heights)} slice planes.",
    )

    if face_count == 0:
        empty_slices = [
            SliceArea(z_mm=float(round(float(z_min + h), 5)), area_mm2=0.0) for h in heights
        ]
        return _CrossSectionResult(
            slice_areas=empty_slices,
            max_area_mm2=0.0,
            effective_slice_height_mm=effective_slice,
            notes=notes,
        )

    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    x_min, y_min, _ = mesh.bounds[0]
    x_max, y_max, _ = mesh.bounds[1]
    xy_area_mm2 = max(0.0, float((x_max - x_min) * (y_max - y_min)))
    if xy_area_mm2 <= 0.0:
        empty_slices = [
            SliceArea(z_mm=float(round(float(z_min + h), 5)), area_mm2=0.0) for h in heights
        ]
        return _CrossSectionResult(
            slice_areas=empty_slices,
            max_area_mm2=0.0,
            effective_slice_height_mm=effective_slice,
            notes=notes,
        )

    sample_limit = 1_200_000
    stride = 1
    if face_count > sample_limit:
        stride = int(math.ceil(face_count / sample_limit))
        notes.append(
            f"Minimum analysis sampled faces at stride {stride} to limit memory on very large meshes."
        )

    bin_count = len(heights)
    bins = _approximate_surface_span_slice_bins(
        vertices=vertices,
        faces=faces,
        z_min=float(z_min),
        effective_slice_mm=effective_slice,
        bin_count=bin_count,
        stride=stride,
        cancel_check=cancel_check,
    )

    max_bin = float(np.max(bins)) if bins.size else 0.0
    if max_bin <= 0.0:
        approx_areas = np.zeros(bin_count, dtype=float)
    else:
        occupancy = bins / max_bin
        bbox_volume_mm3 = max(
            1e-6,
            float((x_max - x_min) * (y_max - y_min) * (z_max - z_min)),
        )
        fill_ratio = float(np.clip(abs(float(mesh.volume)) / bbox_volume_mm3, 0.03, 0.98))
        area_factor = 0.25 + (0.75 * fill_ratio)
        approx_areas = xy_area_mm2 * occupancy * area_factor

    requested_z = float(z_min) + heights
    slice_areas = [
        SliceArea(
            z_mm=float(round(float(z), 5)),
            area_mm2=float(area),
        )
        for z, area in zip(requested_z, approx_areas)
    ]
    max_area = float(np.max(approx_areas)) if len(approx_areas) else 0.0
    return _CrossSectionResult(
        slice_areas=slice_areas,
        max_area_mm2=max_area,
        effective_slice_height_mm=effective_slice,
        notes=notes,
    )


def _cross_section_areas_voxel(
    mesh: trimesh.Trimesh,
    z_min: float,
    heights: np.ndarray,
    requested_slice_mm: float,
    notes: list[str],
    min_voxel_pitch_mm: float = 0.05,
    progress_callback: AnalysisProgressCallback | None = None,
    cancel_check: AnalysisCancelCheck | None = None,
) -> _CrossSectionResult:
    voxel_pitch = max(requested_slice_mm, min_voxel_pitch_mm)
    if voxel_pitch > requested_slice_mm:
        notes.append(
            f"Voxel fallback used {voxel_pitch}mm pitch for speed; per-slice areas are interpolated."
        )

    _check_cancel(cancel_check)
    _report_progress(
        progress_callback,
        "cross_section",
        f"Cross-section fallback is voxelizing at {round(voxel_pitch, 4)}mm pitch for {len(heights)} slice planes.",
    )
    voxel, occupied = _voxelize_mesh(
        mesh,
        pitch_mm=voxel_pitch,
        cancel_check=cancel_check,
    )
    if occupied.size == 0:
        empty_slices = [
            SliceArea(z_mm=float(round(float(z_min + h), 5)), area_mm2=0.0) for h in heights
        ]
        return _CrossSectionResult(
            slice_areas=empty_slices,
            max_area_mm2=0.0,
            effective_slice_height_mm=requested_slice_mm,
            notes=notes,
            voxel_data=(voxel, occupied),
            voxel_pitch_mm=voxel_pitch,
        )

    layer_area_mm2 = occupied.sum(axis=(0, 1)).astype(float) * (voxel_pitch ** 2)
    z_indices = np.arange(occupied.shape[2], dtype=float)
    z_world = _indices_to_points(voxel, np.column_stack([np.zeros_like(z_indices), np.zeros_like(z_indices), z_indices]))[
        :, 2
    ]

    requested_z = z_min + heights
    interpolated_area = np.interp(requested_z, z_world, layer_area_mm2, left=0.0, right=0.0)
    slice_areas = [
        SliceArea(
            z_mm=float(round(float(z), 5)),
            area_mm2=float(area),
        )
        for z, area in zip(requested_z, interpolated_area)
    ]
    max_area = float(np.max(interpolated_area)) if len(interpolated_area) else 0.0
    return _CrossSectionResult(
        slice_areas=slice_areas,
        max_area_mm2=max_area,
        effective_slice_height_mm=requested_slice_mm,
        notes=notes,
        voxel_data=(voxel, occupied),
        voxel_pitch_mm=voxel_pitch,
    )


def _indices_to_points(voxel_grid: object, indices: np.ndarray) -> np.ndarray:
    if hasattr(voxel_grid, "indices_to_points"):
        return voxel_grid.indices_to_points(indices)

    transform = voxel_grid.transform
    homogeneous = np.column_stack([indices, np.ones(len(indices), dtype=float)])
    points = (transform @ homogeneous.T).T[:, :3]
    return points


def _voxelize_mesh(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    cancel_check: AnalysisCancelCheck | None = None,
) -> tuple[object, np.ndarray]:
    selected_backend = _requested_voxel_backend()
    if selected_backend == "open3d":
        try:
            return _voxelize_mesh_with_open3d(mesh, pitch_mm=pitch_mm, cancel_check=cancel_check)
        except ModuleNotFoundError:
            return _voxelize_mesh_with_trimesh(mesh, pitch_mm=pitch_mm, cancel_check=cancel_check)
    return _voxelize_mesh_with_trimesh(mesh, pitch_mm=pitch_mm, cancel_check=cancel_check)


def _voxelize_mesh_with_trimesh(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    cancel_check: AnalysisCancelCheck | None = None,
) -> tuple[trimesh.voxel.VoxelGrid, np.ndarray]:
    _check_cancel(cancel_check)
    voxel = mesh.voxelized(pitch=pitch_mm)
    _check_cancel(cancel_check)
    occupied = np.asarray(voxel.matrix, dtype=bool)
    return voxel, occupied


def _voxelize_mesh_with_open3d(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    cancel_check: AnalysisCancelCheck | None = None,
) -> tuple[_Open3DVoxelGridAdapter, np.ndarray]:
    _check_cancel(cancel_check)
    o3d = _load_open3d_module()

    triangle_mesh = o3d.geometry.TriangleMesh()
    triangle_mesh.vertices = o3d.utility.Vector3dVector(np.asarray(mesh.vertices, dtype=float))
    triangle_mesh.triangles = o3d.utility.Vector3iVector(np.asarray(mesh.faces, dtype=np.int32))
    voxel_grid = o3d.geometry.VoxelGrid.create_from_triangle_mesh(triangle_mesh, voxel_size=float(pitch_mm))

    _check_cancel(cancel_check)
    voxels = list(voxel_grid.get_voxels()) if voxel_grid is not None else []
    origin_mm = np.asarray(getattr(voxel_grid, "origin", np.zeros(3, dtype=float)), dtype=float)
    if not voxels:
        return (
            _Open3DVoxelGridAdapter(
                origin_mm=origin_mm,
                index_offset=np.zeros(3, dtype=np.int32),
                voxel_size_mm=float(pitch_mm),
            ),
            np.zeros((0, 0, 0), dtype=bool),
        )

    grid_indices = np.asarray([np.asarray(voxel.grid_index, dtype=np.int32) for voxel in voxels], dtype=np.int32)
    min_index = grid_indices.min(axis=0)
    max_index = grid_indices.max(axis=0)
    occupied_shape = tuple((max_index - min_index + 1).tolist())
    occupied = np.zeros(occupied_shape, dtype=bool)
    local_indices = grid_indices - min_index.reshape(1, 3)
    occupied[local_indices[:, 0], local_indices[:, 1], local_indices[:, 2]] = True

    try:
        tensor_mesh = o3d.t.geometry.TriangleMesh.from_legacy(triangle_mesh)
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(tensor_mesh)

        x_indices = np.arange(min_index[0], max_index[0] + 1, dtype=np.float32)
        y_indices = np.arange(min_index[1], max_index[1] + 1, dtype=np.float32)
        z_indices = np.arange(min_index[2], max_index[2] + 1, dtype=np.float32)
        query_indices = np.stack(np.meshgrid(x_indices, y_indices, z_indices, indexing="ij"), axis=-1)
        query_points = origin_mm.astype(np.float32).reshape(1, 1, 1, 3) + ((query_indices + 0.5) * np.float32(pitch_mm))
        solid_occupied = np.asarray(
            scene.compute_occupancy(o3d.core.Tensor(query_points), nsamples=3).numpy(),
            dtype=bool,
        )
        occupied |= solid_occupied
    except Exception:
        pass

    _check_cancel(cancel_check)

    return (
        _Open3DVoxelGridAdapter(
            origin_mm=origin_mm,
            index_offset=min_index.astype(np.int32),
            voxel_size_mm=float(pitch_mm),
        ),
        occupied,
    )


def _suction_cup_component_metrics(component_indices: np.ndarray) -> _SuctionCupComponentMetrics | None:
    if component_indices.size == 0:
        return None

    x_span_voxels = int(component_indices[:, 0].max() - component_indices[:, 0].min() + 1)
    y_span_voxels = int(component_indices[:, 1].max() - component_indices[:, 1].min() + 1)
    z_span_voxels = int(component_indices[:, 2].max() - component_indices[:, 2].min() + 1)
    footprint_voxel_count = int(len(np.unique(component_indices[:, :2], axis=0)))
    widest_xy_span_voxels = max(x_span_voxels, y_span_voxels)
    footprint_to_depth_ratio = float(footprint_voxel_count / max(z_span_voxels, 1))

    return _SuctionCupComponentMetrics(
        x_span_voxels=x_span_voxels,
        y_span_voxels=y_span_voxels,
        z_span_voxels=z_span_voxels,
        footprint_voxel_count=footprint_voxel_count,
        widest_xy_span_voxels=widest_xy_span_voxels,
        footprint_to_depth_ratio=footprint_to_depth_ratio,
    )


def _classify_suction_cup_confidence(score: float) -> CavityConfidenceLevel:
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _suction_cup_confidence(
    metrics: _SuctionCupComponentMetrics,
    *,
    pitch_mm: float,
) -> tuple[float, CavityConfidenceLevel, float, float]:
    footprint_area_mm2 = float(metrics.footprint_voxel_count * (pitch_mm ** 2))
    z_span_mm = float(metrics.z_span_voxels * pitch_mm)
    footprint_score = float(np.clip(metrics.footprint_voxel_count / 6.0, 0.0, 1.0))
    footprint_ratio_score = float(np.clip((metrics.footprint_to_depth_ratio - 1.0) / 2.5, 0.0, 1.0))
    span_ratio = float(metrics.widest_xy_span_voxels / max(metrics.z_span_voxels, 1))
    span_score = float(np.clip((span_ratio - 0.75) / 1.5, 0.0, 1.0))
    confidence_score = round(
        (0.45 * footprint_score)
        + (0.35 * footprint_ratio_score)
        + (0.20 * span_score),
        3,
    )
    return (
        confidence_score,
        _classify_suction_cup_confidence(confidence_score),
        footprint_area_mm2,
        z_span_mm,
    )


def _is_plausible_suction_cup_component(
    component_indices: np.ndarray,
    metrics: _SuctionCupComponentMetrics | None = None,
) -> bool:
    metrics = metrics or _suction_cup_component_metrics(component_indices)
    if metrics is None:
        return False

    if metrics.footprint_voxel_count <= 2 and metrics.z_span_voxels >= 2:
        return False
    if metrics.footprint_to_depth_ratio < 1.5 and metrics.widest_xy_span_voxels <= metrics.z_span_voxels:
        return False
    return True


def _detect_suction_cups(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    min_volume_mm3: float = 0.5,
    voxel_data: tuple[object, np.ndarray] | None = None,
    progress_callback: AnalysisProgressCallback | None = None,
    cancel_check: AnalysisCancelCheck | None = None,
) -> list[Cavity]:
    _check_cancel(cancel_check)
    voxel, occupied = voxel_data if voxel_data is not None else _voxelize_mesh(
        mesh,
        pitch_mm=pitch_mm,
        cancel_check=cancel_check,
    )
    if occupied.size == 0:
        return []

    empty = ~occupied
    padded_empty = np.pad(empty, pad_width=1, mode="constant", constant_values=True)
    visited = np.zeros_like(padded_empty, dtype=bool)
    q: deque[tuple[int, int, int]] = deque([(0, 0, 0)])
    visited[0, 0, 0] = True

    while q:
        x, y, z = q.popleft()
        for dx, dy, dz in _NEIGHBORS_3D:
            nx, ny, nz = x + dx, y + dy, z + dz
            if nx < 0 or ny < 0 or nz < 0:
                continue
            if nx >= padded_empty.shape[0] or ny >= padded_empty.shape[1] or nz >= padded_empty.shape[2]:
                continue
            if visited[nx, ny, nz] or not padded_empty[nx, ny, nz]:
                continue
            visited[nx, ny, nz] = True
            q.append((nx, ny, nz))

    enclosed = (padded_empty & ~visited)[1:-1, 1:-1, 1:-1]
    if not enclosed.any():
        return []

    cavities: list[Cavity] = []
    seen = np.zeros_like(enclosed, dtype=bool)
    cavity_id = 1
    seed_points = np.argwhere(enclosed)
    progress_stride = max(1, len(seed_points) // 20) if len(seed_points) else 1
    flood_counter = 0

    for seed_index, seed in enumerate(seed_points, start=1):
        if seed_index == 1 or seed_index % progress_stride == 0 or seed_index == len(seed_points):
            _report_progress(
                progress_callback,
                "detect_suction_cups",
                f"Scanning the voxel grid for suction cups and trapped resin cavities. Checked {seed_index}/{len(seed_points)} enclosed seeds.",
            )
        if seed_index % 64 == 0:
            _check_cancel(cancel_check)
        sx, sy, sz = map(int, seed)
        if seen[sx, sy, sz]:
            continue
        component: list[tuple[int, int, int]] = []
        qq: deque[tuple[int, int, int]] = deque([(sx, sy, sz)])
        seen[sx, sy, sz] = True

        while qq:
            x, y, z = qq.popleft()
            component.append((x, y, z))
            flood_counter += 1
            if flood_counter % 2048 == 0:
                _check_cancel(cancel_check)
            for dx, dy, dz in _NEIGHBORS_3D:
                nx, ny, nz = x + dx, y + dy, z + dz
                if nx < 0 or ny < 0 or nz < 0:
                    continue
                if nx >= enclosed.shape[0] or ny >= enclosed.shape[1] or nz >= enclosed.shape[2]:
                    continue
                if seen[nx, ny, nz] or not enclosed[nx, ny, nz]:
                    continue
                seen[nx, ny, nz] = True
                qq.append((nx, ny, nz))

        volume_mm3 = len(component) * (pitch_mm ** 3)
        if volume_mm3 < min_volume_mm3:
            continue

        component_indices = np.asarray(component, dtype=float)
        metrics = _suction_cup_component_metrics(component_indices)
        if metrics is None or not _is_plausible_suction_cup_component(component_indices, metrics=metrics):
            continue
        confidence_score, confidence_level, xy_footprint_mm2, z_span_mm = _suction_cup_confidence(
            metrics,
            pitch_mm=pitch_mm,
        )
        centroid_index = component_indices.mean(axis=0).reshape(1, 3)
        centroid = _indices_to_points(voxel, centroid_index)[0].tolist()
        cavities.append(
            Cavity(
                id=cavity_id,
                volume_mm3=float(volume_mm3),
                centroid_mm=[float(v) for v in centroid],
                xy_footprint_mm2=xy_footprint_mm2,
                z_span_mm=z_span_mm,
                confidence_score=confidence_score,
                confidence_level=confidence_level,
            )
        )
        cavity_id += 1

    return cavities


def _dilate_2d(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, pad_width=1, mode="constant", constant_values=False)
    out = np.zeros_like(mask, dtype=bool)
    height, width = mask.shape
    for dx in (0, 1, 2):
        for dy in (0, 1, 2):
            out |= padded[dx : dx + height, dy : dy + width]
    return out


def _bboxes_touch_or_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
    padding: int = 2,
) -> bool:
    first_x_min, first_x_max, first_y_min, first_y_max = first
    second_x_min, second_x_max, second_y_min, second_y_max = second
    return not (
        first_x_max + padding < second_x_min
        or second_x_max + padding < first_x_min
        or first_y_max + padding < second_y_min
        or second_y_max + padding < first_y_min
    )


def _bbox_overlap_area(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> int:
    first_x_min, first_x_max, first_y_min, first_y_max = first
    second_x_min, second_x_max, second_y_min, second_y_max = second
    overlap_x = max(0, min(first_x_max, second_x_max) - max(first_x_min, second_x_min) + 1)
    overlap_y = max(0, min(first_y_max, second_y_max) - max(first_y_min, second_y_min) + 1)
    return int(overlap_x * overlap_y)


def _cluster_island_components(components: list[_IslandComponent]) -> list[Island]:
    if not components:
        return []

    active_regions: list[_IslandRegionState] = []
    finished_regions: list[_IslandRegionState] = []

    for component in components:
        still_active: list[_IslandRegionState] = []
        for region in active_regions:
            if region.end_layer_index < component.layer_index - 1:
                finished_regions.append(region)
            else:
                still_active.append(region)
        active_regions = still_active

        matches = [
            region
            for region in active_regions
            if region.end_layer_index == component.layer_index - 1
            and _bboxes_touch_or_overlap(region.last_bbox_xy_idx, component.bbox_xy_idx)
        ]
        if not matches:
            active_regions.append(_IslandRegionState.from_component(component))
            continue

        primary = max(
            matches,
            key=lambda region: (
                _bbox_overlap_area(region.last_bbox_xy_idx, component.bbox_xy_idx),
                region.total_voxel_count,
            ),
        )
        for region in matches:
            if region is primary:
                continue
            primary.absorb_region(region)
            active_regions.remove(region)
        primary.absorb(component)

    finished_regions.extend(active_regions)
    finished_regions.sort(key=lambda region: (region.start_layer_index, region.start_z_mm))
    return [region.to_island() for region in finished_regions]


def _detect_islands(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    min_voxels: int = 6,
    voxel_data: tuple[object, np.ndarray] | None = None,
    progress_callback: AnalysisProgressCallback | None = None,
    cancel_check: AnalysisCancelCheck | None = None,
) -> list[Island]:
    _check_cancel(cancel_check)
    voxel, occupied = voxel_data if voxel_data is not None else _voxelize_mesh(
        mesh,
        pitch_mm=pitch_mm,
        cancel_check=cancel_check,
    )
    if occupied.size == 0:
        return []

    components: list[_IslandComponent] = []
    depth = occupied.shape[2]
    progress_stride = max(1, depth // 20) if depth else 1

    for z_idx in range(depth):
        _check_cancel(cancel_check)
        if z_idx == 0 or (z_idx + 1) % progress_stride == 0 or z_idx == depth - 1:
            _report_progress(
                progress_callback,
                "detect_islands",
                f"Checking sliced layers for unsupported islands. Processed {z_idx + 1}/{depth} layers.",
            )
        current = occupied[:, :, z_idx]
        if not current.any():
            continue

        if z_idx == 0:
            unsupported = current.copy()
        else:
            below = occupied[:, :, z_idx - 1]
            support_mask = _dilate_2d(below)
            unsupported = current & ~support_mask

        if not unsupported.any():
            continue

        visited = np.zeros_like(unsupported, dtype=bool)
        for seed in np.argwhere(unsupported):
            sx, sy = map(int, seed)
            if visited[sx, sy]:
                continue
            count = 0
            q: deque[tuple[int, int]] = deque([(sx, sy)])
            visited[sx, sy] = True
            min_x = sx
            max_x = sx
            min_y = sy
            max_y = sy
            sum_x = 0.0
            sum_y = 0.0
            while q:
                x, y = q.popleft()
                count += 1
                sum_x += x
                sum_y += y
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                if count % 1024 == 0:
                    _check_cancel(cancel_check)
                for dx, dy in _NEIGHBORS_2D:
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= unsupported.shape[0] or ny >= unsupported.shape[1]:
                        continue
                    if visited[nx, ny] or not unsupported[nx, ny]:
                        continue
                    visited[nx, ny] = True
                    q.append((nx, ny))

            if count < min_voxels:
                continue
            centroid_point = _indices_to_points(
                voxel,
                np.array([[sum_x / count, sum_y / count, z_idx]], dtype=float),
            )[0]
            components.append(
                _IslandComponent(
                    layer_index=int(z_idx),
                    z_mm=float(centroid_point[2]),
                    voxel_count=int(count),
                    bbox_xy_idx=(int(min_x), int(max_x), int(min_y), int(max_y)),
                    xy_centroid_mm=[float(centroid_point[0]), float(centroid_point[1])],
                )
            )

    return _cluster_island_components(components)


def _structural_risk(
    max_ratio: float,
    cavity_count: int,
    island_count: int,
    detail_density: float,
) -> float:
    ratio_term = min(max_ratio / 0.25, 1.0)
    cavity_term = min(cavity_count / 4.0, 1.0)
    island_term = min(island_count / 20.0, 1.0)
    detail_term = min(detail_density / 3.0, 1.0)

    score = 100.0 * (
        0.45 * ratio_term
        + 0.25 * cavity_term
        + 0.20 * island_term
        + 0.10 * detail_term
    )
    return float(round(score, 2))


def _curvature_proxy(mesh: trimesh.Trimesh, cancel_check: AnalysisCancelCheck | None = None) -> float | None:
    _check_cancel(cancel_check)
    try:
        import pyvista as pv
    except Exception:
        return None

    try:
        _check_cancel(cancel_check)
        faces = np.hstack(
            [
                np.full((mesh.faces.shape[0], 1), 3, dtype=np.int64),
                mesh.faces.astype(np.int64),
            ]
        ).ravel()
        pv_mesh = pv.PolyData(mesh.vertices, faces)
        curvature = pv_mesh.curvature(curv_type="mean")
        _check_cancel(cancel_check)
        return float(np.nanmean(np.abs(curvature)))
    except Exception:
        return None


def _estimate_model_intent(
    *,
    surface_area_ratio: float,
    volume_mm3: float,
    detail_density: float,
    max_cross_section_ratio: float,
    suction_cup_count: int,
    island_count: int,
) -> tuple[UseCase, list[str]]:
    reasons: list[str] = []
    scores: dict[UseCase, float] = {
        "miniature": 0.0,
        "collectible": 0.0,
        "heavy_use": 0.0,
    }

    if surface_area_ratio >= 0.45:
        scores["miniature"] += 1.8
        reasons.append("High surface-area ratio suggests thin detailed geometry.")
    elif surface_area_ratio <= 0.22:
        scores["heavy_use"] += 1.6
        reasons.append("Low surface-area ratio suggests thicker structural mass.")
    else:
        scores["collectible"] += 1.0
        reasons.append("Mid-range surface-area ratio matches display collectible profile.")

    if volume_mm3 <= 20000:
        scores["miniature"] += 1.2
        reasons.append("Low model volume aligns with miniature intent.")
    elif volume_mm3 >= 70000:
        scores["heavy_use"] += 1.2
        reasons.append("High model volume aligns with heavy-use or structural intent.")
    else:
        scores["collectible"] += 0.8

    if detail_density >= 1.8:
        scores["miniature"] += 0.9
    elif detail_density <= 0.9:
        scores["heavy_use"] += 0.6
    else:
        scores["collectible"] += 0.4

    if island_count >= 3:
        scores["miniature"] += 0.9
        reasons.append("Island count indicates fragile fine features.")
    if suction_cup_count >= 1 or max_cross_section_ratio >= 0.22:
        scores["collectible"] += 1.1
        reasons.append("Large section or hollow behavior suggests collectible-class peel risk.")
    if max_cross_section_ratio >= 0.30:
        scores["heavy_use"] += 0.5

    intent = max(scores, key=scores.get)
    return intent, reasons[:4]
