# Open3D Spike Plan

## Goal

Add an Open3D prototype only where it can plausibly outperform the current Trimesh and PyVista path without destabilizing the existing public API.

The current entrypoint should stay unchanged:

- `app.geometry.analyze_geometry(...)`

The first Open3D spike should target the internals that are already runtime hot spots:

- `_voxelize_mesh`
- `_detect_suction_cups`
- `_detect_islands`
- `_curvature_proxy`

The first spike should not replace exact multiplane cross-sections. The current multiplane path is already isolated and is not the highest-risk stage for large-model runtime.

## Dependency Change

An optional extra is already wired into `pyproject.toml`:

- `open3d-spike`

Install it in a repo-mounted container or a local dev environment with:

```bash
python -m pip install -e .[dev,open3d-spike]
```

For the existing Docker workflow, use:

```bash
docker compose run --rm -v "${PWD}:/app" api sh -lc "python -m pip install -e .[dev,open3d-spike]"
```

## Proposed Adapter Boundary

Add the Open3D work behind internal helpers, not at the API layer.

Recommended shape:

1. Keep Trimesh as the default backend.
2. Add Open3D-only helper functions alongside the existing Trimesh helpers.
3. Switch by an internal backend selector or environment variable during the spike.
4. Compare results with the current output models before changing defaults.

Current prototype status:

- `app.geometry._voxelize_mesh(...)` now supports `RESINLOGIC_GEOMETRY_VOXEL_BACKEND=open3d` as an opt-in backend.
- Trimesh remains the default and fallback path.
- The voxel cross-section fallback now routes through the same selector, so cavity, island, and voxelized cross-section analysis share one backend boundary.
- The Open3D voxel path now unions surface voxels with `RaycastingScene.compute_occupancy(..., nsamples=3)` so interior occupancy is filled instead of treating the grid as surface-only shell data.

## Exact Stage Targets

### `_voxelize_mesh`

Reason:

- Open3D has strong voxel-grid and tensor geometry support.
- This stage feeds both cavity detection and island detection.
- A win here compounds across multiple downstream stages.

Success criteria:

- Lower runtime on large meshes.
- No loss of cavity or island recall on current regression fixtures.

### `_detect_suction_cups`

Reason:

- This stage benefits from fast occupancy access, ray-style reasoning, and reusable voxel structures.
- Open3D ray casting and distance-query capabilities are a plausible fit.

Success criteria:

- Same or lower false-positive rate on narrow sealed voids.
- Faster runtime on hollow or complex interior models.

### `_detect_islands`

Reason:

- This stage depends on voxel connectivity and layered component extraction.
- If voxelization moves, this stage should be benchmarked with it immediately.

Success criteria:

- Same grouped-region output as the current implementation.
- No regression in clustered unsupported-region summaries.

### `_curvature_proxy`

Reason:

- PyVista is currently optional and expensive on large meshes.
- Open3D may offer a cleaner long-term path if surface-detail estimation stays important.

Success criteria:

- Similar ordering and relative scores on high-detail vs low-detail models.
- Better runtime or simpler dependency story than the current path.

## Benchmark Workflow

Two scripts are now available for baseline collection:

- `scripts/benchmark_geometry_analysis.py`
- `scripts/benchmark_repair_backends.py`

Use the geometry script to capture current stage timings before any Open3D branch is added:

```bash
python scripts/benchmark_geometry_analysis.py path/to/model.stl --analysis-level balanced --repeat 3
```

To benchmark the current Open3D voxel prototype explicitly:

```bash
python scripts/benchmark_geometry_analysis.py path/to/model.stl --analysis-level balanced --repeat 3 --voxel-backend open3d
```

Use the repair benchmark separately so repair changes do not get mixed into analysis-stage timing:

```bash
python scripts/benchmark_repair_backends.py path/to/model.stl --backend all
```

## Benchmark Cases

Collect baselines for at least these case types:

1. Small clean watertight model.
2. Broken STL with duplicate and degenerate faces.
3. Large hollow model with cavity risk.
4. Detail-dense figurine or miniature.
5. Wide-base structural model with low curvature detail.

For each case, record:

- total analysis time
- per-stage timings from `performance_ms`
- triangle count
- cavity count
- island count
- note changes that indicate adaptive-profile behavior

## Current Findings

Initial local validation now covers a small watertight box fixture generated directly in the dev environment.

Observed result on that case with `analysis-level=balanced`:

- Trimesh backend: `total=412.3 ms`, `voxelize=62.0 ms`, but it still raised 1 cavity candidate on a simple closed box.
- Open3D backend: `total=720.8 ms`, `voxelize=639.3 ms`, and it reported 0 cavity candidates on the same case.

Interpretation:

- Open3D is not yet a runtime win on small clean models in the current implementation.
- The new solid-occupancy path appears to reduce at least one obvious false-positive cavity case versus the default Trimesh path.
- The next benchmark pass should focus on large hollow and detail-dense repaired STLs before any default-backend decision is reconsidered.

## Acceptance Bar

Do not switch defaults unless the Open3D spike wins on at least one of these axes without regressing the current tests:

1. lower total runtime on large balanced or deep analysis
2. lower runtime in voxel-heavy stages
3. lower false-positive rate for cavity detection
4. reduced dependence on the current PyVista-only curvature path

## Non-Goals For The First Spike

- replacing the public API
- replacing exact multiplane slicing
- rewriting every geometry helper at once
- changing result schemas in `app.models`