from __future__ import annotations

import math
from collections import deque
from pathlib import Path
from time import perf_counter

import numpy as np
import trimesh

from app.models import AnalysisLevel, Cavity, GeometryAnalysis, Island, MeshHealthReport, SliceArea, UseCase

DEFAULT_BUILD_PLATE_MM = (153.36, 77.76)
_NEIGHBORS_3D = (
    (-1, 0, 0),
    (1, 0, 0),
    (0, -1, 0),
    (0, 1, 0),
    (0, 0, -1),
    (0, 0, 1),
)
_NEIGHBORS_2D = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 3)


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


def analyze_geometry(
    file_path: str,
    slice_height_mm: float = 0.01,
    build_plate_mm: tuple[float, float] = DEFAULT_BUILD_PLATE_MM,
    voxel_pitch_mm: float = 0.2,
    max_slices: int = 20000,
    auto_repair: bool = True,
    analysis_level: AnalysisLevel = "balanced",
) -> GeometryAnalysis:
    total_started = perf_counter()
    timings_ms: dict[str, float] = {}

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

    profile = _analysis_profile_config(
        analysis_level=analysis_level,
        max_slices=max_slices,
        voxel_pitch_mm=voxel_pitch_mm,
    )
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

    started = perf_counter()
    if analysis_level == "minimum":
        slice_areas, max_cross_section_mm2, effective_slice_height_mm, slice_notes = _cross_section_areas_fast(
            mesh=mesh,
            slice_height_mm=slice_height_mm,
            max_slices=int(profile["max_slices"]),
        )
    else:
        slice_areas, max_cross_section_mm2, effective_slice_height_mm, slice_notes = _cross_section_areas(
            mesh=mesh,
            slice_height_mm=slice_height_mm,
            max_slices=int(profile["max_slices"]),
        )
    timings_ms["cross_section"] = _elapsed_ms(started)
    notes.extend(slice_notes)

    suction_cups: list[Cavity] = []
    islands: list[Island] = []
    curvature_proxy: float | None = None

    voxel_data: tuple[trimesh.voxel.VoxelGrid, np.ndarray] | None = None
    should_voxelize = bool(profile["include_suction_cups"] or profile["include_islands"])
    if should_voxelize:
        started = perf_counter()
        voxel_data = _voxelize_mesh(mesh, pitch_mm=float(profile["voxel_pitch_mm"]))
        timings_ms["voxelize"] = _elapsed_ms(started)

    if bool(profile["include_suction_cups"]):
        started = perf_counter()
        suction_cups = _detect_suction_cups(
            mesh,
            pitch_mm=float(profile["voxel_pitch_mm"]),
            voxel_data=voxel_data,
        )
        timings_ms["detect_suction_cups"] = _elapsed_ms(started)
    else:
        notes.append("Minimum analysis: suction cup detection skipped for faster processing.")

    if bool(profile["include_islands"]):
        started = perf_counter()
        islands = _detect_islands(
            mesh,
            pitch_mm=float(profile["voxel_pitch_mm"]),
            voxel_data=voxel_data,
        )
        timings_ms["detect_islands"] = _elapsed_ms(started)
    else:
        notes.append("Minimum analysis: island detection skipped for faster processing.")

    if bool(profile["include_curvature"]):
        started = perf_counter()
        curvature_proxy = _curvature_proxy(mesh)
        timings_ms["curvature_proxy"] = _elapsed_ms(started)
    else:
        notes.append("Minimum analysis: curvature proxy skipped for faster processing.")

    started = perf_counter()
    surface_area = float(mesh.area)
    volume_mm3 = float(abs(mesh.volume))
    triangle_count = int(len(mesh.faces))
    detail_density = float(triangle_count / surface_area) if surface_area > 0 else 0.0
    surface_area_ratio = float(surface_area / volume_mm3) if volume_mm3 > 0 else 0.0
    build_plate_area_mm2 = float(build_plate_mm[0] * build_plate_mm[1])
    max_ratio = max_cross_section_mm2 / build_plate_area_mm2 if build_plate_area_mm2 > 0 else 0.0
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

    return GeometryAnalysis(
        file_name=Path(file_path).name,
        mesh_volume_mm3=volume_mm3,
        surface_area_mm2=surface_area,
        surface_area_ratio=surface_area_ratio,
        triangle_count=triangle_count,
        detail_density=detail_density,
        curvature_proxy=curvature_proxy,
        slice_height_mm=effective_slice_height_mm,
        build_plate_area_mm2=build_plate_area_mm2,
        max_cross_section_mm2=float(max_cross_section_mm2),
        max_cross_section_ratio=float(max_ratio),
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


def repair_mesh_file(file_path: str, output_path: str) -> MeshHealthReport:
    mesh = _load_mesh(file_path)
    repaired, repair_actions = _repair_mesh(mesh)
    report = _mesh_health_report(mesh=mesh, repaired=repaired, repair_actions=repair_actions)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(output, file_type="stl")
    return report


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
        connected_components=1,
        boundary_edge_count=0,
        non_manifold_edge_count=0,
        degenerate_face_count=degenerate_faces,
        duplicate_face_count=duplicate_faces,
        repaired=repaired,
        issues=issues,
        repair_actions=repair_actions,
    )


def _repair_mesh(mesh: trimesh.Trimesh) -> tuple[bool, list[str]]:
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
) -> tuple[list[SliceArea], float, float, list[str]]:
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
        )

    try:
        sections = mesh.section_multiplane(
            plane_origin=[0.0, 0.0, float(z_min)],
            plane_normal=[0.0, 0.0, 1.0],
            heights=heights,
        )

        slice_areas: list[SliceArea] = []
        max_area = 0.0
        for offset, section in zip(heights, sections):
            area = _section_area(section)
            max_area = max(max_area, area)
            slice_areas.append(
                SliceArea(
                    z_mm=float(round(float(z_min + offset), 5)),
                    area_mm2=float(area),
                )
            )
        return slice_areas, max_area, effective_slice, notes
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
        )


def _cross_section_areas_fast(
    mesh: trimesh.Trimesh,
    slice_height_mm: float,
    max_slices: int,
) -> tuple[list[SliceArea], float, float, list[str]]:
    z_min, z_max = mesh.bounds[:, 2]
    height = max(0.0, float(z_max - z_min))
    if height <= 0.0:
        return (
            [SliceArea(z_mm=float(round(float(z_min), 5)), area_mm2=0.0)],
            0.0,
            slice_height_mm,
            ["Model has zero Z height; cross-section area is zero."],
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

    notes.append("Minimum analysis: using lightweight face-distribution cross-section approximation.")

    face_count = int(len(mesh.faces))
    if face_count == 0:
        empty_slices = [
            SliceArea(z_mm=float(round(float(z_min + h), 5)), area_mm2=0.0) for h in heights
        ]
        return empty_slices, 0.0, effective_slice, notes

    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    z_values = vertices[:, 2]
    x_min, y_min, _ = mesh.bounds[0]
    x_max, y_max, _ = mesh.bounds[1]
    xy_area_mm2 = max(0.0, float((x_max - x_min) * (y_max - y_min)))
    if xy_area_mm2 <= 0.0:
        empty_slices = [
            SliceArea(z_mm=float(round(float(z_min + h), 5)), area_mm2=0.0) for h in heights
        ]
        return empty_slices, 0.0, effective_slice, notes

    sample_limit = 1_200_000
    stride = 1
    if face_count > sample_limit:
        stride = int(math.ceil(face_count / sample_limit))
        notes.append(
            f"Minimum analysis sampled faces at stride {stride} to limit memory on very large meshes."
        )

    sampled_faces = faces[::stride]
    z_centers = (
        z_values[sampled_faces[:, 0]]
        + z_values[sampled_faces[:, 1]]
        + z_values[sampled_faces[:, 2]]
    ) / 3.0

    bin_count = len(heights)
    bins = np.zeros(bin_count, dtype=float)
    indices = np.floor((z_centers - float(z_min)) / effective_slice).astype(np.int64)
    indices = np.clip(indices, 0, bin_count - 1)
    np.add.at(bins, indices, float(stride))

    # Smooth sharp spikes from centroid-only assignment.
    if bin_count >= 3:
        bins = np.convolve(bins, np.array([0.2, 0.6, 0.2], dtype=float), mode="same")

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
        # Blend occupancy with volume fill ratio to produce stable low-memory area estimates.
        area_factor = 0.35 + (0.65 * fill_ratio)
        approx_areas = xy_area_mm2 * np.sqrt(occupancy) * area_factor

    requested_z = float(z_min) + heights
    slice_areas = [
        SliceArea(
            z_mm=float(round(float(z), 5)),
            area_mm2=float(area),
        )
        for z, area in zip(requested_z, approx_areas)
    ]
    max_area = float(np.max(approx_areas)) if len(approx_areas) else 0.0
    return slice_areas, max_area, effective_slice, notes


def _cross_section_areas_voxel(
    mesh: trimesh.Trimesh,
    z_min: float,
    heights: np.ndarray,
    requested_slice_mm: float,
    notes: list[str],
    min_voxel_pitch_mm: float = 0.05,
) -> tuple[list[SliceArea], float, float, list[str]]:
    voxel_pitch = max(requested_slice_mm, min_voxel_pitch_mm)
    if voxel_pitch > requested_slice_mm:
        notes.append(
            f"Voxel fallback used {voxel_pitch}mm pitch for speed; per-slice areas are interpolated."
        )

    voxel = mesh.voxelized(pitch=voxel_pitch)
    occupied = np.asarray(voxel.matrix, dtype=bool)
    if occupied.size == 0:
        empty_slices = [
            SliceArea(z_mm=float(round(float(z_min + h), 5)), area_mm2=0.0) for h in heights
        ]
        return empty_slices, 0.0, requested_slice_mm, notes

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
    return slice_areas, max_area, requested_slice_mm, notes


def _indices_to_points(voxel_grid: trimesh.voxel.VoxelGrid, indices: np.ndarray) -> np.ndarray:
    if hasattr(voxel_grid, "indices_to_points"):
        return voxel_grid.indices_to_points(indices)

    transform = voxel_grid.transform
    homogeneous = np.column_stack([indices, np.ones(len(indices), dtype=float)])
    points = (transform @ homogeneous.T).T[:, :3]
    return points


def _voxelize_mesh(mesh: trimesh.Trimesh, pitch_mm: float) -> tuple[trimesh.voxel.VoxelGrid, np.ndarray]:
    voxel = mesh.voxelized(pitch=pitch_mm)
    occupied = np.asarray(voxel.matrix, dtype=bool)
    return voxel, occupied


def _detect_suction_cups(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    min_volume_mm3: float = 0.5,
    voxel_data: tuple[trimesh.voxel.VoxelGrid, np.ndarray] | None = None,
) -> list[Cavity]:
    voxel, occupied = voxel_data if voxel_data is not None else _voxelize_mesh(mesh, pitch_mm=pitch_mm)
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

    for seed in np.argwhere(enclosed):
        sx, sy, sz = map(int, seed)
        if seen[sx, sy, sz]:
            continue
        component: list[tuple[int, int, int]] = []
        qq: deque[tuple[int, int, int]] = deque([(sx, sy, sz)])
        seen[sx, sy, sz] = True

        while qq:
            x, y, z = qq.popleft()
            component.append((x, y, z))
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
        centroid_index = component_indices.mean(axis=0).reshape(1, 3)
        centroid = _indices_to_points(voxel, centroid_index)[0].tolist()
        cavities.append(
            Cavity(
                id=cavity_id,
                volume_mm3=float(volume_mm3),
                centroid_mm=[float(v) for v in centroid],
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


def _detect_islands(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    min_voxels: int = 6,
    voxel_data: tuple[trimesh.voxel.VoxelGrid, np.ndarray] | None = None,
) -> list[Island]:
    voxel, occupied = voxel_data if voxel_data is not None else _voxelize_mesh(mesh, pitch_mm=pitch_mm)
    if occupied.size == 0:
        return []

    islands: list[Island] = []
    depth = occupied.shape[2]

    for z_idx in range(depth):
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
            while q:
                x, y = q.popleft()
                count += 1
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
            z_world = _indices_to_points(voxel, np.array([[0, 0, z_idx]], dtype=float))[0, 2]
            islands.append(
                Island(
                    layer_index=int(z_idx),
                    z_mm=float(z_world),
                    voxel_count=int(count),
                )
            )

    return islands


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


def _curvature_proxy(mesh: trimesh.Trimesh) -> float | None:
    try:
        import pyvista as pv
    except Exception:
        return None

    try:
        faces = np.hstack(
            [
                np.full((mesh.faces.shape[0], 1), 3, dtype=np.int64),
                mesh.faces.astype(np.int64),
            ]
        ).ravel()
        pv_mesh = pv.PolyData(mesh.vertices, faces)
        curvature = pv_mesh.curvature(curv_type="mean")
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
