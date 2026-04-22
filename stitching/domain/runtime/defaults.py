from __future__ import annotations

import os

from stitching.domain.runtime.site_config import (
    site_config_bool,
    site_config_float,
    site_config_int,
    site_config_str,
)

_FALLBACK_LEFT_RTSP = "rtsp://admin:password@left-camera.example.invalid:554/cam/realmonitor?channel=1&subtype=0"
_FALLBACK_RIGHT_RTSP = "rtsp://admin:password@right-camera.example.invalid:554/cam/realmonitor?channel=1&subtype=0"
_FALLBACK_HOMOGRAPHY_PATH = "data/runtime_homography.json"
_FALLBACK_CALIBRATION_DEBUG_DIR = "output/calibration"
_FALLBACK_OUTPUT_STANDARD = "realtime_gpu_1080p"
_FALLBACK_INPUT_RUNTIME = "ffmpeg-cuda"
_FALLBACK_INPUT_PIPE_FORMAT = "nv12"
_FALLBACK_RTSP_TRANSPORT = "tcp"
_FALLBACK_INPUT_BUFFER_FRAMES = 8
_FALLBACK_RTSP_TIMEOUT_SEC = 10.0
_FALLBACK_RECONNECT_COOLDOWN_SEC = 0.5
_FALLBACK_PAIR_REUSE_MAX_AGE_MS = 50.0
_FALLBACK_PAIR_REUSE_MAX_CONSECUTIVE = 1
_FALLBACK_SYNC_MATCH_MAX_DELTA_MS = 35.0
_FALLBACK_SYNC_TIME_SOURCE = "pts-offset-auto"
_FALLBACK_SYNC_MANUAL_OFFSET_MS = 0.0
_FALLBACK_SYNC_AUTO_OFFSET_WINDOW_SEC = 4.0
_FALLBACK_SYNC_AUTO_OFFSET_MAX_SEARCH_MS = 500.0
_FALLBACK_SYNC_RECALIBRATION_INTERVAL_SEC = 60.0
_FALLBACK_SYNC_RECALIBRATION_TRIGGER_SKEW_MS = 45.0
_FALLBACK_SYNC_RECALIBRATION_TRIGGER_WAIT_RATIO = 0.50
_FALLBACK_SYNC_AUTO_OFFSET_CONFIDENCE_MIN = 0.85
_FALLBACK_CALIBRATION_INLIERS_FILE = "data/runtime_calibration_inliers.json"
_FALLBACK_STATUS_INTERVAL_SEC = 5.0
_FALLBACK_VIEWER_BACKEND = "auto"
_FALLBACK_PROBE_SOURCE = "standalone"
_FALLBACK_PROBE_RUNTIME = "none"
_FALLBACK_PROBE_TARGET = ""
_FALLBACK_TRANSMIT_RUNTIME = "gpu-direct"
_FALLBACK_TRANSMIT_TARGET = "udp://127.0.0.1:24000?pkt_size=1316"
_FALLBACK_TRANSMIT_BITRATE = "16M"
_FALLBACK_TRANSMIT_PRESET = "p4"
_FALLBACK_TRANSMIT_WIDTH = 0
_FALLBACK_TRANSMIT_HEIGHT = 0
_FALLBACK_OUTPUT_CADENCE_FPS = 30.0
_FALLBACK_TRANSMIT_DEBUG_OVERLAY = False

DEFAULT_LEFT_RTSP = site_config_str("cameras.left_rtsp", _FALLBACK_LEFT_RTSP)
DEFAULT_RIGHT_RTSP = site_config_str("cameras.right_rtsp", _FALLBACK_RIGHT_RTSP)
DEFAULT_HOMOGRAPHY_PATH = site_config_str("paths.homography_file", _FALLBACK_HOMOGRAPHY_PATH)
DEFAULT_CALIBRATION_DEBUG_DIR = site_config_str(
    "paths.calibration_debug_dir",
    _FALLBACK_CALIBRATION_DEBUG_DIR,
)
DEFAULT_OUTPUT_STANDARD = site_config_str("runtime.output_standard", _FALLBACK_OUTPUT_STANDARD)
DEFAULT_INPUT_RUNTIME = site_config_str("runtime.input_runtime", _FALLBACK_INPUT_RUNTIME)
DEFAULT_INPUT_PIPE_FORMAT = site_config_str("runtime.input_pipe_format", _FALLBACK_INPUT_PIPE_FORMAT)
DEFAULT_RTSP_TRANSPORT = site_config_str("runtime.rtsp_transport", _FALLBACK_RTSP_TRANSPORT)
DEFAULT_INPUT_BUFFER_FRAMES = site_config_int(
    "runtime.input_buffer_frames",
    _FALLBACK_INPUT_BUFFER_FRAMES,
)
DEFAULT_RTSP_TIMEOUT_SEC = site_config_float("runtime.rtsp_timeout_sec", _FALLBACK_RTSP_TIMEOUT_SEC)
DEFAULT_RECONNECT_COOLDOWN_SEC = site_config_float(
    "runtime.reconnect_cooldown_sec",
    _FALLBACK_RECONNECT_COOLDOWN_SEC,
)
DEFAULT_PAIR_REUSE_MAX_AGE_MS = site_config_float(
    "runtime.pair_reuse_max_age_ms",
    _FALLBACK_PAIR_REUSE_MAX_AGE_MS,
)
DEFAULT_PAIR_REUSE_MAX_CONSECUTIVE = site_config_int(
    "runtime.pair_reuse_max_consecutive",
    _FALLBACK_PAIR_REUSE_MAX_CONSECUTIVE,
)
DEFAULT_SYNC_MATCH_MAX_DELTA_MS = site_config_float(
    "runtime.sync_match_max_delta_ms",
    _FALLBACK_SYNC_MATCH_MAX_DELTA_MS,
)
DEFAULT_SYNC_TIME_SOURCE = site_config_str(
    "runtime.sync_time_source",
    _FALLBACK_SYNC_TIME_SOURCE,
)
DEFAULT_SYNC_MANUAL_OFFSET_MS = site_config_float(
    "runtime.sync_manual_offset_ms",
    _FALLBACK_SYNC_MANUAL_OFFSET_MS,
)
DEFAULT_SYNC_AUTO_OFFSET_WINDOW_SEC = site_config_float(
    "runtime.sync_auto_offset_window_sec",
    _FALLBACK_SYNC_AUTO_OFFSET_WINDOW_SEC,
)
DEFAULT_SYNC_AUTO_OFFSET_MAX_SEARCH_MS = site_config_float(
    "runtime.sync_auto_offset_max_search_ms",
    _FALLBACK_SYNC_AUTO_OFFSET_MAX_SEARCH_MS,
)
DEFAULT_SYNC_RECALIBRATION_INTERVAL_SEC = site_config_float(
    "runtime.sync_recalibration_interval_sec",
    _FALLBACK_SYNC_RECALIBRATION_INTERVAL_SEC,
)
DEFAULT_SYNC_RECALIBRATION_TRIGGER_SKEW_MS = site_config_float(
    "runtime.sync_recalibration_trigger_skew_ms",
    _FALLBACK_SYNC_RECALIBRATION_TRIGGER_SKEW_MS,
)
DEFAULT_SYNC_RECALIBRATION_TRIGGER_WAIT_RATIO = site_config_float(
    "runtime.sync_recalibration_trigger_wait_ratio",
    _FALLBACK_SYNC_RECALIBRATION_TRIGGER_WAIT_RATIO,
)
DEFAULT_SYNC_AUTO_OFFSET_CONFIDENCE_MIN = site_config_float(
    "runtime.sync_auto_offset_confidence_min",
    _FALLBACK_SYNC_AUTO_OFFSET_CONFIDENCE_MIN,
)
DEFAULT_CALIBRATION_INLIERS_FILE = site_config_str(
    "runtime.calibration_inliers_file",
    _FALLBACK_CALIBRATION_INLIERS_FILE,
)
DEFAULT_STATUS_INTERVAL_SEC = site_config_float(
    "runtime.status_interval_sec",
    _FALLBACK_STATUS_INTERVAL_SEC,
)
DEFAULT_VIEWER_BACKEND = site_config_str("runtime.viewer_backend", _FALLBACK_VIEWER_BACKEND)
DEFAULT_PROBE_SOURCE = site_config_str("runtime.probe.source", _FALLBACK_PROBE_SOURCE)
DEFAULT_PROBE_RUNTIME = site_config_str("runtime.probe.runtime", _FALLBACK_PROBE_RUNTIME)
DEFAULT_PROBE_TARGET = site_config_str("runtime.probe.target", _FALLBACK_PROBE_TARGET)
DEFAULT_TRANSMIT_RUNTIME = site_config_str(
    "runtime.transmit.runtime",
    _FALLBACK_TRANSMIT_RUNTIME,
)
DEFAULT_TRANSMIT_TARGET = site_config_str(
    "runtime.transmit.target",
    _FALLBACK_TRANSMIT_TARGET,
)
DEFAULT_TRANSMIT_BITRATE = site_config_str(
    "runtime.transmit.bitrate",
    _FALLBACK_TRANSMIT_BITRATE,
)
DEFAULT_TRANSMIT_PRESET = site_config_str(
    "runtime.transmit.preset",
    _FALLBACK_TRANSMIT_PRESET,
)
DEFAULT_TRANSMIT_WIDTH = site_config_int(
    "runtime.transmit.width",
    _FALLBACK_TRANSMIT_WIDTH,
)
DEFAULT_TRANSMIT_HEIGHT = site_config_int(
    "runtime.transmit.height",
    _FALLBACK_TRANSMIT_HEIGHT,
)
DEFAULT_OUTPUT_CADENCE_FPS = site_config_float(
    "runtime.output_cadence_fps",
    _FALLBACK_OUTPUT_CADENCE_FPS,
)
DEFAULT_TRANSMIT_DEBUG_OVERLAY = site_config_bool(
    "runtime.transmit.debug_overlay",
    _FALLBACK_TRANSMIT_DEBUG_OVERLAY,
)


def default_left_rtsp() -> str:
    return os.environ.get("HOGAK_LEFT_RTSP", DEFAULT_LEFT_RTSP)


def default_right_rtsp() -> str:
    return os.environ.get("HOGAK_RIGHT_RTSP", DEFAULT_RIGHT_RTSP)


def default_output_standard() -> str:
    return os.environ.get("HOGAK_OUTPUT_STANDARD", DEFAULT_OUTPUT_STANDARD)


def default_output_cadence_fps() -> float:
    raw = os.environ.get("HOGAK_OUTPUT_CADENCE_FPS", str(DEFAULT_OUTPUT_CADENCE_FPS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_OUTPUT_CADENCE_FPS
    if value in {25.0, 30.0}:
        return value
    return DEFAULT_OUTPUT_CADENCE_FPS


def default_viewer_backend() -> str:
    return os.environ.get("HOGAK_VIEWER_BACKEND", DEFAULT_VIEWER_BACKEND)
