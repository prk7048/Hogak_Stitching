from __future__ import annotations

from collections import deque
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from stitching.core import (
    StitchConfig,
    StitchingFailure,
    _apply_gain_bias,
    _blend_feather,
    _blend_seam_path,
    _compensate_exposure,
    _compute_overlap_diff_mean,
    _compute_seam_cost_map,
    _detect_and_match,
    _estimate_affine_homography,
    _estimate_homography,
    _find_seam_path,
    _prepare_warp_plan,
)
from stitching.errors import ErrorCode
from stitching.reporting import (
    StageTimer,
    base_report,
    finalize_total_time,
    mark_failed,
    mark_succeeded,
    write_report,
)


@dataclass(slots=True)
class LiveConfig(StitchConfig):
    """Configuration for live RTSP stitching."""

    # Output controls
    max_duration_sec: float = 0.0
    output_fps: float = 20.0
    process_scale: float = 1.0

    # Initial calibration retries
    calib_max_attempts: int = 180
    calib_retry_sleep_sec: float = 0.02

    # Stream recovery
    max_read_failures: int = 45
    reconnect_cooldown_sec: float = 1.0
    rtsp_transport: str = "tcp"
    rtsp_timeout_sec: float = 10.0

    # Stitch quality controls
    video_ghost_diff_threshold: float = 8.0
    seam_motion_weight: float = 1.5
    seam_temporal_penalty: float = 1.5
    seam_update_interval: int = 12
    adaptive_seam: bool = False

    # Preview
    preview: bool = False

    # Software time-sync controls
    sync_buffer_sec: float = 2.0
    sync_match_max_delta_ms: float = 80.0
    # Positive value means right stream is delayed vs left.
    sync_manual_offset_ms: float = 0.0
    sync_no_pair_timeout_sec: float = 8.0
    # Pairing policy: latest is better for near-live catch-up.
    sync_pair_mode: str = "latest"
    # If output timeline lag exceeds this, skip middle and keep near-live.
    max_live_lag_sec: float = 1.0


@dataclass(slots=True)
class TimedFrame:
    ts: float
    frame: np.ndarray


class RtspBufferedReader:
    """Continuously read one RTSP stream into a bounded timestamped buffer."""

    def __init__(self, *, name: str, url: str, config: LiveConfig) -> None:
        self.name = name
        self.url = url
        self.config = config

        base_fps = float(config.output_fps) if config.output_fps > 0 else 20.0
        maxlen = max(10, int(math.ceil(base_fps * max(0.5, float(config.sync_buffer_sec)) * 3.0)))
        self._buffer: deque[TimedFrame] = deque(maxlen=maxlen)

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cap: cv2.VideoCapture | None = None

        self.frames_read = 0
        self.read_failures = 0
        self.reconnect_count = 0
        self.open_failures = 0
        self.buffer_overflow_drops = 0
        self.stale_drops = 0
        self.last_error = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=f"rtsp-reader-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._release_capture()

    def has_frames(self) -> bool:
        with self._lock:
            return bool(self._buffer)

    def pop_oldest(self) -> TimedFrame | None:
        with self._lock:
            if not self._buffer:
                return None
            return self._buffer.popleft()

    def pop_latest(self) -> TimedFrame | None:
        with self._lock:
            if not self._buffer:
                return None
            latest = self._buffer[-1]
            self.stale_drops += max(0, len(self._buffer) - 1)
            self._buffer.clear()
            return latest

    def keep_latest_only(self) -> None:
        with self._lock:
            if len(self._buffer) <= 1:
                return
            latest = self._buffer[-1]
            dropped = len(self._buffer) - 1
            self._buffer.clear()
            self._buffer.append(latest)
            self.stale_drops += dropped

    def pop_closest(self, *, target_ts: float, max_delta_sec: float) -> tuple[TimedFrame | None, float | None]:
        """
        Pop frame closest to target timestamp.
        Returns (frame, residual_delta_sec) where residual is chosen_ts - target_ts.
        """
        with self._lock:
            if not self._buffer:
                return None, None

            # Drop frames that are definitely too old.
            while len(self._buffer) >= 2 and self._buffer[1].ts <= (target_ts - max_delta_sec):
                self._buffer.popleft()
                self.stale_drops += 1

            best_idx = -1
            best_abs_delta = float("inf")
            for idx, packet in enumerate(self._buffer):
                abs_delta = abs(packet.ts - target_ts)
                if abs_delta < best_abs_delta:
                    best_abs_delta = abs_delta
                    best_idx = idx
                if packet.ts > target_ts and abs_delta > best_abs_delta:
                    break

            if best_idx < 0 or best_abs_delta > max_delta_sec:
                return None, None

            chosen = self._buffer[best_idx]
            for _ in range(best_idx + 1):
                self._buffer.popleft()
            return chosen, (chosen.ts - target_ts)

    def snapshot_metrics(self) -> dict[str, int | str]:
        return {
            "frames_read": int(self.frames_read),
            "read_failures": int(self.read_failures),
            "reconnect_count": int(self.reconnect_count),
            "open_failures": int(self.open_failures),
            "buffer_overflow_drops": int(self.buffer_overflow_drops),
            "stale_drops": int(self.stale_drops),
            "last_error": self.last_error,
        }

    def _release_capture(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _ensure_opened(self) -> bool:
        if self._cap is not None and self._cap.isOpened():
            return True
        try:
            self._cap = _open_rtsp(self.url, self.config)
            return True
        except StitchingFailure as exc:
            self.open_failures += 1
            self.last_error = str(exc.detail)
            self._cap = None
            return False

    def _run(self) -> None:
        fail_streak = 0
        while not self._stop_event.is_set():
            if not self._ensure_opened():
                time.sleep(self.config.reconnect_cooldown_sec)
                continue

            assert self._cap is not None
            ok, frame = self._cap.read()
            recv_ts = time.perf_counter()
            if not ok or frame is None:
                self.read_failures += 1
                fail_streak += 1
                if fail_streak >= int(self.config.max_read_failures):
                    self.reconnect_count += 1
                    fail_streak = 0
                    self._release_capture()
                    time.sleep(self.config.reconnect_cooldown_sec)
                else:
                    time.sleep(0.001)
                continue

            fail_streak = 0
            self.frames_read += 1
            with self._lock:
                if len(self._buffer) == self._buffer.maxlen:
                    self.buffer_overflow_drops += 1
                self._buffer.append(TimedFrame(ts=recv_ts, frame=frame))


def _open_rtsp(url: str, config: LiveConfig) -> cv2.VideoCapture:
    """Open RTSP stream with explicit FFMPEG transport/timeout options."""
    transport = str(config.rtsp_transport).lower().strip()
    if transport not in {"tcp", "udp"}:
        transport = "tcp"
    timeout_us = max(1, int(float(config.rtsp_timeout_sec) * 1_000_000))
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}|stimeout;{timeout_us}"

    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise StitchingFailure(ErrorCode.PROBE_FAIL, f"cannot open stream: {url}")
    return cap


def _resize_frame(frame: np.ndarray, scale: float) -> np.ndarray:
    if scale == 1.0:
        return frame
    h, w = frame.shape[:2]
    new_w = max(2, int(round(w * scale)))
    new_h = max(2, int(round(h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(frame, (new_w, new_h), interpolation=interpolation)


def _resize_to_match(frame: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    target_h, target_w = target_shape
    if frame.shape[:2] == (target_h, target_w):
        return frame
    return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)


def _read_synced_pair(
    *,
    left_reader: RtspBufferedReader,
    right_reader: RtspBufferedReader,
    config: LiveConfig,
) -> tuple[bool, np.ndarray | None, np.ndarray | None, float | None]:
    pair_mode = str(config.sync_pair_mode).lower().strip()
    if pair_mode == "oldest":
        left_packet = left_reader.pop_oldest()
    else:
        left_packet = left_reader.pop_latest()
    if left_packet is None:
        return False, None, None, None

    target_right_ts = left_packet.ts + (float(config.sync_manual_offset_ms) / 1000.0)
    max_delta_sec = max(0.001, float(config.sync_match_max_delta_ms) / 1000.0)
    right_packet, residual_delta_sec = right_reader.pop_closest(
        target_ts=target_right_ts,
        max_delta_sec=max_delta_sec,
    )
    if right_packet is None:
        return False, None, None, None

    left = _resize_frame(left_packet.frame, config.process_scale)
    right = _resize_frame(right_packet.frame, config.process_scale)
    right = _resize_to_match(right, left.shape[:2])
    residual_delta_ms = float(residual_delta_sec * 1000.0) if residual_delta_sec is not None else None
    return True, left, right, residual_delta_ms


def _estimate_h_for_live(
    left_reader: RtspBufferedReader,
    right_reader: RtspBufferedReader,
    config: LiveConfig,
) -> tuple[dict, list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch], np.ndarray, float | None]:
    """Try multiple synced frame pairs until homography estimation succeeds."""
    last_failure: StitchingFailure | None = None
    for _ in range(max(1, int(config.calib_max_attempts))):
        ok, left, right, residual_delta_ms = _read_synced_pair(
            left_reader=left_reader,
            right_reader=right_reader,
            config=config,
        )
        if not ok or left is None or right is None:
            time.sleep(config.calib_retry_sleep_sec)
            continue
        try:
            keypoints_left, keypoints_right, matches = _detect_and_match(left, right, config)
            try:
                homography, inlier_mask = _estimate_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    config,
                )
                used_fallback = False
            except StitchingFailure as exc:
                if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
                    raise
                homography, inlier_mask = _estimate_affine_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    config,
                )
                used_fallback = True

            plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
            return (
                {
                    "frame_left": left,
                    "frame_right": right,
                    "plan": plan,
                    "inliers_count": int(inlier_mask.ravel().sum()),
                    "matches_count": len(matches),
                    "used_fallback": used_fallback,
                    "calib_sync_delta_ms": residual_delta_ms,
                },
                keypoints_left,
                keypoints_right,
                matches,
                inlier_mask,
                residual_delta_ms,
            )
        except StitchingFailure as exc:
            last_failure = exc
            time.sleep(config.calib_retry_sleep_sec)

    if last_failure is not None:
        raise last_failure
    raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "failed to calibrate homography from live stream")


def stitch_live_rtsp(
    left_rtsp: str,
    right_rtsp: str,
    output_path: Path,
    report_path: Path,
    debug_dir: Path,
    config: LiveConfig | None = None,
    frame_hook: Callable[[np.ndarray, np.ndarray, np.ndarray], None] | None = None,
    status_hook: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    config = config or LiveConfig()
    started_at = time.perf_counter()

    report = base_report(
        pipeline="live_rtsp",
        inputs={"left_rtsp": left_rtsp, "right_rtsp": right_rtsp},
        job_id=None,
    )
    stage_times: dict[str, float] = {}
    report["metrics"]["processing_time_sec"] = stage_times
    report["metrics"]["mode"] = "live_rtsp"
    report["metrics"]["process_scale"] = float(config.process_scale)
    report["metrics"]["output_fps"] = float(config.output_fps)
    report["metrics"]["adaptive_seam_enabled"] = bool(config.adaptive_seam)
    report["metrics"]["seam_update_interval"] = int(max(1, config.seam_update_interval))
    report["metrics"]["seam_motion_weight"] = float(max(0.0, config.seam_motion_weight))
    report["metrics"]["rtsp_transport"] = str(config.rtsp_transport).lower().strip()
    report["metrics"]["rtsp_timeout_sec"] = float(config.rtsp_timeout_sec)
    report["metrics"]["sync_buffer_sec"] = float(config.sync_buffer_sec)
    report["metrics"]["sync_match_max_delta_ms"] = float(config.sync_match_max_delta_ms)
    report["metrics"]["sync_manual_offset_ms"] = float(config.sync_manual_offset_ms)
    report["metrics"]["sync_pair_mode"] = str(config.sync_pair_mode)
    report["metrics"]["max_live_lag_sec"] = float(config.max_live_lag_sec)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)

    writer: cv2.VideoWriter | None = None
    left_reader: RtspBufferedReader | None = None
    right_reader: RtspBufferedReader | None = None

    dropped_pairs = 0
    unmatched_pairs = 0
    processed = 0
    written_frames = 0
    catchup_frames = 0
    hold_frames = 0
    seam_updates = 0
    sync_skews_ms: list[float] = []

    try:
        with StageTimer(stage_times, "probe"):
            if status_hook is not None:
                status_hook("probe")
            left_reader = RtspBufferedReader(name="left", url=left_rtsp, config=config)
            right_reader = RtspBufferedReader(name="right", url=right_rtsp, config=config)
            left_reader.start()
            right_reader.start()

            wait_deadline = time.perf_counter() + max(2.0, float(config.rtsp_timeout_sec))
            while time.perf_counter() < wait_deadline:
                if left_reader.has_frames() and right_reader.has_frames():
                    break
                time.sleep(0.01)
            if not left_reader.has_frames() or not right_reader.has_frames():
                raise StitchingFailure(
                    ErrorCode.PROBE_FAIL,
                    "timeout waiting initial frames from both RTSP streams",
                )

        with StageTimer(stage_times, "homography"):
            if status_hook is not None:
                status_hook("homography")
            calib, keypoints_left, keypoints_right, matches, inlier_mask, calib_delta_ms = _estimate_h_for_live(
                left_reader,
                right_reader,
                config,
            )
            frame_left = calib["frame_left"]
            frame_right = calib["frame_right"]
            plan = calib["plan"]
            if calib_delta_ms is not None:
                sync_skews_ms.append(float(calib_delta_ms))
            report["metrics"]["matches_count"] = int(calib["matches_count"])
            report["metrics"]["inliers_count"] = int(calib["inliers_count"])
            if calib["used_fallback"]:
                report["warnings"].append("homography_unstable_fallback_affine")

            debug_matches = cv2.drawMatches(
                frame_left,
                keypoints_left,
                frame_right,
                keypoints_right,
                matches[:200],
                None,
                matchesMask=[int(v) for v in inlier_mask.ravel().tolist()[:200]],
                flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
            )
            cv2.imwrite(str(debug_dir / "live_inliers.jpg"), debug_matches)

        warped_right_probe = cv2.warpPerspective(
            frame_right,
            plan.homography_adjusted,
            (plan.width, plan.height),
        )
        right_mask_probe = cv2.warpPerspective(
            np.ones(frame_right.shape[:2], dtype=np.uint8) * 255,
            plan.homography_adjusted,
            (plan.width, plan.height),
        )
        canvas_left_probe = np.zeros((plan.height, plan.width, 3), dtype=np.uint8)
        left_mask_probe = np.zeros((plan.height, plan.width), dtype=np.uint8)
        lh_probe, lw_probe = frame_left.shape[:2]
        canvas_left_probe[plan.ty : plan.ty + lh_probe, plan.tx : plan.tx + lw_probe] = frame_left
        left_mask_probe[plan.ty : plan.ty + lh_probe, plan.tx : plan.tx + lw_probe] = 255
        overlap_probe = (left_mask_probe > 0) & (right_mask_probe > 0)

        exposure_gain = 1.0
        exposure_bias = 0.0
        if config.exposure_compensation:
            warped_right_probe, exposure_gain, exposure_bias = _compensate_exposure(
                canvas_left=canvas_left_probe,
                warped_right=warped_right_probe,
                overlap=overlap_probe,
                right_mask=right_mask_probe,
                config=config,
            )

        overlap_diff = _compute_overlap_diff_mean(canvas_left_probe, warped_right_probe, overlap_probe)
        use_seam_cut = overlap_diff >= config.video_ghost_diff_threshold or bool(calib["used_fallback"])
        seam_path: np.ndarray | None = None
        if use_seam_cut:
            cost_map_probe = _compute_seam_cost_map(
                canvas_left=canvas_left_probe,
                warped_right=warped_right_probe,
                overlap=overlap_probe,
            )
            seam_path = _find_seam_path(
                overlap=overlap_probe,
                cost_map=cost_map_probe,
                smoothness_penalty=config.seam_smoothness_penalty,
            )
            report["metrics"]["blend_mode"] = "seam_cut"
            report["metrics"]["seam_x"] = int(np.median(seam_path))
        else:
            report["metrics"]["blend_mode"] = "feather"
            report["metrics"]["seam_x"] = None

        report["metrics"]["overlap_diff_mean"] = round(float(overlap_diff), 3)
        report["metrics"]["exposure_gain"] = round(float(exposure_gain), 4)
        report["metrics"]["exposure_bias"] = round(float(exposure_bias), 4)

        out_fps = float(config.output_fps) if config.output_fps > 0 else 20.0
        target_output_frames: int | None = None
        if config.max_duration_sec > 0:
            target_output_frames = max(1, int(math.ceil(float(config.max_duration_sec) * out_fps)))
        report["metrics"]["target_output_frames"] = target_output_frames

        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            out_fps,
            (plan.width, plan.height),
        )
        if not writer.isOpened():
            raise StitchingFailure(ErrorCode.ENCODE_FAIL, f"cannot open encoder: {output_path}")

        loop_started_at = time.perf_counter()
        prev_canvas_left: np.ndarray | None = None
        prev_warped_right: np.ndarray | None = None
        last_stitched: np.ndarray | None = None
        black_frame = np.zeros((plan.height, plan.width, 3), dtype=np.uint8)
        seam_update_interval = max(1, int(config.seam_update_interval))
        temporal_penalty = max(0.0, float(config.seam_temporal_penalty))
        motion_weight = max(0.0, float(config.seam_motion_weight))
        stop_reason = "running"

        assert left_reader is not None
        assert right_reader is not None
        last_pair_at = time.perf_counter()

        with StageTimer(stage_times, "frame_loop"):
            if status_hook is not None:
                status_hook("stitching")
            while True:
                if should_stop is not None and should_stop():
                    stop_reason = "user_stopped"
                    break
                if target_output_frames is not None and written_frames >= target_output_frames:
                    stop_reason = "target_output_duration_reached"
                    break

                now = time.perf_counter()
                if target_output_frames is not None:
                    expected_slot = int((now - loop_started_at) * out_fps)
                    expected_slot = max(0, min(expected_slot, target_output_frames - 1))
                    if expected_slot > written_frames:
                        missing = expected_slot - written_frames
                        fill_frame = last_stitched if last_stitched is not None else black_frame
                        for _ in range(missing):
                            writer.write(fill_frame)
                            written_frames += 1
                            catchup_frames += 1
                        left_reader.keep_latest_only()
                        right_reader.keep_latest_only()
                        continue
                else:
                    # In endless mode, keep nominal output pacing.
                    due = loop_started_at + (written_frames / max(1e-6, out_fps))
                    if now < due:
                        time.sleep(min(0.005, due - now))
                        continue

                ok_pair, left, right, residual_delta_ms = _read_synced_pair(
                    left_reader=left_reader,
                    right_reader=right_reader,
                    config=config,
                )
                if not ok_pair or left is None or right is None:
                    dropped_pairs += 1
                    unmatched_pairs += 1
                    if (time.perf_counter() - last_pair_at) >= max(1.0, float(config.sync_no_pair_timeout_sec)):
                        raise StitchingFailure(
                            ErrorCode.PROBE_FAIL,
                            "sync timeout: no matched frame pairs in configured window",
                        )
                    out_frame = last_stitched if last_stitched is not None else black_frame
                    writer.write(out_frame)
                    written_frames += 1
                    hold_frames += 1
                    if target_output_frames is None:
                        time.sleep(0.001)
                    continue

                if residual_delta_ms is not None:
                    sync_skews_ms.append(float(residual_delta_ms))
                last_pair_at = time.perf_counter()

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
                lh, lw = left.shape[:2]
                canvas_left[plan.ty : plan.ty + lh, plan.tx : plan.tx + lw] = left
                left_mask[plan.ty : plan.ty + lh, plan.tx : plan.tx + lw] = 255

                if config.exposure_compensation:
                    warped_right = _apply_gain_bias(
                        warped_right,
                        gain=exposure_gain,
                        bias=exposure_bias,
                        mask=right_mask,
                    )

                if use_seam_cut and seam_path is not None:
                    if (
                        config.adaptive_seam
                        and processed > 0
                        and (processed % seam_update_interval == 0)
                    ):
                        overlap = (left_mask > 0) & (right_mask > 0)
                        cost_map = _compute_seam_cost_map(
                            canvas_left=canvas_left,
                            warped_right=warped_right,
                            overlap=overlap,
                            prev_canvas_left=prev_canvas_left,
                            prev_warped_right=prev_warped_right,
                            motion_weight=motion_weight,
                        )
                        seam_path = _find_seam_path(
                            overlap=overlap,
                            cost_map=cost_map,
                            smoothness_penalty=config.seam_smoothness_penalty,
                            prev_seam_path=seam_path,
                            temporal_penalty=temporal_penalty,
                        )
                        seam_updates += 1
                    stitched = _blend_seam_path(
                        canvas_left=canvas_left,
                        warped_right=warped_right,
                        left_mask=left_mask,
                        right_mask=right_mask,
                        seam_path=seam_path,
                        transition_px=config.seam_transition_px,
                    )
                else:
                    stitched = _blend_feather(canvas_left, warped_right, left_mask, right_mask)

                writer.write(stitched)
                processed += 1
                written_frames += 1
                last_stitched = stitched
                prev_canvas_left = canvas_left
                prev_warped_right = warped_right
                if frame_hook is not None:
                    frame_hook(left, right, stitched)

                # If we are too late versus output timeline, drop middle and keep near-live.
                if out_fps > 0:
                    elapsed_slots = (time.perf_counter() - loop_started_at) * out_fps
                    lag_slots = max(0.0, elapsed_slots - float(written_frames))
                    lag_sec = lag_slots / out_fps
                    if lag_sec > max(0.0, float(config.max_live_lag_sec)):
                        left_reader.keep_latest_only()
                        right_reader.keep_latest_only()

                if config.preview:
                    cv2.imshow("live_stitched", stitched)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        stop_reason = "preview_quit"
                        break

        if processed <= 0:
            raise StitchingFailure(ErrorCode.ENCODE_FAIL, "no output frames produced")

        mark_succeeded(report)
        report["metrics"]["processed_frames"] = int(written_frames)
        report["metrics"]["stitched_frames"] = int(processed)
        report["metrics"]["catchup_frames"] = int(catchup_frames)
        report["metrics"]["hold_frames"] = int(hold_frames)
        report["metrics"]["dropped_pairs"] = int(dropped_pairs)
        report["metrics"]["unmatched_pairs"] = int(unmatched_pairs)

        left_stats = left_reader.snapshot_metrics()
        right_stats = right_reader.snapshot_metrics()
        report["metrics"]["reconnect_left_count"] = int(left_stats["reconnect_count"])
        report["metrics"]["reconnect_right_count"] = int(right_stats["reconnect_count"])
        report["metrics"]["left_read_failures"] = int(left_stats["read_failures"])
        report["metrics"]["right_read_failures"] = int(right_stats["read_failures"])
        report["metrics"]["left_buffer_overflow_drops"] = int(left_stats["buffer_overflow_drops"])
        report["metrics"]["right_buffer_overflow_drops"] = int(right_stats["buffer_overflow_drops"])
        report["metrics"]["left_stale_drops"] = int(left_stats["stale_drops"])
        report["metrics"]["right_stale_drops"] = int(right_stats["stale_drops"])

        if sync_skews_ms:
            arr = np.asarray(sync_skews_ms, dtype=np.float32)
            report["metrics"]["pair_skew_ms_mean"] = round(float(np.mean(arr)), 3)
            report["metrics"]["pair_skew_ms_abs_p95"] = round(float(np.percentile(np.abs(arr), 95)), 3)
            report["metrics"]["pair_skew_ms_abs_max"] = round(float(np.max(np.abs(arr))), 3)
        else:
            report["metrics"]["pair_skew_ms_mean"] = None
            report["metrics"]["pair_skew_ms_abs_p95"] = None
            report["metrics"]["pair_skew_ms_abs_max"] = None

        report["metrics"]["seam_updates"] = int(seam_updates)
        report["metrics"]["output_resolution"] = [int(plan.width), int(plan.height)]
        elapsed = max(1e-6, time.perf_counter() - loop_started_at)
        report["metrics"]["observed_output_fps"] = round(float(written_frames / elapsed), 3)
        report["metrics"]["observed_stitch_fps"] = round(float(processed / elapsed), 3)
        report["metrics"]["output_duration_sec"] = round(float(written_frames / out_fps), 3)
        report["metrics"]["stop_reason"] = stop_reason

    except StitchingFailure as exc:
        mark_failed(report, exc.code, exc.detail)
        if status_hook is not None:
            status_hook("failed")
    except Exception as exc:  # pragma: no cover - runtime guard
        mark_failed(report, ErrorCode.INTERNAL_ERROR, f"unexpected error: {exc}")
        if status_hook is not None:
            status_hook("failed")
    finally:
        if left_reader is not None:
            left_reader.stop()
        if right_reader is not None:
            right_reader.stop()
        if writer is not None:
            writer.release()
        if config.preview:
            cv2.destroyAllWindows()
        finalize_total_time(report, started_at)
        write_report(report_path, report)
        if status_hook is not None and report.get("status") == "succeeded":
            status_hook("succeeded")

    return report
