from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, HTTPException

from stitching.domain.project.models import (
    ProjectActionResponseModel,
    ProjectStartRequestModel,
    ProjectStateModel,
)
from stitching.domain.runtime.errors import ProjectBlockedError, ProjectRequestError


def project_state_payload(
    backend: Any,
    mesh_refresh: Any,
    *,
    project_state_func: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    return ProjectStateModel.from_api_payload(
        project_state_func(backend.state(), mesh_refresh.state())
    ).to_api_dict(include_legacy=False)


def project_action_payload(
    backend: Any,
    mesh_refresh: Any,
    result: Any,
    *,
    project_state_func: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    fallback_message: str = "",
) -> dict[str, Any]:
    payload = result if isinstance(result, dict) else {}
    if "state" not in payload:
        payload = {
            **payload,
            "state": project_state_func(backend.state(), mesh_refresh.state()),
        }
    response = ProjectActionResponseModel.model_validate(payload)
    if fallback_message and not str(response.message or "").strip():
        response.message = fallback_message
    return response.to_api_dict(include_legacy=False)


def install_project_routes(
    app: FastAPI,
    *,
    backend: Any,
    mesh_refresh: Any,
    project_state_func: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    project_start_response_func: Callable[[Any, Any, dict[str, Any] | None], dict[str, Any]],
) -> None:
    @app.get("/api/project/state", response_model=ProjectStateModel)
    def project_state():
        return project_state_payload(
            backend,
            mesh_refresh,
            project_state_func=project_state_func,
        )

    @app.post("/api/project/start", response_model=ProjectActionResponseModel)
    def project_start(body: ProjectStartRequestModel | None = None):
        try:
            request_body = body.to_request_dict() if body is not None else {}
            return project_action_payload(
                backend,
                mesh_refresh,
                project_start_response_func(backend, mesh_refresh, request_body),
                project_state_func=project_state_func,
            )
        except ProjectRequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ProjectBlockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            detail = str(exc).strip() or "unexpected project start failure"
            raise HTTPException(status_code=500, detail=detail) from exc

    @app.post("/api/project/stop", response_model=ProjectActionResponseModel)
    def project_stop():
        current_state = ProjectStateModel.from_api_payload(project_state_func(backend.state(), mesh_refresh.state()))
        if not bool(current_state.running):
            return ProjectActionResponseModel(
                ok=True,
                message="Project is already stopped.",
                state=current_state,
            ).to_api_dict(include_legacy=False)
        result = backend.stop()
        return project_action_payload(
            backend,
            mesh_refresh,
            result,
            project_state_func=project_state_func,
            fallback_message="Project stopped.",
        )
