#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.geometry import analyze_geometry


def _average_stage_times(runs: list[dict[str, object]]) -> dict[str, float]:
    if not runs:
        return {}
    totals: dict[str, float] = {}
    for run in runs:
        performance = run["performance_ms"]
        for key, value in performance.items():
            totals[key] = totals.get(key, 0.0) + float(value)
    return {key: round(value / len(runs), 3) for key, value in totals.items()}


def _analyze_once(file_path: Path, args: argparse.Namespace) -> dict[str, object]:
    backend_env = "RESINLOGIC_GEOMETRY_VOXEL_BACKEND"
    previous_backend = os.getenv(backend_env)
    try:
        if args.voxel_backend is None:
            os.environ.pop(backend_env, None)
        else:
            os.environ[backend_env] = args.voxel_backend

        analysis = analyze_geometry(
            str(file_path),
            slice_height_mm=float(args.slice_height),
            voxel_pitch_mm=float(args.voxel_pitch),
            max_slices=int(args.max_slices),
            auto_repair=bool(args.auto_repair),
            analysis_level=args.analysis_level,
        )
    finally:
        if previous_backend is None:
            os.environ.pop(backend_env, None)
        else:
            os.environ[backend_env] = previous_backend

    return {
        "triangle_count": int(analysis.triangle_count),
        "max_cross_section_mm2": float(analysis.max_cross_section_mm2),
        "suction_cup_count": len(analysis.suction_cups),
        "island_count": len(analysis.islands),
        "performance_ms": analysis.performance_ms,
        "notes_tail": analysis.notes[-5:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture baseline stage timings for geometry analysis runs.",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more mesh files to analyze.",
    )
    parser.add_argument(
        "--analysis-level",
        choices=["minimum", "balanced", "deep"],
        default="balanced",
        help="Analysis level to benchmark.",
    )
    parser.add_argument(
        "--slice-height",
        type=float,
        default=0.05,
        help="Slice height in mm.",
    )
    parser.add_argument(
        "--voxel-pitch",
        type=float,
        default=0.2,
        help="Voxel pitch in mm.",
    )
    parser.add_argument(
        "--max-slices",
        type=int,
        default=20000,
        help="Maximum slice count.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of repeated runs per mesh.",
    )
    parser.add_argument(
        "--voxel-backend",
        choices=["trimesh", "open3d"],
        default=None,
        help="Override the internal voxel backend for this benchmark run.",
    )
    parser.add_argument(
        "--auto-repair",
        action="store_true",
        help="Include repair time in the analysis baseline.",
    )
    args = parser.parse_args()

    results: list[dict[str, object]] = []
    repeat_count = max(1, int(args.repeat))

    for value in args.files:
        file_path = Path(value).expanduser().resolve()
        if not file_path.exists():
            raise SystemExit(f"Mesh file not found: {file_path}")
        runs = [_analyze_once(file_path, args) for _ in range(repeat_count)]
        results.append(
            {
                "file": str(file_path),
                "analysis_level": args.analysis_level,
                "voxel_backend": args.voxel_backend or os.getenv("RESINLOGIC_GEOMETRY_VOXEL_BACKEND", "default"),
                "repeat": repeat_count,
                "average_performance_ms": _average_stage_times(runs),
                "runs": runs,
            }
        )

    print(json.dumps({"results": results}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())