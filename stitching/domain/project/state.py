from __future__ import annotations

from typing import Any, Callable

from stitching.domain.geometry.models import GeometryTruthModel
from stitching.domain.project.models import (
    ProjectDebugModel,
    ProjectDebugStepModel,
    ProjectLogEntryModel,
    ProjectStateModel,
)
from stitching.domain.project.status import (
    build_project_status_context,
    zero_copy_status as _zero_copy_status_impl,
)
from stitching.domain.runtime.models import OutputPathTruthModel, RuntimeTruthModel, ZeroCopyTruthModel


def metrics_output_failure_reason(
    metrics: dict[str, Any] | None,
    *,
    is_pending_direct_fill_bridge_state: Callable[..., bool],
    command_line_token: Callable[[Any, str], str],
) -> str:
    if not isinstance(metrics, dict):
        return ""
    runtime_mode = str(metrics.get("production_output_runtime_mode") or "").strip().lower()
    command_line = str(metrics.get("production_output_command_line") or "").strip()
    bridge_reason = command_line_token(command_line, "bridge-reason")
    last_error = str(metrics.get("production_output_last_error") or "").strip()
    if is_pending_direct_fill_bridge_state(
        runtime_mode=runtime_mode,
        bridge_reason=bridge_reason,
        last_error=last_error,
    ):
        return ""
    if last_error:
        return last_error
    mode_token = command_line_token(command_line, "mode")
    if mode_token == "direct-required-blocked" and bridge_reason:
        return f"gpu-direct direct-only requirement failed: {bridge_reason}"
    if runtime_mode == "native-nvenc-direct-blocked" and bridge_reason:
        return f"gpu-direct direct-only requirement failed: {bridge_reason}"
    if runtime_mode == "native-nvenc-bridge" and bridge_reason:
        return f"gpu-direct bridge active: {bridge_reason}"
    status = str(metrics.get("status") or "").strip().lower()
    if status in {
        "warp_plan_failed",
        "homography_load_failed",
        "virtual_center_rectilinear_requires_gpu",
        "virtual_center_rectilinear_mesh_blocked_degraded",
        "virtual_center_rectilinear_mesh_map_missing",
    }:
        return status.replace("_", " ")
    if status.endswith("_failed") or status.endswith("_blocked"):
        return status.replace("_", " ")
    if status in {"gpu_only_output_blocked", "reader_start_failed", "input decode failed", "stitch_failed"}:
        return status.replace("_", " ")
    return ""


def confirm_output_timeout_sec(plan: Any) -> float:
    timeout_sec = 10.0
    reload_payload = getattr(plan, "reload_payload", None)
    reload_payload = reload_payload if isinstance(reload_payload, dict) else {}
    inputs = reload_payload.get("inputs") if isinstance(reload_payload.get("inputs"), dict) else {}
    max_input_timeout_sec = 0.0
    for side in ("left", "right"):
        side_payload = inputs.get(side) if isinstance(inputs.get(side), dict) else {}
        try:
            max_input_timeout_sec = max(max_input_timeout_sec, float(side_payload.get("timeout_sec") or 0.0))
        except (TypeError, ValueError):
            continue
    if max_input_timeout_sec > 0.0:
        timeout_sec = max(timeout_sec, max_input_timeout_sec + 10.0)
    return timeout_sec


def build_project_state_model(
    runtime_state: dict[str, Any],
    mesh_refresh_state: dict[str, Any],
    *,
    merge_runtime_and_mesh_refresh_state: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    configured_rtsp_urls_for_request: Callable[[dict[str, Any] | None], tuple[str, str]],
    require_configured_rtsp_urls: Callable[..., Any],
    project_start_needs_mesh_refresh: Callable[[dict[str, Any], Exception | None], bool],
    is_recoverable_missing_geometry_reason: Callable[[Any], bool],
    project_log_entries: Callable[..., list[dict[str, Any]]],
    build_debug_steps: Callable[..., list[dict[str, Any]]],
    debug_stage_from_phase: Callable[[Any], str],
    project_receive_uri_from_target: Callable[[Any], str],
) -> ProjectStateModel:
    merged = merge_runtime_and_mesh_refresh_state(runtime_state, mesh_refresh_state)
    status_context = build_project_status_context(
        merged,
        configured_rtsp_urls_for_request=configured_rtsp_urls_for_request,
        require_configured_rtsp_urls=require_configured_rtsp_urls,
        project_start_needs_mesh_refresh=project_start_needs_mesh_refresh,
        is_recoverable_missing_geometry_reason=is_recoverable_missing_geometry_reason,
    )
    running = bool(status_context["running"])
    status = str(status_context["status"])
    start_phase = str(status_context["start_phase"])
    phase = str(status_context["phase"])
    status_message = str(status_context["status_message"])
    blocker_reason = str(status_context["blocker_reason"])
    can_start = bool(status_context["can_start"])
    can_stop = bool(status_context["can_stop"])
    output_target = str(merged.get("production_output_target") or "").strip()
    output_mode = (
        str(merged.get("output_path_mode") or "").strip()
        or str(merged.get("production_output_runtime_mode") or "").strip()
    )
    output_bridge_reason = str(merged.get("output_bridge_reason") or "").strip()
    production_output_last_error = str(merged.get("production_output_last_error") or "").strip()

    project_log = project_log_entries(merged.get("recent_events") or [])
    debug_steps = build_debug_steps(current_phase=start_phase, status=status, project_log=project_log)

    lifecycle_state = status
    zero_copy_blockers = [str(item).strip() for item in list(merged.get("zero_copy_blockers") or []) if str(item).strip()]
    zero_copy_ready = bool(merged.get("zero_copy_ready"))
    zero_copy_status = _zero_copy_status_impl(ready=zero_copy_ready, blockers=zero_copy_blockers)

    return ProjectStateModel(
        lifecycle_state=lifecycle_state,
        phase=phase,
        status_message=status_message,
        running=running,
        can_start=can_start,
        can_stop=can_stop,
        blocker_reason=blocker_reason if status in {"blocked", "error"} else "",
        geometry=GeometryTruthModel(
            model=str(merged.get("runtime_active_model") or "").strip(),
            requested_residual_model=str(merged.get("runtime_requested_residual_model") or "").strip(),
            residual_model=str(merged.get("runtime_active_residual_model") or "").strip(),
            artifact_path=str(merged.get("runtime_active_artifact_path") or "").strip(),
            artifact_checksum=str(merged.get("runtime_artifact_checksum") or "").strip(),
            launch_ready=bool(merged.get("runtime_launch_ready")),
            launch_ready_reason=str(status_context["runtime_launch_ready_reason"]),
            rollout_status=str(merged.get("geometry_rollout_status") or "").strip(),
            fallback_used=bool(merged.get("fallback_used")),
            operator_visible=bool(merged.get("geometry_operator_visible")),
        ),
        runtime=RuntimeTruthModel(
            status=str(merged.get("status") or "").strip() or lifecycle_state,
            running=running,
            pid=int(merged["runtime_pid"]) if merged.get("runtime_pid") not in (None, "") else None,
            phase=phase,
            active_model=str(merged.get("runtime_active_model") or "").strip(),
            active_residual_model=str(merged.get("runtime_active_residual_model") or "").strip(),
            gpu_path_mode=str(merged.get("gpu_path_mode") or "unknown").strip() or "unknown",
            gpu_path_ready=bool(merged.get("gpu_path_ready")),
            input_path_mode=str(merged.get("input_path_mode") or "").strip(),
            output_path_mode=output_mode,
        ),
        output=OutputPathTruthModel(
            receive_uri=project_receive_uri_from_target(output_target) or "udp://@:24000",
            target=output_target,
            mode=output_mode,
            direct=bool(merged.get("output_path_direct")),
            bridge=bool(merged.get("output_path_bridge")),
            bridge_reason=output_bridge_reason,
            last_error=production_output_last_error,
        ),
        zero_copy=ZeroCopyTruthModel(
            ready=zero_copy_ready,
            reason=str(merged.get("zero_copy_reason") or "").strip(),
            blockers=zero_copy_blockers,
            status=zero_copy_status,
        ),
        recent_events=[
            ProjectLogEntryModel.model_validate(entry if isinstance(entry, dict) else {})
            for entry in project_log
        ],
        debug=ProjectDebugModel(
            enabled=True,
            current_stage=debug_stage_from_phase(start_phase),
            steps=[
                ProjectDebugStepModel.model_validate(step if isinstance(step, dict) else {})
                for step in debug_steps
            ],
        ),
    )


def build_project_state(
    runtime_state: dict[str, Any],
    mesh_refresh_state: dict[str, Any],
    *,
    merge_runtime_and_mesh_refresh_state: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    configured_rtsp_urls_for_request: Callable[[dict[str, Any] | None], tuple[str, str]],
    require_configured_rtsp_urls: Callable[..., Any],
    project_start_needs_mesh_refresh: Callable[[dict[str, Any], Exception | None], bool],
    is_recoverable_missing_geometry_reason: Callable[[Any], bool],
    project_log_entries: Callable[..., list[dict[str, Any]]],
    build_debug_steps: Callable[..., list[dict[str, Any]]],
    debug_stage_from_phase: Callable[[Any], str],
    project_receive_uri_from_target: Callable[[Any], str],
) -> dict[str, Any]:
    model = build_project_state_model(
        runtime_state,
        mesh_refresh_state,
        merge_runtime_and_mesh_refresh_state=merge_runtime_and_mesh_refresh_state,
        configured_rtsp_urls_for_request=configured_rtsp_urls_for_request,
        require_configured_rtsp_urls=require_configured_rtsp_urls,
        project_start_needs_mesh_refresh=project_start_needs_mesh_refresh,
        is_recoverable_missing_geometry_reason=is_recoverable_missing_geometry_reason,
        project_log_entries=project_log_entries,
        build_debug_steps=build_debug_steps,
        debug_stage_from_phase=debug_stage_from_phase,
        project_receive_uri_from_target=project_receive_uri_from_target,
    )
    return model.to_api_dict(include_legacy=True)
