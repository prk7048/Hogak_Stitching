import unittest

from stitching.domain.project.status import build_project_status_context


class ProjectStatusTests(unittest.TestCase):
    def test_check_config_counts_as_starting(self) -> None:
        context = build_project_status_context(
            {
                "running": False,
                "project_start_phase": "check_config",
                "project_status_message": "",
                "runtime_launch_ready": True,
                "runtime_launch_ready_reason": "",
            },
            configured_rtsp_urls_for_request=lambda request: ("rtsp://10.0.0.1/live", "rtsp://10.0.0.2/live"),
            require_configured_rtsp_urls=lambda left, right, context: None,
            project_start_needs_mesh_refresh=lambda merged, exc: False,
            is_recoverable_missing_geometry_reason=lambda value: False,
        )

        self.assertEqual(context["status"], "starting")
        self.assertEqual(context["phase"], "check_config")
        self.assertFalse(context["can_start"])
        self.assertIn("Start Project", context["status_message"])

    def test_last_error_is_reported_as_error_when_no_blocker_exists(self) -> None:
        context = build_project_status_context(
            {
                "running": False,
                "project_start_phase": "idle",
                "project_status_message": "",
                "runtime_launch_ready": True,
                "runtime_launch_ready_reason": "",
                "last_error": "native runtime launch handshake failed",
            },
            configured_rtsp_urls_for_request=lambda request: ("rtsp://10.0.0.1/live", "rtsp://10.0.0.2/live"),
            require_configured_rtsp_urls=lambda left, right, context: None,
            project_start_needs_mesh_refresh=lambda merged, exc: False,
            is_recoverable_missing_geometry_reason=lambda value: False,
        )

        self.assertEqual(context["status"], "error")
        self.assertEqual(context["blocker_reason"], "")
        self.assertTrue(context["can_start"])
        self.assertIn("failed", context["status_message"].lower())
