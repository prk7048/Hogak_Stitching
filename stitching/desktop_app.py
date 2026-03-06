from __future__ import annotations

from collections import deque
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace

import cv2
import numpy as np

from stitching.core import (
    _apply_gain_bias,
    StitchConfig,
    StitchingFailure,
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
from stitching.ffmpeg_reader import FfmpegRtspReader
from stitching.ffmpeg_runtime import FfmpegRuntimeError


@dataclass(slots=True)
class DesktopConfig:
    left_rtsp: str
    right_rtsp: str
    input_runtime: str = "opencv"
    rtsp_transport: str = "tcp"
    rtsp_timeout_sec: float = 10.0
    reconnect_cooldown_sec: float = 1.0
    sync_buffer_sec: float = 0.6
    sync_match_max_delta_ms: float = 35.0
    sync_manual_offset_ms: float = 0.0
    sync_pair_mode: str = "none"
    max_display_width: int = 2880
    process_scale: float = 1.0
    min_matches: int = 20
    min_inliers: int = 8
    ratio_test: float = 0.82
    ransac_thresh: float = 6.0
    stitch_every_n: int = 1
    max_features: int = 2800
    stitch_output_scale: float = 1.0
    gpu_mode: str = "on"  # off | auto | on
    gpu_device: int = 0
    cpu_threads: int = 0
    manual_points: int = 4
    headless_benchmark: bool = False
    benchmark_log_interval_sec: float = 1.0
    benchmark_duration_sec: float = 0.0


@dataclass(slots=True)
class TimedFrame:
    ts: float
    frame: np.ndarray


class RtspReader:
    def __init__(self, *, name: str, url: str, config: DesktopConfig) -> None:
        self.name = name
        self.url = url
        self.config = config

        base_fps = 30.0
        maxlen = max(10, int(round(base_fps * max(0.3, float(config.sync_buffer_sec)) * 2.0)))
        self._buffer: deque[TimedFrame] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_packet: TimedFrame | None = None
        self._cap: cv2.VideoCapture | None = None
        self._last_error = ""
        self._frames_total = 0
        self._buffer_overflow_drops = 0
        self._stale_drops = 0

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
            return None if self._frame_packet is None else self._frame_packet.frame

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
            dropped = max(0, len(self._buffer) - 1)
            self._stale_drops += dropped
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
            self._stale_drops += dropped

    def pop_closest(self, *, target_ts: float, max_delta_sec: float) -> tuple[TimedFrame | None, float | None]:
        with self._lock:
            if not self._buffer:
                return None, None

            while len(self._buffer) >= 2 and self._buffer[1].ts <= (target_ts - max_delta_sec):
                self._buffer.popleft()
                self._stale_drops += 1

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

    def snapshot_stats(self) -> dict[str, int | str]:
        with self._lock:
            return {
                "frames_total": int(self._frames_total),
                "last_error": self._last_error,
                "buffer_size": int(len(self._buffer)),
                "buffer_overflow_drops": int(self._buffer_overflow_drops),
                "stale_drops": int(self._stale_drops),
                "runtime": "opencv",
            }

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def _set_frame(self, frame: np.ndarray, recv_ts: float) -> None:
        with self._lock:
            packet = TimedFrame(ts=float(recv_ts), frame=frame)
            if len(self._buffer) == self._buffer.maxlen:
                self._buffer_overflow_drops += 1
            self._buffer.append(packet)
            self._frame_packet = packet
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
                self._set_frame(frame, time.perf_counter())

            self._release()
            if not self._stop_event.is_set():
                time.sleep(cooldown)


def _build_rtsp_reader(*, name: str, url: str, config: DesktopConfig) -> RtspReader | FfmpegRtspReader:
    runtime = str(config.input_runtime).lower().strip()
    if runtime in {"ffmpeg", "ffmpeg-cpu", "ffmpeg-cuda"}:
        ffmpeg_runtime = "ffmpeg-cuda" if runtime == "ffmpeg-cuda" else "ffmpeg-cpu"
        return FfmpegRtspReader(
            name=name,
            url=url,
            transport=config.rtsp_transport,
            timeout_sec=float(config.rtsp_timeout_sec),
            reconnect_cooldown_sec=float(config.reconnect_cooldown_sec),
            sync_buffer_sec=float(config.sync_buffer_sec),
            runtime=ffmpeg_runtime,
        )
    return RtspReader(name=name, url=url, config=config)


def _resize_frame(frame: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return frame
    h, w = frame.shape[:2]
    nw = max(2, int(round(w * scale)))
    nh = max(2, int(round(h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(frame, (nw, nh), interpolation=interpolation)


def _resize_to_match(frame: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    target_h, target_w = target_shape
    if frame.shape[:2] == (target_h, target_w):
        return frame
    return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)


def _read_synced_pair(
    *,
    left_reader: RtspReader,
    right_reader: RtspReader,
    config: DesktopConfig,
) -> tuple[bool, np.ndarray | None, np.ndarray | None, float | None]:
    pair_mode = str(config.sync_pair_mode).lower().strip()
    if pair_mode == "none":
        left_packet = left_reader.pop_latest()
        right_packet = right_reader.pop_latest()
        if left_packet is None or right_packet is None:
            return False, None, None, None
        left = _resize_frame(left_packet.frame, float(config.process_scale))
        right = _resize_frame(right_packet.frame, float(config.process_scale))
        right = _resize_to_match(right, left.shape[:2])
        return True, left, right, 0.0
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

    left = _resize_frame(left_packet.frame, float(config.process_scale))
    right = _resize_frame(right_packet.frame, float(config.process_scale))
    right = _resize_to_match(right, left.shape[:2])
    residual_delta_ms = float(residual_delta_sec * 1000.0) if residual_delta_sec is not None else None
    return True, left, right, residual_delta_ms


class _CounterFpsMeter:
    def __init__(self, window_sec: float = 1.5) -> None:
        self._window_sec = max(0.5, float(window_sec))
        self._samples: deque[tuple[float, int]] = deque(maxlen=256)
        self._fps = 0.0

    def update(self, count: int) -> float:
        now = time.perf_counter()
        self._samples.append((now, max(0, int(count))))
        while len(self._samples) >= 2 and now - self._samples[0][0] > self._window_sec:
            self._samples.popleft()
        if len(self._samples) < 2:
            return self._fps
        t0, c0 = self._samples[0]
        t1, c1 = self._samples[-1]
        dt = t1 - t0
        dc = c1 - c0
        if dt <= 1e-6 or dc < 0:
            return self._fps
        current = float(dc) / float(dt)
        self._fps = current if self._fps <= 1e-6 else (self._fps * 0.7 + current * 0.3)
        return self._fps


class SystemStatsSampler:
    def __init__(self, interval_sec: float = 1.0) -> None:
        self._interval_sec = max(0.2, float(interval_sec))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cpu_percent = 0.0
        self._cpu_per_core: list[float] = []
        self._gpu_percent = -1.0
        self._gpu_mem_used = -1.0
        self._gpu_mem_total = -1.0
        self._gpu_temp_c = -1.0

        self._psutil = None
        try:
            import psutil  # type: ignore

            self._psutil = psutil
        except Exception:
            self._psutil = None
        self._nvidia_smi = self._resolve_nvidia_smi()
        self._powershell = self._resolve_powershell()

    @staticmethod
    def _resolve_nvidia_smi() -> str | None:
        found = shutil.which("nvidia-smi")
        if found:
            return found
        candidates = [
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
            r"C:\Windows\System32\nvidia-smi.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    @staticmethod
    def _resolve_powershell() -> str | None:
        found = shutil.which("powershell")
        if found:
            return found
        candidate = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        return candidate if os.path.exists(candidate) else None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="desktop-stats-sampler")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

    def snapshot(self) -> dict[str, float | list[float]]:
        with self._lock:
            return {
                "cpu_percent": float(self._cpu_percent),
                "cpu_per_core": list(self._cpu_per_core),
                "gpu_percent": float(self._gpu_percent),
                "gpu_mem_used": float(self._gpu_mem_used),
                "gpu_mem_total": float(self._gpu_mem_total),
                "gpu_temp_c": float(self._gpu_temp_c),
            }

    def _sample_cpu(self) -> tuple[float, list[float]]:
        if self._psutil is not None:
            try:
                per_core = [float(v) for v in self._psutil.cpu_percent(interval=None, percpu=True)]
                if per_core:
                    return float(sum(per_core) / len(per_core)), per_core
            except Exception:
                pass
        return self._sample_cpu_powershell()

    def _sample_cpu_powershell(self) -> tuple[float, list[float]]:
        if self._powershell is None:
            return -1.0, []
        try:
            out = subprocess.check_output(
                [
                    self._powershell,
                    "-NoProfile",
                    "-Command",
                    "Get-Counter '\\Processor(*)\\% Processor Time' | "
                    "Select-Object -ExpandProperty CounterSamples | "
                    "ForEach-Object { '{0},{1}' -f $_.InstanceName,$_.CookedValue }",
                ],
                timeout=2.5,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            total = -1.0
            per_core_pairs: list[tuple[int, float]] = []
            for raw in out.splitlines():
                line = raw.strip()
                if not line or "," not in line:
                    continue
                name, value = line.split(",", 1)
                try:
                    pct = float(value)
                except ValueError:
                    continue
                if name == "_Total":
                    total = pct
                elif name.isdigit():
                    per_core_pairs.append((int(name), pct))
            per_core_pairs.sort(key=lambda item: item[0])
            per_core = [pct for _, pct in per_core_pairs]
            if total < 0.0 and per_core:
                total = float(sum(per_core) / len(per_core))
            return total, per_core
        except Exception:
            return -1.0, []

    def _sample_gpu(self) -> tuple[float, float, float, float]:
        if self._nvidia_smi is None:
            return -1.0, -1.0, -1.0, -1.0
        try:
            out = subprocess.check_output(
                [
                    self._nvidia_smi,
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                timeout=1.5,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            line = out.strip().splitlines()[0]
            vals = [x.strip() for x in line.split(",")]
            if len(vals) < 4:
                return -1.0, -1.0, -1.0, -1.0
            return float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
        except Exception:
            return -1.0, -1.0, -1.0, -1.0

    def _run(self) -> None:
        # Prime psutil reading
        if self._psutil is not None:
            try:
                self._psutil.cpu_percent(interval=None, percpu=True)
            except Exception:
                pass
        while not self._stop_event.is_set():
            cpu, per_core = self._sample_cpu()
            gpu, gmem_used, gmem_total, gtemp = self._sample_gpu()
            with self._lock:
                self._cpu_percent = cpu
                self._cpu_per_core = per_core
                self._gpu_percent = gpu
                self._gpu_mem_used = gmem_used
                self._gpu_mem_total = gmem_total
                self._gpu_temp_c = gtemp
            time.sleep(self._interval_sec)


def _configure_runtime(config: DesktopConfig) -> None:
    cv2.setUseOptimized(True)
    thread_count = int(config.cpu_threads) if int(config.cpu_threads) > 0 else int(os.cpu_count() or 1)
    try:
        cv2.setNumThreads(max(1, thread_count))
    except Exception:
        pass
    os.environ.setdefault("OMP_NUM_THREADS", str(max(1, thread_count)))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(max(1, thread_count)))
    os.environ.setdefault("MKL_NUM_THREADS", str(max(1, thread_count)))


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
        self._left_mask_template: np.ndarray | None = None
        self._left_canvas_template: np.ndarray | None = None
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
        self._gpu_feature_enabled = False
        self._gpu_feature_reason = "gpu feature disabled"
        self._gpu_warp_count = 0
        self._gpu_match_count = 0
        self._gpu_blend_count = 0
        self._cpu_warp_count = 0
        self._cpu_match_count = 0
        self._cpu_blend_count = 0
        self._gpu_errors = 0
        self._gpu_feature_errors = 0
        self._gpu_frame: cv2.cuda_GpuMat | None = None
        self._gpu_left_frame: cv2.cuda_GpuMat | None = None
        self._gpu_left_canvas: cv2.cuda_GpuMat | None = None
        self._gpu_left_canvas_roi: cv2.cuda_GpuMat | None = None
        self._blend_overlap_bbox: tuple[int, int, int, int] | None = None
        self._blend_only_left_mask: np.ndarray | None = None
        self._blend_only_right_mask: np.ndarray | None = None
        self._blend_overlap_mask: np.ndarray | None = None
        self._blend_weight_left: np.ndarray | None = None
        self._blend_weight_right: np.ndarray | None = None
        self._blend_buffer: np.ndarray | None = None
        self._use_seam_cut = False
        self._seam_path: np.ndarray | None = None
        self._seam_update_interval = 10
        self._seam_temporal_penalty = 1.5
        self._seam_motion_weight = 1.5
        self._overlap_diff_mean = 0.0
        self._exposure_gain = 1.0
        self._exposure_bias = 0.0
        self._prev_canvas_left_for_seam: np.ndarray | None = None
        self._prev_warped_right_for_seam: np.ndarray | None = None
        self._gpu_mask_only_left: cv2.cuda_GpuMat | None = None
        self._gpu_mask_only_right: cv2.cuda_GpuMat | None = None
        self._gpu_mask_overlap: cv2.cuda_GpuMat | None = None
        self._gpu_weight_left: cv2.cuda_GpuMat | None = None
        self._gpu_weight_right: cv2.cuda_GpuMat | None = None
        self._gpu_overlap_left_coeff: cv2.cuda_GpuMat | None = None
        self._gpu_overlap_right_coeff: cv2.cuda_GpuMat | None = None
        self._resolve_gpu_mode()
        self._resolve_gpu_feature_mode()

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

    def _resolve_gpu_feature_mode(self) -> None:
        if not self._gpu_enabled:
            self._gpu_feature_enabled = False
            self._gpu_feature_reason = "gpu unavailable"
            return
        if not hasattr(cv2, "cuda_ORB_create"):
            self._gpu_feature_enabled = False
            self._gpu_feature_reason = "cuda ORB unavailable"
            return
        if not hasattr(cv2, "cuda") or not hasattr(cv2.cuda, "DescriptorMatcher_createBFMatcher"):
            self._gpu_feature_enabled = False
            self._gpu_feature_reason = "cuda descriptor matcher unavailable"
            return
        self._gpu_feature_enabled = True
        self._gpu_feature_reason = "cuda ORB matcher enabled"

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
            self._refresh_plan_templates(left.shape[:2], right.shape[:2])
            self._prepare_blend_strategy(left, right)
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

    def _detect_and_match_gpu(
        self,
        left: np.ndarray,
        right: np.ndarray,
        cfg: StitchConfig,
    ) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch]]:
        gpu_left = cv2.cuda_GpuMat()
        gpu_right = cv2.cuda_GpuMat()
        gpu_left.upload(left)
        gpu_right.upload(right)
        gray_left = cv2.cuda.cvtColor(gpu_left, cv2.COLOR_BGR2GRAY)
        gray_right = cv2.cuda.cvtColor(gpu_right, cv2.COLOR_BGR2GRAY)

        orb = cv2.cuda_ORB_create(nfeatures=int(cfg.max_features))
        kps_left_gpu, desc_left = orb.detectAndComputeAsync(gray_left, None)
        kps_right_gpu, desc_right = orb.detectAndComputeAsync(gray_right, None)
        if desc_left is None or desc_right is None:
            raise RuntimeError("descriptor extraction failed")
        keypoints_left = orb.convert(kps_left_gpu)
        keypoints_right = orb.convert(kps_right_gpu)

        matcher = cv2.cuda.DescriptorMatcher_createBFMatcher(cv2.NORM_HAMMING)
        knn_matches = matcher.knnMatch(desc_left, desc_right, k=2)
        good_matches: list[cv2.DMatch] = []
        for pair in knn_matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < cfg.ratio_test * n.distance:
                good_matches.append(m)
        if len(good_matches) < cfg.min_matches:
            raise RuntimeError(f"matches below threshold: {len(good_matches)} < {cfg.min_matches}")
        return keypoints_left, keypoints_right, good_matches

    def _detect_and_match_auto(
        self,
        left: np.ndarray,
        right: np.ndarray,
        cfg: StitchConfig,
    ) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch]]:
        if self._gpu_feature_enabled:
            try:
                out = self._detect_and_match_gpu(left, right, cfg)
                self._gpu_match_count += 1
                return out
            except Exception:
                self._gpu_feature_errors += 1
        out = _detect_and_match(left, right, cfg)
        self._cpu_match_count += 1
        return out

    def _refresh_plan_templates(self, left_shape: tuple[int, int], right_shape: tuple[int, int]) -> None:
        assert self._plan is not None
        self._left_canvas_template = np.zeros((self._plan.height, self._plan.width, 3), dtype=np.uint8)
        self._left_mask_template = np.zeros((self._plan.height, self._plan.width), dtype=np.uint8)
        lh, lw = left_shape
        self._left_mask_template[self._plan.ty : self._plan.ty + lh, self._plan.tx : self._plan.tx + lw] = 255
        self._right_mask_template = cv2.warpPerspective(
            np.ones(right_shape, dtype=np.uint8) * 255,
            self._plan.homography_adjusted,
            (self._plan.width, self._plan.height),
        )
        left_valid = self._left_mask_template > 0
        right_valid = self._right_mask_template > 0
        self._blend_only_left_mask = left_valid & ~right_valid
        self._blend_only_right_mask = right_valid & ~left_valid
        self._blend_overlap_mask = left_valid & right_valid
        self._blend_buffer = np.zeros((self._plan.height, self._plan.width, 3), dtype=np.float32)
        self._update_blend_weights()

    def _update_blend_weights(self, seam_path: np.ndarray | None = None) -> None:
        overlap = self._blend_overlap_mask
        if overlap is None or not np.any(overlap):
            self._blend_overlap_bbox = None
            self._blend_weight_left = None
            self._blend_weight_right = None
            self._gpu_mask_only_left = None
            self._gpu_mask_only_right = None
            self._gpu_mask_overlap = None
            self._gpu_weight_left = None
            self._gpu_weight_right = None
            self._gpu_overlap_left_coeff = None
            self._gpu_overlap_right_coeff = None
            return

        if seam_path is None:
            assert self._left_mask_template is not None
            assert self._right_mask_template is not None
            left_valid = self._left_mask_template > 0
            right_valid = self._right_mask_template > 0
            dist_left = cv2.distanceTransform(left_valid.astype(np.uint8), cv2.DIST_L2, 3)
            dist_right = cv2.distanceTransform(right_valid.astype(np.uint8), cv2.DIST_L2, 3)
            denom = dist_left + dist_right + 1e-6
            weight_left = dist_left / denom
            weight_right = dist_right / denom
        else:
            transition = max(2, int(self._cfg.seam_transition_px))
            weight_left = np.zeros(overlap.shape, dtype=np.float32)
            weight_right = np.zeros(overlap.shape, dtype=np.float32)
            ys, xs = np.where(overlap)
            y0, y1 = int(ys.min()), int(ys.max()) + 1
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            x_coords = np.arange(x0, x1, dtype=np.float32)[None, :]
            seam_roi = np.asarray(seam_path[y0:y1], dtype=np.float32)[:, None]
            right_w = np.clip((x_coords - (seam_roi - transition / 2.0)) / transition, 0.0, 1.0)
            left_w = 1.0 - right_w
            overlap_roi = overlap[y0:y1, x0:x1]
            weight_left_roi = weight_left[y0:y1, x0:x1]
            weight_right_roi = weight_right[y0:y1, x0:x1]
            weight_left_roi[overlap_roi] = left_w[overlap_roi]
            weight_right_roi[overlap_roi] = right_w[overlap_roi]

        self._blend_weight_left = weight_left
        self._blend_weight_right = weight_right

        ys, xs = np.where(overlap)
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        self._blend_overlap_bbox = (y0, y1, x0, x1)

        if self._gpu_enabled:
            left_only_f = np.repeat(self._blend_only_left_mask[:, :, None].astype(np.float32), 3, axis=2)
            right_only_f = np.repeat(self._blend_only_right_mask[:, :, None].astype(np.float32), 3, axis=2)
            overlap_f = np.repeat(overlap[:, :, None].astype(np.float32), 3, axis=2)
            weight_left_f = np.repeat(weight_left[:, :, None].astype(np.float32), 3, axis=2)
            weight_right_f = np.repeat(weight_right[:, :, None].astype(np.float32), 3, axis=2)
            assert self._left_shape is not None
            if self._gpu_left_canvas is None:
                self._gpu_left_canvas = cv2.cuda_GpuMat()
            self._gpu_left_canvas.create(self._plan.height, self._plan.width, cv2.CV_8UC3)
            self._gpu_left_canvas.setTo((0, 0, 0, 0))
            if self._gpu_left_frame is None:
                self._gpu_left_frame = cv2.cuda_GpuMat()
            lh, lw = self._left_shape
            self._gpu_left_canvas_roi = self._gpu_left_canvas.rowRange(
                self._plan.ty,
                self._plan.ty + lh,
            ).colRange(
                self._plan.tx,
                self._plan.tx + lw,
            )
            self._gpu_mask_only_left = cv2.cuda_GpuMat()
            self._gpu_mask_only_right = cv2.cuda_GpuMat()
            self._gpu_mask_overlap = cv2.cuda_GpuMat()
            self._gpu_weight_left = cv2.cuda_GpuMat()
            self._gpu_weight_right = cv2.cuda_GpuMat()
            self._gpu_overlap_left_coeff = cv2.cuda_GpuMat()
            self._gpu_overlap_right_coeff = cv2.cuda_GpuMat()
            self._gpu_mask_only_left.upload(left_only_f)
            self._gpu_mask_only_right.upload(right_only_f)
            self._gpu_mask_overlap.upload(overlap_f)
            self._gpu_weight_left.upload(weight_left_f)
            self._gpu_weight_right.upload(weight_right_f)
            overlap_roi = overlap[y0:y1, x0:x1]
            left_coeff_roi = weight_left[y0:y1, x0:x1].copy()
            right_coeff_roi = weight_right[y0:y1, x0:x1].copy()
            left_coeff_roi[~overlap_roi] = 1.0
            right_coeff_roi[~overlap_roi] = 0.0
            left_coeff_roi_f = np.repeat(left_coeff_roi[:, :, None].astype(np.float32), 3, axis=2)
            right_coeff_roi_f = np.repeat(right_coeff_roi[:, :, None].astype(np.float32), 3, axis=2)
            self._gpu_overlap_left_coeff.upload(left_coeff_roi_f)
            self._gpu_overlap_right_coeff.upload(right_coeff_roi_f)
        else:
            self._gpu_left_frame = None
            self._gpu_left_canvas = None
            self._gpu_left_canvas_roi = None
            self._gpu_mask_only_left = None
            self._gpu_mask_only_right = None
            self._gpu_mask_overlap = None
            self._gpu_weight_left = None
            self._gpu_weight_right = None
            self._gpu_overlap_left_coeff = None
            self._gpu_overlap_right_coeff = None

    def _prepare_blend_strategy(self, left: np.ndarray, right: np.ndarray) -> None:
        assert self._plan is not None
        warped_right_probe = cv2.warpPerspective(
            right,
            self._plan.homography_adjusted,
            (self._plan.width, self._plan.height),
        )
        right_mask_probe = self._right_mask_template
        left_mask_probe = self._left_mask_template
        left_canvas_probe = self._left_canvas_template.copy() if self._left_canvas_template is not None else None
        if right_mask_probe is None or left_mask_probe is None or left_canvas_probe is None:
            self._use_seam_cut = False
            self._seam_path = None
            self._overlap_diff_mean = 0.0
            self._exposure_gain = 1.0
            self._exposure_bias = 0.0
            return

        lh, lw = left.shape[:2]
        left_canvas_probe[self._plan.ty : self._plan.ty + lh, self._plan.tx : self._plan.tx + lw] = left
        overlap_probe = (left_mask_probe > 0) & (right_mask_probe > 0)

        exposure_gain = 1.0
        exposure_bias = 0.0
        if self._cfg.exposure_compensation:
            warped_right_probe, exposure_gain, exposure_bias = _compensate_exposure(
                canvas_left=left_canvas_probe,
                warped_right=warped_right_probe,
                overlap=overlap_probe,
                right_mask=right_mask_probe,
                config=self._cfg,
            )

        overlap_diff = _compute_overlap_diff_mean(left_canvas_probe, warped_right_probe, overlap_probe)
        use_seam_cut = bool(overlap_diff >= 8.0)
        seam_path: np.ndarray | None = None
        if use_seam_cut and np.any(overlap_probe):
            cost_map = _compute_seam_cost_map(
                canvas_left=left_canvas_probe,
                warped_right=warped_right_probe,
                overlap=overlap_probe,
            )
            seam_path = _find_seam_path(
                overlap=overlap_probe,
                cost_map=cost_map,
                smoothness_penalty=self._cfg.seam_smoothness_penalty,
            )

        self._use_seam_cut = use_seam_cut and seam_path is not None
        self._seam_path = seam_path
        self._update_blend_weights(seam_path if self._use_seam_cut else None)
        self._overlap_diff_mean = float(overlap_diff)
        self._exposure_gain = float(exposure_gain)
        self._exposure_bias = float(exposure_bias)
        self._prev_canvas_left_for_seam = None
        self._prev_warped_right_for_seam = None

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
                keypoints_left, keypoints_right, matches = self._detect_and_match_auto(left, right, candidate_cfg)
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
                self._refresh_plan_templates(left.shape[:2], right.shape[:2])
                self._prepare_blend_strategy(left, right)
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
            gpu_warped: cv2.cuda_GpuMat | None = None
            if self._gpu_enabled:
                try:
                    if self._gpu_frame is None:
                        self._gpu_frame = cv2.cuda_GpuMat()
                    self._gpu_frame.upload(right)
                    gpu_warped = cv2.cuda.warpPerspective(
                        self._gpu_frame,
                        self._plan.homography_adjusted,
                        (self._plan.width, self._plan.height),
                    )
                    right_warped = None
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
                self._refresh_plan_templates(left.shape[:2], right.shape[:2])
                right_mask = self._right_mask_template
            left_mask = self._left_mask_template
            if left_mask is None:
                self._refresh_plan_templates(left.shape[:2], right.shape[:2])
                left_mask = self._left_mask_template
            assert right_mask is not None
            assert left_mask is not None

            can_gpu_blend = (
                self._gpu_enabled
                and gpu_warped is not None
                and self._gpu_left_frame is not None
                and self._gpu_left_canvas is not None
                and self._gpu_left_canvas_roi is not None
                and self._gpu_overlap_left_coeff is not None
                and self._gpu_overlap_right_coeff is not None
                and self._blend_overlap_bbox is not None
            )
            need_cpu_right = not can_gpu_blend
            if right_warped is None and need_cpu_right:
                assert gpu_warped is not None
                right_warped = gpu_warped.download()

            if self._cfg.exposure_compensation and right_warped is not None:
                right_warped = _apply_gain_bias(
                    right_warped,
                    gain=self._exposure_gain,
                    bias=self._exposure_bias,
                    mask=right_mask,
                )

            if can_gpu_blend:
                try:
                    assert self._gpu_left_frame is not None
                    assert self._gpu_left_canvas is not None
                    assert self._gpu_left_canvas_roi is not None
                    assert self._gpu_overlap_left_coeff is not None
                    assert self._gpu_overlap_right_coeff is not None
                    assert self._blend_overlap_bbox is not None
                    self._gpu_left_frame.upload(left)
                    gpu_warped.copyTo(self._gpu_left_canvas)
                    self._gpu_left_frame.copyTo(self._gpu_left_canvas_roi)
                    y0, y1, x0, x1 = self._blend_overlap_bbox
                    gpu_output_overlap = self._gpu_left_canvas.rowRange(y0, y1).colRange(x0, x1)
                    gpu_right_overlap = gpu_warped.rowRange(y0, y1).colRange(x0, x1)
                    gpu_left_overlap_f = gpu_output_overlap.convertTo(cv2.CV_32F)
                    if self._cfg.exposure_compensation:
                        gpu_right_overlap_f = gpu_right_overlap.convertTo(
                            cv2.CV_32F,
                            alpha=float(self._exposure_gain),
                            beta=float(self._exposure_bias),
                        )
                    else:
                        gpu_right_overlap_f = gpu_right_overlap.convertTo(cv2.CV_32F)
                    gpu_left_overlap_weighted = cv2.cuda.multiply(gpu_left_overlap_f, self._gpu_overlap_left_coeff)
                    gpu_right_overlap_weighted = cv2.cuda.multiply(gpu_right_overlap_f, self._gpu_overlap_right_coeff)
                    gpu_overlap_merged = cv2.cuda.add(gpu_left_overlap_weighted, gpu_right_overlap_weighted)
                    if self._desktop_cfg.headless_benchmark:
                        stitched = self._last_stitched
                    else:
                        gpu_overlap_u8 = gpu_overlap_merged.convertTo(cv2.CV_8U)
                        gpu_overlap_u8.copyTo(gpu_output_overlap)
                        stitched = self._gpu_left_canvas.download()
                    self._gpu_blend_count += 1
                except Exception:
                    self._gpu_errors += 1
                    if right_warped is None:
                        right_warped = gpu_warped.download()
                        if self._cfg.exposure_compensation:
                            right_warped = _apply_gain_bias(
                                right_warped,
                                gain=self._exposure_gain,
                                bias=self._exposure_bias,
                                mask=right_mask,
                            )
                    left_canvas = (
                        self._left_canvas_template.copy()
                        if self._left_canvas_template is not None
                        else np.zeros((self._plan.height, self._plan.width, 3), dtype=np.uint8)
                    )
                    lh, lw = left.shape[:2]
                    left_canvas[self._plan.ty : self._plan.ty + lh, self._plan.tx : self._plan.tx + lw] = left
                    stitched = _blend_feather(left_canvas, right_warped, left_mask, right_mask)
                    self._cpu_blend_count += 1
            elif (
                self._blend_buffer is not None
                and self._blend_only_left_mask is not None
                and self._blend_only_right_mask is not None
                and self._blend_overlap_mask is not None
            ):
                left_canvas = (
                    self._left_canvas_template.copy()
                    if self._left_canvas_template is not None
                    else np.zeros((self._plan.height, self._plan.width, 3), dtype=np.uint8)
                )
                lh, lw = left.shape[:2]
                left_canvas[self._plan.ty : self._plan.ty + lh, self._plan.tx : self._plan.tx + lw] = left
                blend = self._blend_buffer
                blend.fill(0.0)
                blend[self._blend_only_left_mask] = left_canvas[self._blend_only_left_mask]
                blend[self._blend_only_right_mask] = right_warped[self._blend_only_right_mask]
                if (
                    self._blend_weight_left is not None
                    and self._blend_weight_right is not None
                    and np.any(self._blend_overlap_mask)
                ):
                    ov = self._blend_overlap_mask
                    wl = self._blend_weight_left[ov][:, None]
                    wr = self._blend_weight_right[ov][:, None]
                    blend[ov] = left_canvas[ov] * wl + right_warped[ov] * wr
                np.clip(blend, 0, 255, out=blend)
                stitched = blend.astype(np.uint8)
                self._cpu_blend_count += 1
            else:
                if right_warped is None:
                    assert gpu_warped is not None
                    right_warped = gpu_warped.download()
                    if self._cfg.exposure_compensation:
                        right_warped = _apply_gain_bias(
                            right_warped,
                            gain=self._exposure_gain,
                            bias=self._exposure_bias,
                            mask=right_mask,
                        )
                left_canvas = (
                    self._left_canvas_template.copy()
                    if self._left_canvas_template is not None
                    else np.zeros((self._plan.height, self._plan.width, 3), dtype=np.uint8)
                )
                lh, lw = left.shape[:2]
                left_canvas[self._plan.ty : self._plan.ty + lh, self._plan.tx : self._plan.tx + lw] = left
                stitched = _blend_feather(left_canvas, right_warped, left_mask, right_mask)
                self._cpu_blend_count += 1

            if stitched is not None:
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
            "gpu_feature_enabled": bool(self._gpu_feature_enabled),
            "gpu_feature_reason": self._gpu_feature_reason,
            "gpu_warp_count": int(self._gpu_warp_count),
            "gpu_match_count": int(self._gpu_match_count),
            "gpu_blend_count": int(self._gpu_blend_count),
            "cpu_warp_count": int(self._cpu_warp_count),
            "cpu_match_count": int(self._cpu_match_count),
            "cpu_blend_count": int(self._cpu_blend_count),
            "blend_mode": "seam_cut" if self._use_seam_cut else "feather",
            "overlap_diff_mean": float(self._overlap_diff_mean),
            "gpu_errors": int(self._gpu_errors),
            "gpu_feature_errors": int(self._gpu_feature_errors),
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
        self._status = "waiting for both streams"
        self._metrics: dict[str, float | int | str] = {}
        self._worker_timestamps: deque[float] = deque(maxlen=240)
        self._pair_skew_ms: deque[float] = deque(maxlen=240)

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

    def snapshot(self) -> tuple[np.ndarray | None, str, dict[str, float | int | str]]:
        with self._lock:
            stitched = self._latest_stitched
            return stitched, self._status, dict(self._metrics)

    def _current_worker_fps(self) -> float:
        if len(self._worker_timestamps) < 2:
            return 0.0
        dt = self._worker_timestamps[-1] - self._worker_timestamps[0]
        return float((len(self._worker_timestamps) - 1) / dt) if dt > 1e-6 else 0.0

    def _run(self) -> None:
        while not self._stop_event.is_set():
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

            if self._left is None or self._right is None or not self._left.has_frames() or not self._right.has_frames():
                metrics = self._stitcher.metrics_snapshot()
                recent_work = bool(self._worker_timestamps and (time.perf_counter() - self._worker_timestamps[-1]) < 0.5)
                metrics["worker_fps"] = self._current_worker_fps()
                metrics["status"] = "waiting next frame" if recent_work else "waiting for both streams"
                metrics["pair_skew_ms_mean"] = (
                    float(sum(self._pair_skew_ms) / len(self._pair_skew_ms)) if self._pair_skew_ms else 0.0
                )
                with self._lock:
                    self._status = str(metrics["status"])
                    self._metrics = metrics
                time.sleep(0.01)
                continue

            ok_pair, lf, rf, residual_delta_ms = _read_synced_pair(
                left_reader=self._left,
                right_reader=self._right,
                config=self._cfg,
            )
            if not ok_pair or lf is None or rf is None:
                metrics = self._stitcher.metrics_snapshot()
                recent_work = bool(self._worker_timestamps and (time.perf_counter() - self._worker_timestamps[-1]) < 0.5)
                metrics["worker_fps"] = self._current_worker_fps()
                metrics["status"] = "waiting next frame" if recent_work else "waiting synced pair"
                metrics["pair_skew_ms_mean"] = (
                    float(sum(self._pair_skew_ms) / len(self._pair_skew_ms)) if self._pair_skew_ms else 0.0
                )
                with self._lock:
                    self._status = str(metrics["status"])
                    self._metrics = metrics
                time.sleep(0.002)
                continue

            if residual_delta_ms is not None:
                self._pair_skew_ms.append(float(residual_delta_ms))

            stitched, status = self._stitcher.stitch(lf, rf)
            if stitched is not None:
                stitched = _resize_frame(stitched, max(0.1, float(self._cfg.stitch_output_scale)))

            now = time.perf_counter()
            self._worker_timestamps.append(now)
            metrics = self._stitcher.metrics_snapshot()
            left_pts, right_pts = self._stitcher.manual_points_snapshot()
            metrics["manual_left_points"] = left_pts
            metrics["manual_right_points"] = right_pts
            metrics["pair_skew_ms_mean"] = (
                float(sum(self._pair_skew_ms) / len(self._pair_skew_ms)) if self._pair_skew_ms else 0.0
            )
            metrics["pair_skew_ms_abs_max"] = (
                float(max(abs(v) for v in self._pair_skew_ms)) if self._pair_skew_ms else 0.0
            )
            metrics["worker_fps"] = self._current_worker_fps()
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


def _build_log_panel(
    width: int,
    lines: list[str],
    height: int = 180,
    font_scale: float = 0.58,
    thickness: int = 1,
    line_step: int = 22,
    header: str | None = None,
) -> np.ndarray:
    panel = np.zeros((height, width, 3), dtype=np.uint8)
    panel[:] = (12, 12, 12)
    y = 20
    if header:
        cv2.putText(panel, header, (12, y + 8), cv2.FONT_HERSHEY_SIMPLEX, font_scale + 0.18, (240, 240, 240), max(1, thickness + 1), cv2.LINE_AA)
        y += line_step + 8
    for line in lines:
        cv2.putText(panel, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (210, 210, 210), thickness, cv2.LINE_AA)
        y += line_step
        if y > height - 10:
            break
    return panel


def _draw_status_overlay(panel: np.ndarray, text: str) -> None:
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
    x1, y1 = 10, 10
    x2 = x1 + tw + 14
    y2 = y1 + th + baseline + 10
    cv2.rectangle(panel, (x1, y1), (x2, y2), (0, 0, 0), thickness=-1)
    cv2.rectangle(panel, (x1, y1), (x2, y2), (220, 220, 220), thickness=1)
    cv2.putText(panel, text, (x1 + 7, y2 - baseline - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (245, 245, 245), 2, cv2.LINE_AA)


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
        cv2.circle(panel, (dx, dy), 4, color, -1, cv2.LINE_AA)
        cv2.putText(panel, str(idx), (dx + 6, dy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)


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


def _format_headless_benchmark_line(
    *,
    elapsed_sec: float,
    metrics: dict[str, float | int | str],
    left_fps: float,
    right_fps: float,
    cpu_percent: float,
    gpu_percent: float,
    left_stats: dict[str, int | str],
    right_stats: dict[str, int | str],
) -> str:
    return (
        f"[bench {elapsed_sec:7.2f}s] "
        f"status={metrics.get('status', '-')} "
        f"new={float(metrics.get('stitch_fps', 0.0)):.2f} "
        f"worker={float(metrics.get('worker_fps', 0.0)):.2f} "
        f"left={left_fps:.2f} right={right_fps:.2f} "
        f"matches={int(metrics.get('matches', 0))} inliers={int(metrics.get('inliers', 0))} "
        f"gpu={gpu_percent:.1f}% cpu={cpu_percent:.1f}% "
        f"gpu_warp={int(metrics.get('gpu_warp_count', 0))} gpu_blend={int(metrics.get('gpu_blend_count', 0))} "
        f"stale={int(left_stats.get('stale_drops', 0))}/{int(right_stats.get('stale_drops', 0))}"
    )


def run_desktop(config: DesktopConfig) -> int:
    _configure_runtime(config)
    left_url = config.left_rtsp.strip()
    right_url = config.right_rtsp.strip()
    if not left_url and not right_url:
        print("Provide at least one RTSP URL via --left-rtsp or --right-rtsp")
        return 2

    try:
        left = _build_rtsp_reader(name="left", url=left_url, config=config) if left_url else None
        right = _build_rtsp_reader(name="right", url=right_url, config=config) if right_url else None
    except FfmpegRuntimeError as exc:
        print(f"FFmpeg runtime setup failed: {exc}")
        return 2
    if left is not None:
        left.start()
    if right is not None:
        right.start()

    stitch_worker = StitchWorker(config=config, left=left, right=right)
    stitch_worker.start()
    stats_sampler = SystemStatsSampler(interval_sec=1.0)
    stats_sampler.start()

    window_name = "RTSP Desktop Stitch Dashboard"
    panorama_window_name = "RTSP Panorama"
    if not config.headless_benchmark:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.namedWindow(panorama_window_name, cv2.WINDOW_NORMAL)

    ui_state: dict[str, object] = {
        "left_map": None,
        "right_map": None,
        "left_panel_rect": None,
        "right_panel_rect": None,
        "stitch_panel_rect": None,
        "log_panel_rect": None,
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
            ("log_panel_rect", "log"),
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

    if not config.headless_benchmark:
        cv2.setMouseCallback(window_name, _on_mouse, ui_state)

    panorama_display_timestamps: deque[float] = deque(maxlen=240)
    dashboard_display_timestamps: deque[float] = deque(maxlen=240)
    left_fps_meter = _CounterFpsMeter()
    right_fps_meter = _CounterFpsMeter()
    cached_log_panel: np.ndarray | None = None
    cached_dashboard_canvas: np.ndarray | None = None
    last_log_refresh_at = 0.0
    last_dashboard_refresh_at = 0.0
    benchmark_started_at = time.perf_counter()
    last_benchmark_log_at = benchmark_started_at

    try:
        while True:
            now = time.perf_counter()
            if config.headless_benchmark and float(config.benchmark_duration_sec) > 0.0:
                if (now - benchmark_started_at) >= float(config.benchmark_duration_sec):
                    break

            left_frame = left.latest_frame() if left is not None else None
            right_frame = right.latest_frame() if right is not None else None
            stitched, stitch_status, metrics = stitch_worker.snapshot()

            left_stats = left.snapshot_stats() if left is not None else {"frames_total": 0, "last_error": "disabled"}
            right_stats = right.snapshot_stats() if right is not None else {"frames_total": 0, "last_error": "disabled"}
            left_stream_fps = left_fps_meter.update(int(left_stats["frames_total"]))
            right_stream_fps = right_fps_meter.update(int(right_stats["frames_total"]))
            stitch_compute_fps = float(metrics.get("stitch_fps", 0.0))

            sys_stats = stats_sampler.snapshot()
            gpu_pct = float(sys_stats["gpu_percent"])
            gpu_mem_used = float(sys_stats["gpu_mem_used"])
            gpu_mem_total = float(sys_stats["gpu_mem_total"])
            gpu_temp = float(sys_stats["gpu_temp_c"])
            cpu_per_core = [float(v) for v in sys_stats.get("cpu_per_core", [])]
            cpu_core_lines: list[str] = []
            if cpu_per_core:
                chunk = 8
                for start in range(0, len(cpu_per_core), chunk):
                    segment = cpu_per_core[start : start + chunk]
                    cpu_core_lines.append(
                        "cpu cores "
                        + "  ".join(f"{start + idx}:{value:4.1f}%" for idx, value in enumerate(segment))
                    )

            if config.headless_benchmark:
                metrics["status"] = stitch_status
                if (now - last_benchmark_log_at) >= max(0.1, float(config.benchmark_log_interval_sec)):
                    print(
                        _format_headless_benchmark_line(
                            elapsed_sec=now - benchmark_started_at,
                            metrics=metrics,
                            left_fps=left_stream_fps,
                            right_fps=right_stream_fps,
                            cpu_percent=float(sys_stats["cpu_percent"]),
                            gpu_percent=gpu_pct,
                            left_stats=left_stats,
                            right_stats=right_stats,
                        ),
                        flush=True,
                    )
                    last_benchmark_log_at = now
                time.sleep(0.001)
                continue

            reference = left_frame if left_frame is not None else right_frame
            if reference is not None:
                rh, rw = reference.shape[:2]
                ref_aspect = float(rw) / float(max(1, rh))
            else:
                ref_aspect = 16.0 / 9.0

            panel_w = max(480, int(config.max_display_width // 2))
            panel_h = max(180, int(panel_w / max(0.1, ref_aspect)))

            panorama_display_timestamps.append(now)
            panorama_out_fps = 0.0
            if len(panorama_display_timestamps) >= 2:
                dt = panorama_display_timestamps[-1] - panorama_display_timestamps[0]
                if dt > 1e-6:
                    panorama_out_fps = (len(panorama_display_timestamps) - 1) / dt

            panorama_panel = _fit_to_panel_with_map(
                stitched,
                max(1280, int(config.max_display_width * 1.1)),
                max(280, int(panel_h * 0.9)),
                f"Panorama: {stitch_status}",
            )[0]
            _draw_status_overlay(panorama_panel, f"PANORAMA new {stitch_compute_fps:.1f} / out {panorama_out_fps:.1f}")
            cv2.imshow(panorama_window_name, panorama_panel)

            manual_mode = bool(metrics.get("manual_mode", False))
            dashboard_refresh_interval = 0.05 if manual_mode else 0.25
            if cached_dashboard_canvas is None or (now - last_dashboard_refresh_at) >= dashboard_refresh_interval:
                dashboard_display_timestamps.append(now)
                dashboard_fps = 0.0
                if len(dashboard_display_timestamps) >= 2:
                    dt = dashboard_display_timestamps[-1] - dashboard_display_timestamps[0]
                    if dt > 1e-6:
                        dashboard_fps = (len(dashboard_display_timestamps) - 1) / dt

                left_panel, left_map_local = _fit_to_panel_with_map(left_frame, panel_w, panel_h, "Left stream (raw)")
                right_panel, right_map_local = _fit_to_panel_with_map(right_frame, panel_w, panel_h, "Right stream (raw)")
                manual_left_points = list(metrics.get("manual_left_points", []))  # type: ignore[arg-type]
                manual_right_points = list(metrics.get("manual_right_points", []))  # type: ignore[arg-type]
                _draw_manual_points(left_panel, left_map_local, manual_left_points)
                _draw_manual_points(right_panel, right_map_local, manual_right_points)
                _draw_status_overlay(left_panel, f"LEFT {left_stream_fps:.1f} FPS")
                _draw_status_overlay(right_panel, f"RIGHT {right_stream_fps:.1f} FPS")
                top_row = np.hstack([left_panel, right_panel])
                stitched_panel_h = max(220, int(top_row.shape[0] * 0.9))
                stitch_panel, _ = _fit_to_panel_with_map(
                    stitched,
                    top_row.shape[1],
                    stitched_panel_h,
                    f"Panorama: {stitch_status}",
                )
                _draw_status_overlay(stitch_panel, f"STITCH new {stitch_compute_fps:.1f} / out {panorama_out_fps:.1f}")

                left_map = _offset_map(left_map_local, 0, 0)
                right_map = _offset_map(right_map_local, panel_w, 0)
                ui_state["left_map"] = left_map
                ui_state["right_map"] = right_map

                log_panel_h = max(320, int(top_row.shape[0] * 0.78))
                if cached_log_panel is None or (now - last_log_refresh_at) >= 0.25:
                    left_runtime = str(left_stats.get("runtime", "-"))
                    right_runtime = str(right_stats.get("runtime", "-"))
                    monitor_lines = [
                        f"status={metrics.get('status', stitch_status)}  frame={int(metrics.get('frame_index', 0))}  dashboard_fps={dashboard_fps:.2f}",
                        f"matches={int(metrics.get('matches', 0))}  inliers={int(metrics.get('inliers', 0))}  stitch_new_fps={stitch_compute_fps:.2f}  panorama_out_fps={panorama_out_fps:.2f}  worker_fps={float(metrics.get('worker_fps', 0.0)):.2f}",
                        f"left_fps={left_stream_fps:.2f}  right_fps={right_stream_fps:.2f}  pair_skew_ms={float(metrics.get('pair_skew_ms_mean', 0.0)):.2f}",
                        f"input_runtime={str(config.input_runtime)}  left_runtime={left_runtime}  right_runtime={right_runtime}",
                        f"stitched_count={int(metrics.get('stitched_count', 0))}  reused_count={int(metrics.get('reused_count', 0))}  stitch_every_n={int(config.stitch_every_n)}  process_scale={float(config.process_scale):.2f}",
                        f"manual L/R={int(metrics.get('manual_left', 0))}/{int(metrics.get('manual_right', 0))} target={int(metrics.get('manual_target', 0))}  manual_mode={manual_mode}",
                        f"CPU usage: {sys_stats['cpu_percent']:.1f}%  threads={int(config.cpu_threads) if int(config.cpu_threads) > 0 else int(os.cpu_count() or 1)}  GPU usage: {gpu_pct:.1f}%  GPU temp: {gpu_temp:.0f} C",
                        f"GPU memory: {gpu_mem_used:.0f}/{gpu_mem_total:.0f} MB",
                        f"gpu_enabled={bool(metrics.get('gpu_enabled', False))}  gpu_reason={metrics.get('gpu_reason', '-')}",
                        f"gpu_feature={bool(metrics.get('gpu_feature_enabled', False))}  feature_reason={metrics.get('gpu_feature_reason', '-')}",
                        f"blend_mode={metrics.get('blend_mode', '-')}  overlap_diff={float(metrics.get('overlap_diff_mean', 0.0)):.2f}",
                        f"gpu_warp={int(metrics.get('gpu_warp_count', 0))}  cpu_warp={int(metrics.get('cpu_warp_count', 0))}  gpu_match={int(metrics.get('gpu_match_count', 0))}  cpu_match={int(metrics.get('cpu_match_count', 0))}",
                        f"gpu_blend={int(metrics.get('gpu_blend_count', 0))}  cpu_blend={int(metrics.get('cpu_blend_count', 0))}",
                        f"gpu_errors={int(metrics.get('gpu_errors', 0))}  gpu_feature_errors={int(metrics.get('gpu_feature_errors', 0))}",
                        f"left_frames={int(left_stats['frames_total'])} buf={int(left_stats.get('buffer_size', 0))} stale={int(left_stats.get('stale_drops', 0))}  right_frames={int(right_stats['frames_total'])} buf={int(right_stats.get('buffer_size', 0))} stale={int(right_stats.get('stale_drops', 0))}",
                        f"left_err={left_stats['last_error'] or '-'}",
                        f"right_err={right_stats['last_error'] or '-'}",
                        "controls: q/ESC=quit, m=manual stitch mode, a=auto mode, click panel=focus",
                    ]
                    monitor_lines[5:5] = cpu_core_lines
                    cached_log_panel = _build_log_panel(
                        width=top_row.shape[1],
                        lines=monitor_lines,
                        height=log_panel_h,
                        font_scale=0.82,
                        thickness=2,
                        line_step=30,
                        header="System Monitor",
                    )
                    last_log_refresh_at = now
                log_panel = cached_log_panel

                canvas_raw = np.vstack([top_row, stitch_panel, log_panel])

                ui_state["left_panel_rect"] = (0, 0, panel_w, panel_h)
                ui_state["right_panel_rect"] = (panel_w, 0, panel_w * 2, panel_h)
                stitch_y1 = top_row.shape[0]
                ui_state["stitch_panel_rect"] = (0, stitch_y1, top_row.shape[1], stitch_y1 + stitch_panel.shape[0])
                log_y1 = stitch_y1 + stitch_panel.shape[0]
                ui_state["log_panel_rect"] = (0, log_y1, top_row.shape[1], log_y1 + log_panel.shape[0])

                focus = ui_state.get("focus_panel")
                if focus in {"left", "right", "stitch", "log"}:
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
                        focus_frame = log_panel
                        focus_label = "Focused: System monitor (click to return)"

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
                    ui_state["log_panel_rect"] = None

                canvas = _fit_width(canvas_raw, max_width=max(960, int(config.max_display_width)))
                ui_scale = canvas.shape[1] / float(max(1, canvas_raw.shape[1]))
                ui_state["ui_scale"] = ui_scale
                ui_state["manual_mode"] = manual_mode
                if manual_mode:
                    mx = int(float(ui_state.get("mouse_x", 0.0)) * float(ui_state.get("ui_scale", 1.0)))
                    my = int(float(ui_state.get("mouse_y", 0.0)) * float(ui_state.get("ui_scale", 1.0)))
                    _draw_magnifier(canvas, mx, my)
                cached_dashboard_canvas = canvas
                last_dashboard_refresh_at = now
                cv2.imshow(window_name, cached_dashboard_canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("m"):
                stitch_worker.request_manual_calibration()
            if key == ord("a"):
                stitch_worker.request_auto_mode()
    except KeyboardInterrupt:
        pass
    finally:
        stitch_worker.stop()
        stats_sampler.stop()
        if left is not None:
            left.stop()
        if right is not None:
            right.stop()
        if not config.headless_benchmark:
            cv2.destroyAllWindows()

    return 0
