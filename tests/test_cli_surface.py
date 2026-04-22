import unittest

from stitching.cli import parse_args
from stitching.domain.geometry.refresh_service import DEFAULT_CLIP_FRAMES, DEFAULT_MESH_REFRESH_WARMUP_FRAMES
from stitching.domain.runtime.defaults import DEFAULT_RTSP_TIMEOUT_SEC, DEFAULT_RTSP_TRANSPORT


class CliSurfaceTests(unittest.TestCase):
    def test_operator_server_is_supported(self) -> None:
        args = parse_args(["operator-server"])
        self.assertEqual(args.command, "operator-server")

    def test_mesh_refresh_is_supported(self) -> None:
        args = parse_args(["mesh-refresh"])
        self.assertEqual(args.command, "mesh-refresh")
        self.assertTrue(hasattr(args, "clip_frames"))
        self.assertEqual(args.rtsp_transport, DEFAULT_RTSP_TRANSPORT)
        self.assertEqual(args.rtsp_timeout_sec, DEFAULT_RTSP_TIMEOUT_SEC)
        self.assertEqual(args.clip_frames, DEFAULT_CLIP_FRAMES)
        self.assertEqual(args.warmup_frames, DEFAULT_MESH_REFRESH_WARMUP_FRAMES)

    def test_removed_legacy_command_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            parse_args(["run-runtime"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_mesh_refresh_rejects_interactive_calibration_flags(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            parse_args(["mesh-refresh", "--calibration-mode", "manual"])
        self.assertNotEqual(ctx.exception.code, 0)
