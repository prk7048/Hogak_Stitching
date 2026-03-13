from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "output" / "debug"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stitching.project_defaults import (  # noqa: E402
    DEFAULT_NATIVE_INPUT_BUFFER_FRAMES,
    DEFAULT_NATIVE_INPUT_PIPE_FORMAT,
    DEFAULT_NATIVE_INPUT_RUNTIME,
    DEFAULT_NATIVE_PAIR_REUSE_MAX_AGE_MS,
    DEFAULT_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE,
    DEFAULT_NATIVE_RECONNECT_COOLDOWN_SEC,
    DEFAULT_NATIVE_RTSP_TIMEOUT_SEC,
    DEFAULT_NATIVE_RTSP_TRANSPORT,
    default_left_rtsp,
    default_output_standard,
    default_right_rtsp,
)


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def counter_delta(first: dict[str, Any], last: dict[str, Any], key: str) -> int:
    return safe_int(last.get(key)) - safe_int(first.get(key))


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def add_common_runtime_input_args(
    parser: argparse.ArgumentParser,
    *,
    include_left_right: bool = True,
    include_output_standard: bool = True,
) -> None:
    if include_left_right:
        parser.add_argument("--left-rtsp", default=default_left_rtsp())
        parser.add_argument("--right-rtsp", default=default_right_rtsp())
    parser.add_argument(
        "--input-runtime",
        default=DEFAULT_NATIVE_INPUT_RUNTIME,
        choices=["opencv", "ffmpeg", "ffmpeg-cpu", "ffmpeg-cuda"],
    )
    parser.add_argument(
        "--input-pipe-format",
        default=DEFAULT_NATIVE_INPUT_PIPE_FORMAT,
        choices=["bgr24", "nv12"],
    )
    parser.add_argument(
        "--transport",
        default=DEFAULT_NATIVE_RTSP_TRANSPORT,
        choices=["tcp", "udp"],
    )
    if include_output_standard:
        parser.add_argument("--output-standard", default=default_output_standard())
    parser.add_argument("--input-buffer-frames", type=int, default=DEFAULT_NATIVE_INPUT_BUFFER_FRAMES)
    parser.add_argument("--rtsp-timeout-sec", type=float, default=DEFAULT_NATIVE_RTSP_TIMEOUT_SEC)
    parser.add_argument("--reconnect-cooldown-sec", type=float, default=DEFAULT_NATIVE_RECONNECT_COOLDOWN_SEC)
    parser.add_argument("--pair-reuse-max-age-ms", type=float, default=DEFAULT_NATIVE_PAIR_REUSE_MAX_AGE_MS)
    parser.add_argument("--pair-reuse-max-consecutive", type=int, default=DEFAULT_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE)
