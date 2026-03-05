from __future__ import annotations

from collections import deque
import os
import threading
import time
from dataclasses import dataclass

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
    max_display_width: int = 1920
    process_scale: float = 1.0
    min_matches: int = 20
    min_inliers: int = 10
    ratio_test: float = 0.75
    ransac_thresh: float = 5.0
    stitch_every_n: int = 3
    max_features: int = 1200
    stitch_output_scale: float = 0.6


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


class DesktopStitcher:
    def __init__(self, config: DesktopConfig) -> None:
        self._stitch_every_n = max(1, int(config.stitch_every_n))
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
        self._last_calib_try = 0.0
        self._last_status = "calibration pending"
        self._last_stitched: np.ndarray | None = None
        self._frame_index = 0

        self._matches_last = 0
        self._inliers_last = 0
        self._stitched_count = 0
        self._reused_count = 0
        self._stitch_timestamps: deque[float] = deque(maxlen=240)

    def _need_recalibration(self, left: np.ndarray, right: np.ndarray) -> bool:
        if self._plan is None:
            return True
        return self._left_shape != left.shape[:2] or self._right_shape != right.shape[:2]

    def _try_calibrate(self, left: np.ndarray, right: np.ndarray) -> None:
        now = time.perf_counter()
        if now - self._last_calib_try < 0.8:
            return
        self._last_calib_try = now
        try:
            keypoints_left, keypoints_right, matches = _detect_and_match(left, right, self._cfg)
            try:
                homography, inlier_mask = _estimate_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    self._cfg,
                )
            except StitchingFailure:
                homography, inlier_mask = _estimate_affine_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    self._cfg,
                )
            self._plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, self._cfg)
            self._left_shape = left.shape[:2]
            self._right_shape = right.shape[:2]
            self._matches_last = int(len(matches))
            self._inliers_last = int(inlier_mask.ravel().sum()) if inlier_mask is not None else 0
            self._last_status = "calibrated"
        except Exception as exc:
            self._plan = None
            self._last_status = f"calibrating: {exc}"

    def stitch(self, left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray | None, str]:
        self._frame_index += 1
        if self._need_recalibration(left, right):
            self._plan = None
            self._try_calibrate(left, right)
            if self._plan is None:
                if self._last_stitched is not None:
                    return self._last_stitched, self._last_status
                return None, self._last_status

        assert self._plan is not None
        if self._last_stitched is not None and self._frame_index % self._stitch_every_n != 0:
            self._reused_count += 1
            return self._last_stitched, "stitching (reused)"

        try:
            right_warped = cv2.warpPerspective(
                right,
                self._plan.homography_adjusted,
                (self._plan.width, self._plan.height),
            )
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
        }


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
        self._status = "waiting for both streams"
        self._metrics: dict[str, float | int | str] = {
            "status": "waiting for both streams",
            "frame_index": 0,
            "matches": 0,
            "inliers": 0,
            "stitched_count": 0,
            "reused_count": 0,
            "stitch_fps": 0.0,
            "worker_fps": 0.0,
        }
        self._worker_timestamps: deque[float] = deque(maxlen=240)

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

    def snapshot(self) -> tuple[np.ndarray | None, str, dict[str, float | int | str]]:
        with self._lock:
            frame = self._latest_stitched.copy() if self._latest_stitched is not None else None
            return frame, self._status, dict(self._metrics)

    def _set_snapshot(self, frame: np.ndarray | None, status: str, metrics: dict[str, float | int | str]) -> None:
        with self._lock:
            if frame is not None:
                self._latest_stitched = frame
            self._status = status
            self._metrics = dict(metrics)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            left_frame = self._left.latest_frame() if self._left is not None else None
            right_frame = self._right.latest_frame() if self._right is not None else None

            if left_frame is None or right_frame is None:
                metrics = self._stitcher.metrics_snapshot()
                metrics["status"] = "waiting for both streams"
                self._set_snapshot(None, "waiting for both streams", metrics)
                time.sleep(0.01)
                continue

            lf = _resize_frame(left_frame, float(self._cfg.process_scale))
            rf = _resize_frame(right_frame, float(self._cfg.process_scale))
            if lf.shape[:2] != rf.shape[:2]:
                rf = cv2.resize(rf, (lf.shape[1], lf.shape[0]), interpolation=cv2.INTER_LINEAR)

            stitched, status = self._stitcher.stitch(lf, rf)
            if stitched is not None:
                stitched = _resize_frame(stitched, max(0.1, float(self._cfg.stitch_output_scale)))

            self._worker_timestamps.append(time.perf_counter())
            metrics = self._stitcher.metrics_snapshot()
            if len(self._worker_timestamps) >= 2:
                dt = self._worker_timestamps[-1] - self._worker_timestamps[0]
                metrics["worker_fps"] = float((len(self._worker_timestamps) - 1) / dt) if dt > 1e-6 else 0.0
            else:
                metrics["worker_fps"] = 0.0
            metrics["status"] = status
            self._set_snapshot(stitched, status, metrics)


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


def _resize_frame(frame: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return frame
    h, w = frame.shape[:2]
    nw = max(2, int(round(w * scale)))
    nh = max(2, int(round(h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(frame, (nw, nh), interpolation=interpolation)


def _fit_to_panel(frame: np.ndarray | None, panel_w: int, panel_h: int, text: str) -> np.ndarray:
    panel = _placeholder(panel_w, panel_h, text)
    if frame is None:
        return panel

    fh, fw = frame.shape[:2]
    if fh <= 0 or fw <= 0:
        return panel

    scale = min(panel_w / float(fw), panel_h / float(fh))
    nw = max(2, int(round(fw * scale)))
    nh = max(2, int(round(fh * scale)))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)

    y = (panel_h - nh) // 2
    x = (panel_w - nw) // 2
    panel[y : y + nh, x : x + nw] = resized
    return panel


def _build_log_panel(width: int, lines: list[str], height: int = 170) -> np.ndarray:
    panel = np.zeros((height, width, 3), dtype=np.uint8)
    panel[:] = (12, 12, 12)
    y = 24
    for line in lines:
        cv2.putText(panel, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (210, 210, 210), 1, cv2.LINE_AA)
        y += 22
        if y > height - 10:
            break
    return panel


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

    cv2.namedWindow("RTSP Desktop Stitch Preview", cv2.WINDOW_NORMAL)
    display_timestamps: deque[float] = deque(maxlen=240)

    try:
        while True:
            left_frame = left.latest_frame() if left is not None else None
            right_frame = right.latest_frame() if right is not None else None
            stitched, stitch_status, metrics = stitch_worker.snapshot()

            reference = left_frame if left_frame is not None else right_frame
            if reference is not None:
                rh, rw = reference.shape[:2]
                ref_aspect = float(rw) / float(max(1, rh))
            else:
                ref_aspect = 16.0 / 9.0

            panel_w = max(320, int(config.max_display_width // 3))
            panel_h = max(180, int(panel_w / max(0.1, ref_aspect)))

            top_row = np.hstack(
                [
                    _fit_to_panel(left_frame, panel_w, panel_h, "Left stream (raw)"),
                    _fit_to_panel(right_frame, panel_w, panel_h, "Right stream (raw)"),
                    _fit_to_panel(stitched, panel_w, panel_h, f"Stitched: {stitch_status}"),
                ]
            )

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
                f"left_frames={int(left_stats['frames_total'])}  right_frames={int(right_stats['frames_total'])}",
                f"left_err={left_stats['last_error'] or '-'}",
                f"right_err={right_stats['last_error'] or '-'}",
            ]
            log_panel = _build_log_panel(top_row.shape[1], lines)

            canvas = np.vstack([top_row, log_panel])
            canvas = _fit_width(canvas, max_width=max(960, int(config.max_display_width)))

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
            cv2.imshow("RTSP Desktop Stitch Preview", canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        stitch_worker.stop()
        if left is not None:
            left.stop()
        if right is not None:
            right.stop()
        cv2.destroyAllWindows()

    return 0
