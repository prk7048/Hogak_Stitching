from __future__ import annotations

import os


DEFAULT_LEFT_RTSP = "rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1&subtype=0"
DEFAULT_RIGHT_RTSP = "rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1&subtype=0"
DEFAULT_NATIVE_HOMOGRAPHY_PATH = "output/native/runtime_homography.json"
DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR = "output/native/calibration"
DEFAULT_OUTPUT_STANDARD = "realtime_hq_1080p"


def default_left_rtsp() -> str:
    return os.environ.get("HOGAK_LEFT_RTSP", DEFAULT_LEFT_RTSP)


def default_right_rtsp() -> str:
    return os.environ.get("HOGAK_RIGHT_RTSP", DEFAULT_RIGHT_RTSP)


def default_output_standard() -> str:
    return os.environ.get("HOGAK_OUTPUT_STANDARD", DEFAULT_OUTPUT_STANDARD)
