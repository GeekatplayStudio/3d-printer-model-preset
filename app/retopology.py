from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.models import RetopologyMode

BLENDER_EXECUTABLE_ENV = "RESINLOGIC_BLENDER_EXECUTABLE"


class RetopologyError(RuntimeError):
    pass


BLENDER_SCRIPT = """
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import bpy


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _select_mesh_objects() -> list[object]:
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    bpy.ops.object.select_all(action='DESELECT')
    for obj in meshes:
        obj.select_set(True)
    if meshes:
        bpy.context.view_layer.objects.active = meshes[0]
    return meshes


def _merge_imported_meshes() -> object:
    meshes = _select_mesh_objects()
    if not meshes:
        _fail('No mesh objects were imported from the STL file.')
    if len(meshes) > 1:
        bpy.ops.object.join()
    active = bpy.context.view_layer.objects.active
    if active is None or active.type != 'MESH':
        _fail('Imported STL did not produce an active mesh object.')
    return active


def main() -> None:
    args = sys.argv
    if '--' not in args:
        _fail('Missing Blender retopology config path.')
    config_path = Path(args[args.index('--') + 1])
    config = json.loads(config_path.read_text(encoding='utf-8'))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.stl_import(filepath=config['input_path'])
    obj = _merge_imported_meshes()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.ops.object.shade_smooth()

    if config['mode'] == 'voxel':
        mesh = obj.data
        mesh.remesh_mode = 'VOXEL'
        mesh.remesh_voxel_size = float(config['voxel_size_mm'])
        mesh.remesh_voxel_adaptivity = float(config.get('voxel_adaptivity', 0.0))
        if hasattr(mesh, 'use_remesh_preserve_volume'):
            mesh.use_remesh_preserve_volume = True
        if hasattr(mesh, 'use_remesh_preserve_attributes'):
            mesh.use_remesh_preserve_attributes = False
        bpy.ops.object.voxel_remesh()
    elif config['mode'] == 'quad':
        bpy.ops.object.quadriflow_remesh(
            mode='FACES',
            target_faces=int(config['target_faces']),
            use_preserve_sharp=bool(config.get('preserve_sharp', True)),
            use_preserve_boundary=bool(config.get('preserve_boundary', True)),
        )
    else:
        _fail(f"Unsupported retopology mode: {config['mode']}")

    obj = _merge_imported_meshes()
    obj.data.validate(verbose=False)
    obj.data.update()
    bpy.ops.wm.stl_export(filepath=config['output_path'])


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
"""


def _blender_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv(BLENDER_EXECUTABLE_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())

    for executable_name in ("blender.exe", "blender"):
        resolved = shutil.which(executable_name)
        if resolved:
            candidates.append(Path(resolved))

    if os.name == "nt":
        for root in (os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)")):
            if not root:
                continue
            base = Path(root) / "Blender Foundation"
            if not base.exists():
                continue
            for child in sorted(base.glob("Blender*"), reverse=True):
                executable = child / "blender.exe"
                if executable.exists():
                    candidates.append(executable)

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        normalized = str(candidate.resolve(strict=False))
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(candidate)
    return unique


def resolve_blender_executable() -> Path:
    for candidate in _blender_candidates():
        if candidate.exists():
            return candidate
    raise RetopologyError(
        "Blender executable was not found. Install Blender and put it on PATH, or set "
        f"{BLENDER_EXECUTABLE_ENV} to the Blender executable path."
    )


def run_blender_retopology(
    *,
    input_path: str,
    output_path: str,
    mode: RetopologyMode,
    target_faces: int | None = None,
    voxel_size_mm: float | None = None,
    preserve_sharp: bool = True,
    preserve_boundary: bool = True,
    timeout_seconds: int = 1200,
) -> list[str]:
    blender = resolve_blender_executable()
    output_target = Path(output_path)
    output_target.parent.mkdir(parents=True, exist_ok=True)

    config = {
        "input_path": str(Path(input_path).resolve()),
        "output_path": str(output_target.resolve()),
        "mode": mode,
        "target_faces": int(target_faces) if target_faces is not None else None,
        "voxel_size_mm": float(voxel_size_mm) if voxel_size_mm is not None else None,
        "voxel_adaptivity": 0.0,
        "preserve_sharp": bool(preserve_sharp),
        "preserve_boundary": bool(preserve_boundary),
    }

    with tempfile.TemporaryDirectory(prefix="resinlogic-retopo-") as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / "retopology-config.json"
        script_path = temp_path / "retopology-runner.py"
        config_path.write_text(json.dumps(config, ensure_ascii=True, indent=2), encoding="utf-8")
        script_path.write_text(BLENDER_SCRIPT, encoding="utf-8")

        command = [
            str(blender),
            "--background",
            "--factory-startup",
            "--python",
            str(script_path),
            "--",
            str(config_path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RetopologyError(
                f"Blender retopology timed out after {timeout_seconds} seconds."
            ) from exc

    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "Unknown Blender failure").strip()
        raise RetopologyError(f"Blender retopology failed: {details}")
    if not output_target.exists() or output_target.stat().st_size == 0:
        raise RetopologyError("Blender completed without producing a retopologized STL file.")

    notes = [f"Retopology backend: Blender ({blender.name})."]
    if mode == "quad":
        notes.append(f"Quadriflow remesh requested approximately {int(target_faces or 0):,} faces.")
        if preserve_sharp:
            notes.append("Sharp feature preservation was requested during quadriflow remesh.")
        if preserve_boundary:
            notes.append("Boundary preservation was requested during quadriflow remesh.")
    else:
        notes.append(f"Voxel remesh rewrote the shell at {float(voxel_size_mm or 0.0):.4f} mm voxel size.")
    return notes