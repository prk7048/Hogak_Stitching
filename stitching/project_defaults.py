from __future__ import annotations

import os


DEFAULT_LEFT_RTSP = "rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1&subtype=0"
DEFAULT_RIGHT_RTSP = "rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1&subtype=0"
DEFAULT_NATIVE_HOMOGRAPHY_PATH = "output/native/runtime_homography.json"
DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR = "output/native/calibration"
DEFAULT_OUTPUT_STANDARD = "realtime_hq_1080p"
DEFAULT_NATIVE_INPUT_RUNTIME = "ffmpeg-cuda"
DEFAULT_NATIVE_INPUT_PIPE_FORMAT = "nv12"
DEFAULT_NATIVE_RTSP_TRANSPORT = "udp"
DEFAULT_NATIVE_INPUT_BUFFER_FRAMES = 8
DEFAULT_NATIVE_RTSP_TIMEOUT_SEC = 10.0
DEFAULT_NATIVE_RECONNECT_COOLDOWN_SEC = 0.5
DEFAULT_NATIVE_PAIR_REUSE_MAX_AGE_MS = 140.0
DEFAULT_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE = 4
DEFAULT_NATIVE_SYNC_MATCH_MAX_DELTA_MS = 35.0
DEFAULT_NATIVE_STATUS_INTERVAL_SEC = 5.0
DEFAULT_NATIVE_VIEWER_BACKEND = "auto"
DEFAULT_NATIVE_PROBE_SOURCE = "auto"
DEFAULT_NATIVE_PROBE_TARGET = "udp://127.0.0.1:23000?pkt_size=1316"
DEFAULT_NATIVE_TRANSMIT_TARGET = "udp://127.0.0.1:24000?pkt_size=1316"
DEFAULT_NATIVE_VLC_PREVIEW_TARGET = "tcp://127.0.0.1:24001"


def default_left_rtsp() -> str:
    return os.environ.get("HOGAK_LEFT_RTSP", DEFAULT_LEFT_RTSP)


def default_right_rtsp() -> str:
    return os.environ.get("HOGAK_RIGHT_RTSP", DEFAULT_RIGHT_RTSP)


def default_output_standard() -> str:
    return os.environ.get("HOGAK_OUTPUT_STANDARD", DEFAULT_OUTPUT_STANDARD)


def default_native_viewer_backend() -> str:
    return os.environ.get("HOGAK_VIEWER_BACKEND", DEFAULT_NATIVE_VIEWER_BACKEND)
