import unittest
from pathlib import Path

from stitching.domain.geometry.refresh_service import _mesh_refresh_config_from_body
from stitching.domain.runtime.defaults import (
    DEFAULT_CALIBRATION_DEBUG_DIR,
    DEFAULT_CALIBRATION_INLIERS_FILE,
    DEFAULT_HOMOGRAPHY_PATH,
    DEFAULT_RTSP_TIMEOUT_SEC,
    DEFAULT_RTSP_TRANSPORT,
)


class MeshRefreshConfigTests(unittest.TestCase):
    def test_service_defaults_match_runtime_defaults(self) -> None:
        config = _mesh_refresh_config_from_body({})

        self.assertEqual(config.output_path, Path(DEFAULT_HOMOGRAPHY_PATH))
        self.assertEqual(config.inliers_output_path, Path(DEFAULT_CALIBRATION_INLIERS_FILE))
        self.assertEqual(config.debug_dir, Path(DEFAULT_CALIBRATION_DEBUG_DIR))
        self.assertEqual(config.rtsp_transport, DEFAULT_RTSP_TRANSPORT)
        self.assertEqual(config.rtsp_timeout_sec, DEFAULT_RTSP_TIMEOUT_SEC)
        self.assertEqual(config.calibration_mode, "auto")
