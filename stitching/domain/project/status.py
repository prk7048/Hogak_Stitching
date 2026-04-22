from __future__ import annotations

from typing import Any, Callable


STARTING_PHASES = {
    "check_config",
    "checking_inputs",
    "refreshing_mesh",
    "connect_inputs",
    "capture_frames",
    "match_features",
    "solve_geometry",
    "build_artifact",
    "artifact_ready",
    "preparing_runtime",
    "starting_runtime",
    "launch_runtime",
    "confirm_output",
}


def project_config_blocker(
    *,
    configured_rtsp_urls_for_request: Callable[[dict[str, Any] | None], tuple[str, str]],
    require_configured_rtsp_urls: Callable[..., Any],
) -> str:
    try:
        left_rtsp, right_rtsp = configured_rtsp_urls_for_request(None)
        require_configured_rtsp_urls(left_rtsp, right_rtsp, context="Start Project")
    except Exception as exc:
        return str(exc)
    return ""


def zero_copy_status(*, ready: bool, blockers: list[str]) -> str:
    if ready:
        return "ready"
    if blockers:
        return "blocked"
    return "pending"


def build_project_status_context(
    merged: dict[str, Any],
    *,
    configured_rtsp_urls_for_request: Callable[[dict[str, Any] | None], tuple[str, str]],
    require_configured_rtsp_urls: Callable[..., Any],
    project_start_needs_mesh_refresh: Callable[[dict[str, Any], Exception | None], bool],
    is_recoverable_missing_geometry_reason: Callable[[Any], bool],
) -> dict[str, Any]:
    merged = dict(merged or {})
    running = bool(merged.get("running"))
    last_error = str(merged.get("last_error") or "").strip()
    if last_error.lower().startswith("gpu-direct bridge active:"):
        last_error = ""
    config_blocker = project_config_blocker(
        configured_rtsp_urls_for_request=configured_rtsp_urls_for_request,
        require_configured_rtsp_urls=require_configured_rtsp_urls,
    )

    start_phase = str(merged.get("project_start_phase") or "").strip().lower()
    status_message = str(merged.get("project_status_message") or "").strip()
    merged_blocker = str(merged.get("blocker_reason") or "").strip()
    runtime_launch_ready_reason = str(merged.get("runtime_launch_ready_reason") or "").strip()
    needs_mesh_refresh = project_start_needs_mesh_refresh(merged, None)
    if needs_mesh_refresh:
        if is_recoverable_missing_geometry_reason(last_error):
            last_error = ""
        if is_recoverable_missing_geometry_reason(merged_blocker):
            merged_blocker = ""
        if is_recoverable_missing_geometry_reason(runtime_launch_ready_reason):
            runtime_launch_ready_reason = (
                "Start Project will regenerate stitch geometry because no launch-ready rigid artifact is active."
            )
    runtime_blocker = ""
    if not needs_mesh_refresh and not bool(merged.get("runtime_launch_ready")):
        runtime_blocker = runtime_launch_ready_reason
    blocker_reason = config_blocker or merged_blocker or runtime_blocker

    if running:
        status = "running"
    elif start_phase in STARTING_PHASES:
        status = "starting"
    elif blocker_reason:
        status = "blocked"
    elif last_error:
        status = "error"
    else:
        status = "idle"

    if status == "running" and not status_message:
        status_message = "Project is running. Open the external player to confirm the stitched runtime output."
    elif status == "starting" and not status_message:
        status_message = "Start Project is preparing the stitched runtime."
    elif status == "blocked" and not status_message:
        status_message = blocker_reason or "Project start is blocked."
    elif status == "error" and not status_message:
        status_message = last_error or "Project start failed."
    elif not status_message:
        status_message = (
            "Start Project will regenerate stitch geometry because no launch-ready rigid artifact is active."
            if needs_mesh_refresh
            else "Project is ready to start."
        )

    return {
        "running": running,
        "last_error": last_error,
        "start_phase": start_phase,
        "phase": start_phase or ("running" if running else "idle"),
        "needs_mesh_refresh": needs_mesh_refresh,
        "blocker_reason": blocker_reason,
        "status": status,
        "status_message": status_message,
        "can_start": not running and status != "starting" and not blocker_reason,
        "can_stop": running,
        "runtime_launch_ready_reason": runtime_launch_ready_reason,
    }
