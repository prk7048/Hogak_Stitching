import unittest

from stitching.domain.runtime.site_config import RuntimeSiteConfigError, require_configured_rtsp_urls


PLACEHOLDER_LEFT = "rtsp://admin:password@left-camera.example.invalid:554/cam/realmonitor?channel=1&subtype=0"
PLACEHOLDER_RIGHT = "rtsp://admin:password@right-camera.example.invalid:554/cam/realmonitor?channel=1&subtype=0"


class ConfigGuidanceTests(unittest.TestCase):
    def test_placeholder_rtsp_error_points_to_runtime_local(self) -> None:
        with self.assertRaises(RuntimeSiteConfigError) as ctx:
            require_configured_rtsp_urls(PLACEHOLDER_LEFT, PLACEHOLDER_RIGHT, context="Start Project")

        message = str(ctx.exception)
        self.assertIn("config/runtime.local.json", message)
        self.assertIn("HOGAK_LEFT_RTSP", message)
        self.assertIn("config/runtime.json keeps placeholder values", message)
        self.assertIn("left_rtsp", message)
        self.assertIn("right_rtsp", message)
