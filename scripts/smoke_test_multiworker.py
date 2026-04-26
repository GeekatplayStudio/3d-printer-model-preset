from __future__ import annotations

import argparse
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
import trimesh


def _broken_mesh_bytes() -> bytes:
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
        [0, 1, 2],
        [0, 1, 2],
        [0, 1, 1],
        [0, 2, 3],
        [0, 3, 1],
        [1, 3, 2],
        [4, 5, 6],
    ]
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    return mesh.export(file_type="stl")


def _load_cancel_mesh(path: Path | None) -> tuple[str, bytes]:
    if path is not None:
        return path.name, path.read_bytes()
    mesh = trimesh.creation.icosphere(subdivisions=8, radius=100.0)
    return "generated_cancel_mesh.stl", mesh.export(file_type="stl")


def _request_kwargs(timeout: float) -> dict[str, Any]:
    return {"timeout": timeout}


def _ensure_seeded_catalog(base_url: str) -> dict[str, Any]:
    options = requests.get(f"{base_url}/wizard/catalog/options", **_request_kwargs(30)).json()
    if options.get("printers_by_target", {}).get("msla") and options.get("materials_by_target", {}).get("msla"):
        return options
    setup = requests.post(
        f"{base_url}/wizard/database/setup",
        json={
            "mode": "official_local",
            "replace_existing": False,
            "source": "multiworker_smoke",
        },
        **_request_kwargs(120),
    )
    setup.raise_for_status()
    options = requests.get(f"{base_url}/wizard/catalog/options", **_request_kwargs(30))
    options.raise_for_status()
    return options.json()


def _pick_msla_pair(options: dict[str, Any]) -> tuple[str, str]:
    printers = options.get("printers_by_target", {}).get("msla") or []
    materials = options.get("materials_by_target", {}).get("msla") or []
    compatibility = options.get("compatibility_by_target", {}).get("msla", {})
    if not printers or not materials:
        raise RuntimeError("No MSLA printer/material pair is available for the smoke test.")
    printer = printers[0]
    compatible_materials = compatibility.get(printer) or materials
    if not compatible_materials:
        raise RuntimeError(f"Printer '{printer}' has no compatible MSLA materials.")
    return printer, compatible_materials[0]


def _download(base_url: str, url: str) -> requests.Response:
    response = requests.get(f"{base_url}{url}", **_request_kwargs(60))
    response.raise_for_status()
    return response


def _run_fix_and_settings(base_url: str) -> dict[str, Any]:
    fix_response = requests.post(
        f"{base_url}/wizard/model/fix",
        files={"file": ("broken.stl", io.BytesIO(_broken_mesh_bytes()), "model/stl")},
        data={"slice_height_mm": "0.2", "analysis_level": "balanced"},
        **_request_kwargs(120),
    )
    fix_response.raise_for_status()
    fix_body = fix_response.json()
    if not fix_body.get("download_url"):
        raise RuntimeError("Fix response did not include a download URL.")
    repaired_download = _download(base_url, fix_body["download_url"])
    if not repaired_download.content:
        raise RuntimeError("Fix download returned an empty artifact.")

    options = _ensure_seeded_catalog(base_url)
    printer, material = _pick_msla_pair(options)

    settings_response = requests.post(
        f"{base_url}/wizard/settings/recommend",
        json={
            "analysis": fix_body["analysis"],
            "printer": printer,
            "resin_type": material,
            "use_case": "miniature",
            "ambient_temp_c": 22.0,
            "film_releases": 100,
        },
        **_request_kwargs(120),
    )
    settings_response.raise_for_status()
    settings_body = settings_response.json()
    if not settings_body.get("cfg_download_url") or not settings_body.get("settings_download_url"):
        raise RuntimeError("Settings response did not include both download URLs.")
    cfg_download = _download(base_url, settings_body["cfg_download_url"])
    json_download = _download(base_url, settings_body["settings_download_url"])
    if not cfg_download.content:
        raise RuntimeError("Slicer profile download returned an empty artifact.")
    if not json_download.content:
        raise RuntimeError("Settings JSON download returned an empty artifact.")

    return {
        "fix": {
            "fully_repaired": fix_body.get("fully_repaired"),
            "repair_actions": fix_body.get("repair_actions"),
            "download_bytes": len(repaired_download.content),
        },
        "settings": {
            "target_process": settings_body.get("target_process"),
            "slicer_name": settings_body.get("slicer_name"),
            "cfg_bytes": len(cfg_download.content),
            "settings_json_bytes": len(json_download.content),
        },
    }


def _run_cancel_flow(base_url: str, cancel_model_path: Path | None) -> dict[str, Any]:
    file_name, payload = _load_cancel_mesh(cancel_model_path)
    job_id = f"multiworker-smoke-{uuid4().hex[:10]}"

    def _submit_check() -> requests.Response:
        return requests.post(
            f"{base_url}/wizard/model/check",
            files={"file": (file_name, io.BytesIO(payload), "model/stl")},
            data={
                "slice_height_mm": "0.05",
                "analysis_level": "balanced",
                "progress_job_id": job_id,
            },
            **_request_kwargs(240),
        )

    samples: list[dict[str, Any]] = []
    cancel_response: dict[str, Any] | None = None
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_submit_check)
        for _ in range(120):
            time.sleep(0.5)
            status_response = requests.get(
                f"{base_url}/wizard/model/check/status/{job_id}",
                **_request_kwargs(30),
            )
            status_response.raise_for_status()
            status_body = status_response.json()
            samples.append(
                {
                    "status": status_body.get("status"),
                    "stage": status_body.get("stage"),
                    "cancel_requested": status_body.get("cancel_requested"),
                }
            )
            if status_body.get("status") == "running":
                cancel = requests.post(
                    f"{base_url}/wizard/model/check/status/{job_id}/cancel",
                    **_request_kwargs(30),
                )
                cancel.raise_for_status()
                cancel_response = cancel.json()
                break
            if future.done():
                break
        check_response = future.result()

    final_status = requests.get(
        f"{base_url}/wizard/model/check/status/{job_id}",
        **_request_kwargs(30),
    )
    final_status.raise_for_status()
    check_body = check_response.json()
    final_body = final_status.json()

    if cancel_response is None:
        raise RuntimeError(
            f"Analysis completed before cancellation could be issued. Final status was '{final_body.get('status')}'."
        )
    if check_response.status_code != 409:
        raise RuntimeError(f"Expected cancelled check response to return 409, got {check_response.status_code}.")
    if final_body.get("status") != "cancelled":
        raise RuntimeError(f"Expected final status 'cancelled', got '{final_body.get('status')}'.")

    return {
        "job_id": job_id,
        "status_samples": samples,
        "cancel_response": cancel_response,
        "check_response": {
            "status_code": check_response.status_code,
            "body": check_body,
        },
        "final_status": final_body,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a live wizard smoke test against a multi-worker Docker API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cancel-model-path", type=Path, default=None)
    args = parser.parse_args()

    summary = {
        "base_url": args.base_url.rstrip("/"),
        "fix_and_settings": _run_fix_and_settings(args.base_url.rstrip("/")),
        "cancel_flow": _run_cancel_flow(args.base_url.rstrip("/"), args.cancel_model_path),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())