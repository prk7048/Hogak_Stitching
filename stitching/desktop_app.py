from __future__ import annotations

from collections import deque
import os
import threading
import time
from dataclasses import dataclass, replace

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


@dataclass(slots=True)
class DesktopConfig:
    left_rtsp: str
    right_rtsp: str
    rtsp_transport: str = "tcp"
    rtsp_timeout_sec: float = 10.0
    reconnect_cooldown_sec: float = 1.0
    max_display_width: int = 2880
    process_scale: float = 1.0
    min_matches: int = 20
    min_inliers: int = 8
    ratio_test: float = 0.82
    ransac_thresh: float = 6.0
    stitch_every_n: int = 3
    max_features: int = 2800
    stitch_output_scale: float = 0.6
    gpu_mode: str = "on"  # off | auto | on
    gpu_device: int = 0
    manual_points: int = 4
    inlier_preview_interval_sec: float = 5.0


class RtspReader:
    def __init__(self, *, name: str, url: str, config: DesktopConfig) -> None:
        self.name = name
        self.url = url
        self.config = config

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: np.ndarray | None = None
        self._cap: cv2.VideoCapture | None = None
        self._last_error = ""
        self._frames_total = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"desktop-rtsp-{self.name}")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._release()

    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def snapshot_stats(self) -> dict[str, int | str]:
        with self._lock:
            return {
                "frames_total": int(self._frames_total),
                "last_error": self._last_error,
            }

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def _set_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
            self._last_error = ""
            self._frames_total += 1

    def _release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _open(self) -> cv2.VideoCapture:
        transport = str(self.config.rtsp_transport).lower().strip()
        if transport not in {"tcp", "udp"}:
            transport = "tcp"
        timeout_us = max(1, int(max(0.1, float(self.config.rtsp_timeout_sec)) * 1_000_000))
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            f"rtsp_transport;{transport}|stimeout;{timeout_us}|fflags;nobuffer|flags;low_delay"
        )

        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        cap.release()

        cap = cv2.VideoCapture(self.url)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        cap.release()
        raise RuntimeError(f"cannot open RTSP: {self.url}")

    def _run(self) -> None:
        cooldown = max(0.2, float(self.config.reconnect_cooldown_sec))
        while not self._stop_event.is_set():
            try:
                self._cap = self._open()
            except Exception as exc:
                self._set_error(str(exc))
                time.sleep(cooldown)
                continue

            assert self._cap is not None
            while not self._stop_event.is_set():
                ok = self._cap.grab()
                if not ok:
                    self._set_error("grab failed, reconnecting")
                    break
                ok, frame = self._cap.retrieve()
                if not ok or frame is None:
                    continue
                self._set_frame(frame)

            self._release()
            if not self._stop_event.is_set():
                time.sleep(cooldown)


def _resize_frame(frame: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return frame
    h, w = frame.shape[:2]
    nw = max(2, int(round(w * scale)))
    nh = max(2, int(round(h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(frame, (nw, nh), interpolation=interpolation)


class DesktopStitcher:
    def __init__(self, config: DesktopConfig) -> None:
        self._desktop_cfg = config
        self._stitch_every_n = max(1, int(config.stitch_every_n))
        self._manual_target = max(4, int(config.manual_points))
        self._cfg = StitchConfig(
            min_matches=int(config.min_matches),
            min_inliers=int(config.min_inliers),
            ratio_test=float(config.ratio_test),
            ransac_reproj_threshold=float(config.ransac_thresh),
            max_features=max(500, int(config.max_features)),
        )
        self._plan = None
        self._left_shape: tuple[int, int] | None = None
        self._right_shape: tuple[int, int] | None = None
        self._right_mask_template: np.ndarray | None = None
        self._last_calib_try = 0.0
        self._last_status = "auto calibration pending"
        self._last_stitched: np.ndarray | None = None
        self._frame_index = 0

        self._matches_last = 0
        self._inliers_last = 0
        self._stitched_count = 0
        self._reused_count = 0
        self._stitch_timestamps: deque[float] = deque(maxlen=240)

        self._gpu_enabled = False
        self._gpu_reason = "gpu disabled"
        self._gpu_warp_count = 0
        self._cpu_warp_count = 0
        self._gpu_errors = 0
        self._resolve_gpu_mode()

        self._manual_required_initial = False
        self._manual_mode = False
        self._manual_left: list[tuple[float, float, int, int]] = []
        self._manual_right: list[tuple[float, float, int, int]] = []

    def _auto_calibration_candidates(self) -> list[StitchConfig]:
        base = self._cfg
        relaxed = replace(
            base,
            min_matches=max(16, int(base.min_matches * 0.8)),
            min_inliers=max(6, int(base.min_inliers * 0.8)),
            ratio_test=min(0.90, float(base.ratio_test) + 0.06),
            ransac_reproj_threshold=min(10.0, float(base.ransac_reproj_threshold) + 1.5),
            max_features=min(6000, int(base.max_features * 1.7)),
        )
        relaxed_more = replace(
            base,
            min_matches=max(14, int(base.min_matches * 0.7)),
            min_inliers=max(6, int(base.min_inliers * 0.75)),
            ratio_test=min(0.92, float(base.ratio_test) + 0.10),
            ransac_reproj_threshold=min(12.0, float(base.ransac_reproj_threshold) + 2.5),
            max_features=min(7000, int(base.max_features * 2.2)),
        )
        return [base, relaxed, relaxed_more]

    def _resolve_gpu_mode(self) -> None:
        mode = str(self._desktop_cfg.gpu_mode).strip().lower()
        if mode not in {"off", "auto", "on"}:
            mode = "on"
        if mode == "off":
            self._gpu_enabled = False
            self._gpu_reason = "gpu mode off"
            return

        if not hasattr(cv2, "cuda"):
            self._gpu_enabled = False
            self._gpu_reason = "WARNING: OpenCV CUDA module not found, fallback CPU"
            return
        try:
            count = int(cv2.cuda.getCudaEnabledDeviceCount())
        except Exception as exc:
            self._gpu_enabled = False
            self._gpu_reason = f"WARNING: CUDA detect failed ({exc}), fallback CPU"
            return
        if count <= 0:
            self._gpu_enabled = False
            self._gpu_reason = "WARNING: no CUDA device, fallback CPU"
            return

        device = max(0, int(self._desktop_cfg.gpu_device))
        if device >= count:
            device = 0
        try:
            cv2.cuda.setDevice(device)
        except Exception as exc:
            self._gpu_enabled = False
            self._gpu_reason = f"WARNING: setDevice failed ({exc}), fallback CPU"
            return

        self._gpu_enabled = True
        self._gpu_reason = f"cuda device {device}"

    def request_manual_calibration(self) -> None:
        self._manual_mode = True
        self._manual_left.clear()
        self._manual_right.clear()
        self._last_status = "manual point mode: click left/right points"

    def request_auto_mode(self) -> None:
        self._manual_mode = False
        self._manual_required_initial = False
        self._manual_left.clear()
        self._manual_right.clear()
        self._last_status = "auto mode requested"

    def add_manual_point(self, side: str, x: float, y: float, raw_w: int, raw_h: int) -> None:
        point = (float(x), float(y), int(raw_w), int(raw_h))
        if side == "left":
            self._manual_left.append(point)
            if len(self._manual_left) > self._manual_target:
                self._manual_left = self._manual_left[-self._manual_target :]
        elif side == "right":
            self._manual_right.append(point)
            if len(self._manual_right) > self._manual_target:
                self._manual_right = self._manual_right[-self._manual_target :]

    def _manual_ready(self) -> bool:
        return len(self._manual_left) >= self._manual_target and len(self._manual_right) >= self._manual_target

    @staticmethod
    def _project_manual_points(
        points: list[tuple[float, float, int, int]],
        target_shape: tuple[int, int],
    ) -> np.ndarray:
        th, tw = target_shape
        out: list[tuple[float, float]] = []
        for x, y, rw, rh in points:
            sx = tw / float(max(1, rw))
            sy = th / float(max(1, rh))
            out.append((x * sx, y * sy))
        return np.asarray(out, dtype=np.float32).reshape(-1, 1, 2)

    def _apply_manual_calibration(self, left: np.ndarray, right: np.ndarray) -> bool:
        if not self._manual_ready():
            return False
        try:
            src_points = self._project_manual_points(self._manual_right[-self._manual_target :], right.shape[:2])
            dst_points = self._project_manual_points(self._manual_left[-self._manual_target :], left.shape[:2])
            homography, inlier_mask = cv2.findHomography(
                src_points,
                dst_points,
                cv2.RANSAC,
                self._cfg.ransac_reproj_threshold,
            )
            if homography is None or inlier_mask is None:
                raise RuntimeError("manual homography failed")

            self._plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, self._cfg)
            self._left_shape = left.shape[:2]
            self._right_shape = right.shape[:2]
            self._right_mask_template = cv2.warpPerspective(
                np.ones(right.shape[:2], dtype=np.uint8) * 255,
                self._plan.homography_adjusted,
                (self._plan.width, self._plan.height),
            )
            self._matches_last = self._manual_target
            self._inliers_last = int(inlier_mask.ravel().sum())
            self._manual_required_initial = False
            self._manual_mode = False
            self._manual_left.clear()
            self._manual_right.clear()
            self._last_status = "manual calibrated"
            return True
        except Exception as exc:
            self._plan = None
            self._last_status = f"manual calibration failed: {exc}"
            self._manual_left.clear()
            self._manual_right.clear()
            return False

    def _need_recalibration(self, left: np.ndarray, right: np.ndarray) -> bool:
        if self._plan is None:
            return True
        return self._left_shape != left.shape[:2] or self._right_shape != right.shape[:2]

    def _try_auto_calibrate(self, left: np.ndarray, right: np.ndarray) -> None:
        now = time.perf_counter()
        if now - self._last_calib_try < 0.8:
            return
        self._last_calib_try = now
        last_exc: Exception | None = None
        for idx, candidate_cfg in enumerate(self._auto_calibration_candidates(), start=1):
            try:
                keypoints_left, keypoints_right, matches = _detect_and_match(left, right, candidate_cfg)
                try:
                    homography, inlier_mask = _estimate_homography(
                        keypoints_left,
                        keypoints_right,
                        matches,
                        candidate_cfg,
                    )
                except StitchingFailure:
                    homography, inlier_mask = _estimate_affine_homography(
                        keypoints_left,
                        keypoints_right,
                        matches,
                        candidate_cfg,
                    )

                self._cfg = candidate_cfg
                self._plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, self._cfg)
                self._left_shape = left.shape[:2]
                self._right_shape = right.shape[:2]
                self._right_mask_template = cv2.warpPerspective(
                    np.ones(right.shape[:2], dtype=np.uint8) * 255,
                    self._plan.homography_adjusted,
                    (self._plan.width, self._plan.height),
                )
                self._matches_last = int(len(matches))
                self._inliers_last = int(inlier_mask.ravel().sum()) if inlier_mask is not None else 0
                if idx == 1:
                    self._last_status = "auto calibrated"
                else:
                    self._last_status = f"auto calibrated (relaxed#{idx - 1})"
                return
            except Exception as exc:
                last_exc = exc

        self._plan = None
        self._last_status = f"auto calibrating: {last_exc}" if last_exc else "auto calibrating"

    def build_inlier_preview(self, left: np.ndarray, right: np.ndarray) -> np.ndarray | None:
        try:
            keypoints_left, keypoints_right, matches = _detect_and_match(left, right, self._cfg)
            try:
                _, inlier_mask = _estimate_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    self._cfg,
                )
            except StitchingFailure:
                _, inlier_mask = _estimate_affine_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    self._cfg,
                )
            limit = min(120, len(matches))
            return cv2.drawMatches(
                left,
                keypoints_left,
                right,
                keypoints_right,
                matches[:limit],
                None,
                matchesMask=[int(v) for v in inlier_mask.ravel().tolist()[:limit]],
                flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
            )
        except Exception:
            return None

    def stitch(self, left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray | None, str]:
        self._frame_index += 1

        if self._manual_mode:
            if self._manual_ready() and self._apply_manual_calibration(left, right):
                pass
            else:
                status = (
                    f"manual points: L{len(self._manual_left)}/{self._manual_target}, "
                    f"R{len(self._manual_right)}/{self._manual_target}"
                )
                self._last_status = status
                if self._last_stitched is not None:
                    return self._last_stitched, status
                return None, status

        if self._need_recalibration(left, right):
            self._plan = None
            self._try_auto_calibrate(left, right)
            if self._plan is None:
                if self._last_stitched is not None:
                    return self._last_stitched, self._last_status
                return None, self._last_status

        assert self._plan is not None
        if self._last_stitched is not None and self._frame_index % self._stitch_every_n != 0:
            self._reused_count += 1
            return self._last_stitched, "stitching (reused)"

        try:
            if self._gpu_enabled:
                try:
                    gpu_in = cv2.cuda_GpuMat()
                    gpu_in.upload(right)
                    gpu_warped = cv2.cuda.warpPerspective(
                        gpu_in,
                        self._plan.homography_adjusted,
                        (self._plan.width, self._plan.height),
                    )
                    right_warped = gpu_warped.download()
                    self._gpu_warp_count += 1
                except Exception:
                    self._gpu_errors += 1
                    right_warped = cv2.warpPerspective(
                        right,
                        self._plan.homography_adjusted,
                        (self._plan.width, self._plan.height),
                    )
                    self._cpu_warp_count += 1
            else:
                right_warped = cv2.warpPerspective(
                    right,
                    self._plan.homography_adjusted,
                    (self._plan.width, self._plan.height),
                )
                self._cpu_warp_count += 1

            right_mask = self._right_mask_template
            if right_mask is None:
                right_mask = cv2.warpPerspective(
                    np.ones(right.shape[:2], dtype=np.uint8) * 255,
                    self._plan.homography_adjusted,
                    (self._plan.width, self._plan.height),
                )

            left_canvas = np.zeros((self._plan.height, self._plan.width, 3), dtype=np.uint8)
            left_mask = np.zeros((self._plan.height, self._plan.width), dtype=np.uint8)
            lh, lw = left.shape[:2]
            left_canvas[self._plan.ty : self._plan.ty + lh, self._plan.tx : self._plan.tx + lw] = left
            left_mask[self._plan.ty : self._plan.ty + lh, self._plan.tx : self._plan.tx + lw] = 255
            stitched = _blend_feather(left_canvas, right_warped, left_mask, right_mask)

            self._last_stitched = stitched
            self._stitched_count += 1
            self._stitch_timestamps.append(time.perf_counter())
            return stitched, "stitching"
        except Exception as exc:
            self._plan = None
            self._last_status = f"recalibrate: {exc}"
            if self._last_stitched is not None:
                return self._last_stitched, self._last_status
            return None, self._last_status

    def metrics_snapshot(self) -> dict[str, float | int | str]:
        stitch_fps = 0.0
        if len(self._stitch_timestamps) >= 2:
            dt = self._stitch_timestamps[-1] - self._stitch_timestamps[0]
            if dt > 1e-6:
                stitch_fps = (len(self._stitch_timestamps) - 1) / dt
        return {
            "status": self._last_status,
            "frame_index": int(self._frame_index),
            "matches": int(self._matches_last),
            "inliers": int(self._inliers_last),
            "stitched_count": int(self._stitched_count),
            "reused_count": int(self._reused_count),
            "stitch_fps": float(stitch_fps),
            "gpu_enabled": bool(self._gpu_enabled),
            "gpu_reason": self._gpu_reason,
            "gpu_warp_count": int(self._gpu_warp_count),
            "cpu_warp_count": int(self._cpu_warp_count),
            "gpu_errors": int(self._gpu_errors),
            "manual_left": int(len(self._manual_left)),
            "manual_right": int(len(self._manual_right)),
            "manual_target": int(self._manual_target),
            "manual_mode": bool(self._manual_mode or self._manual_required_initial),
        }

    def manual_points_snapshot(
        self,
    ) -> tuple[list[tuple[float, float, int, int]], list[tuple[float, float, int, int]]]:
        return list(self._manual_left), list(self._manual_right)


class StitchWorker:
    def __init__(self, *, config: DesktopConfig, left: RtspReader | None, right: RtspReader | None) -> None:
        self._cfg = config
        self._left = left
        self._right = right
        self._stitcher = DesktopStitcher(config)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._lock = threading.Lock()
        self._latest_stitched: np.ndarray | None = None
        self._latest_inlier_preview: np.ndarray | None = None
        self._status = "waiting for both streams"
        self._metrics: dict[str, float | int | str] = {}
        self._worker_timestamps: deque[float] = deque(maxlen=240)
        self._last_inlier_preview_at = 0.0

        self._manual_request = False
        self._auto_request = False
        self._manual_clicks: deque[tuple[str, float, float, int, int]] = deque()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="desktop-stitch-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def request_manual_calibration(self) -> None:
        with self._lock:
            self._manual_request = True

    def request_auto_mode(self) -> None:
        with self._lock:
            self._auto_request = True

    def push_manual_click(self, side: str, x: float, y: float, raw_w: int, raw_h: int) -> None:
        with self._lock:
            self._manual_clicks.append((side, x, y, raw_w, raw_h))

    def snapshot(self) -> tuple[np.ndarray | None, np.ndarray | None, str, dict[str, float | int | str]]:
        with self._lock:
            stitched = self._latest_stitched.copy() if self._latest_stitched is not None else None
            inlier = self._latest_inlier_preview.copy() if self._latest_inlier_preview is not None else None
            return stitched, inlier, self._status, dict(self._metrics)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            left_frame = self._left.latest_frame() if self._left is not None else None
            right_frame = self._right.latest_frame() if self._right is not None else None

            with self._lock:
                manual_request = self._manual_request
                self._manual_request = False
                auto_request = self._auto_request
                self._auto_request = False
                clicks = list(self._manual_clicks)
                self._manual_clicks.clear()

            if manual_request:
                self._stitcher.request_manual_calibration()
            if auto_request:
                self._stitcher.request_auto_mode()
            for side, x, y, rw, rh in clicks:
                self._stitcher.add_manual_point(side, x, y, rw, rh)

            if left_frame is None or right_frame is None:
                metrics = self._stitcher.metrics_snapshot()
                metrics["status"] = "waiting for both streams"
                with self._lock:
                    self._status = "waiting for both streams"
                    self._metrics = metrics
                time.sleep(0.01)
                continue

            lf = _resize_frame(left_frame, float(self._cfg.process_scale))
            rf = _resize_frame(right_frame, float(self._cfg.process_scale))
            if lf.shape[:2] != rf.shape[:2]:
                rf = cv2.resize(rf, (lf.shape[1], lf.shape[0]), interpolation=cv2.INTER_LINEAR)

            stitched, status = self._stitcher.stitch(lf, rf)
            if stitched is not None:
                stitched = _resize_frame(stitched, max(0.1, float(self._cfg.stitch_output_scale)))

            now = time.perf_counter()
            if now - self._last_inlier_preview_at >= max(1.0, float(self._cfg.inlier_preview_interval_sec)):
                preview = self._stitcher.build_inlier_preview(lf, rf)
                if preview is not None:
                    self._last_inlier_preview_at = now
                    with self._lock:
                        self._latest_inlier_preview = preview

            self._worker_timestamps.append(now)
            metrics = self._stitcher.metrics_snapshot()
            left_pts, right_pts = self._stitcher.manual_points_snapshot()
            metrics["manual_left_points"] = left_pts
            metrics["manual_right_points"] = right_pts
            if len(self._worker_timestamps) >= 2:
                dt = self._worker_timestamps[-1] - self._worker_timestamps[0]
                metrics["worker_fps"] = float((len(self._worker_timestamps) - 1) / dt) if dt > 1e-6 else 0.0
            else:
                metrics["worker_fps"] = 0.0
            metrics["status"] = status

            with self._lock:
                if stitched is not None:
                    self._latest_stitched = stitched
                self._status = status
                self._metrics = metrics


def _fit_width(frame: np.ndarray, max_width: int) -> np.ndarray:
    if frame.shape[1] <= max_width:
        return frame
    scale = max_width / float(frame.shape[1])
    new_w = max(2, int(round(frame.shape[1] * scale)))
    new_h = max(2, int(round(frame.shape[0] * scale)))
    return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _placeholder(width: int, height: int, text: str) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (20, 20, 20)
    cv2.putText(frame, text, (20, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (180, 180, 180), 2, cv2.LINE_AA)
    return frame


def _fit_to_panel_with_map(
    frame: np.ndarray | None,
    panel_w: int,
    panel_h: int,
    text: str,
) -> tuple[np.ndarray, tuple[int, int, int, int, int, int] | None]:
    panel = _placeholder(panel_w, panel_h, text)
    if frame is None:
        return panel, None

    fh, fw = frame.shape[:2]
    if fh <= 0 or fw <= 0:
        return panel, None

    scale = min(panel_w / float(fw), panel_h / float(fh))
    nw = max(2, int(round(fw * scale)))
    nh = max(2, int(round(fh * scale)))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)

    y = (panel_h - nh) // 2
    x = (panel_w - nw) // 2
    panel[y : y + nh, x : x + nw] = resized
    return panel, (x, y, nw, nh, fw, fh)


def _build_log_panel(width: int, lines: list[str], height: int = 180) -> np.ndarray:
    panel = np.zeros((height, width, 3), dtype=np.uint8)
    panel[:] = (12, 12, 12)
    y = 24
    for line in lines:
        cv2.putText(panel, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (210, 210, 210), 1, cv2.LINE_AA)
        y += 22
        if y > height - 10:
            break
    return panel


def _build_action_buttons(panel: np.ndarray) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    h, w = panel.shape[:2]
    bw, bh = 210, 44
    x2 = w - 12
    y1 = h - bh - 10
    x1 = x2 - bw
    y2 = y1 + bh
    manual_rect = (x1, y1, x2, y2)
    cv2.rectangle(panel, (x1, y1), (x2, y2), (40, 120, 230), thickness=-1)
    cv2.rectangle(panel, (x1, y1), (x2, y2), (200, 220, 255), thickness=1)
    cv2.putText(panel, "Manual Stitch", (x1 + 10, y1 + 29), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (245, 245, 245), 2, cv2.LINE_AA)

    x2b = x1 - 12
    x1b = x2b - bw
    auto_rect = (x1b, y1, x2b, y2)
    cv2.rectangle(panel, (x1b, y1), (x2b, y2), (80, 80, 80), thickness=-1)
    cv2.rectangle(panel, (x1b, y1), (x2b, y2), (200, 200, 200), thickness=1)
    cv2.putText(panel, "Auto Retry", (x1b + 28, y1 + 29), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (245, 245, 245), 2, cv2.LINE_AA)
    return manual_rect, auto_rect


def _draw_manual_points(
    panel: np.ndarray,
    panel_map: tuple[int, int, int, int, int, int] | None,
    points: list[tuple[float, float, int, int]],
) -> None:
    if panel_map is None:
        return
    px, py, iw, ih, src_w, src_h = panel_map
    colors = [
        (0, 0, 255),
        (0, 255, 0),
        (255, 0, 0),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 0),
    ]
    for idx, (rx, ry, rw, rh) in enumerate(points, start=1):
        sx = src_w / float(max(1, rw))
        sy = src_h / float(max(1, rh))
        fx = rx * sx
        fy = ry * sy
        dx = px + int(round((fx / float(max(1, src_w))) * iw))
        dy = int(round((fy / float(max(1, src_h))) * ih)) + py
        color = colors[(idx - 1) % len(colors)]
        cv2.circle(panel, (dx, dy), 7, color, -1, cv2.LINE_AA)
        cv2.putText(panel, str(idx), (dx + 8, dy - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)


def _offset_map(
    panel_map: tuple[int, int, int, int, int, int] | None,
    ox: int,
    oy: int,
) -> tuple[int, int, int, int, int, int] | None:
    if panel_map is None:
        return None
    x, y, w, h, sw, sh = panel_map
    return (x + ox, y + oy, w, h, sw, sh)


def _draw_magnifier(
    canvas: np.ndarray,
    mouse_x: int,
    mouse_y: int,
    zoom_size: int = 120,
    sample_radius: int = 28,
) -> None:
    h, w = canvas.shape[:2]
    x1 = max(0, mouse_x - sample_radius)
    y1 = max(0, mouse_y - sample_radius)
    x2 = min(w, mouse_x + sample_radius)
    y2 = min(h, mouse_y + sample_radius)
    patch = canvas[y1:y2, x1:x2]
    if patch.size == 0:
        return
    zoom = cv2.resize(patch, (zoom_size, zoom_size), interpolation=cv2.INTER_NEAREST)
    cv2.circle(zoom, (zoom_size // 2, zoom_size // 2), 3, (0, 255, 255), -1, cv2.LINE_AA)
    zx = min(w - zoom_size - 10, mouse_x + 20)
    zy = min(h - zoom_size - 10, mouse_y + 20)
    zx = max(10, zx)
    zy = max(10, zy)
    canvas[zy : zy + zoom_size, zx : zx + zoom_size] = zoom
    cv2.rectangle(canvas, (zx, zy), (zx + zoom_size, zy + zoom_size), (230, 230, 230), 1)


def run_desktop(config: DesktopConfig) -> int:
    left_url = config.left_rtsp.strip()
    right_url = config.right_rtsp.strip()
    if not left_url and not right_url:
        print("Provide at least one RTSP URL via --left-rtsp or --right-rtsp")
        return 2

    left = RtspReader(name="left", url=left_url, config=config) if left_url else None
    right = RtspReader(name="right", url=right_url, config=config) if right_url else None
    if left is not None:
        left.start()
    if right is not None:
        right.start()

    stitch_worker = StitchWorker(config=config, left=left, right=right)
    stitch_worker.start()

    window_name = "RTSP Desktop Stitch Preview"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    ui_state: dict[str, object] = {
        "left_map": None,
        "right_map": None,
        "manual_button_rect": None,
        "auto_button_rect": None,
        "left_panel_rect": None,
        "right_panel_rect": None,
        "stitch_panel_rect": None,
        "inlier_panel_rect": None,
        "focus_panel": None,
        "ui_scale": 1.0,
        "mouse_x": 0.0,
        "mouse_y": 0.0,
        "manual_mode": False,
        "worker": stitch_worker,
    }

    def _on_mouse(event: int, x: int, y: int, _flags: int, userdata: object) -> None:
        state = userdata
        if not isinstance(state, dict):
            return
        worker = state.get("worker")
        if not isinstance(worker, StitchWorker):
            return
        scale = float(state.get("ui_scale", 1.0))
        if scale <= 0:
            scale = 1.0
        sx = x / scale
        sy = y / scale

        if event == cv2.EVENT_MOUSEMOVE:
            state["mouse_x"] = sx
            state["mouse_y"] = sy
            return
        if event != cv2.EVENT_LBUTTONDOWN:
            return

        focus = state.get("focus_panel")
        if isinstance(focus, str) and focus:
            state["focus_panel"] = None
            return

        for key, panel_name in (
            ("left_panel_rect", "left"),
            ("right_panel_rect", "right"),
            ("stitch_panel_rect", "stitch"),
            ("inlier_panel_rect", "inlier"),
        ):
            rect = state.get(key)
            if isinstance(rect, tuple) and len(rect) == 4:
                x1, y1, x2, y2 = rect
                if x1 <= sx <= x2 and y1 <= sy <= y2:
                    # In manual mode, left/right click keeps point-picking behavior.
                    if bool(state.get("manual_mode", False)) and panel_name in {"left", "right"}:
                        break
                    state["focus_panel"] = panel_name
                    return

        mrect = state.get("manual_button_rect")
        if isinstance(mrect, tuple) and len(mrect) == 4:
            x1, y1, x2, y2 = mrect
            if x1 <= sx <= x2 and y1 <= sy <= y2:
                worker.request_manual_calibration()
                return
        arect = state.get("auto_button_rect")
        if isinstance(arect, tuple) and len(arect) == 4:
            x1, y1, x2, y2 = arect
            if x1 <= sx <= x2 and y1 <= sy <= y2:
                worker.request_auto_mode()
                return

        for side_key, side_name in (("left_map", "left"), ("right_map", "right")):
            m = state.get(side_key)
            if not isinstance(m, tuple) or len(m) != 6:
                continue
            abs_x, abs_y, iw, ih, src_w, src_h = m
            if abs_x <= sx < abs_x + iw and abs_y <= sy < abs_y + ih:
                px = (sx - abs_x) * (src_w / float(max(1, iw)))
                py = (sy - abs_y) * (src_h / float(max(1, ih)))
                worker.push_manual_click(side_name, px, py, int(src_w), int(src_h))
                return

    cv2.setMouseCallback(window_name, _on_mouse, ui_state)

    display_timestamps: deque[float] = deque(maxlen=240)

    try:
        while True:
            left_frame = left.latest_frame() if left is not None else None
            right_frame = right.latest_frame() if right is not None else None
            stitched, inlier_preview, stitch_status, metrics = stitch_worker.snapshot()

            reference = left_frame if left_frame is not None else right_frame
            if reference is not None:
                rh, rw = reference.shape[:2]
                ref_aspect = float(rw) / float(max(1, rh))
            else:
                ref_aspect = 16.0 / 9.0

            panel_w = max(320, int(config.max_display_width // 3))
            panel_h = max(180, int(panel_w / max(0.1, ref_aspect)))

            left_panel, left_map_local = _fit_to_panel_with_map(left_frame, panel_w, panel_h, "Left stream (raw)")
            right_panel, right_map_local = _fit_to_panel_with_map(right_frame, panel_w, panel_h, "Right stream (raw)")
            stitch_panel, _ = _fit_to_panel_with_map(stitched, panel_w, panel_h, f"Stitched: {stitch_status}")

            manual_left_points = list(metrics.get("manual_left_points", []))  # type: ignore[arg-type]
            manual_right_points = list(metrics.get("manual_right_points", []))  # type: ignore[arg-type]
            _draw_manual_points(left_panel, left_map_local, manual_left_points)
            _draw_manual_points(right_panel, right_map_local, manual_right_points)
            top_row = np.hstack([left_panel, right_panel, stitch_panel])

            left_map = _offset_map(left_map_local, 0, 0)
            right_map = _offset_map(right_map_local, panel_w, 0)
            ui_state["left_map"] = left_map
            ui_state["right_map"] = right_map

            display_timestamps.append(time.perf_counter())
            display_fps = 0.0
            if len(display_timestamps) >= 2:
                dt = display_timestamps[-1] - display_timestamps[0]
                if dt > 1e-6:
                    display_fps = (len(display_timestamps) - 1) / dt

            left_stats = left.snapshot_stats() if left is not None else {"frames_total": 0, "last_error": "disabled"}
            right_stats = right.snapshot_stats() if right is not None else {"frames_total": 0, "last_error": "disabled"}

            lines = [
                f"status={metrics.get('status', stitch_status)}  frame={int(metrics.get('frame_index', 0))}  display_fps={display_fps:.2f}",
                f"matches={int(metrics.get('matches', 0))}  inliers={int(metrics.get('inliers', 0))}  stitch_fps={float(metrics.get('stitch_fps', 0.0)):.2f}  worker_fps={float(metrics.get('worker_fps', 0.0)):.2f}",
                f"stitched_count={int(metrics.get('stitched_count', 0))}  reused_count={int(metrics.get('reused_count', 0))}  stitch_every_n={int(config.stitch_every_n)}",
                f"manual L/R={int(metrics.get('manual_left', 0))}/{int(metrics.get('manual_right', 0))} target={int(metrics.get('manual_target', 0))}",
                f"gpu_enabled={bool(metrics.get('gpu_enabled', False))}  gpu_reason={metrics.get('gpu_reason', '-')}",
                f"gpu_warp={int(metrics.get('gpu_warp_count', 0))}  cpu_warp={int(metrics.get('cpu_warp_count', 0))}  gpu_errors={int(metrics.get('gpu_errors', 0))}",
                f"left_frames={int(left_stats['frames_total'])}  right_frames={int(right_stats['frames_total'])}",
            ]
            log_panel = _build_log_panel(top_row.shape[1], lines)
            manual_rect_local, auto_rect_local = _build_action_buttons(log_panel)
            ui_state["manual_button_rect"] = (
                manual_rect_local[0],
                manual_rect_local[1] + top_row.shape[0],
                manual_rect_local[2],
                manual_rect_local[3] + top_row.shape[0],
            )
            ui_state["auto_button_rect"] = (
                auto_rect_local[0],
                auto_rect_local[1] + top_row.shape[0],
                auto_rect_local[2],
                auto_rect_local[3] + top_row.shape[0],
            )

            inlier_panel = _fit_to_panel_with_map(
                inlier_preview,
                top_row.shape[1],
                230,
                "Inlier Preview (refresh ~5s)",
            )[0]

            canvas_raw = np.vstack([top_row, log_panel, inlier_panel])

            # panel rects in raw canvas coordinates (before fit_width scaling)
            ui_state["left_panel_rect"] = (0, 0, panel_w, panel_h)
            ui_state["right_panel_rect"] = (panel_w, 0, panel_w * 2, panel_h)
            ui_state["stitch_panel_rect"] = (panel_w * 2, 0, panel_w * 3, panel_h)
            inlier_y1 = top_row.shape[0] + log_panel.shape[0]
            ui_state["inlier_panel_rect"] = (0, inlier_y1, top_row.shape[1], inlier_y1 + inlier_panel.shape[0])

            focus = ui_state.get("focus_panel")
            if focus in {"left", "right", "stitch", "inlier"}:
                if focus == "left":
                    focus_frame = left_frame
                    focus_label = "Focused: Left stream (click to return)"
                elif focus == "right":
                    focus_frame = right_frame
                    focus_label = "Focused: Right stream (click to return)"
                elif focus == "stitch":
                    focus_frame = stitched
                    focus_label = "Focused: Stitched output (click to return)"
                else:
                    focus_frame = inlier_preview
                    focus_label = "Focused: Inlier preview (click to return)"

                focus_canvas, _ = _fit_to_panel_with_map(
                    focus_frame,
                    max(640, int(config.max_display_width)),
                    max(360, int(max(640, int(config.max_display_width)) / max(0.1, ref_aspect))),
                    focus_label,
                )
                canvas_raw = focus_canvas
                ui_state["left_panel_rect"] = None
                ui_state["right_panel_rect"] = None
                ui_state["stitch_panel_rect"] = None
                ui_state["inlier_panel_rect"] = None

            canvas = _fit_width(canvas_raw, max_width=max(960, int(config.max_display_width)))
            ui_scale = canvas.shape[1] / float(max(1, canvas_raw.shape[1]))
            ui_state["ui_scale"] = ui_scale
            ui_state["manual_mode"] = bool(metrics.get("manual_mode", False))

            cv2.putText(
                canvas,
                f"status: {stitch_status}",
                (14, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (30, 255, 30),
                2,
                cv2.LINE_AA,
            )
            if bool(ui_state.get("manual_mode", False)):
                mx = int(float(ui_state.get("mouse_x", 0.0)) * float(ui_state.get("ui_scale", 1.0)))
                my = int(float(ui_state.get("mouse_y", 0.0)) * float(ui_state.get("ui_scale", 1.0)))
                _draw_magnifier(canvas, mx, my)
            cv2.imshow(window_name, canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("m"):
                stitch_worker.request_manual_calibration()
    finally:
        stitch_worker.stop()
        if left is not None:
            left.stop()
        if right is not None:
            right.stop()
        cv2.destroyAllWindows()

    return 0
