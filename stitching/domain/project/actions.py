from __future__ import annotations

from typing import Any, Callable

from stitching.domain.project.models import (
    ProjectActionResponseModel,
    ProjectStateModel,
)
from stitching.domain.runtime.errors import ProjectBlockedError, ProjectRequestError


def project_start_response_model(
    backend: Any,
    mesh_refresh: Any,
    body: dict[str, Any] | None = None,
    *,
    project_state_func: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    configured_rtsp_urls_for_request: Callable[[dict[str, Any] | None], tuple[str, str]],
    require_configured_rtsp_urls: Callable[..., Any],
    internal_mesh_refresh: Callable[..., dict[str, Any]],
    merge_runtime_and_mesh_refresh_state: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    project_start_needs_mesh_refresh: Callable[[dict[str, Any], Exception | None], bool],
    request_force_mesh_refresh: Callable[[dict[str, Any] | None], bool],
) -> ProjectActionResponseModel:
    request = body or {}
    initial_state = project_state_func(backend.state(), mesh_refresh.state())
    if bool(initial_state.get("running")):
        return ProjectActionResponseModel(
            ok=True,
            message="Project is already running.",
            state=ProjectStateModel.from_api_payload(initial_state),
        )

    backend.set_project_progress("check_config", "Checking runtime config and camera inputs.")
    try:
        left_rtsp, right_rtsp = configured_rtsp_urls_for_request(request)
        require_configured_rtsp_urls(left_rtsp, right_rtsp, context="Start Project")
    except Exception as exc:
        backend.set_project_progress("blocked", str(exc))
        raise ProjectRequestError(str(exc)) from exc

    explicit_artifact_path = backend._resolve_requested_artifact_path(request)
    force_mesh_refresh = request_force_mesh_refresh(request)
    mesh_refresh_triggered = False
    prepare_result: dict[str, Any] | None = None
    if force_mesh_refresh:
        if explicit_artifact_path is not None:
            detail = "refresh_geometry cannot be used together with geometry.artifact_path"
            backend.set_project_progress("blocked", detail)
            raise ProjectRequestError(detail)
        backend.set_project_progress("connect_inputs", "Connecting to the camera streams.")
        try:
            internal_mesh_refresh(mesh_refresh, request, progress=backend.set_project_progress)
        except Exception as exc:
            detail = str(exc).strip() or "mesh refresh failed"
            backend.set_project_progress("blocked", detail)
            raise ProjectBlockedError(detail) from exc
        mesh_refresh_triggered = True
    try:
        backend.set_project_progress("preparing_runtime", "Preparing runtime.")
        prepare_result = backend.prepare(request)
    except ProjectBlockedError as exc:
        latest_state = merge_runtime_and_mesh_refresh_state(backend.state(), mesh_refresh.state())
        if explicit_artifact_path is None and not mesh_refresh_triggered and project_start_needs_mesh_refresh(latest_state, exc):
            backend.set_project_progress("connect_inputs", "Connecting to the camera streams.")
            try:
                internal_mesh_refresh(mesh_refresh, request, progress=backend.set_project_progress)
            except Exception as mesh_exc:
                detail = str(mesh_exc).strip() or "mesh refresh failed"
                backend.set_project_progress("blocked", detail)
                raise ProjectBlockedError(detail) from mesh_exc
            mesh_refresh_triggered = True
            backend.set_project_progress("preparing_runtime", "Preparing runtime.")
            prepare_result = backend.prepare(request)
        else:
            backend.set_project_progress("blocked", str(exc))
            raise
    except ProjectRequestError as exc:
        backend.set_project_progress("blocked", str(exc))
        raise
    except Exception as exc:
        backend.set_project_progress("error", str(exc))
        raise

    prepared_project_state = project_state_func(backend.state(), mesh_refresh.state())
    if explicit_artifact_path is None and not mesh_refresh_triggered and project_start_needs_mesh_refresh(
        prepared_project_state,
        None,
    ):
        backend.set_project_progress("connect_inputs", "Connecting to the camera streams.")
        try:
            internal_mesh_refresh(mesh_refresh, request, progress=backend.set_project_progress)
        except Exception as exc:
            detail = str(exc).strip() or "mesh refresh failed"
            backend.set_project_progress("blocked", detail)
            raise ProjectBlockedError(detail) from exc
        mesh_refresh_triggered = True
        backend.set_project_progress("preparing_runtime", "Preparing runtime.")
        prepare_result = backend.prepare(request)
        prepared_project_state = project_state_func(backend.state(), mesh_refresh.state())
    prepared_blocker = str(prepared_project_state.get("blocker_reason") or "").strip()
    if prepared_blocker:
        backend.set_project_progress("blocked", prepared_blocker)
        raise ProjectBlockedError(prepared_blocker)
    if not bool(prepared_project_state.get("runtime_launch_ready")):
        reason = str(prepared_project_state.get("runtime_launch_ready_reason") or "Runtime launch is blocked.")
        backend.set_project_progress("blocked", reason)
        raise ProjectBlockedError(reason)

    backend.set_project_progress("launch_runtime", "Launching the native runtime.")
    try:
        result = backend.start(request)
    except (ProjectBlockedError, ProjectRequestError) as exc:
        backend.set_project_progress("blocked", str(exc))
        raise
    except Exception as exc:
        backend.set_project_progress("error", str(exc))
        raise

    response = ProjectActionResponseModel(
        ok=bool(result.get("ok", True)) if isinstance(result, dict) else True,
        message=str(result.get("message") or "").strip() if isinstance(result, dict) else "",
        state=ProjectStateModel.from_api_payload(project_state_func(backend.state(), mesh_refresh.state())),
    )
    message = response.message or "Project started."
    if mesh_refresh_triggered:
        response.message = f"Stitch geometry was recalculated automatically. {message}".strip()
    elif prepare_result is not None and bool(prepare_result.get("auto_calibrated")):
        response.message = f"Stitch geometry was recalculated automatically. {message}".strip()
    else:
        response.message = message
    return response


def project_start_response(
    backend: Any,
    mesh_refresh: Any,
    body: dict[str, Any] | None = None,
    *,
    project_state_func: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    configured_rtsp_urls_for_request: Callable[[dict[str, Any] | None], tuple[str, str]],
    require_configured_rtsp_urls: Callable[..., Any],
    internal_mesh_refresh: Callable[..., dict[str, Any]],
    merge_runtime_and_mesh_refresh_state: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    project_start_needs_mesh_refresh: Callable[[dict[str, Any], Exception | None], bool],
    request_force_mesh_refresh: Callable[[dict[str, Any] | None], bool],
) -> dict[str, Any]:
    model = project_start_response_model(
        backend,
        mesh_refresh,
        body,
        project_state_func=project_state_func,
        configured_rtsp_urls_for_request=configured_rtsp_urls_for_request,
        require_configured_rtsp_urls=require_configured_rtsp_urls,
        internal_mesh_refresh=internal_mesh_refresh,
        merge_runtime_and_mesh_refresh_state=merge_runtime_and_mesh_refresh_state,
        project_start_needs_mesh_refresh=project_start_needs_mesh_refresh,
        request_force_mesh_refresh=request_force_mesh_refresh,
    )
    return model.to_api_dict(include_legacy=True)
