from __future__ import annotations

import os

from stitching.runtime_site_config import (
    site_config_bool,
    site_config_float,
    site_config_int,
    site_config_str,
)

_FALLBACK_LEFT_RTSP = "rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1&subtype=0"
_FALLBACK_RIGHT_RTSP = "rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1&subtype=0"
_FALLBACK_NATIVE_HOMOGRAPHY_PATH = "data/runtime_homography.json"
_FALLBACK_NATIVE_CALIBRATION_DEBUG_DIR = "output/calibration"
_FALLBACK_OUTPUT_STANDARD = "realtime_hq_1080p"
_FALLBACK_NATIVE_INPUT_RUNTIME = "ffmpeg-cuda"
_FALLBACK_NATIVE_INPUT_PIPE_FORMAT = "nv12"
_FALLBACK_NATIVE_RTSP_TRANSPORT = "udp"
_FALLBACK_NATIVE_INPUT_BUFFER_FRAMES = 8
_FALLBACK_NATIVE_RTSP_TIMEOUT_SEC = 10.0
_FALLBACK_NATIVE_RECONNECT_COOLDOWN_SEC = 0.5
_FALLBACK_NATIVE_PAIR_REUSE_MAX_AGE_MS = 140.0
_FALLBACK_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE = 4
_FALLBACK_NATIVE_SYNC_MATCH_MAX_DELTA_MS = 35.0
_FALLBACK_NATIVE_STATUS_INTERVAL_SEC = 5.0
_FALLBACK_NATIVE_VIEWER_BACKEND = "auto"
_FALLBACK_NATIVE_PROBE_SOURCE = "standalone"
_FALLBACK_NATIVE_PROBE_RUNTIME = "ffmpeg"
_FALLBACK_NATIVE_PROBE_TARGET = "udp://127.0.0.1:23000?pkt_size=1316"
_FALLBACK_NATIVE_TRANSMIT_RUNTIME = "gpu-direct"
_FALLBACK_NATIVE_TRANSMIT_TARGET = "udp://127.0.0.1:24000?pkt_size=1316"
_FALLBACK_NATIVE_TRANSMIT_BITRATE = "16M"
_FALLBACK_NATIVE_TRANSMIT_PRESET = "p4"
_FALLBACK_NATIVE_TRANSMIT_WIDTH = 0
_FALLBACK_NATIVE_TRANSMIT_HEIGHT = 0
_FALLBACK_NATIVE_OUTPUT_CADENCE_FPS = 30.0
_FALLBACK_NATIVE_TRANSMIT_DEBUG_OVERLAY = True

DEFAULT_LEFT_RTSP = site_config_str("cameras.left_rtsp", _FALLBACK_LEFT_RTSP)
DEFAULT_RIGHT_RTSP = site_config_str("cameras.right_rtsp", _FALLBACK_RIGHT_RTSP)
DEFAULT_NATIVE_HOMOGRAPHY_PATH = site_config_str("paths.homography_file", _FALLBACK_NATIVE_HOMOGRAPHY_PATH)
DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR = site_config_str(
    "paths.calibration_debug_dir",
    _FALLBACK_NATIVE_CALIBRATION_DEBUG_DIR,
)
DEFAULT_OUTPUT_STANDARD = site_config_str("runtime.output_standard", _FALLBACK_OUTPUT_STANDARD)
DEFAULT_NATIVE_INPUT_RUNTIME = site_config_str("runtime.input_runtime", _FALLBACK_NATIVE_INPUT_RUNTIME)
DEFAULT_NATIVE_INPUT_PIPE_FORMAT = site_config_str("runtime.input_pipe_format", _FALLBACK_NATIVE_INPUT_PIPE_FORMAT)
DEFAULT_NATIVE_RTSP_TRANSPORT = site_config_str("runtime.rtsp_transport", _FALLBACK_NATIVE_RTSP_TRANSPORT)
DEFAULT_NATIVE_INPUT_BUFFER_FRAMES = site_config_int(
    "runtime.input_buffer_frames",
    _FALLBACK_NATIVE_INPUT_BUFFER_FRAMES,
)
DEFAULT_NATIVE_RTSP_TIMEOUT_SEC = site_config_float("runtime.rtsp_timeout_sec", _FALLBACK_NATIVE_RTSP_TIMEOUT_SEC)
DEFAULT_NATIVE_RECONNECT_COOLDOWN_SEC = site_config_float(
    "runtime.reconnect_cooldown_sec",
    _FALLBACK_NATIVE_RECONNECT_COOLDOWN_SEC,
)
DEFAULT_NATIVE_PAIR_REUSE_MAX_AGE_MS = site_config_float(
    "runtime.pair_reuse_max_age_ms",
    _FALLBACK_NATIVE_PAIR_REUSE_MAX_AGE_MS,
)
DEFAULT_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE = site_config_int(
    "runtime.pair_reuse_max_consecutive",
    _FALLBACK_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE,
)
DEFAULT_NATIVE_SYNC_MATCH_MAX_DELTA_MS = site_config_float(
    "runtime.sync_match_max_delta_ms",
    _FALLBACK_NATIVE_SYNC_MATCH_MAX_DELTA_MS,
)
DEFAULT_NATIVE_STATUS_INTERVAL_SEC = site_config_float(
    "runtime.status_interval_sec",
    _FALLBACK_NATIVE_STATUS_INTERVAL_SEC,
)
DEFAULT_NATIVE_VIEWER_BACKEND = site_config_str("runtime.viewer_backend", _FALLBACK_NATIVE_VIEWER_BACKEND)
DEFAULT_NATIVE_PROBE_SOURCE = site_config_str("runtime.probe.source", _FALLBACK_NATIVE_PROBE_SOURCE)
DEFAULT_NATIVE_PROBE_RUNTIME = site_config_str("runtime.probe.runtime", _FALLBACK_NATIVE_PROBE_RUNTIME)
DEFAULT_NATIVE_PROBE_TARGET = site_config_str("runtime.probe.target", _FALLBACK_NATIVE_PROBE_TARGET)
DEFAULT_NATIVE_TRANSMIT_RUNTIME = site_config_str(
    "runtime.transmit.runtime",
    _FALLBACK_NATIVE_TRANSMIT_RUNTIME,
)
DEFAULT_NATIVE_TRANSMIT_TARGET = site_config_str(
    "runtime.transmit.target",
    _FALLBACK_NATIVE_TRANSMIT_TARGET,
)
DEFAULT_NATIVE_TRANSMIT_BITRATE = site_config_str(
    "runtime.transmit.bitrate",
    _FALLBACK_NATIVE_TRANSMIT_BITRATE,
)
DEFAULT_NATIVE_TRANSMIT_PRESET = site_config_str(
    "runtime.transmit.preset",
    _FALLBACK_NATIVE_TRANSMIT_PRESET,
)
DEFAULT_NATIVE_TRANSMIT_WIDTH = site_config_int(
    "runtime.transmit.width",
    _FALLBACK_NATIVE_TRANSMIT_WIDTH,
)
DEFAULT_NATIVE_TRANSMIT_HEIGHT = site_config_int(
    "runtime.transmit.height",
    _FALLBACK_NATIVE_TRANSMIT_HEIGHT,
)
DEFAULT_NATIVE_OUTPUT_CADENCE_FPS = site_config_float(
    "runtime.output_cadence_fps",
    _FALLBACK_NATIVE_OUTPUT_CADENCE_FPS,
)
DEFAULT_NATIVE_TRANSMIT_DEBUG_OVERLAY = site_config_bool(
    "runtime.transmit.debug_overlay",
    _FALLBACK_NATIVE_TRANSMIT_DEBUG_OVERLAY,
)


def default_left_rtsp() -> str:
    return os.environ.get("HOGAK_LEFT_RTSP", DEFAULT_LEFT_RTSP)


def default_right_rtsp() -> str:
    return os.environ.get("HOGAK_RIGHT_RTSP", DEFAULT_RIGHT_RTSP)


def default_output_standard() -> str:
    return os.environ.get("HOGAK_OUTPUT_STANDARD", DEFAULT_OUTPUT_STANDARD)


def default_native_output_cadence_fps() -> float:
    raw = os.environ.get("HOGAK_OUTPUT_CADENCE_FPS", str(DEFAULT_NATIVE_OUTPUT_CADENCE_FPS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_NATIVE_OUTPUT_CADENCE_FPS
    if value in {25.0, 30.0}:
        return value
    return DEFAULT_NATIVE_OUTPUT_CADENCE_FPS


def default_native_viewer_backend() -> str:
    return os.environ.get("HOGAK_VIEWER_BACKEND", DEFAULT_NATIVE_VIEWER_BACKEND)
