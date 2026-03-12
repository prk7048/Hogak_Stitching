from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from stitching.core import (
    StitchConfig,
    StitchingFailure,
    _blend_feather,
    _detect_and_match,
    _estimate_affine_homography,
    _estimate_homography,
    _prepare_warp_plan,
)
from stitching.errors import ErrorCode


@dataclass(slots=True)
class NativeCalibrationConfig(StitchConfig):
    left_rtsp: str = ""
    right_rtsp: str = ""
    output_path: Path = Path("output/native/runtime_homography.json")
    debug_dir: Path = Path("output/native/calibration")
    rtsp_transport: str = "tcp"
    rtsp_timeout_sec: float = 10.0
    warmup_frames: int = 45
    process_scale: float = 1.0


class _FfmpegCaptureEnv:
    def __init__(self, transport: str, timeout_sec: float) -> None:
        self._transport = transport
        self._timeout_sec = timeout_sec
        self._prev = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")

    def __enter__(self) -> None:
        timeout_us = max(100_000, int(self._timeout_sec * 1_000_000))
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            f"rtsp_transport;{self._transport}|timeout;{timeout_us}"
        )

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._prev is None:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._prev


def _open_capture(url: str, transport: str, timeout_sec: float) -> cv2.VideoCapture:
    with _FfmpegCaptureEnv(transport=transport, timeout_sec=timeout_sec):
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise StitchingFailure(ErrorCode.PROBE_FAIL, f"cannot open rtsp: {url}")
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def _resize_frame(frame: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return frame
    height, width = frame.shape[:2]
    new_width = max(2, int(round(width * scale)))
    new_height = max(2, int(round(height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(frame, (new_width, new_height), interpolation=interpolation)


def _resize_to_match(frame: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    target_h, target_w = target_shape
    if frame.shape[:2] == (target_h, target_w):
        return frame
    return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)


def _capture_pair(config: NativeCalibrationConfig) -> tuple[np.ndarray, np.ndarray]:
    left_cap = _open_capture(config.left_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    right_cap = _open_capture(config.right_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    left_frame: np.ndarray | None = None
    right_frame: np.ndarray | None = None
    deadline = time.time() + max(1.0, float(config.rtsp_timeout_sec))
    left_count = 0
    right_count = 0
    target_count = max(1, int(config.warmup_frames))

    try:
        while time.time() < deadline:
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
            raise StitchingFailure(
                ErrorCode.PROBE_FAIL,
                f"failed to capture representative frames (left={left_count}, right={right_count})",
            )
        left_resized = _resize_frame(left_frame, config.process_scale)
        right_resized = _resize_frame(right_frame, config.process_scale)
        right_resized = _resize_to_match(right_resized, left_resized.shape[:2])
        return left_resized, right_resized
    finally:
        left_cap.release()
        right_cap.release()


def _save_homography_file(path: Path, homography: np.ndarray, metadata: dict) -> None:
    payload = {
        "version": 1,
        "saved_at_epoch_sec": int(time.time()),
        "homography": homography.tolist(),
        "metadata": metadata,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _draw_inlier_preview(
    left: np.ndarray,
    right: np.ndarray,
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    inlier_mask: np.ndarray,
) -> np.ndarray:
    return cv2.drawMatches(
        left,
        keypoints_left,
        right,
        keypoints_right,
        matches[:200],
        None,
        matchesMask=[int(v) for v in inlier_mask.ravel().tolist()[:200]],
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
    )


def _write_debug_outputs(
    config: NativeCalibrationConfig,
    left: np.ndarray,
    right: np.ndarray,
    stitched: np.ndarray,
    inlier_preview: np.ndarray,
) -> None:
    config.debug_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(config.debug_dir / "native_calibration_left.jpg"), left)
    cv2.imwrite(str(config.debug_dir / "native_calibration_right.jpg"), right)
    cv2.imwrite(str(config.debug_dir / "native_calibration_inliers.jpg"), inlier_preview)
    cv2.imwrite(str(config.debug_dir / "native_calibration_preview.jpg"), stitched)


def calibrate_native_homography(config: NativeCalibrationConfig) -> dict:
    left, right = _capture_pair(config)
    keypoints_left, keypoints_right, matches = _detect_and_match(left, right, config)

    transform_model = "homography"
    try:
        homography, inlier_mask = _estimate_homography(keypoints_left, keypoints_right, matches, config)
    except StitchingFailure as exc:
        if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
            raise
        homography, inlier_mask = _estimate_affine_homography(keypoints_left, keypoints_right, matches, config)
        transform_model = "affine_fallback"

    plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)

    warped_right = cv2.warpPerspective(
        right,
        plan.homography_adjusted,
        (plan.width, plan.height),
    )
    right_mask = cv2.warpPerspective(
        np.ones(right.shape[:2], dtype=np.uint8) * 255,
        plan.homography_adjusted,
        (plan.width, plan.height),
    )
    canvas_left = np.zeros((plan.height, plan.width, 3), dtype=np.uint8)
    left_mask = np.zeros((plan.height, plan.width), dtype=np.uint8)
    left_h, left_w = left.shape[:2]
    canvas_left[plan.ty : plan.ty + left_h, plan.tx : plan.tx + left_w] = left
    left_mask[plan.ty : plan.ty + left_h, plan.tx : plan.tx + left_w] = 255
    stitched = _blend_feather(canvas_left, warped_right, left_mask, right_mask)
    inlier_preview = _draw_inlier_preview(left, right, keypoints_left, keypoints_right, matches, inlier_mask)
    _write_debug_outputs(config, left, right, stitched, inlier_preview)

    inliers_count = int(inlier_mask.ravel().sum())
    metadata = {
        "source": "native_runtime_calibration",
        "left_rtsp": config.left_rtsp,
        "right_rtsp": config.right_rtsp,
        "rtsp_transport": config.rtsp_transport,
        "process_scale": float(config.process_scale),
        "matches_count": int(len(matches)),
        "inliers_count": inliers_count,
        "transform_model": transform_model,
        "left_resolution": [int(left.shape[1]), int(left.shape[0])],
        "right_resolution": [int(right.shape[1]), int(right.shape[0])],
        "output_resolution": [int(plan.width), int(plan.height)],
        "debug_dir": str(config.debug_dir),
    }
    _save_homography_file(config.output_path, homography, metadata)
    return {
        "homography_file": str(config.output_path),
        "debug_dir": str(config.debug_dir),
        "matches_count": int(len(matches)),
        "inliers_count": inliers_count,
        "transform_model": transform_model,
        "output_resolution": [int(plan.width), int(plan.height)],
    }


def run_native_calibration(args: argparse.Namespace) -> int:
    config = NativeCalibrationConfig(
        left_rtsp=str(args.left_rtsp),
        right_rtsp=str(args.right_rtsp),
        output_path=Path(args.out),
        debug_dir=Path(args.debug_dir),
        rtsp_transport=str(args.rtsp_transport),
        rtsp_timeout_sec=max(1.0, float(args.rtsp_timeout_sec)),
        warmup_frames=max(1, int(args.warmup_frames)),
        process_scale=max(0.1, float(args.process_scale)),
        min_matches=max(8, int(args.min_matches)),
        min_inliers=max(6, int(args.min_inliers)),
        ratio_test=float(args.ratio_test),
        ransac_reproj_threshold=float(args.ransac_thresh),
        max_features=max(500, int(args.max_features)),
    )
    try:
        result = calibrate_native_homography(config)
    except StitchingFailure as exc:
        print(f"native calibration failed: {exc.code.value}: {exc.detail}")
        return 2
    except ModuleNotFoundError as exc:
        if exc.name == "cv2":
            print("Missing dependency: opencv-python. Install requirements in your venv first.")
            return 2
        raise

    output_width, output_height = result["output_resolution"]
    print(
        f"homography_saved={result['homography_file']} "
        f"output={output_width}x{output_height} "
        f"matches={result['matches_count']} "
        f"inliers={result['inliers_count']} "
        f"model={result['transform_model']}"
    )
    return 0
