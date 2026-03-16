from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

from stitching.core import (
    StitchConfig,
    StitchingFailure,
    _blend_feather,
    _estimate_affine_homography,
    _estimate_homography,
    _prepare_warp_plan,
)
from stitching.deep_feature_matching import detect_and_match_deep
from stitching.errors import ErrorCode
from stitching.project_defaults import (
    DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR,
    DEFAULT_NATIVE_HOMOGRAPHY_PATH,
)
from stitching.runtime_site_config import require_configured_rtsp_urls


@dataclass(slots=True)
class NativeCalibrationConfig(StitchConfig):
    left_rtsp: str = ""
    right_rtsp: str = ""
    output_path: Path = Path(DEFAULT_NATIVE_HOMOGRAPHY_PATH)
    debug_dir: Path = Path(DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR)
    rtsp_transport: str = "tcp"
    rtsp_timeout_sec: float = 10.0
    warmup_frames: int = 45
    process_scale: float = 1.0
    calibration_mode: str = "assisted"
    assisted_reproj_threshold: float = 12.0
    assisted_max_auto_matches: int = 600
    match_backend: str = "auto"
    deep_backend: str = "auto"


@dataclass(slots=True)
class _CalibrationCandidate:
    homography: np.ndarray
    inlier_mask: np.ndarray
    keypoints_left: list[cv2.KeyPoint]
    keypoints_right: list[cv2.KeyPoint]
    matches: list[cv2.DMatch]
    calibration_mode: str
    transform_model: str
    seed_guidance_model: str
    score: float
    inliers_count: int
    match_count: int
    inlier_ratio: float
    mean_reprojection_error: float
    match_score: float
    geometry_score: float
    visual_score: float
    output_width: int
    output_height: int
    overlap_luma_diff: float
    overlap_edge_diff: float
    ghosting_score: float
    backend_name: str


class _AssistedCalibrationUi:
    _LEFT_PANEL = "left"
    _RIGHT_PANEL = "right"
    _BUTTON_COMPLETE = "complete"
    _BUTTON_UNDO = "undo"
    _BUTTON_RESET = "reset"

    def __init__(
        self,
        left: np.ndarray,
        right: np.ndarray,
        *,
        status: str = "",
        left_overlap_hint: tuple[int, int, int, int] | None = None,
        right_overlap_hint: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._left = left
        self._right = right
        self._left_points: list[tuple[float, float]] = []
        self._right_points: list[tuple[float, float]] = []
        self._status = status or "Click matching points in left/right images, then press COMPLETE."
        self._left_overlap_hint = left_overlap_hint
        self._right_overlap_hint = right_overlap_hint
        self._window_name = "Native Calibration Assisted Mode"
        self._done = False
        self._cancelled = False
        self._layout = self._build_layout()

    def run(self) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self._window_name, self._on_mouse)
        try:
            while True:
                canvas = self._render()
                cv2.imshow(self._window_name, canvas)
                key = cv2.waitKey(20) & 0xFF
                if key in (27, ord("q")):
                    self._cancelled = True
                    break
                if key in (13, 32):
                    if self._can_complete():
                        self._done = True
                        break
                if key in (8, ord("z")):
                    self._undo_last()
                if key == ord("r"):
                    self._reset()
                if self._done:
                    break
        finally:
            cv2.destroyWindow(self._window_name)
        if self._cancelled:
            raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "assisted calibration cancelled by user")
        return list(self._left_points), list(self._right_points)

    def _build_layout(self) -> dict[str, tuple[int, int, int, int]]:
        left_h, left_w = self._left.shape[:2]
        right_h, right_w = self._right.shape[:2]
        panel_h = 540
        left_panel_w = max(320, int(round(left_w * (panel_h / float(max(1, left_h))))))
        right_panel_w = max(320, int(round(right_w * (panel_h / float(max(1, right_h))))))
        gap = 24
        header_h = 92
        footer_h = 96
        width = left_panel_w + right_panel_w + gap * 3
        height = header_h + panel_h + footer_h + gap * 2
        left_rect = (gap, header_h, left_panel_w, panel_h)
        right_rect = (gap * 2 + left_panel_w, header_h, right_panel_w, panel_h)
        button_y = header_h + panel_h + 22
        complete_rect = (width - 220, button_y, 180, 44)
        undo_rect = (40, button_y, 140, 44)
        reset_rect = (200, button_y, 140, 44)
        return {
            "canvas": (0, 0, width, height),
            self._LEFT_PANEL: left_rect,
            self._RIGHT_PANEL: right_rect,
            self._BUTTON_COMPLETE: complete_rect,
            self._BUTTON_UNDO: undo_rect,
            self._BUTTON_RESET: reset_rect,
        }

    def _render(self) -> np.ndarray:
        _, _, width, height = self._layout["canvas"]
        canvas = np.full((height, width, 3), 18, dtype=np.uint8)
        title = "Assisted calibration: click matching points in order. COMPLETE finishes immediately."
        subtitle = (
            f"left={len(self._left_points)} right={len(self._right_points)}  "
            "highlighted boxes = likely overlap area"
        )
        cv2.putText(canvas, title, (24, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (230, 230, 230), 2, cv2.LINE_AA)
        cv2.putText(canvas, subtitle, (24, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (180, 210, 255), 1, cv2.LINE_AA)
        cv2.putText(canvas, self._status, (24, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (200, 200, 200), 1, cv2.LINE_AA)

        left_panel = self._draw_panel(self._left, self._LEFT_PANEL, "LEFT")
        right_panel = self._draw_panel(self._right, self._RIGHT_PANEL, "RIGHT")
        lx, ly, lw, lh = self._layout[self._LEFT_PANEL]
        rx, ry, rw, rh = self._layout[self._RIGHT_PANEL]
        canvas[ly : ly + lh, lx : lx + lw] = left_panel
        canvas[ry : ry + rh, rx : rx + rw] = right_panel

        self._draw_button(canvas, self._BUTTON_UNDO, "UNDO", (70, 70, 70))
        self._draw_button(canvas, self._BUTTON_RESET, "RESET", (70, 70, 70))
        self._draw_button(canvas, self._BUTTON_COMPLETE, "COMPLETE", (60, 140, 70))
        return canvas

    def _draw_panel(self, frame: np.ndarray, panel_key: str, label: str) -> np.ndarray:
        px, py, pw, ph = self._layout[panel_key]
        resized = cv2.resize(frame, (pw, ph), interpolation=cv2.INTER_AREA)
        cv2.rectangle(resized, (0, 0), (pw - 1, ph - 1), (90, 90, 90), 1)
        cv2.putText(resized, label, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
        points = self._left_points if panel_key == self._LEFT_PANEL else self._right_points
        overlap_hint = self._left_overlap_hint if panel_key == self._LEFT_PANEL else self._right_overlap_hint
        src_h, src_w = frame.shape[:2]
        if overlap_hint is not None:
            hx, hy, hw, hh = overlap_hint
            ox1 = int(round((hx / float(max(1, src_w))) * pw))
            ox2 = int(round(((hx + hw) / float(max(1, src_w))) * pw))
            ox1 = max(0, min(pw - 1, ox1))
            ox2 = max(0, min(pw - 1, ox2))
            band_margin = max(0, int(round(ph * 0.04)))
            oy1 = band_margin
            oy2 = max(oy1 + 12, ph - band_margin - 1)
            min_box_width = max(18, int(round(pw * 0.12)))
            if ox2 - ox1 < min_box_width:
                center_x = int(round((ox1 + ox2) * 0.5))
                half_w = max(1, min_box_width // 2)
                ox1 = max(0, center_x - half_w)
                ox2 = min(pw - 1, center_x + half_w)
            if ox2 > ox1 and oy2 > oy1:
                cv2.rectangle(resized, (ox1, oy1), (ox2, oy2), (0, 220, 255), 1)
                cv2.putText(
                    resized,
                    "suggested overlap",
                    (max(8, ox1 + 8), max(26, oy1 + 22)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (0, 220, 255),
                    2,
                    cv2.LINE_AA,
                )
        colors = [
            (0, 0, 255),
            (0, 255, 0),
            (255, 0, 0),
            (0, 255, 255),
            (255, 0, 255),
            (255, 255, 0),
        ]
        for idx, (x, y) in enumerate(points, start=1):
            dx = int(round((x / float(max(1, src_w))) * pw))
            dy = int(round((y / float(max(1, src_h))) * ph))
            color = colors[(idx - 1) % len(colors)]
            cv2.circle(resized, (dx, dy), 5, color, -1, cv2.LINE_AA)
            cv2.putText(resized, str(idx), (dx + 7, dy - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2, cv2.LINE_AA)
        return resized

    def _draw_button(
        self,
        canvas: np.ndarray,
        key: str,
        text: str,
        color: tuple[int, int, int],
    ) -> None:
        x, y, w, h = self._layout[key]
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (230, 230, 230), 1)
        cv2.putText(
            canvas,
            text,
            (x + 16, y + 29),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )

    def _on_mouse(self, event: int, x: int, y: int, _flags: int, _userdata: object | None = None) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        button = self._hit_test_button(x, y)
        if button == self._BUTTON_COMPLETE:
            if self._can_complete():
                self._done = True
            return
        if button == self._BUTTON_UNDO:
            self._undo_last()
            return
        if button == self._BUTTON_RESET:
            self._reset()
            return
        panel = self._hit_test_panel(x, y)
        if panel is None:
            return
        self._append_point(panel, x, y)

    def _hit_test_button(self, x: int, y: int) -> str | None:
        for key in (self._BUTTON_COMPLETE, self._BUTTON_UNDO, self._BUTTON_RESET):
            bx, by, bw, bh = self._layout[key]
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return key
        return None

    def _hit_test_panel(self, x: int, y: int) -> str | None:
        for key in (self._LEFT_PANEL, self._RIGHT_PANEL):
            px, py, pw, ph = self._layout[key]
            if px <= x < px + pw and py <= y < py + ph:
                return key
        return None

    def _append_point(self, panel: str, x: int, y: int) -> None:
        px, py, pw, ph = self._layout[panel]
        src = self._left if panel == self._LEFT_PANEL else self._right
        src_h, src_w = src.shape[:2]
        rx = min(max(0.0, (x - px) * (src_w / float(max(1, pw)))), float(max(0, src_w - 1)))
        ry = min(max(0.0, (y - py) * (src_h / float(max(1, ph)))), float(max(0, src_h - 1)))
        if panel == self._LEFT_PANEL:
            self._left_points.append((rx, ry))
            self._status = f"Added LEFT point #{len(self._left_points)}"
        else:
            self._right_points.append((rx, ry))
            self._status = f"Added RIGHT point #{len(self._right_points)}"

    def _undo_last(self) -> None:
        if len(self._left_points) > len(self._right_points):
            self._left_points.pop()
        elif len(self._right_points) > len(self._left_points):
            self._right_points.pop()
        elif self._left_points and self._right_points:
            self._left_points.pop()
            self._right_points.pop()
        self._status = "Undid last point input"

    def _reset(self) -> None:
        self._left_points.clear()
        self._right_points.clear()
        self._status = "Reset all picked points"

    def _can_complete(self) -> bool:
        if len(self._left_points) != len(self._right_points):
            self._status = "Left/right point counts must match before COMPLETE."
            return False
        self._status = "Completing assisted calibration"
        return True


class _CalibrationReviewUi:
    _BUTTON_CONFIRM = "confirm"
    _BUTTON_CANCEL = "cancel"

    def __init__(
        self,
        *,
        inlier_preview: np.ndarray,
        stitched_preview: np.ndarray,
        summary_lines: list[str],
    ) -> None:
        self._inlier_preview = inlier_preview
        self._stitched_preview = stitched_preview
        self._summary_lines = summary_lines
        self._window_name = "Native Calibration Review"
        self._confirmed = False
        self._cancelled = False
        self._layout = self._build_layout()

    def run(self) -> bool:
        cv2.namedWindow(self._window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self._window_name, self._on_mouse)
        try:
            while True:
                canvas = self._render()
                cv2.imshow(self._window_name, canvas)
                key = cv2.waitKey(20) & 0xFF
                if key in (13, 32):
                    self._confirmed = True
                    break
                if key in (27, ord("q"), ord("c")):
                    self._cancelled = True
                    break
                if self._confirmed or self._cancelled:
                    break
        finally:
            cv2.destroyWindow(self._window_name)
        return bool(self._confirmed and not self._cancelled)

    def _build_layout(self) -> dict[str, tuple[int, int, int, int]]:
        panel_w = 780
        panel_h = 420
        gap = 20
        header_h = 120
        footer_h = 88
        width = panel_w * 2 + gap * 3
        height = header_h + panel_h + footer_h + gap * 2
        return {
            "canvas": (0, 0, width, height),
            "inliers": (gap, header_h, panel_w, panel_h),
            "stitched": (gap * 2 + panel_w, header_h, panel_w, panel_h),
            self._BUTTON_CANCEL: (40, header_h + panel_h + 22, 180, 44),
            self._BUTTON_CONFIRM: (width - 220, header_h + panel_h + 22, 180, 44),
        }

    def _render(self) -> np.ndarray:
        _, _, width, height = self._layout["canvas"]
        canvas = np.full((height, width, 3), 18, dtype=np.uint8)
        cv2.putText(canvas, "Review calibration result before launch", (24, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.82, (235, 235, 235), 2, cv2.LINE_AA)
        for idx, line in enumerate(self._summary_lines, start=1):
            cv2.putText(canvas, line, (24, 34 + idx * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (210, 210, 210), 1, cv2.LINE_AA)
        self._draw_panel(canvas, "inliers", self._inlier_preview, "Inlier Matches")
        self._draw_panel(canvas, "stitched", self._stitched_preview, "Stitched Preview")
        self._draw_button(canvas, self._BUTTON_CANCEL, "CANCEL", (80, 80, 80))
        self._draw_button(canvas, self._BUTTON_CONFIRM, "CONFIRM", (60, 140, 70))
        return canvas

    def _draw_panel(self, canvas: np.ndarray, key: str, frame: np.ndarray, label: str) -> None:
        x, y, w, h = self._layout[key]
        panel = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        canvas[y : y + h, x : x + w] = panel
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (120, 120, 120), 1)
        cv2.putText(canvas, label, (x + 12, y + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)

    def _draw_button(self, canvas: np.ndarray, key: str, text: str, color: tuple[int, int, int]) -> None:
        x, y, w, h = self._layout[key]
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (230, 230, 230), 1)
        cv2.putText(canvas, text, (x + 18, y + 29), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (245, 245, 245), 2, cv2.LINE_AA)

    def _on_mouse(self, event: int, x: int, y: int, _flags: int, _userdata: object | None = None) -> None:
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        for key, attr in ((self._BUTTON_CONFIRM, "_confirmed"), (self._BUTTON_CANCEL, "_cancelled")):
            bx, by, bw, bh = self._layout[key]
            if bx <= x <= bx + bw and by <= y <= by + bh:
                setattr(self, attr, True)
                return


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


def _estimate_seed_guidance_transform(
    left_points: list[tuple[float, float]],
    right_points: list[tuple[float, float]],
    config: NativeCalibrationConfig,
) -> tuple[np.ndarray, str]:
    if not left_points:
        return np.eye(3, dtype=np.float64), "identity"
    if len(left_points) == 1:
        dx = float(left_points[0][0] - right_points[0][0])
        dy = float(left_points[0][1] - right_points[0][1])
        transform = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]], dtype=np.float64)
        return transform, "translation"
    if len(left_points) < 4:
        src_points = np.float32(right_points).reshape(-1, 2)
        dst_points = np.float32(left_points).reshape(-1, 2)
        affine, _ = cv2.estimateAffinePartial2D(
            src_points,
            dst_points,
            method=cv2.LMEDS,
        )
        if affine is None:
            raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "seed affine estimation returned null")
        transform = np.eye(3, dtype=np.float64)
        transform[:2, :] = affine
        return transform, "affine_seed"
    src_points = np.float32(right_points).reshape(-1, 1, 2)
    dst_points = np.float32(left_points).reshape(-1, 1, 2)
    homography, inlier_mask = cv2.findHomography(
        src_points,
        dst_points,
        cv2.RANSAC,
        config.ransac_reproj_threshold,
    )
    if homography is None or inlier_mask is None or int(inlier_mask.ravel().sum()) < 4:
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "seed homography estimation returned null or too few inliers")
    return homography, "homography_seed"


def _reprojection_error(homography: np.ndarray, right_point: tuple[float, float], left_point: tuple[float, float]) -> float:
    src = np.float32([[right_point]]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(src, homography).reshape(-1, 2)[0]
    dst = np.float32(left_point)
    return float(np.linalg.norm(projected - dst))


def _detect_and_match_classic_raw(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch]]:
    gray_left = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)
    detector = cv2.ORB_create(nfeatures=config.max_features)
    keypoints_left, descriptors_left = detector.detectAndCompute(gray_left, None)
    keypoints_right, descriptors_right = detector.detectAndCompute(gray_right, None)
    if descriptors_left is None or descriptors_right is None:
        raise StitchingFailure(ErrorCode.OVERLAP_LOW, "descriptor extraction failed")
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn_matches = matcher.knnMatch(descriptors_left, descriptors_right, k=2)
    good_matches: list[cv2.DMatch] = []
    for pair in knn_matches:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < config.ratio_test * n.distance:
            good_matches.append(m)
    return keypoints_left, keypoints_right, good_matches


def _guidance_threshold_px(config: NativeCalibrationConfig, seed_model: str) -> float:
    base = float(config.assisted_reproj_threshold)
    if seed_model == "translation":
        return max(80.0, base * 8.0)
    if seed_model == "affine_seed":
        return max(40.0, base * 4.0)
    if seed_model == "homography_seed":
        return max(20.0, base * 2.0)
    return max(20.0, base * 2.0)


def _assisted_min_matches(config: NativeCalibrationConfig) -> int:
    return max(8, min(int(config.min_matches), 20))


def _build_assisted_matches(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
    left_points: list[tuple[float, float]],
    right_points: list[tuple[float, float]],
) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch], str, str, str]:
    if not left_points:
        keypoints_left, keypoints_right, matches, backend_name = _detect_auto_matches(left, right, config)
        return keypoints_left, keypoints_right, matches, "auto", "none", backend_name

    keypoints_left_auto, keypoints_right_auto, auto_matches, backend_name = _detect_auto_matches(left, right, config)
    seed_transform, seed_model = _estimate_seed_guidance_transform(left_points, right_points, config)
    threshold_px = _guidance_threshold_px(config, seed_model)
    filtered_auto_matches = []
    scored_matches: list[tuple[float, cv2.DMatch]] = []
    for match in auto_matches:
        left_pt = keypoints_left_auto[match.queryIdx].pt
        right_pt = keypoints_right_auto[match.trainIdx].pt
        reproj_error = _reprojection_error(seed_transform, right_pt, left_pt)
        scored_matches.append((reproj_error, match))
        if reproj_error <= threshold_px:
            filtered_auto_matches.append(match)
    if len(filtered_auto_matches) < _assisted_min_matches(config):
        scored_matches.sort(key=lambda item: item[0])
        filtered_auto_matches = [match for _, match in scored_matches[: min(len(scored_matches), config.assisted_max_auto_matches)]]
    filtered_auto_matches = filtered_auto_matches[: max(0, int(config.assisted_max_auto_matches))]
    if len(filtered_auto_matches) < _assisted_min_matches(config):
        raise StitchingFailure(
            ErrorCode.OVERLAP_LOW,
            f"seed-guided matches below threshold: {len(filtered_auto_matches)} < {_assisted_min_matches(config)}",
        )
    return keypoints_left_auto, keypoints_right_auto, filtered_auto_matches, "assisted", seed_model, backend_name


def _detect_auto_matches(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch], str]:
    backend = str(config.match_backend).lower().strip()
    if backend in {"auto", "deep"}:
        try:
            deep_result = detect_and_match_deep(left, right, config)
            return deep_result.keypoints_left, deep_result.keypoints_right, deep_result.matches, deep_result.backend_name
        except StitchingFailure as exc:
            if backend == "deep":
                raise
            if exc.code != ErrorCode.INTERNAL_ERROR:
                raise
    keypoints_left, keypoints_right, matches = _detect_and_match_classic_raw(left, right, config)
    if len(matches) < int(config.min_matches):
        raise StitchingFailure(
            ErrorCode.OVERLAP_LOW,
            f"matches below threshold: {len(matches)} < {int(config.min_matches)}",
        )
    backend_name = "classic" if backend != "deep" else "classic_fallback"
    return keypoints_left, keypoints_right, matches, backend_name


def _validate_calibration_quality(
    left: np.ndarray,
    right: np.ndarray,
    plan_width: int,
    plan_height: int,
    inliers_count: int,
    config: NativeCalibrationConfig,
) -> None:
    left_h, left_w = left.shape[:2]
    right_h, right_w = right.shape[:2]
    max_input_w = max(left_w, right_w)
    max_input_h = max(left_h, right_h)
    if inliers_count < max(12, int(config.min_inliers)):
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"calibration quality too low: inliers {inliers_count} < {max(12, int(config.min_inliers))}",
        )
    if plan_height > int(max_input_h * 1.6):
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"calibration geometry too tall: {plan_width}x{plan_height}",
        )
    if plan_width > int(max_input_w * 3.5):
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"calibration geometry too wide: {plan_width}x{plan_height}",
        )
    if plan_width <= max_input_w:
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"calibration geometry not panoramic enough: {plan_width}x{plan_height}",
        )


def _clamp_overlap_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int] | None:
    x1 = max(0, int(np.floor(x)))
    y1 = max(0, int(np.floor(y)))
    x2 = min(image_width, int(np.ceil(x + w)))
    y2 = min(image_height, int(np.ceil(y + h)))
    if x2 - x1 < 12 or y2 - y1 < 12:
        return None
    return x1, y1, x2 - x1, y2 - y1


def _robust_overlap_rect(
    points: np.ndarray,
    *,
    image_width: int,
    image_height: int,
    prefer_side: str,
) -> tuple[int, int, int, int] | None:
    if points.size == 0 or len(points) < 6:
        return None
    xs = points[:, 0]
    ys = points[:, 1]
    x1 = float(np.percentile(xs, 8))
    x2 = float(np.percentile(xs, 92))
    y1 = float(np.percentile(ys, 8))
    y2 = float(np.percentile(ys, 92))

    width = max(24.0, x2 - x1)
    height = max(24.0, y2 - y1)

    x_pad = width * 0.24
    y_pad = height * 0.22
    x1 -= x_pad
    x2 += x_pad
    y1 -= y_pad
    y2 += y_pad

    min_width = image_width * 0.42
    current_width = x2 - x1
    if current_width < min_width:
        expand = (min_width - current_width) * 0.5
        x1 -= expand
        x2 += expand

    max_width = image_width * 0.78
    current_width = x2 - x1
    if current_width > max_width:
        if prefer_side == "right":
            x2 = min(float(image_width), x2)
            x1 = x2 - max_width
        else:
            x1 = max(0.0, x1)
            x2 = x1 + max_width

    min_height = image_height * 0.34
    current_height = y2 - y1
    if current_height < min_height:
        expand = (min_height - current_height) * 0.5
        y1 -= expand
        y2 += expand

    max_height = image_height * 0.86
    current_height = y2 - y1
    if current_height > max_height:
        center_y = (y1 + y2) * 0.5
        y1 = center_y - max_height * 0.5
        y2 = center_y + max_height * 0.5

    return _clamp_overlap_rect(x1, y1, x2 - x1, y2 - y1, image_width, image_height)


def _estimate_overlap_hints(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    try:
        keypoints_left, keypoints_right, matches = _detect_and_match_classic_raw(left, right, config)
    except StitchingFailure:
        return None, None
    if len(matches) < 8:
        return None, None
    sorted_matches = sorted(matches, key=lambda match: float(match.distance))
    limit = min(len(sorted_matches), 60)
    left_pts = np.float32([keypoints_left[m.queryIdx].pt for m in sorted_matches[:limit]])
    right_pts = np.float32([keypoints_right[m.trainIdx].pt for m in sorted_matches[:limit]])
    left_rect = _robust_overlap_rect(
        left_pts,
        image_width=left.shape[1],
        image_height=left.shape[0],
        prefer_side="right",
    )
    right_rect = _robust_overlap_rect(
        right_pts,
        image_width=right.shape[1],
        image_height=right.shape[0],
        prefer_side="left",
    )
    return left_rect, right_rect


def _candidate_score(
    *,
    match_score: float,
    geometry_score: float,
    visual_score: float,
) -> float:
    return (match_score * 0.40) + (geometry_score * 0.30) + (visual_score * 0.30)


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _mean_reprojection_error(
    homography: np.ndarray,
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    inlier_mask: np.ndarray,
) -> float:
    errors: list[float] = []
    mask_values = inlier_mask.ravel().tolist()
    for match, keep in zip(matches, mask_values):
        if not int(keep):
            continue
        left_pt = keypoints_left[match.queryIdx].pt
        right_pt = keypoints_right[match.trainIdx].pt
        errors.append(_reprojection_error(homography, right_pt, left_pt))
    if not errors:
        return 9999.0
    return float(np.mean(np.asarray(errors, dtype=np.float32)))


def _compute_match_score(
    *,
    inliers_count: int,
    match_count: int,
    inlier_ratio: float,
    mean_reprojection_error: float,
    config: NativeCalibrationConfig,
) -> float:
    min_inliers = max(12.0, float(config.min_inliers))
    target_inliers = max(min_inliers * 2.5, 50.0)
    inliers_term = _clamp_unit(inliers_count / target_inliers)
    ratio_term = _clamp_unit(inlier_ratio / 0.70)
    match_term = _clamp_unit(match_count / max(float(config.min_matches) * 2.0, 100.0))
    reproj_term = _clamp_unit(1.0 - (mean_reprojection_error / 8.0))
    return (
        (inliers_term * 0.45)
        + (ratio_term * 0.25)
        + (match_term * 0.10)
        + (reproj_term * 0.20)
    )


def _compute_geometry_score(
    *,
    plan_width: int,
    plan_height: int,
    left: np.ndarray,
    right: np.ndarray,
) -> tuple[float, float]:
    left_h, left_w = left.shape[:2]
    right_h, right_w = right.shape[:2]
    max_input_w = max(left_w, right_w)
    max_input_h = max(left_h, right_h)

    width_ratio = plan_width / float(max(1, max_input_w))
    height_ratio = plan_height / float(max(1, max_input_h))
    width_penalty = max(0.0, width_ratio - 2.8) / 1.2
    height_penalty = max(0.0, height_ratio - 1.35) / 0.65
    pano_penalty = max(0.0, 1.0 - width_ratio) * 0.5
    distortion_penalty = _clamp_unit((width_penalty * 0.45) + (height_penalty * 0.45) + pano_penalty)
    return (1.0 - distortion_penalty), distortion_penalty


def _compute_visual_metrics(
    *,
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
) -> tuple[float, float, float, float]:
    overlap_mask = cv2.bitwise_and(left_mask, right_mask)
    overlap_pixels = int(cv2.countNonZero(overlap_mask))
    if overlap_pixels <= 0:
        return 0.50, 0.50, 0.50, 0.25

    left_gray = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY)
    right_gray = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(left_gray, right_gray)
    luma_diff = float(cv2.mean(diff, mask=overlap_mask)[0] / 255.0)

    left_edges = cv2.Canny(left_gray, 48, 144)
    right_edges = cv2.Canny(right_gray, 48, 144)
    edge_diff = cv2.absdiff(left_edges, right_edges)
    edge_diff_mean = float(cv2.mean(edge_diff, mask=overlap_mask)[0] / 255.0)

    ghosting = min(1.0, (luma_diff * 0.55) + (edge_diff_mean * 0.45))
    visual_score = 1.0 - min(1.0, (luma_diff * 0.45) + (edge_diff_mean * 0.30) + (ghosting * 0.25))
    return visual_score, luma_diff, edge_diff_mean, ghosting


def _build_candidate(
    *,
    left: np.ndarray,
    right: np.ndarray,
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    calibration_mode: str,
    seed_guidance_model: str,
    backend_name: str,
    config: NativeCalibrationConfig,
    enforce_quality_gate: bool,
) -> _CalibrationCandidate:
    transform_model = "homography"
    try:
        homography, inlier_mask = _estimate_homography(keypoints_left, keypoints_right, matches, config)
    except StitchingFailure as exc:
        if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
            raise
        homography, inlier_mask = _estimate_affine_homography(keypoints_left, keypoints_right, matches, config)
        transform_model = "affine_fallback"
    plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
    inliers_count = int(inlier_mask.ravel().sum())
    match_count = int(len(matches))
    inlier_ratio = float(inliers_count / float(max(1, match_count)))
    mean_reprojection_error = _mean_reprojection_error(homography, keypoints_left, keypoints_right, matches, inlier_mask)
    if enforce_quality_gate:
        _validate_calibration_quality(left, right, plan.width, plan.height, inliers_count, config)

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

    match_score = _compute_match_score(
        inliers_count=inliers_count,
        match_count=match_count,
        inlier_ratio=inlier_ratio,
        mean_reprojection_error=mean_reprojection_error,
        config=config,
    )
    geometry_score, _distortion_penalty = _compute_geometry_score(
        plan_width=plan.width,
        plan_height=plan.height,
        left=left,
        right=right,
    )
    visual_score, overlap_luma_diff, overlap_edge_diff, ghosting_score = _compute_visual_metrics(
        canvas_left=canvas_left,
        warped_right=warped_right,
        left_mask=left_mask,
        right_mask=right_mask,
    )
    score = _candidate_score(
        match_score=match_score,
        geometry_score=geometry_score,
        visual_score=visual_score,
    )
    return _CalibrationCandidate(
        homography=homography,
        inlier_mask=inlier_mask,
        keypoints_left=keypoints_left,
        keypoints_right=keypoints_right,
        matches=matches,
        calibration_mode=calibration_mode,
        transform_model=transform_model,
        seed_guidance_model=seed_guidance_model,
        score=score,
        inliers_count=inliers_count,
        match_count=match_count,
        inlier_ratio=inlier_ratio,
        mean_reprojection_error=mean_reprojection_error,
        match_score=match_score,
        geometry_score=geometry_score,
        visual_score=visual_score,
        output_width=int(plan.width),
        output_height=int(plan.height),
        overlap_luma_diff=overlap_luma_diff,
        overlap_edge_diff=overlap_edge_diff,
        ghosting_score=ghosting_score,
        backend_name=backend_name,
    )


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
    requested_mode = str(config.calibration_mode).lower().strip()
    left_points: list[tuple[float, float]] = []
    right_points: list[tuple[float, float]] = []
    left_overlap_hint, right_overlap_hint = _estimate_overlap_hints(left, right, config)
    if requested_mode in {"assisted", "manual"}:
        left_points, right_points = _AssistedCalibrationUi(
            left,
            right,
            left_overlap_hint=left_overlap_hint,
            right_overlap_hint=right_overlap_hint,
        ).run()

    candidates: list[_CalibrationCandidate] = []
    failures: list[str] = []

    # Baseline auto path is always preserved and evaluated first.
    try:
        auto_kp_left, auto_kp_right, auto_matches, auto_backend_name = _detect_auto_matches(left, right, config)
        candidates.append(
            _build_candidate(
                left=left,
                right=right,
                keypoints_left=auto_kp_left,
                keypoints_right=auto_kp_right,
                matches=auto_matches,
                calibration_mode="auto",
                seed_guidance_model="none",
                backend_name=auto_backend_name,
                config=config,
                enforce_quality_gate=False,
            )
        )
    except StitchingFailure as exc:
        failures.append(f"auto:{exc.code.value}:{exc.detail}")

    # Assisted/manual points only propose an improvement candidate; they never replace auto unless better.
    if left_points:
        try:
            assisted_kp_left, assisted_kp_right, assisted_matches, assisted_mode, seed_guidance_model, assisted_backend_name = _build_assisted_matches(
                left,
                right,
                config,
                left_points,
                right_points,
            )
            candidates.append(
                _build_candidate(
                    left=left,
                    right=right,
                    keypoints_left=assisted_kp_left,
                    keypoints_right=assisted_kp_right,
                    matches=assisted_matches,
                    calibration_mode=assisted_mode,
                    seed_guidance_model=seed_guidance_model,
                    backend_name=assisted_backend_name,
                    config=config,
                    enforce_quality_gate=True,
                )
            )
        except StitchingFailure as exc:
            failures.append(f"assisted:{exc.code.value}:{exc.detail}")

    if not candidates:
        if failures:
            detail = " | ".join(failures)
            raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, f"no valid calibration candidate ({detail})")
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "no valid calibration candidate")

    auto_candidate = next((item for item in candidates if item.calibration_mode == "auto"), None)
    best_candidate = max(candidates, key=lambda item: item.score)
    score_margin = 0.03
    if auto_candidate is not None and best_candidate is not auto_candidate:
        if float(best_candidate.score) < float(auto_candidate.score) + score_margin:
            best_candidate = auto_candidate
    homography = best_candidate.homography
    inlier_mask = best_candidate.inlier_mask
    keypoints_left = best_candidate.keypoints_left
    keypoints_right = best_candidate.keypoints_right
    matches = best_candidate.matches
    calibration_mode_effective = best_candidate.calibration_mode
    transform_model = best_candidate.transform_model
    seed_guidance_model = best_candidate.seed_guidance_model

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
    inliers_count = int(inlier_mask.ravel().sum())
    review_lines = [
        f"mode={calibration_mode_effective}  seed={seed_guidance_model}  model={transform_model}",
        f"score={best_candidate.score:.3f}  match={best_candidate.match_score:.3f}  geom={best_candidate.geometry_score:.3f}  visual={best_candidate.visual_score:.3f}",
        f"matches={len(matches)}  inliers={inliers_count}  inlier_ratio={best_candidate.inlier_ratio:.3f}  repr_err={best_candidate.mean_reprojection_error:.2f}px",
        f"output={plan.width}x{plan.height}  luma_diff={best_candidate.overlap_luma_diff:.3f}  edge_diff={best_candidate.overlap_edge_diff:.3f}  ghost={best_candidate.ghosting_score:.3f}",
        f"manual_points={min(len(left_points), len(right_points))}  backend={best_candidate.backend_name}",
        "CONFIRM saves this homography and launches runtime. CANCEL stops here.",
    ]
    if not _CalibrationReviewUi(
        inlier_preview=inlier_preview,
        stitched_preview=stitched,
        summary_lines=review_lines,
    ).run():
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "calibration review cancelled by user")
    _write_debug_outputs(config, left, right, stitched, inlier_preview)
    metadata = {
        "source": "native_runtime_calibration",
        "calibration_mode_requested": str(config.calibration_mode),
        "calibration_mode_effective": calibration_mode_effective,
        "left_rtsp": config.left_rtsp,
        "right_rtsp": config.right_rtsp,
        "rtsp_transport": config.rtsp_transport,
        "process_scale": float(config.process_scale),
        "manual_points_count": int(min(len(left_points), len(right_points))),
        "match_backend_requested": str(config.match_backend),
        "deep_backend_requested": str(config.deep_backend),
        "match_backend_effective": best_candidate.backend_name,
        "selected_candidate": calibration_mode_effective,
        "seed_guidance_model": seed_guidance_model,
        "candidate_failures": failures,
        "candidate_count": int(len(candidates)),
        "candidate_score": float(best_candidate.score),
        "match_score": float(best_candidate.match_score),
        "geometry_score": float(best_candidate.geometry_score),
        "visual_score": float(best_candidate.visual_score),
        "matches_count": int(len(matches)),
        "inliers_count": inliers_count,
        "inlier_ratio": float(best_candidate.inlier_ratio),
        "mean_reprojection_error": float(best_candidate.mean_reprojection_error),
        "overlap_luma_diff": float(best_candidate.overlap_luma_diff),
        "overlap_edge_diff": float(best_candidate.overlap_edge_diff),
        "ghosting_score": float(best_candidate.ghosting_score),
        "transform_model": transform_model,
        "left_resolution": [int(left.shape[1]), int(left.shape[0])],
        "right_resolution": [int(right.shape[1]), int(right.shape[0])],
        "output_resolution": [int(plan.width), int(plan.height)],
        "debug_dir": str(config.debug_dir),
        "candidates": [
            {
                "name": item.calibration_mode,
                "seed_guidance_model": item.seed_guidance_model,
                "backend_name": item.backend_name,
                "transform_model": item.transform_model,
                "score": float(item.score),
                "match_score": float(item.match_score),
                "geometry_score": float(item.geometry_score),
                "visual_score": float(item.visual_score),
                "matches_count": int(item.match_count),
                "inliers_count": int(item.inliers_count),
                "inlier_ratio": float(item.inlier_ratio),
                "mean_reprojection_error": float(item.mean_reprojection_error),
                "output_resolution": [int(item.output_width), int(item.output_height)],
                "overlap_luma_diff": float(item.overlap_luma_diff),
                "overlap_edge_diff": float(item.overlap_edge_diff),
                "ghosting_score": float(item.ghosting_score),
                "accepted": bool(item is best_candidate),
            }
            for item in candidates
        ],
    }
    _save_homography_file(config.output_path, homography, metadata)
    return {
        "homography_file": str(config.output_path),
        "debug_dir": str(config.debug_dir),
        "matches_count": int(len(matches)),
        "inliers_count": inliers_count,
        "manual_points_count": int(min(len(left_points), len(right_points))),
        "calibration_mode": calibration_mode_effective,
        "seed_guidance_model": seed_guidance_model,
        "candidate_failures": failures,
        "transform_model": transform_model,
        "candidate_score": float(best_candidate.score),
        "match_score": float(best_candidate.match_score),
        "geometry_score": float(best_candidate.geometry_score),
        "visual_score": float(best_candidate.visual_score),
        "inlier_ratio": float(best_candidate.inlier_ratio),
        "mean_reprojection_error": float(best_candidate.mean_reprojection_error),
        "output_resolution": [int(plan.width), int(plan.height)],
        "match_backend": best_candidate.backend_name,
    }


def run_native_calibration(args: argparse.Namespace) -> int:
    require_configured_rtsp_urls(
        str(args.left_rtsp),
        str(args.right_rtsp),
        context="native calibration",
    )
    config = NativeCalibrationConfig(
        left_rtsp=str(args.left_rtsp),
        right_rtsp=str(args.right_rtsp),
        output_path=Path(args.out),
        debug_dir=Path(args.debug_dir),
        rtsp_transport=str(args.rtsp_transport),
        rtsp_timeout_sec=max(1.0, float(args.rtsp_timeout_sec)),
        warmup_frames=max(1, int(args.warmup_frames)),
        process_scale=max(0.1, float(args.process_scale)),
        calibration_mode=str(args.calibration_mode),
        assisted_reproj_threshold=max(1.0, float(args.assisted_reproj_threshold)),
        assisted_max_auto_matches=max(0, int(args.assisted_max_auto_matches)),
        match_backend=str(args.match_backend),
        deep_backend=str(args.deep_backend),
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
        f"manual_points={result['manual_points_count']} "
        f"mode={result['calibration_mode']} "
        f"seed_model={result['seed_guidance_model']} "
        f"model={result['transform_model']} "
        f"backend={result['match_backend']} "
        f"score={result['candidate_score']:.3f} "
        f"repr_err={result['mean_reprojection_error']:.2f}"
    )
    if bool(getattr(args, "launch_runtime", False)):
        repo_root = Path(__file__).resolve().parent.parent
        runtime_command = [
            sys.executable,
            "-m",
            "stitching.cli",
            "native-runtime",
            "--no-output-ui",
        ]
        print(f"launching_runtime={' '.join(runtime_command)}")
        completed = subprocess.run(
            runtime_command,
            cwd=str(repo_root),
            env=os.environ.copy(),
            check=False,
        )
        return int(completed.returncode)
    return 0
