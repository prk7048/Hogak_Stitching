from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from stitching.domain.runtime.backend.api import create_app as _create_app_impl
from stitching.domain.runtime.backend.debug import (
    _build_debug_steps,
    _debug_stage_from_phase,
    _project_log_entries,
)
from stitching.domain.geometry.refresh_service import MeshRefreshService
from stitching.domain.runtime.backend.helpers import (
    _configured_rtsp_urls_for_request,
    _internal_mesh_refresh,
    _request_force_mesh_refresh,
)
from stitching.domain.runtime.backend.status import (
    _is_recoverable_missing_geometry_reason,
    _merge_runtime_and_mesh_refresh_state,
    _project_receive_uri_from_target,
    _project_start_needs_mesh_refresh,
)
from stitching.domain.project.state import (
    build_project_state as _build_project_state_impl,
)
from stitching.domain.project.actions import (
    project_start_response as _project_start_response_impl,
)
from stitching.domain.runtime.service import (
    RuntimeService,
)
from stitching.domain.runtime.site_config import require_configured_rtsp_urls

# Compatibility note: this facade still defines the operator-facing runtime
# metrics contract, including output_queue_capacity and
# production_output_queue_capacity.



def _project_state(runtime_state: dict[str, Any], mesh_refresh_state: dict[str, Any]) -> dict[str, Any]:
    return _build_project_state_impl(
        runtime_state,
        mesh_refresh_state,
        merge_runtime_and_mesh_refresh_state=_merge_runtime_and_mesh_refresh_state,
        configured_rtsp_urls_for_request=_configured_rtsp_urls_for_request,
        require_configured_rtsp_urls=require_configured_rtsp_urls,
        project_start_needs_mesh_refresh=_project_start_needs_mesh_refresh,
        is_recoverable_missing_geometry_reason=_is_recoverable_missing_geometry_reason,
        project_log_entries=_project_log_entries,
        build_debug_steps=_build_debug_steps,
        debug_stage_from_phase=_debug_stage_from_phase,
        project_receive_uri_from_target=_project_receive_uri_from_target,
    )

def _project_start_response(
    backend: "RuntimeService",
    mesh_refresh: MeshRefreshService,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _project_start_response_impl(
        backend,
        mesh_refresh,
        body,
        project_state_func=_project_state,
        configured_rtsp_urls_for_request=_configured_rtsp_urls_for_request,
        require_configured_rtsp_urls=require_configured_rtsp_urls,
        internal_mesh_refresh=_internal_mesh_refresh,
        merge_runtime_and_mesh_refresh_state=_merge_runtime_and_mesh_refresh_state,
        project_start_needs_mesh_refresh=_project_start_needs_mesh_refresh,
        request_force_mesh_refresh=_request_force_mesh_refresh,
    )


def create_app(
    *,
    service: RuntimeService | None = None,
    mesh_refresh_service: MeshRefreshService | None = None,
    frontend_dist_dir: str | Path | None = None,
) -> FastAPI:
    return _create_app_impl(
        service=service,
        mesh_refresh_service=mesh_refresh_service,
        frontend_dist_dir=frontend_dist_dir,
        runtime_service_factory=RuntimeService,
        project_state_func=_project_state,
        project_start_response_func=_project_start_response,
    )


app = create_app()


def main() -> int:
    try:
        import uvicorn
    except Exception as exc:
        raise RuntimeError("uvicorn is required to run the runtime backend") from exc

    host = os.environ.get("HOGAK_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("HOGAK_BACKEND_PORT", "8088"))
    uvicorn.run("stitching.domain.runtime.backend:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
