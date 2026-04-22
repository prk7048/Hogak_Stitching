import unittest
from typing import Any

from stitching.domain.runtime.backend.api import create_app


def sample_project_state(*, lifecycle_state: str = "idle", phase: str = "idle", running: bool = False) -> dict[str, Any]:
    return {
        "lifecycle_state": lifecycle_state,
        "phase": phase,
        "status_message": "Project is running." if running else "Project is ready to start.",
        "running": running,
        "can_start": not running,
        "can_stop": running,
        "blocker_reason": "",
        "geometry": {
            "model": "virtual-center-rectilinear-rigid",
            "requested_residual_model": "rigid",
            "residual_model": "rigid",
            "artifact_path": "data/runtime_geometry.json",
            "artifact_checksum": "abc123",
            "launch_ready": True,
            "launch_ready_reason": "",
            "rollout_status": "active",
            "fallback_used": False,
            "operator_visible": True,
        },
        "runtime": {
            "status": lifecycle_state,
            "running": running,
            "pid": 0,
            "phase": phase,
            "active_model": "virtual-center-rectilinear-rigid",
            "active_residual_model": "rigid",
            "gpu_path_mode": "native-nvenc-direct",
            "gpu_path_ready": True,
            "input_path_mode": "ffmpeg-cuda",
            "output_path_mode": "native-nvenc-direct",
        },
        "output": {
            "receive_uri": "udp://@:24000",
            "target": "udp://127.0.0.1:24000?pkt_size=1316",
            "mode": "native-nvenc-direct",
            "direct": True,
            "bridge": False,
            "bridge_reason": "",
            "last_error": "",
        },
        "zero_copy": {
            "ready": True,
            "reason": "",
            "blockers": [],
            "status": "ready",
        },
        "recent_events": [],
        "debug": {
            "enabled": False,
            "current_stage": "",
            "steps": [],
        },
    }


class DummyBackend:
    def __init__(self) -> None:
        self._state = sample_project_state()

    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def stop(self) -> dict[str, Any]:
        self._state = sample_project_state()
        return {"ok": True, "message": "Project stopped.", "state": self._state}


class DummyMeshRefreshService:
    def state(self) -> dict[str, Any]:
        return {"status": "idle"}


def route_for(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"route not found: {method} {path}")


class ProjectApiSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = DummyBackend()
        self.app = create_app(
            service=self.backend,
            mesh_refresh_service=DummyMeshRefreshService(),
            frontend_dist_dir="frontend/dist-missing-for-tests",
            project_state_func=lambda runtime_state, mesh_state: runtime_state,
            project_start_response_func=lambda backend, mesh_refresh, body: {
                "ok": True,
                "message": "Project started.",
                "state": sample_project_state(lifecycle_state="running", phase="running", running=True),
            },
        )

    def test_project_routes_are_limited_to_state_start_stop(self) -> None:
        project_routes = {
            route.path: set(route.methods)
            for route in self.app.routes
            if str(getattr(route, "path", "")).startswith("/api/project/")
        }
        self.assertEqual(set(project_routes), {"/api/project/state", "/api/project/start", "/api/project/stop"})
        self.assertIn("GET", project_routes["/api/project/state"])
        self.assertEqual(project_routes["/api/project/start"], {"POST"})
        self.assertEqual(project_routes["/api/project/stop"], {"POST"})

    def test_state_payload_uses_current_contract_only(self) -> None:
        route = route_for(self.app, "/api/project/state", "GET")
        payload = route.endpoint()

        self.assertEqual(payload["lifecycle_state"], "idle")
        self.assertEqual(payload["geometry"]["residual_model"], "rigid")
        self.assertNotIn("status", payload)
        self.assertNotIn("start_phase", payload)

    def test_frontend_fallback_page_describes_current_public_surface(self) -> None:
        route = route_for(self.app, "/{full_path:path}", "GET")
        response = route.endpoint("")
        html = response.body.decode("utf-8")

        self.assertIn("/api/project/state", html)
        self.assertIn("/api/project/start", html)
        self.assertIn("/api/project/stop", html)
        self.assertIn("virtual-center-rectilinear-rigid", html)
        self.assertNotIn("/bakeoff", html)
