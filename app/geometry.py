from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import numpy as np
import trimesh

from app.models import Cavity, GeometryAnalysis, Island, SliceArea, UseCase

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


def analyze_geometry(
    file_path: str,
    slice_height_mm: float = 0.01,
    build_plate_mm: tuple[float, float] = DEFAULT_BUILD_PLATE_MM,
    voxel_pitch_mm: float = 0.2,
    max_slices: int = 20000,
) -> GeometryAnalysis:
    mesh = _load_mesh(file_path)
    notes: list[str] = []

    slice_areas, max_cross_section_mm2, effective_slice_height_mm, slice_notes = _cross_section_areas(
        mesh=mesh,
        slice_height_mm=slice_height_mm,
        max_slices=max_slices,
    )
    notes.extend(slice_notes)

    suction_cups = _detect_suction_cups(mesh, pitch_mm=voxel_pitch_mm)
    islands = _detect_islands(mesh, pitch_mm=voxel_pitch_mm)
    curvature_proxy = _curvature_proxy(mesh)

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
        estimated_intent=estimated_intent,
        intent_reasons=intent_reasons,
        structural_risk_score=structural_risk,
        notes=notes,
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


def _cross_section_areas_voxel(
    mesh: trimesh.Trimesh,
    z_min: float,
    heights: np.ndarray,
    requested_slice_mm: float,
    notes: list[str],
) -> tuple[list[SliceArea], float, float, list[str]]:
    voxel_pitch = max(requested_slice_mm, 0.05)
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


def _detect_suction_cups(
    mesh: trimesh.Trimesh,
    pitch_mm: float,
    min_volume_mm3: float = 0.5,
) -> list[Cavity]:
    voxel = mesh.voxelized(pitch=pitch_mm)
    occupied = np.asarray(voxel.matrix, dtype=bool)
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
) -> list[Island]:
    voxel = mesh.voxelized(pitch=pitch_mm)
    occupied = np.asarray(voxel.matrix, dtype=bool)
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
