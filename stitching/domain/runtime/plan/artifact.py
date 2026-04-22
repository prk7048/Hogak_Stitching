from __future__ import annotations

from pathlib import Path
from typing import Any

from stitching.domain.runtime.defaults import DEFAULT_HOMOGRAPHY_PATH
from stitching.domain.geometry.artifact import (
    load_runtime_geometry_artifact,
    runtime_geometry_artifact_path,
)
from stitching.domain.geometry.policy import geometry_rollout_metadata
from stitching.domain.runtime.site_config import load_runtime_site_config


def resolve_requested_artifact_path(request: dict[str, Any] | None = None) -> Path | None:
    request = request or {}
    nested_geometry = request.get("geometry") if isinstance(request.get("geometry"), dict) else {}
    artifact_value = request.get("geometry_artifact_path") or nested_geometry.get("artifact_path")
    if not artifact_value:
        return None
    return Path(str(artifact_value)).expanduser()


def ensure_default_geometry_artifact(request: dict[str, Any] | None = None) -> dict[str, Any]:
    request = request or {}
    explicit_artifact_path = resolve_requested_artifact_path(request)
    if explicit_artifact_path is not None:
        if not explicit_artifact_path.exists():
            raise ValueError(f"requested geometry artifact does not exist: {explicit_artifact_path}")
        artifact = load_runtime_geometry_artifact(explicit_artifact_path)
        rollout = geometry_rollout_metadata(artifact)
        if str(rollout.get("geometry_residual_model") or "").strip().lower() != "rigid":
            raise ValueError(
                "requested geometry artifact is not rigid; only virtual-center-rectilinear-rigid is allowed on the product path"
            )
        if not bool(rollout.get("launch_ready")):
            detail = str(rollout.get("launch_ready_reason") or "requested geometry artifact is not launch-ready").strip()
            raise ValueError(detail)
        return {
            "calibrated": False,
            "artifact_path": str(explicit_artifact_path),
            "geometry_model": rollout["geometry_model"],
            "launch_ready": bool(rollout["launch_ready"]),
            "message": "explicit rigid geometry artifact selected",
        }

    site_config = load_runtime_site_config()
    paths = site_config.get("paths", {})
    homography_file = Path(str(paths.get("homography_file") or DEFAULT_HOMOGRAPHY_PATH)).expanduser()
    geometry_artifact = runtime_geometry_artifact_path(homography_file)

    if geometry_artifact.exists():
        artifact = load_runtime_geometry_artifact(geometry_artifact)
        rollout = geometry_rollout_metadata(artifact)
        if (
            bool(rollout["launch_ready"])
            and bool(rollout["geometry_operator_visible"])
            and str(rollout.get("geometry_residual_model") or "").strip().lower() == "rigid"
        ):
            return {
                "calibrated": False,
                "artifact_path": str(geometry_artifact),
                "geometry_model": rollout["geometry_model"],
                "launch_ready": True,
                "message": "existing launch-ready rigid geometry artifact reused",
            }
        if str(rollout.get("geometry_residual_model") or "").strip().lower() != "rigid":
            raise ValueError(
                "default runtime geometry artifact does not resolve to the active rigid artifact. "
                "Run mesh-refresh first to regenerate the active rigid artifact."
            )
        if not bool(rollout["launch_ready"]):
            detail = str(rollout.get("launch_ready_reason") or "default runtime geometry artifact is not launch-ready").strip()
            raise ValueError(
                f"default runtime geometry artifact is not launch-ready: {detail}"
            )

    raise ValueError(
        "No launch-ready rigid runtime geometry artifact is available. "
        "Run mesh-refresh first to regenerate the active rigid artifact."
    )


def resolve_geometry_artifact_for_plan(plan_request: dict[str, Any]) -> tuple[Path, dict[str, Any] | None]:
    request = plan_request.get("request") if isinstance(plan_request.get("request"), dict) else {}
    request_geometry = plan_request.get("request_geometry") if isinstance(plan_request.get("request_geometry"), dict) else {}
    paths = plan_request.get("paths") if isinstance(plan_request.get("paths"), dict) else {}

    homography_file = Path(str(paths.get("homography_file") or DEFAULT_HOMOGRAPHY_PATH)).expanduser()
    geometry_artifact = runtime_geometry_artifact_path(homography_file)
    geometry_artifact_value = request.get("geometry_artifact_path") or request_geometry.get("artifact_path")
    if geometry_artifact_value:
        geometry_artifact = Path(str(geometry_artifact_value)).expanduser()

    artifact: dict[str, Any] | None = None
    if geometry_artifact.exists():
        artifact = load_runtime_geometry_artifact(geometry_artifact)
    return geometry_artifact, artifact
