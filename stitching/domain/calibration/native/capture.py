from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import numpy as np

from stitching.errors import ErrorCode

if TYPE_CHECKING:
    import cv2 as cv2_types

    CvVideoCapture = cv2_types.VideoCapture
else:
    CvVideoCapture = Any


class FfmpegCaptureEnv:
    def __init__(self, transport: str, timeout_sec: float) -> None:
        self._transport = transport
        self._timeout_sec = timeout_sec
        self._prev = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")

    def __enter__(self) -> None:
        timeout_us = max(100_000, int(self._timeout_sec * 1_000_000))
        capture_options = [f"rtsp_transport;{self._transport}", f"timeout;{timeout_us}"]
        if str(self._transport or "").strip().lower() == "udp":
            capture_options.extend(
                [
                    f"fifo_size;{8 * 1024 * 1024}",
                    "overrun_nonfatal;1",
                ]
            )
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "|".join(capture_options)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._prev is None:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._prev


def open_capture(
    url: str,
    transport: str,
    timeout_sec: float,
    *,
    ffmpeg_capture_env_cls: type[FfmpegCaptureEnv],
    cv2_module: Any,
    stitching_failure_cls: type[Exception],
) -> CvVideoCapture:
    with ffmpeg_capture_env_cls(transport=transport, timeout_sec=timeout_sec):
        cap = cv2_module.VideoCapture(url, cv2_module.CAP_FFMPEG)
    if not cap.isOpened():
        raise stitching_failure_cls(ErrorCode.PROBE_FAIL, f"cannot open rtsp: {url}")
    cap.set(cv2_module.CAP_PROP_BUFFERSIZE, 1)
    return cap


def resize_frame(
    frame: np.ndarray,
    scale: float,
    *,
    cv2_module: Any,
) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return frame
    height, width = frame.shape[:2]
    new_width = max(2, int(round(width * scale)))
    new_height = max(2, int(round(height * scale)))
    interpolation = cv2_module.INTER_AREA if scale < 1.0 else cv2_module.INTER_LINEAR
    return cv2_module.resize(frame, (new_width, new_height), interpolation=interpolation)


def resize_to_match(
    frame: np.ndarray,
    target_shape: tuple[int, int],
    *,
    cv2_module: Any,
) -> np.ndarray:
    target_h, target_w = target_shape
    if frame.shape[:2] == (target_h, target_w):
        return frame
    return cv2_module.resize(frame, (target_w, target_h), interpolation=cv2_module.INTER_LINEAR)


def capture_pair(
    config: Any,
    *,
    open_capture_func: Any,
    resize_frame_func: Any,
    resize_to_match_func: Any,
    time_module: Any,
    stitching_failure_cls: type[Exception],
) -> tuple[np.ndarray, np.ndarray]:
    left_cap = open_capture_func(config.left_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    right_cap = open_capture_func(config.right_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    left_frame: np.ndarray | None = None
    right_frame: np.ndarray | None = None
    deadline = time_module.time() + max(1.0, float(config.rtsp_timeout_sec))
    left_count = 0
    right_count = 0
    target_count = max(1, int(config.warmup_frames))

    try:
        while time_module.time() < deadline:
            if left_count < target_count:
                ok_left, frame_left = left_cap.read()
                if ok_left:
                    left_frame = frame_left
                    left_count += 1
            if right_count < target_count:
                ok_right, frame_right = right_cap.read()
                if ok_right:
                    right_frame = frame_right
                    right_count += 1
            if left_count >= target_count and right_count >= target_count:
                break
        if left_frame is None or right_frame is None:
            raise stitching_failure_cls(
                ErrorCode.PROBE_FAIL,
                f"failed to capture representative frames (left={left_count}, right={right_count})",
            )
        left_resized = resize_frame_func(left_frame, config.process_scale)
        right_resized = resize_frame_func(right_frame, config.process_scale)
        right_resized = resize_to_match_func(right_resized, left_resized.shape[:2])
        return left_resized, right_resized
    finally:
        left_cap.release()
        right_cap.release()
