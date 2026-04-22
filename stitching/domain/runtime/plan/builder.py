from __future__ import annotations

from typing import Any, Callable

from stitching.domain.geometry.policy import geometry_rollout_metadata
from stitching.domain.runtime.plan.artifact import (
    ensure_default_geometry_artifact as _ensure_default_geometry_artifact_impl,
    resolve_geometry_artifact_for_plan as _resolve_geometry_artifact_for_plan_impl,
    resolve_requested_artifact_path as _resolve_requested_artifact_path_impl,
)
from stitching.domain.runtime.plan.gpu_policy import (
    gpu_only_blockers_for_plan as _gpu_only_blockers_for_plan_impl,
)
from stitching.domain.runtime.plan.launch import (
    build_launch_spec_and_reload_payload as _build_launch_spec_and_reload_payload_impl,
)
from stitching.domain.runtime.plan.normalization import (
    configured_rtsp_urls as _configured_rtsp_urls_impl,
    normalize_runtime_plan_request as _normalize_runtime_plan_request_impl,
)
from stitching.domain.runtime.site_config import load_runtime_site_config, require_configured_rtsp_urls


def resolve_requested_artifact_path(request: dict[str, Any] | None = None):
    return _resolve_requested_artifact_path_impl(request)


def ensure_default_geometry_artifact(request: dict[str, Any] | None = None) -> dict[str, Any]:
    return _ensure_default_geometry_artifact_impl(request)


def build_runtime_plan(
    request: dict[str, Any] | None = None,
    *,
    plan_factory: Callable[..., Any],
) -> Any:
    site_config = load_runtime_site_config()
    plan_request = _normalize_runtime_plan_request_impl(request, site_config=site_config)
    left_rtsp, right_rtsp = _configured_rtsp_urls_impl(plan_request)
    if not left_rtsp or not right_rtsp:
        raise ValueError("left_rtsp and right_rtsp must be configured")
    require_configured_rtsp_urls(left_rtsp, right_rtsp, context="runtime backend")

    geometry_artifact, artifact = _resolve_geometry_artifact_for_plan_impl(plan_request)
    launch_spec, reload_payload = _build_launch_spec_and_reload_payload_impl(
        plan_request,
        left_rtsp=left_rtsp,
        right_rtsp=right_rtsp,
        geometry_artifact=geometry_artifact,
    )
    rollout = geometry_rollout_metadata(artifact or {})
    summary = {
        "geometry_artifact_path": str(geometry_artifact),
        "left_rtsp": left_rtsp,
        "right_rtsp": right_rtsp,
        "probe_target": str(launch_spec.output_target or ""),
        "transmit_target": str(launch_spec.production_output_target or ""),
        "output_runtime_mode": str(launch_spec.output_runtime or ""),
        "production_output_runtime_mode": str(launch_spec.production_output_runtime or ""),
        "sync_pair_mode": str(launch_spec.sync_pair_mode),
        "runtime_schema_version": 2,
        "gpu_only_mode": str(launch_spec.gpu_mode).strip().lower() == "only",
        "geometry_artifact_model": rollout["geometry_model"],
        "geometry_residual_model": rollout["geometry_residual_model"],
        "geometry_rollout_status": rollout["geometry_rollout_status"],
        "geometry_operator_visible": bool(rollout["geometry_operator_visible"]),
        "geometry_fallback_only": bool(rollout["geometry_fallback_only"]),
        "launch_ready": bool(rollout["launch_ready"]),
        "launch_ready_reason": str(rollout["launch_ready_reason"]),
    }
    return plan_factory(
        geometry_artifact_path=geometry_artifact,
        launch_spec=launch_spec,
        reload_payload=reload_payload,
        summary=summary,
    )


def gpu_only_blockers_for_plan(plan: Any) -> list[str]:
    return _gpu_only_blockers_for_plan_impl(plan)
