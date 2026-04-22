from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI

from stitching.domain.runtime.backend.frontend import install_frontend_routes
from stitching.domain.geometry.refresh_service import MeshRefreshService
from stitching.domain.project.routes import install_project_routes


def create_app(
    *,
    service: Any | None = None,
    mesh_refresh_service: MeshRefreshService | None = None,
    frontend_dist_dir: str | Path | None = None,
    runtime_service_factory: Callable[[], Any] | None = None,
    project_state_func: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    project_start_response_func: Callable[[Any, Any, dict[str, Any] | None], dict[str, Any]] | None = None,
) -> FastAPI:
    if project_state_func is None or project_start_response_func is None:
        raise ValueError("project_state_func and project_start_response_func are required")
    if service is None and runtime_service_factory is None:
        raise ValueError("runtime_service_factory is required when service is not provided")

    backend = service if service is not None else runtime_service_factory()
    mesh_refresh = mesh_refresh_service if mesh_refresh_service is not None else MeshRefreshService()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        try:
            yield
        finally:
            try:
                backend.stop()
            except Exception:
                pass

    app = FastAPI(
        title="Hogak Runtime API",
        version="2",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.runtime_service = backend
    app.state.mesh_refresh_service = mesh_refresh

    install_project_routes(
        app,
        backend=backend,
        mesh_refresh=mesh_refresh,
        project_state_func=project_state_func,
        project_start_response_func=project_start_response_func,
    )

    install_frontend_routes(app, frontend_dist_dir=frontend_dist_dir)

    return app
