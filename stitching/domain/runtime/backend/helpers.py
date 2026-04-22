from __future__ import annotations

from typing import Any

from stitching.domain.geometry.refresh_service import MeshRefreshService
from stitching.domain.runtime.site_config import load_runtime_site_config


def _configured_rtsp_urls_for_request(request: dict[str, Any] | None = None) -> tuple[str, str]:
    site_config = load_runtime_site_config()
    cameras = site_config.get("cameras", {}) if isinstance(site_config.get("cameras"), dict) else {}
    request = request or {}
    left_inputs = request.get("inputs", {}).get("left", {}) if isinstance(request.get("inputs"), dict) else {}
    right_inputs = request.get("inputs", {}).get("right", {}) if isinstance(request.get("inputs"), dict) else {}
    left_rtsp = str(
        request.get("left_rtsp")
        or left_inputs.get("url")
        or cameras.get("left_rtsp")
        or ""
    ).strip()
    right_rtsp = str(
        request.get("right_rtsp")
        or right_inputs.get("url")
        or cameras.get("right_rtsp")
        or ""
    ).strip()
    return left_rtsp, right_rtsp


def _internal_mesh_refresh(
    mesh_refresh: MeshRefreshService,
    body: dict[str, Any] | None = None,
    *,
    progress: Any = None,
) -> dict[str, Any]:
    result = mesh_refresh.run_with_progress(body, progress=progress)
    if not isinstance(result, dict):
        raise ValueError("mesh-refresh did not return a JSON object")
    return result


def _request_force_mesh_refresh(request: dict[str, Any] | None = None) -> bool:
    request = request or {}
    nested_geometry = request.get("geometry") if isinstance(request.get("geometry"), dict) else {}
    nested_runtime = request.get("runtime") if isinstance(request.get("runtime"), dict) else {}
    for value in (
        request.get("refresh_geometry"),
        request.get("force_mesh_refresh"),
        nested_geometry.get("refresh"),
        nested_geometry.get("refresh_geometry"),
        nested_runtime.get("refresh_geometry"),
    ):
        if value is None:
            continue
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    return False
