#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app.geometry as geometry


def _mesh_snapshot(mesh) -> dict[str, object]:
    return {
        "faces": int(len(mesh.faces)),
        "vertices": int(len(mesh.vertices)),
        "watertight": bool(mesh.is_watertight),
        "duplicate_faces": int(geometry._duplicate_face_count(mesh)),
        "degenerate_faces": int(geometry._degenerate_face_count(mesh)),
        "connected_components": int(geometry._component_count(mesh)),
    }


def _benchmark_backend(file_path: Path, backend: str) -> dict[str, object]:
    repair_fn = {
        "trimesh": geometry._repair_mesh_with_trimesh,
        "pymeshlab": geometry._repair_mesh_with_pymeshlab,
    }[backend]
    source_mesh = geometry._load_mesh(str(file_path))
    before = _mesh_snapshot(source_mesh)
    mesh = source_mesh.copy()

    try:
        started = perf_counter()
        repaired, actions = repair_fn(mesh)
        elapsed_ms = round((perf_counter() - started) * 1000.0, 3)
    except ModuleNotFoundError as exc:
        return {
            "backend": backend,
            "status": "skipped",
            "reason": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "backend": backend,
            "status": "error",
            "reason": str(exc),
        }

    return {
        "backend": backend,
        "status": "ok",
        "elapsed_ms": elapsed_ms,
        "repaired": bool(repaired),
        "actions": actions,
        "before": before,
        "after": _mesh_snapshot(mesh),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark Trimesh and optional PyMeshLab repair backends on one or more mesh files.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more mesh files to benchmark.",
    )
    parser.add_argument(
        "--backend",
        choices=["all", "trimesh", "pymeshlab"],
        default="all",
        help="Backend to benchmark. Use 'all' to compare both backends.",
    )
    args = parser.parse_args()

    backends = ["trimesh", "pymeshlab"] if args.backend == "all" else [args.backend]
    results: list[dict[str, object]] = []

    for value in args.files:
        file_path = Path(value).expanduser().resolve()
        if not file_path.exists():
            raise SystemExit(f"Mesh file not found: {file_path}")
        results.append(
            {
                "file": str(file_path),
                "benchmarks": [_benchmark_backend(file_path, backend) for backend in backends],
            }
        )

    print(json.dumps({"results": results}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())