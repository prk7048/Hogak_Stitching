from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from stitching.domain.calibration.native.calibration import (
    NativeCalibrationConfig,
    _open_capture,
    _resize_frame,
    _resize_to_match,
)
from stitching.domain.runtime.service.launcher import NativeCaptureSpec
from stitching.domain.runtime.site_config import load_runtime_site_config


DEFAULT_NATIVE_CAPTURE_SUBDIR = "native_capture"
DEFAULT_NATIVE_CAPTURE_MANIFEST = "capture_manifest.json"


def native_capture_dir(session_dir: Path) -> Path:
    return Path(session_dir) / DEFAULT_NATIVE_CAPTURE_SUBDIR


def native_capture_manifest_path(session_dir: Path) -> Path:
    return native_capture_dir(session_dir) / DEFAULT_NATIVE_CAPTURE_MANIFEST


def capture_clip_opencv(
    config: NativeCalibrationConfig,
    *,
    clip_frames: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    left_cap = _open_capture(config.left_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    right_cap = _open_capture(config.right_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    captured: list[tuple[np.ndarray, np.ndarray]] = []
    deadline = time.time() + max(4.0, float(config.rtsp_timeout_sec) * 2.0)
    warmup_remaining = max(1, int(config.warmup_frames))
    try:
        while time.time() < deadline and warmup_remaining > 0:
            ok_left, frame_left = left_cap.read()
            ok_right, frame_right = right_cap.read()
            if ok_left and frame_left is not None and ok_right and frame_right is not None:
                warmup_remaining -= 1
        while time.time() < deadline and len(captured) < max(1, int(clip_frames)):
            ok_left, frame_left = left_cap.read()
            ok_right, frame_right = right_cap.read()
            if not ok_left or frame_left is None or not ok_right or frame_right is None:
                continue
            left_frame = _resize_frame(frame_left, config.process_scale)
            right_frame = _resize_frame(frame_right, config.process_scale)
            right_frame = _resize_to_match(right_frame, left_frame.shape[:2])
            captured.append((left_frame, right_frame))
    finally:
        left_cap.release()
        right_cap.release()
    if not captured:
        raise ValueError("failed to capture a mesh-refresh clip with OpenCV fallback")
    return captured


def build_native_capture_spec(
    config: NativeCalibrationConfig,
    *,
    session_dir: Path,
    clip_frames: int,
) -> NativeCaptureSpec:
    site_config = load_runtime_site_config()
    runtime = site_config.get("runtime", {}) if isinstance(site_config.get("runtime"), dict) else {}
    defaults = NativeCaptureSpec()
    return NativeCaptureSpec(
        left_rtsp=str(config.left_rtsp),
        right_rtsp=str(config.right_rtsp),
        output_dir=str(native_capture_dir(session_dir)),
        clip_frames=max(1, int(clip_frames)),
        warmup_frames=max(0, int(config.warmup_frames)),
        input_runtime=str(runtime.get("input_runtime") or defaults.input_runtime),
        input_pipe_format=str(runtime.get("input_pipe_format") or defaults.input_pipe_format),
        ffmpeg_bin="",
        frame_width=max(1, int(runtime.get("frame_width") or runtime.get("width") or defaults.frame_width)),
        frame_height=max(1, int(runtime.get("frame_height") or runtime.get("height") or defaults.frame_height)),
        transport=str(config.rtsp_transport or runtime.get("rtsp_transport") or defaults.transport),
        input_buffer_frames=max(1, int(runtime.get("input_buffer_frames") or defaults.input_buffer_frames)),
        disable_freeze_detection=not bool(runtime.get("enable_freeze_detection", True)),
        video_codec=str(runtime.get("video_codec") or defaults.video_codec),
        timeout_sec=max(1.0, float(config.rtsp_timeout_sec)),
        reconnect_cooldown_sec=max(
            0.1,
            float(runtime.get("reconnect_cooldown_sec") or defaults.reconnect_cooldown_sec),
        ),
        sync_pair_mode=str(runtime.get("sync_pair_mode") or defaults.sync_pair_mode),
        sync_match_max_delta_ms=max(
            1.0,
            float(runtime.get("sync_match_max_delta_ms") or defaults.sync_match_max_delta_ms),
        ),
        sync_time_source=str(runtime.get("sync_time_source") or defaults.sync_time_source),
        sync_manual_offset_ms=float(runtime.get("sync_manual_offset_ms") or defaults.sync_manual_offset_ms),
        gpu_mode=str(runtime.get("gpu_mode") or defaults.gpu_mode),
        gpu_device=max(0, int(runtime.get("gpu_device") or defaults.gpu_device)),
    )


def load_native_capture_manifest(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("native capture manifest must be a JSON object")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("native capture manifest does not contain any frame pairs")
    return payload


def resolve_native_capture_frame_path(base_dir: Path, relative_path: str, *, index: int, side: str) -> Path:
    candidate = (base_dir / relative_path).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(
            f"native capture frame#{index} {side}_path points outside the capture bundle"
        ) from exc
    return candidate


def load_native_capture_clip(
    manifest_path: Path,
    *,
    process_scale: float,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], dict[str, Any]]:
    payload = load_native_capture_manifest(manifest_path)
    frames = payload.get("frames") or []
    base_dir = Path(manifest_path).parent.resolve()
    captured: list[tuple[np.ndarray, np.ndarray]] = []
    for index, item in enumerate(frames):
        if not isinstance(item, dict):
            raise ValueError(f"native capture frame#{index} is not a JSON object")
        left_rel = str(item.get("left_path") or "").strip()
        right_rel = str(item.get("right_path") or "").strip()
        if not left_rel or not right_rel:
            raise ValueError(f"native capture frame#{index} is missing left/right image paths")
        left_path = resolve_native_capture_frame_path(base_dir, left_rel, index=index, side="left")
        right_path = resolve_native_capture_frame_path(base_dir, right_rel, index=index, side="right")
        left_frame = cv2.imread(str(left_path), cv2.IMREAD_COLOR)
        right_frame = cv2.imread(str(right_path), cv2.IMREAD_COLOR)
        if left_frame is None or right_frame is None:
            raise ValueError(f"native capture frame#{index} could not be decoded from disk")
        left_frame = _resize_frame(left_frame, process_scale)
        right_frame = _resize_frame(right_frame, process_scale)
        right_frame = _resize_to_match(right_frame, left_frame.shape[:2])
        captured.append((left_frame, right_frame))

    pairing = payload.get("pairing") if isinstance(payload.get("pairing"), dict) else {}
    summary = {
        "capture_source": "native_paired_capture",
        "capture_manifest_path": str(manifest_path),
        "capture_pairing_mode": str(pairing.get("pair_mode") or ""),
        "capture_pairing_time_domain": str(pairing.get("resolved_time_domain") or ""),
        "capture_pairing_requested_time_source": str(pairing.get("requested_time_source") or ""),
        "capture_pairing_max_delta_ms": float(pairing.get("max_delta_ms") or 0.0),
        "capture_pairing_mean_delta_ms": float(pairing.get("mean_delta_ms") or 0.0),
        "capture_pairing_worst_delta_ms": float(pairing.get("worst_delta_ms") or 0.0),
        "capture_fallback_reason": "",
    }
    return captured, summary


def capture_clip(
    config: NativeCalibrationConfig,
    *,
    clip_frames: int,
    session_dir: Path,
    run_native_capture_func: Callable[[NativeCaptureSpec], Any],
    build_native_capture_spec_func: Callable[..., NativeCaptureSpec] = build_native_capture_spec,
    native_capture_manifest_path_func: Callable[[Path], Path] = native_capture_manifest_path,
    load_native_capture_clip_func: Callable[..., tuple[list[tuple[np.ndarray, np.ndarray]], dict[str, Any]]] = load_native_capture_clip,
    capture_clip_opencv_func: Callable[..., list[tuple[np.ndarray, np.ndarray]]] = capture_clip_opencv,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], dict[str, Any]]:
    native_error = ""
    try:
        capture_dir = native_capture_dir(session_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)
        completed = run_native_capture_func(
            build_native_capture_spec_func(
                config,
                session_dir=session_dir,
                clip_frames=max(1, int(clip_frames)),
            )
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"returncode={completed.returncode}"
            raise ValueError(f"native paired capture failed: {detail}")
        manifest_path = native_capture_manifest_path_func(session_dir)
        if not manifest_path.exists():
            raise ValueError(f"native capture manifest was not written: {manifest_path}")
        return load_native_capture_clip_func(manifest_path, process_scale=float(config.process_scale))
    except Exception as exc:
        native_error = str(exc)

    captured = capture_clip_opencv_func(config, clip_frames=max(1, int(clip_frames)))
    return captured, {
        "capture_source": "opencv_ffmpeg_capture",
        "capture_manifest_path": "",
        "capture_pairing_mode": "",
        "capture_pairing_time_domain": "arrival-sequential-fallback",
        "capture_pairing_requested_time_source": "",
        "capture_pairing_max_delta_ms": 0.0,
        "capture_pairing_mean_delta_ms": 0.0,
        "capture_pairing_worst_delta_ms": 0.0,
        "capture_fallback_reason": native_error,
    }
