from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

try:
    import gradio as _gradio_event_types

    GradioSelectData = _gradio_event_types.SelectData
except Exception:
    class GradioSelectData:  # type: ignore[no-redef]
        pass

from stitching.distortion_calibration import (
    DISTORTION_MODEL_FISHEYE,
    DISTORTION_MODEL_PINHOLE,
    MIN_MANUAL_LINE_COUNT,
    RECOMMENDED_MANUAL_LINE_COUNT,
    DistortionProfile,
    ManualLineSegment,
    ResolvedDistortion,
    apply_distortion_profile,
    apply_profile_metrics,
    capture_representative_frames,
    clone_distortion_profile,
    distortion_parameter_specs,
    distortion_profile_from_state,
    distortion_profile_to_state,
    evaluate_profile_against_guided_lines,
    estimate_manual_guided_distortion,
    load_distortion_profile,
    load_homography_distortion_reference,
    load_line_hints,
    render_distortion_preview,
    render_line_hints_preview,
    save_line_hints,
)
from stitching.final_stream_viewer import FinalStreamViewerSpec, launch_final_stream_viewer
from stitching.native_calibration import (
    NativeCalibrationConfig,
    backup_homography_file,
    calibrate_native_homography_from_frames,
    save_native_calibration_artifacts,
)
from stitching.output_presets import OUTPUT_PRESETS, get_output_preset
from stitching.project_defaults import (
    DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR,
    DEFAULT_NATIVE_CALIBRATION_INLIERS_FILE,
    DEFAULT_NATIVE_DISTORTION_AUTO_SAVE,
    DEFAULT_NATIVE_DISTORTION_CAMERA_MODEL,
    DEFAULT_NATIVE_DISTORTION_HORIZONTAL_FOV_DEG,
    DEFAULT_NATIVE_DISTORTION_LENS_MODEL_HINT,
    DEFAULT_NATIVE_DISTORTION_MODE,
    DEFAULT_NATIVE_DISTORTION_VERTICAL_FOV_DEG,
    DEFAULT_NATIVE_HOMOGRAPHY_PATH,
    DEFAULT_NATIVE_INPUT_BUFFER_FRAMES,
    DEFAULT_NATIVE_LEFT_DISTORTION_FILE,
    DEFAULT_NATIVE_LEFT_DISTORTION_HINTS_FILE,
    DEFAULT_NATIVE_RIGHT_DISTORTION_FILE,
    DEFAULT_NATIVE_RIGHT_DISTORTION_HINTS_FILE,
    DEFAULT_NATIVE_RTSP_TRANSPORT,
    DEFAULT_NATIVE_USE_SAVED_DISTORTION,
    default_output_standard,
)
from stitching.runtime_client import RuntimeClient
from stitching.runtime_launcher import RuntimeLaunchSpec


DEFAULT_UI_PORT = 7860


def _pick_available_local_port(preferred: int, *, host: str = "127.0.0.1", search_span: int = 20) -> int:
    base = max(1, int(preferred))
    span = max(1, int(search_span))
    for port in range(base, base + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    return 0


def _legacy_cli():
    from stitching import native_runtime_cli as legacy

    return legacy


def gradio_available() -> bool:
    try:
        import gradio  # noqa: F401
    except Exception:
        return False
    return True


def _import_gradio():
    import gradio as gr

    return gr


def _bgr_to_rgb(frame: np.ndarray | None) -> np.ndarray | None:
    if frame is None or frame.size <= 0:
        return None
    if frame.ndim == 2:
        return np.stack([frame, frame, frame], axis=-1)
    return frame[:, :, ::-1].copy()


def _line_to_label(index: int, line: ManualLineSegment) -> str:
    return (
        f"{index + 1}: "
        f"({int(round(line.start[0]))},{int(round(line.start[1]))}) -> "
        f"({int(round(line.end[0]))},{int(round(line.end[1]))})"
    )


def _extract_event_xy(evt: Any) -> tuple[float, float] | None:
    if evt is None:
        return None
    index = getattr(evt, "index", None)
    if isinstance(index, (tuple, list)) and len(index) >= 2:
        return (float(index[0]), float(index[1]))
    return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


SESSION_MIN_SUPPORT_POINTS = 96
SESSION_MIN_FIT_SCORE = 0.10
SESSION_MIN_EDGE_COVERAGE = 0.25
SESSION_MIN_SUPPORT_DISTRIBUTION = 0.25
SESSION_MIN_ABSOLUTE_IMPROVEMENT = 0.02
SESSION_MIN_RELATIVE_IMPROVEMENT = 0.07
SESSION_MIN_SAVED_RELATIVE_GAIN = 0.05
SESSION_MIN_SAVED_ABSOLUTE_GAIN = 0.01
SESSION_MAX_COVERAGE_DROP = 0.05
SESSION_MAX_DISTRIBUTION_DROP = 0.05
SESSION_MAX_BLACK_BORDER_RATIO = 0.18
SESSION_MIN_USABLE_AREA_RATIO = 0.78


def _profile_improvement(profile: DistortionProfile | None) -> float:
    if profile is None:
        return 0.0
    raw_residual = float(profile.raw_straightness_residual or 0.0)
    return raw_residual - float(profile.straightness_residual or 0.0)


def _required_profile_improvement(profile: DistortionProfile | None) -> float:
    if profile is None:
        return SESSION_MIN_ABSOLUTE_IMPROVEMENT
    raw_residual = float(profile.raw_straightness_residual or 0.0)
    return max(SESSION_MIN_ABSOLUTE_IMPROVEMENT, raw_residual * SESSION_MIN_RELATIVE_IMPROVEMENT)


def _profile_session_rejection_reason(profile: DistortionProfile | None) -> str:
    if profile is None:
        return "session candidate unavailable"
    if int(profile.line_count or 0) < MIN_MANUAL_LINE_COUNT:
        return f"not enough lines ({int(profile.line_count or 0)}/{MIN_MANUAL_LINE_COUNT})"
    if int(profile.support_points or 0) < SESSION_MIN_SUPPORT_POINTS:
        return f"support points too low ({int(profile.support_points or 0)} < {SESSION_MIN_SUPPORT_POINTS})"
    if float(profile.fit_score or 0.0) < SESSION_MIN_FIT_SCORE:
        return f"fit score too low ({float(profile.fit_score or 0.0):.3f} < {SESSION_MIN_FIT_SCORE:.2f})"
    if float(profile.edge_coverage or 0.0) < SESSION_MIN_EDGE_COVERAGE:
        return f"edge coverage too low ({float(profile.edge_coverage or 0.0):.2f} < {SESSION_MIN_EDGE_COVERAGE:.2f})"
    if float(profile.support_distribution or 0.0) < SESSION_MIN_SUPPORT_DISTRIBUTION:
        return (
            f"support distribution too low "
            f"({float(profile.support_distribution or 0.0):.2f} < {SESSION_MIN_SUPPORT_DISTRIBUTION:.2f})"
        )
    if float(profile.black_border_ratio or 0.0) > SESSION_MAX_BLACK_BORDER_RATIO:
        return (
            f"black border too large "
            f"({float(profile.black_border_ratio or 0.0):.2f} > {SESSION_MAX_BLACK_BORDER_RATIO:.2f})"
        )
    if float(profile.usable_area_ratio or 0.0) < SESSION_MIN_USABLE_AREA_RATIO:
        return (
            f"usable area too small "
            f"({float(profile.usable_area_ratio or 0.0):.2f} < {SESSION_MIN_USABLE_AREA_RATIO:.2f})"
        )
    improvement = _profile_improvement(profile)
    required = _required_profile_improvement(profile)
    if improvement < required:
        return f"raw improvement too small ({improvement:.4f} < {required:.4f})"
    return ""


def _profile_passes_session_gate(profile: DistortionProfile | None) -> bool:
    return _profile_session_rejection_reason(profile) == ""


def _session_beats_saved(session_profile: DistortionProfile | None, saved_profile: DistortionProfile | None) -> bool:
    if session_profile is None:
        return False
    if saved_profile is None:
        return _profile_passes_session_gate(session_profile)
    if not _profile_passes_session_gate(session_profile):
        return False
    residual_gain = float(saved_profile.straightness_residual or 0.0) - float(session_profile.straightness_residual or 0.0)
    relative_gain = residual_gain / max(1e-6, float(saved_profile.straightness_residual or 0.0))
    coverage_ok = float(session_profile.edge_coverage or 0.0) >= (float(saved_profile.edge_coverage or 0.0) - SESSION_MAX_COVERAGE_DROP)
    distribution_ok = float(session_profile.support_distribution or 0.0) >= (
        float(saved_profile.support_distribution or 0.0) - SESSION_MAX_DISTRIBUTION_DROP
    )
    return bool(
        coverage_ok
        and distribution_ok
        and (
            residual_gain >= SESSION_MIN_SAVED_ABSOLUTE_GAIN
            or relative_gain >= SESSION_MIN_SAVED_RELATIVE_GAIN
        )
    )


def _camera_recommendation_status(
    *,
    saved_profile: DistortionProfile | None,
    session_profile: DistortionProfile | None,
) -> str:
    _ = saved_profile
    if _profile_passes_session_gate(session_profile):
        return "session-candidate"
    return "raw"


def _profile_summary(profile: DistortionProfile | None, *, label: str) -> str:
    if profile is None:
        return f"- {label}: unavailable"
    return (
        f"- {label}: model={profile.model} fit={float(profile.fit_score):.3f} "
        f"residual={float(profile.straightness_residual):.4f} "
        f"raw={float(profile.raw_straightness_residual):.4f} "
        f"coverage={float(profile.edge_coverage):.2f} "
        f"distribution={float(profile.support_distribution):.2f} "
        f"black={float(profile.black_border_ratio):.2f} "
        f"usable={float(profile.usable_area_ratio):.2f}"
    )


def _distortion_status_markdown(
    *,
    slot: str,
    lines: list[ManualLineSegment],
    pending_point: tuple[float, float] | None,
    selected_index: int | None,
    saved_profile: DistortionProfile | None,
    auto_profile: DistortionProfile | None,
    session_profile: DistortionProfile | None,
) -> str:
    selected_text = f"{selected_index + 1}" if selected_index is not None and 0 <= selected_index < len(lines) else "none"
    pending_text = f"({int(round(pending_point[0]))}, {int(round(pending_point[1]))})" if pending_point is not None else "none"
    recommended = _camera_recommendation_status(saved_profile=saved_profile, session_profile=session_profile)
    rejection_reason = _profile_session_rejection_reason(session_profile) if recommended != "session-candidate" else ""
    return "\n".join(
        [
            f"### {slot.upper()} Line Hints",
            f"- lines={len(lines)} / min {MIN_MANUAL_LINE_COUNT} / recommended {RECOMMENDED_MANUAL_LINE_COUNT}+",
            f"- selected={selected_text} | pending={pending_text}",
            f"- recommendation={recommended}",
            f"- guardrail={rejection_reason or 'ready for session candidate'}",
            "- interactive baseline is raw only",
            _profile_summary(auto_profile, label="guided"),
            _profile_summary(session_profile, label="session"),
        ]
    )


def _camera_tuning_markdown(
    *,
    slot: str,
    baseline_name: str,
    model_name: str,
    profile: DistortionProfile | None,
    saved_profile: DistortionProfile | None,
    auto_profile: DistortionProfile | None,
) -> str:
    recommendation = _camera_recommendation_status(saved_profile=saved_profile, session_profile=profile)
    rejection_reason = _profile_session_rejection_reason(profile) if recommendation != "session-candidate" else ""
    if profile is None:
        return "\n".join(
            [
                f"### {slot.upper()} Tuning",
                f"- baseline={baseline_name}",
                f"- model={model_name}",
                f"- recommendation={recommendation}",
                "- current session candidate unavailable",
            ]
        )
    delta = float(profile.raw_straightness_residual) - float(profile.straightness_residual)
    return "\n".join(
        [
            f"### {slot.upper()} Tuning",
            f"- baseline={baseline_name}",
            f"- model={model_name}",
            f"- recommendation={recommendation}",
            f"- residual={float(profile.straightness_residual):.4f}",
            f"- improvement={delta:.4f}",
            f"- fit={float(profile.fit_score):.3f} | coverage={float(profile.edge_coverage):.2f} | distribution={float(profile.support_distribution):.2f}",
            f"- black-border={float(profile.black_border_ratio):.2f} | usable-area={float(profile.usable_area_ratio):.2f}",
            f"- projection={str(profile.chosen_projection_mode or 'camera-matrix')}",
            f"- reason={str(profile.chosen_model_reason or '')}",
            f"- rejected={rejection_reason or 'accepted behind guardrails'}",
        ]
    )


def _distortion_review_status_markdown(
    *,
    selected_source: str,
    recommended_source: str,
    left_session: DistortionProfile | None,
    right_session: DistortionProfile | None,
    review_messages: list[str],
) -> str:
    lines = [
        "### Distortion Review",
        f"- selected source={selected_source}",
        f"- recommended source={recommended_source}",
        "",
        _profile_summary(left_session, label="left current-session"),
        _profile_summary(right_session, label="right current-session"),
    ]
    if left_session is not None:
        lines.append(f"- left guardrail={_profile_session_rejection_reason(left_session) or 'pass'}")
    if right_session is not None:
        lines.append(f"- right guardrail={_profile_session_rejection_reason(right_session) or 'pass'}")
    if review_messages:
        lines.extend(["", "Review notes:"])
        lines.extend(f"- {message}" for message in review_messages)
    return "\n".join(lines)


def _stitch_review_status_markdown(
    *,
    selected_source: str,
    recommended_source: str,
    homography_reference: str,
    review_messages: list[str],
    inlier_payload: dict[str, Any] | None = None,
    show_calibration_inliers: bool = True,
) -> str:
    lines = [
        "### Stitch Review",
        f"- selected source={selected_source}",
        f"- recommended source={recommended_source}",
        f"- homography reference={homography_reference}",
        f"- calibration inliers={'on' if show_calibration_inliers and inlier_payload is not None else 'off'}",
    ]
    if inlier_payload is not None:
        lines.append(f"- inliers={int(inlier_payload.get('inliers_count') or 0)}  ratio={float(inlier_payload.get('inlier_ratio') or 0.0):.3f}")
    else:
        lines.append("- calibration inlier overlay unavailable")
    if review_messages:
        lines.extend(["", "Review notes:"])
        lines.extend(f"- {message}" for message in review_messages)
    return "\n".join(lines)


@dataclass(slots=True)
class CameraUiState:
    slot: str
    frames: list[np.ndarray] = field(default_factory=list)
    lines: list[ManualLineSegment] = field(default_factory=list)
    pending_point: tuple[float, float] | None = None
    selected_index: int | None = None
    replace_index: int | None = None
    saved_profile: DistortionProfile | None = None
    auto_profile: DistortionProfile | None = None
    session_profile: DistortionProfile | None = None
    session_state: dict[str, float] = field(default_factory=dict)
    baseline_name: str = "raw"
    model_name: str = DISTORTION_MODEL_PINHOLE
    preview_scale_x: float = 1.0
    preview_scale_y: float = 1.0

    @property
    def frame(self) -> np.ndarray | None:
        return self.frames[-1] if self.frames else None


class _RuntimePreviewWorker:
    def __init__(self, target: str) -> None:
        self._target = str(target or "").strip()
        self._latest_frame: np.ndarray | None = None
        self._status = "idle"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def latest_frame(self) -> np.ndarray | None:
        return self._latest_frame

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        if not self._target or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        try:
            import cv2  # type: ignore
        except Exception:
            self._status = "opencv unavailable"
            return
        capture: Any | None = None
        while not self._stop_event.is_set():
            if capture is None:
                try:
                    capture = cv2.VideoCapture(self._target, cv2.CAP_FFMPEG)
                except Exception:
                    capture = None
                if capture is None or not capture.isOpened():
                    self._status = "preview connect failed"
                    if capture is not None:
                        capture.release()
                    capture = None
                    time.sleep(1.0)
                    continue
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._status = "preview connected"
            ok, frame = capture.read()
            if not ok or frame is None:
                self._status = "preview waiting"
                capture.release()
                capture = None
                time.sleep(0.25)
                continue
            self._latest_frame = frame
        if capture is not None:
            capture.release()


def _dashboard_markdown(
    *,
    latest_metrics: dict[str, Any],
    recent_events: deque[str],
    runtime_active: bool,
    homography_reference: str,
    review_source: str,
    left_distortion: ResolvedDistortion | None,
    right_distortion: ResolvedDistortion | None,
    stderr_tail: str,
    workflow_summary: str = "",
    inlier_payload: dict[str, Any] | None = None,
    show_calibration_inliers: bool = True,
) -> str:
    lines = [
        "### Runtime Dashboard",
        f"- runtime active: {runtime_active}",
        f"- review source: {review_source}",
        f"- homography reference: {homography_reference}",
    ]
    if runtime_active:
        lines.extend(
            [
                f"- stitch fps={float(latest_metrics.get('stitch_actual_fps') or 0.0):.2f}",
                f"- transmit fps={float(latest_metrics.get('transmit_fps') or 0.0):.2f}",
                f"- pair skew={float(latest_metrics.get('pair_skew_ms_mean') or 0.0):.2f} ms",
                f"- pair source skew={float(latest_metrics.get('pair_source_skew_ms_mean') or 0.0):.2f} ms",
                f"- source time mode={str(latest_metrics.get('source_time_mode') or '')}",
            ]
        )
    lines.append("- distortion=disabled (raw only)")
    if inlier_payload is not None:
        lines.append(
            f"- calibration inliers={'shown' if show_calibration_inliers else 'hidden'} "
            f"count={int(inlier_payload.get('inliers_count') or 0)} "
            f"ratio={float(inlier_payload.get('inlier_ratio') or 0.0):.3f}"
        )
    else:
        lines.append("- calibration inlier overlay unavailable")
    if workflow_summary.strip():
        lines.extend(["", "Session summary:", workflow_summary.strip()])
    if recent_events:
        lines.extend(["", "Recent events:"])
        lines.extend(f"- {event}" for event in list(recent_events)[:10])
    if stderr_tail.strip():
        lines.extend(["", "stderr tail:", "```text", stderr_tail.strip()[-1500:], "```"])
    return "\n".join(lines)


def _render_candidate_preview(
    frame: np.ndarray,
    *,
    slot: str,
    lines: list[ManualLineSegment],
    profile: DistortionProfile | None,
) -> np.ndarray | None:
    if frame is None or frame.size <= 0:
        return None
    corrected = apply_distortion_profile(frame, profile)
    return _bgr_to_rgb(render_distortion_preview(frame, corrected, lines=lines, profile=profile, label=slot.upper()))


def _load_homography_payload(path: str | Path) -> dict[str, Any] | None:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return None
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_calibration_inliers_payload(
    *,
    homography_file: str | Path,
    inliers_file: str | Path = DEFAULT_NATIVE_CALIBRATION_INLIERS_FILE,
) -> dict[str, Any] | None:
    sidecar_path = Path(inliers_file).expanduser()
    if not sidecar_path.exists():
        return None
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    current_homography = _load_homography_payload(homography_file)
    if not current_homography:
        return None
    current_reference = str(current_homography.get("distortion_reference") or "raw").strip().lower()
    payload_reference = str(payload.get("distortion_reference") or "raw").strip().lower()
    if payload_reference != current_reference:
        return None
    try:
        current_matrix = np.asarray(current_homography.get("homography"), dtype=np.float64).reshape(3, 3)
        payload_matrix = np.asarray(payload.get("homography"), dtype=np.float64).reshape(3, 3)
    except Exception:
        return None
    if not np.allclose(current_matrix, payload_matrix, atol=1e-3, rtol=1e-3):
        return None
    return payload


def _project_point(matrix: np.ndarray, x: float, y: float) -> tuple[float, float] | None:
    vec = np.asarray([float(x), float(y), 1.0], dtype=np.float64)
    projected = matrix @ vec
    if abs(float(projected[2])) <= 1e-9:
        return None
    return float(projected[0] / projected[2]), float(projected[1] / projected[2])


def _draw_calibration_inlier_overlay(
    frame: np.ndarray | None,
    *,
    inlier_payload: dict[str, Any] | None,
) -> np.ndarray | None:
    if frame is None or frame.size <= 0 or not inlier_payload:
        return frame
    try:
        import cv2  # type: ignore
    except Exception:
        return frame
    try:
        output_resolution = inlier_payload.get("output_resolution") or [frame.shape[1], frame.shape[0]]
        ref_w = max(1.0, float(output_resolution[0]))
        ref_h = max(1.0, float(output_resolution[1]))
        matrix = np.asarray(inlier_payload.get("homography"), dtype=np.float64).reshape(3, 3)
        left_points = list(inlier_payload.get("left_inlier_points") or [])
        right_points = list(inlier_payload.get("right_inlier_points") or [])
    except Exception:
        return frame
    scale_x = float(frame.shape[1]) / ref_w
    scale_y = float(frame.shape[0]) / ref_h
    overlay = frame.copy()
    pair_count = min(len(left_points), len(right_points))
    if pair_count <= 0:
        return overlay
    if pair_count > 120:
        indices = np.linspace(0, pair_count - 1, num=120, dtype=np.int32).tolist()
    else:
        indices = list(range(pair_count))
    for idx in indices:
        left_point = left_points[idx]
        right_point = right_points[idx]
        if not isinstance(left_point, (list, tuple)) or len(left_point) < 2:
            continue
        if not isinstance(right_point, (list, tuple)) or len(right_point) < 2:
            continue
        projected = _project_point(matrix, float(right_point[0]), float(right_point[1]))
        if projected is None:
            continue
        left_px = int(round(float(left_point[0]) * scale_x))
        left_py = int(round(float(left_point[1]) * scale_y))
        right_px = int(round(projected[0] * scale_x))
        right_py = int(round(projected[1] * scale_y))
        cv2.line(overlay, (left_px, left_py), (right_px, right_py), (60, 220, 255), 1, lineType=cv2.LINE_AA)
        cv2.circle(overlay, (left_px, left_py), 3, (60, 220, 90), -1, lineType=cv2.LINE_AA)
        cv2.circle(overlay, (right_px, right_py), 3, (70, 150, 255), -1, lineType=cv2.LINE_AA)
    return overlay


def _fit_preview_frame(
    frame: np.ndarray | None,
    *,
    max_width: int = 960,
    max_height: int = 640,
) -> tuple[np.ndarray | None, float, float]:
    if frame is None or frame.size <= 0:
        return None, 1.0, 1.0
    height, width = frame.shape[:2]
    scale = min(float(max_width) / float(max(1, width)), float(max_height) / float(max(1, height)), 1.0)
    if scale >= 0.999:
        return frame.copy(), 1.0, 1.0
    try:
        import cv2  # type: ignore
    except Exception:
        return frame.copy(), 1.0, 1.0
    new_width = max(2, int(round(width * scale)))
    new_height = max(2, int(round(height * scale)))
    preview = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
    return preview, float(width) / float(new_width), float(height) / float(new_height)


def _pair_to_label(index: int, left_point: tuple[float, float], right_point: tuple[float, float]) -> str:
    return (
        f"{index + 1}: "
        f"L({int(round(left_point[0]))},{int(round(left_point[1]))}) "
        f"<-> R({int(round(right_point[0]))},{int(round(right_point[1]))})"
    )


def _build_candidate_inlier_payload(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    try:
        return {
            "version": 1,
            "left_resolution": list(result.get("metadata", {}).get("left_resolution") or []),
            "right_resolution": list(result.get("metadata", {}).get("right_resolution") or []),
            "output_resolution": list(result.get("output_resolution") or []),
            "homography": np.asarray(result.get("homography_matrix"), dtype=np.float64).reshape(3, 3).tolist(),
            "distortion_reference": str(result.get("distortion_reference") or "raw"),
            "inliers_count": int(result.get("inliers_count") or 0),
            "inlier_ratio": float(result.get("inlier_ratio") or 0.0),
            "left_inlier_points": [list(point) for point in (result.get("left_inlier_points") or [])],
            "right_inlier_points": [list(point) for point in (result.get("right_inlier_points") or [])],
        }
    except Exception:
        return None


def _compose_simple_stitch_from_payload(
    *,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    homography: np.ndarray | list[list[float]],
    output_resolution: list[int] | tuple[int, int] | None,
    inlier_payload: dict[str, Any] | None = None,
) -> np.ndarray | None:
    try:
        import cv2  # type: ignore
    except Exception:
        return None
    try:
        matrix = np.asarray(homography, dtype=np.float64).reshape(3, 3)
    except Exception:
        return None
    try:
        out_w = int(output_resolution[0]) if output_resolution else int(left_frame.shape[1])
        out_h = int(output_resolution[1]) if output_resolution else int(left_frame.shape[0])
    except Exception:
        out_w = int(left_frame.shape[1])
        out_h = int(left_frame.shape[0])
    out_w = max(out_w, int(left_frame.shape[1]))
    out_h = max(out_h, int(left_frame.shape[0]))
    stitched = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    stitched[: left_frame.shape[0], : left_frame.shape[1]] = left_frame
    warped_right = cv2.warpPerspective(right_frame, matrix, (out_w, out_h))
    right_mask = np.any(warped_right > 0, axis=2)
    left_mask = np.any(stitched > 0, axis=2)
    overlap = right_mask & left_mask
    stitched[right_mask & ~left_mask] = warped_right[right_mask & ~left_mask]
    if np.any(overlap):
        stitched[overlap] = np.clip(
            stitched[overlap].astype(np.float32) * 0.5 + warped_right[overlap].astype(np.float32) * 0.5,
            0,
            255,
        ).astype(np.uint8)
    return _draw_calibration_inlier_overlay(stitched, inlier_payload=inlier_payload)


def _compose_simple_stitch(
    *,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    left_profile: DistortionProfile | None,
    right_profile: DistortionProfile | None,
    homography_file: str | Path,
    inlier_payload: dict[str, Any] | None = None,
) -> np.ndarray | None:
    try:
        import cv2  # type: ignore
    except Exception:
        return None

    payload = _load_homography_payload(homography_file)
    if not payload:
        return None
    matrix_raw = payload.get("homography")
    if not isinstance(matrix_raw, list):
        return None
    try:
        matrix = np.asarray(matrix_raw, dtype=np.float64).reshape(3, 3)
    except Exception:
        return None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    output_resolution = metadata.get("output_resolution") if isinstance(metadata, dict) else None
    try:
        out_w = int(output_resolution[0]) if output_resolution else int(left_frame.shape[1])
        out_h = int(output_resolution[1]) if output_resolution else int(left_frame.shape[0])
    except Exception:
        out_w = int(left_frame.shape[1])
        out_h = int(left_frame.shape[0])
    out_w = max(out_w, int(left_frame.shape[1]))
    out_h = max(out_h, int(left_frame.shape[0]))

    left_corrected = apply_distortion_profile(left_frame, left_profile)
    right_corrected = apply_distortion_profile(right_frame, right_profile)
    stitched = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    stitched[: left_corrected.shape[0], : left_corrected.shape[1]] = left_corrected
    warped_right = cv2.warpPerspective(right_corrected, matrix, (out_w, out_h))
    right_mask = np.any(warped_right > 0, axis=2)
    left_mask = np.any(stitched > 0, axis=2)
    overlap = right_mask & left_mask
    stitched[right_mask & ~left_mask] = warped_right[right_mask & ~left_mask]
    if np.any(overlap):
        stitched[overlap] = np.clip(
            (
                stitched[overlap].astype(np.float32) * 0.5
                + warped_right[overlap].astype(np.float32) * 0.5
            ),
            0,
            255,
        ).astype(np.uint8)
    return _draw_calibration_inlier_overlay(stitched, inlier_payload=inlier_payload)


def _compose_review_canvas(
    *,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    left_profile: DistortionProfile | None,
    right_profile: DistortionProfile | None,
    homography_file: str | Path,
    inlier_payload: dict[str, Any] | None = None,
) -> np.ndarray | None:
    left_preview_bgr = render_distortion_preview(
        left_frame,
        apply_distortion_profile(left_frame, left_profile),
        lines=[],
        profile=left_profile,
        label="LEFT",
    )
    right_preview_bgr = render_distortion_preview(
        right_frame,
        apply_distortion_profile(right_frame, right_profile),
        lines=[],
        profile=right_profile,
        label="RIGHT",
    )
    stacked_rgb = _bgr_to_rgb(np.vstack([left_preview_bgr, right_preview_bgr]))
    stitched = _compose_simple_stitch(
        left_frame=left_frame,
        right_frame=right_frame,
        left_profile=left_profile,
        right_profile=right_profile,
        homography_file=homography_file,
        inlier_payload=inlier_payload,
    )
    stitched_rgb = _bgr_to_rgb(stitched)
    if stacked_rgb is None:
        return stitched_rgb
    if stitched_rgb is None:
        return stacked_rgb
    if stitched_rgb.shape[1] != stacked_rgb.shape[1]:
        try:
            import cv2  # type: ignore
        except Exception:
            return stacked_rgb
        scale = stacked_rgb.shape[1] / float(max(1, stitched_rgb.shape[1]))
        stitched_rgb = cv2.resize(
            stitched_rgb,
            (stacked_rgb.shape[1], max(2, int(round(stitched_rgb.shape[0] * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    pad = np.full((40, stacked_rgb.shape[1], 3), 18, dtype=np.uint8)
    return np.vstack([stacked_rgb, pad, stitched_rgb])


@dataclass(slots=True)
class RuntimeUiSession:
    args: argparse.Namespace
    current_step: str = "start"
    output_standard: str = field(default_factory=default_output_standard)
    run_calibration_first: bool = True
    open_vlc_low_latency: bool = False
    use_saved_as_starting_point: bool = False
    saved_distortion_ready: bool = False
    saved_distortion_summaries: list[str] = field(default_factory=list)
    homography_reference: str = "raw"
    selected_review_source: str = "raw"
    recommended_review_source: str = "raw"
    left: CameraUiState = field(default_factory=lambda: CameraUiState(slot="left"))
    right: CameraUiState = field(default_factory=lambda: CameraUiState(slot="right"))
    review_messages: list[str] = field(default_factory=list)
    runtime_client: RuntimeClient | None = None
    latest_metrics: dict[str, Any] = field(default_factory=dict)
    recent_events: deque[str] = field(default_factory=lambda: deque(maxlen=16))
    hello_payload: dict[str, Any] = field(default_factory=dict)
    runtime_preview_worker: _RuntimePreviewWorker | None = None
    viewer_proc: Any | None = None
    vlc_proc: Any | None = None
    probe_source: str = "disabled"
    probe_target_for_viewer: str = ""
    transmit_target_for_display: str = ""
    homography_messages: list[str] = field(default_factory=list)
    left_distortion_runtime: ResolvedDistortion = field(default_factory=ResolvedDistortion)
    right_distortion_runtime: ResolvedDistortion = field(default_factory=ResolvedDistortion)
    show_calibration_inliers: bool = True
    left_manual_points: list[tuple[float, float]] = field(default_factory=list)
    right_manual_points: list[tuple[float, float]] = field(default_factory=list)
    calibration_pending_side: str = "left"
    calibration_pending_point: tuple[float, float] | None = None
    calibration_selected_pair_index: int | None = None
    calibration_candidate_result: dict[str, Any] | None = None
    calibration_candidate_config: NativeCalibrationConfig | None = None

    def __post_init__(self) -> None:
        self.output_standard = str(getattr(self.args, "output_standard", "") or default_output_standard())
        self.open_vlc_low_latency = bool(getattr(self.args, "open_vlc_low_latency", False))
        self.use_saved_as_starting_point = False
        for _message in _legacy_cli()._normalize_distortion_args(self.args):
            pass
        self.saved_distortion_ready = bool(self.left_saved_path.exists() and self.right_saved_path.exists())
        self.homography_reference = load_homography_distortion_reference(
            str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH)
        )
        legacy = _legacy_cli()
        self.saved_distortion_summaries = legacy._saved_distortion_start_status(self.args)

    def workflow_markdown(self) -> str:
        step_labels = {
            "start": "1. Start",
            "assisted-calibration": "2. Assisted calibration",
            "calibration-review": "3. Calibration review",
            "stitch-review": "4. Stitch review",
            "dashboard": "5. Runtime dashboard",
        }
        return " | ".join(
            [
                f"**{step_labels.get(self.current_step, self.current_step)}**",
                f"homography={self.homography_reference}",
                f"stitch=raw",
                f"inliers={'on' if self.show_calibration_inliers else 'off'}",
            ]
        )

    def dashboard_workflow_markdown(self) -> str:
        lines = [
            f"- current step={self.current_step}",
            f"- homography reference={self.homography_reference}",
            f"- manual pairs={min(len(self.left_manual_points), len(self.right_manual_points))}",
            f"- calibration inliers={'enabled' if self.inlier_overlay_payload() is not None else 'unavailable'}",
        ]
        return "\n".join(lines)

    @property
    def left_hint_path(self) -> Path:
        return Path(DEFAULT_NATIVE_LEFT_DISTORTION_HINTS_FILE).expanduser()

    @property
    def right_hint_path(self) -> Path:
        return Path(DEFAULT_NATIVE_RIGHT_DISTORTION_HINTS_FILE).expanduser()

    @property
    def left_saved_path(self) -> Path:
        return Path(str(getattr(self.args, "left_distortion_file", DEFAULT_NATIVE_LEFT_DISTORTION_FILE) or DEFAULT_NATIVE_LEFT_DISTORTION_FILE)).expanduser()

    @property
    def right_saved_path(self) -> Path:
        return Path(str(getattr(self.args, "right_distortion_file", DEFAULT_NATIVE_RIGHT_DISTORTION_FILE) or DEFAULT_NATIVE_RIGHT_DISTORTION_FILE)).expanduser()

    def _metadata(self) -> dict[str, Any]:
        legacy = _legacy_cli()
        return legacy._runtime_distortion_metadata(self.args)

    def _camera(self, slot: str) -> CameraUiState:
        return self.left if str(slot) == "left" else self.right

    def _camera_paths(self, slot: str) -> tuple[Path, Path]:
        if str(slot) == "left":
            return self.left_hint_path, self.left_saved_path
        return self.right_hint_path, self.right_saved_path

    def prepare_start(
        self,
        output_standard: str,
        run_calibration_first: bool,
        open_vlc_low_latency: bool,
        use_saved_as_starting_point: bool,
    ) -> str:
        self._apply_start_settings(
            output_standard=output_standard,
            run_calibration_first=run_calibration_first,
            open_vlc_low_latency=open_vlc_low_latency,
            use_saved_as_starting_point=use_saved_as_starting_point,
            step="start",
        )
        self._capture_and_load_workflow_assets()
        self.homography_reference, self.homography_messages = _legacy_cli()._ensure_runtime_homography_ready(
            self.args,
            left_distortion=ResolvedDistortion(),
            right_distortion=ResolvedDistortion(),
        )
        return self.start_markdown()

    def prepare_assisted_calibration_start(
        self,
        output_standard: str,
        run_calibration_first: bool,
        open_vlc_low_latency: bool,
        use_saved_as_starting_point: bool,
    ) -> str:
        self._apply_start_settings(
            output_standard=output_standard,
            run_calibration_first=run_calibration_first,
            open_vlc_low_latency=open_vlc_low_latency,
            use_saved_as_starting_point=use_saved_as_starting_point,
            step="assisted-calibration",
        )
        self._capture_and_load_workflow_assets(quick=True)
        self.homography_reference = load_homography_distortion_reference(
            str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH)
        )
        self.homography_messages = []
        self.recent_events.appendleft("Representative frames are ready for assisted calibration.")
        return self.start_markdown()

    def begin_assisted_calibration_start(
        self,
        output_standard: str,
        run_calibration_first: bool,
        open_vlc_low_latency: bool,
        use_saved_as_starting_point: bool,
    ) -> str:
        self._apply_start_settings(
            output_standard=output_standard,
            run_calibration_first=run_calibration_first,
            open_vlc_low_latency=open_vlc_low_latency,
            use_saved_as_starting_point=use_saved_as_starting_point,
            step="assisted-calibration",
        )
        self.homography_reference = load_homography_distortion_reference(
            str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH)
        )
        self.homography_messages = []
        self.recent_events.appendleft("Opening assisted calibration and preparing representative frames...")
        return self.start_markdown()

    def _apply_start_settings(
        self,
        *,
        output_standard: str,
        run_calibration_first: bool,
        open_vlc_low_latency: bool,
        use_saved_as_starting_point: bool,
        step: str,
    ) -> None:
        self.output_standard = str(output_standard or default_output_standard())
        self.run_calibration_first = bool(run_calibration_first)
        self.open_vlc_low_latency = bool(open_vlc_low_latency)
        _ = use_saved_as_starting_point
        self.use_saved_as_starting_point = False
        self.current_step = str(step)
        self.args.output_standard = self.output_standard
        self.args.open_vlc_low_latency = self.open_vlc_low_latency

    def start_markdown(self) -> str:
        summary = self.active_homography_summary()
        lines = [
            "### Start",
            f"- output standard={self.output_standard}",
            f"- run calibration first={self.run_calibration_first}",
            f"- open VLC low-latency transmit={self.open_vlc_low_latency}",
            "- distortion=disabled (raw only)",
            f"- homography reference={summary['distortion_reference']}",
            f"- manual_points_count={summary['manual_points_count']}",
            f"- inliers_count={summary['inliers_count']}",
            f"- inlier_ratio={summary['inlier_ratio']:.3f}",
            f"- mean_reprojection_error={summary['mean_reprojection_error']:.3f}",
            f"- calibration inlier overlay={'available' if summary['inlier_sidecar_ready'] else 'unavailable'}",
            f"- use current homography={'enabled' if summary['launch_ready'] else 'disabled'}",
        ]
        if self.homography_messages:
            lines.extend(["", "Homography preparation:"])
            lines.extend(f"- {message}" for message in self.homography_messages)
        return "\n".join(lines)

    def current_homography_payload(self) -> dict[str, Any] | None:
        return _load_homography_payload(
            str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH)
        )

    def active_homography_summary(self) -> dict[str, Any]:
        payload = self.current_homography_payload() or {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        inlier_payload = self.inlier_overlay_payload()
        distortion_reference = str(
            payload.get("distortion_reference") or metadata.get("distortion_reference") or self.homography_reference or "raw"
        ).strip().lower()
        manual_points_count = int(metadata.get("manual_points_count") or 0)
        inliers_count = int(metadata.get("inliers_count") or (inlier_payload or {}).get("inliers_count") or 0)
        inlier_ratio = float(metadata.get("inlier_ratio") or (inlier_payload or {}).get("inlier_ratio") or 0.0)
        mean_reprojection_error = float(metadata.get("mean_reprojection_error") or 0.0)
        sidecar_ready = inlier_payload is not None
        launch_ready = bool(distortion_reference == "raw" and manual_points_count >= 4 and sidecar_ready)
        return {
            "distortion_reference": distortion_reference,
            "manual_points_count": manual_points_count,
            "inliers_count": inliers_count,
            "inlier_ratio": inlier_ratio,
            "mean_reprojection_error": mean_reprojection_error,
            "inlier_sidecar_ready": sidecar_ready,
            "launch_ready": launch_ready,
        }

    def use_current_homography_enabled(self) -> bool:
        return bool(self.active_homography_summary().get("launch_ready"))

    def _capture_and_load_workflow_assets(self, *, quick: bool = False) -> None:
        input_buffer_frames = int(getattr(self.args, "input_buffer_frames", DEFAULT_NATIVE_INPUT_BUFFER_FRAMES) or DEFAULT_NATIVE_INPUT_BUFFER_FRAMES)
        if quick:
            sample_frames = 1
            warmup_frames = min(10, max(3, input_buffer_frames))
        else:
            sample_frames = min(8, max(5, input_buffer_frames))
            warmup_frames = min(24, max(8, input_buffer_frames * 2))
        transport = str(getattr(self.args, "rtsp_transport", DEFAULT_NATIVE_RTSP_TRANSPORT) or DEFAULT_NATIVE_RTSP_TRANSPORT)
        timeout_sec = float(getattr(self.args, "rtsp_timeout_sec", 10.0) or 10.0)
        effective_timeout_sec = min(timeout_sec, 3.0) if quick else timeout_sec
        self.recent_events.appendleft(
            "Capturing representative frames "
            f"(quick={'on' if quick else 'off'}, warmup={warmup_frames}, samples={sample_frames}, timeout={effective_timeout_sec:.1f}s)."
        )
        self.left.frames = capture_representative_frames(
            str(self.args.left_rtsp),
            transport=transport,
            timeout_sec=effective_timeout_sec,
            warmup_frames=warmup_frames,
            sample_frames=sample_frames,
        )
        self.right.frames = capture_representative_frames(
            str(self.args.right_rtsp),
            transport=transport,
            timeout_sec=effective_timeout_sec,
            warmup_frames=warmup_frames,
            sample_frames=sample_frames,
        )
        self.left.saved_profile = None
        self.right.saved_profile = None
        self.left.lines = []
        self.right.lines = []
        self.left.pending_point = None
        self.right.pending_point = None
        self.left.selected_index = None
        self.right.selected_index = None
        self.left.replace_index = None
        self.right.replace_index = None
        self.left.auto_profile = None
        self.right.auto_profile = None
        self.left.session_profile = None
        self.right.session_profile = None
        self.left.session_state = {}
        self.right.session_state = {}
        self.selected_review_source = "raw"
        self.recommended_review_source = "raw"
        self.review_messages.clear()
        self.left_manual_points = []
        self.right_manual_points = []
        self.calibration_pending_side = "left"
        self.calibration_pending_point = None
        self.calibration_selected_pair_index = None
        self.calibration_candidate_result = None
        self.calibration_candidate_config = None

    def inlier_overlay_payload(self) -> dict[str, Any] | None:
        return _load_calibration_inliers_payload(
            homography_file=str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH),
            inliers_file=DEFAULT_NATIVE_CALIBRATION_INLIERS_FILE,
        )

    def calibration_pair_count(self) -> int:
        return min(len(self.left_manual_points), len(self.right_manual_points))

    def calibration_pair_choices(self) -> list[tuple[str, str]]:
        return [
            (_pair_to_label(index, left_point, right_point), str(index))
            for index, (left_point, right_point) in enumerate(zip(self.left_manual_points, self.right_manual_points))
        ]

    def _render_assisted_pair_preview(self, slot: str) -> np.ndarray | None:
        camera = self._camera(slot)
        frame = camera.frame
        if frame is None or frame.size <= 0:
            return None
        try:
            import cv2  # type: ignore
        except Exception:
            return _bgr_to_rgb(frame)
        preview_bgr, scale_x, scale_y = _fit_preview_frame(frame)
        if preview_bgr is None:
            return None
        camera.preview_scale_x = scale_x
        camera.preview_scale_y = scale_y
        points = self.left_manual_points if slot == "left" else self.right_manual_points
        overlay = preview_bgr.copy()
        for idx, point in enumerate(points):
            px = int(round(float(point[0]) / max(1e-6, scale_x)))
            py = int(round(float(point[1]) / max(1e-6, scale_y)))
            selected = self.calibration_selected_pair_index == idx
            color = (0, 220, 255) if selected else ((60, 220, 90) if slot == "left" else (70, 150, 255))
            radius = 6 if selected else 4
            cv2.circle(overlay, (px, py), radius, color, -1, lineType=cv2.LINE_AA)
            cv2.putText(
                overlay,
                str(idx + 1),
                (px + 7, py - 7),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        if slot == "left" and self.calibration_pending_side == "right" and self.calibration_pending_point is not None:
            pending_x = int(round(float(self.calibration_pending_point[0]) / max(1e-6, scale_x)))
            pending_y = int(round(float(self.calibration_pending_point[1]) / max(1e-6, scale_y)))
            cv2.circle(overlay, (pending_x, pending_y), 8, (255, 220, 60), 2, lineType=cv2.LINE_AA)
        badge = f"{slot.upper()} | click {self.calibration_pending_side.upper()} next"
        cv2.rectangle(overlay, (0, 0), (min(340, overlay.shape[1] - 1), 34), (18, 18, 18), -1)
        cv2.putText(overlay, badge, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (235, 235, 235), 2, cv2.LINE_AA)
        return _bgr_to_rgb(overlay)

    def assisted_calibration_markdown(self) -> str:
        selected_text = (
            str(self.calibration_selected_pair_index + 1)
            if self.calibration_selected_pair_index is not None and 0 <= self.calibration_selected_pair_index < self.calibration_pair_count()
            else "none"
        )
        lines = [
            "### Assisted Calibration",
            "- left click -> right click creates one correspondence pair",
            f"- pending side={self.calibration_pending_side}",
            f"- pair count={self.calibration_pair_count()} / min 4 / recommended 6-10",
            f"- selected pair={selected_text}",
        ]
        if self.calibration_pending_side == "right" and self.calibration_pending_point is not None:
            lines.append(
                f"- pending left point=({int(round(self.calibration_pending_point[0]))}, {int(round(self.calibration_pending_point[1]))})"
            )
        return "\n".join(lines)

    def render_assisted_calibration_state(
        self,
    ) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        left_preview = self._render_assisted_pair_preview("left")
        right_preview = self._render_assisted_pair_preview("right")
        selected = (
            str(self.calibration_selected_pair_index)
            if self.calibration_selected_pair_index is not None and 0 <= self.calibration_selected_pair_index < self.calibration_pair_count()
            else None
        )
        return left_preview, right_preview, self.assisted_calibration_markdown(), self.calibration_pair_choices(), selected

    def _map_click_to_frame(self, slot: str, evt: Any) -> tuple[float, float] | None:
        point = _extract_event_xy(evt)
        camera = self._camera(slot)
        frame = camera.frame
        if point is None or frame is None:
            return None
        frame_h, frame_w = frame.shape[:2]
        mapped_x = float(np.clip(point[0] * camera.preview_scale_x, 0.0, float(max(1, frame_w - 1))))
        mapped_y = float(np.clip(point[1] * camera.preview_scale_y, 0.0, float(max(1, frame_h - 1))))
        return mapped_x, mapped_y

    def add_calibration_click(
        self,
        slot: str,
        evt: Any,
    ) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        mapped = self._map_click_to_frame(slot, evt)
        if mapped is None:
            return self.render_assisted_calibration_state()
        if slot == "left":
            self.calibration_pending_point = mapped
            self.calibration_pending_side = "right"
            return self.render_assisted_calibration_state()
        if self.calibration_pending_side != "right" or self.calibration_pending_point is None:
            return self.render_assisted_calibration_state()
        self.left_manual_points.append((float(self.calibration_pending_point[0]), float(self.calibration_pending_point[1])))
        self.right_manual_points.append((float(mapped[0]), float(mapped[1])))
        self.calibration_pending_point = None
        self.calibration_pending_side = "left"
        self.calibration_selected_pair_index = self.calibration_pair_count() - 1
        return self.render_assisted_calibration_state()

    def select_calibration_pair(
        self,
        selected_value: str | None,
    ) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        if selected_value is None or str(selected_value).strip() == "":
            self.calibration_selected_pair_index = None
        else:
            index = _safe_int(selected_value, default=-1)
            self.calibration_selected_pair_index = index if 0 <= index < self.calibration_pair_count() else None
        return self.render_assisted_calibration_state()

    def undo_last_calibration_pair(
        self,
    ) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        if self.calibration_pending_side == "right" and self.calibration_pending_point is not None:
            self.calibration_pending_point = None
            self.calibration_pending_side = "left"
        elif self.left_manual_points and self.right_manual_points:
            self.left_manual_points.pop()
            self.right_manual_points.pop()
            if self.calibration_selected_pair_index is not None and self.calibration_selected_pair_index >= self.calibration_pair_count():
                self.calibration_selected_pair_index = self.calibration_pair_count() - 1 if self.calibration_pair_count() > 0 else None
        return self.render_assisted_calibration_state()

    def delete_selected_calibration_pair(
        self,
    ) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        if self.calibration_selected_pair_index is not None and 0 <= self.calibration_selected_pair_index < self.calibration_pair_count():
            del self.left_manual_points[self.calibration_selected_pair_index]
            del self.right_manual_points[self.calibration_selected_pair_index]
            if self.calibration_pair_count() <= 0:
                self.calibration_selected_pair_index = None
            else:
                self.calibration_selected_pair_index = min(self.calibration_selected_pair_index, self.calibration_pair_count() - 1)
        return self.render_assisted_calibration_state()

    def clear_calibration_pairs(
        self,
    ) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        self.left_manual_points = []
        self.right_manual_points = []
        self.calibration_pending_side = "left"
        self.calibration_pending_point = None
        self.calibration_selected_pair_index = None
        return self.render_assisted_calibration_state()

    def refresh_calibration_frames(
        self,
    ) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        self._capture_and_load_workflow_assets(quick=True)
        self.current_step = "assisted-calibration"
        return self.render_assisted_calibration_state()

    def _build_assisted_calibration_config(self) -> NativeCalibrationConfig:
        return NativeCalibrationConfig(
            left_rtsp=str(self.args.left_rtsp),
            right_rtsp=str(self.args.right_rtsp),
            output_path=Path(str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH)),
            inliers_output_path=Path(DEFAULT_NATIVE_CALIBRATION_INLIERS_FILE),
            debug_dir=Path(DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR),
            rtsp_transport=str(getattr(self.args, "rtsp_transport", DEFAULT_NATIVE_RTSP_TRANSPORT) or DEFAULT_NATIVE_RTSP_TRANSPORT),
            rtsp_timeout_sec=max(1.0, float(getattr(self.args, "rtsp_timeout_sec", 10.0) or 10.0)),
            warmup_frames=max(1, int(getattr(self.args, "warmup_frames", 45) or 45)),
            process_scale=max(0.1, float(getattr(self.args, "process_scale", 1.0) or 1.0)),
            calibration_mode="assisted",
            assisted_reproj_threshold=max(1.0, float(getattr(self.args, "assisted_reproj_threshold", 12.0) or 12.0)),
            assisted_max_auto_matches=max(0, int(getattr(self.args, "assisted_max_auto_matches", 600) or 600)),
            match_backend=str(getattr(self.args, "match_backend", "classic") or "classic"),
            distortion_mode="off",
            use_saved_distortion=False,
            distortion_auto_save=False,
            left_distortion_file=Path(DEFAULT_NATIVE_LEFT_DISTORTION_FILE),
            right_distortion_file=Path(DEFAULT_NATIVE_RIGHT_DISTORTION_FILE),
            distortion_lens_model_hint=str(getattr(self.args, "distortion_lens_model_hint", DEFAULT_NATIVE_DISTORTION_LENS_MODEL_HINT) or DEFAULT_NATIVE_DISTORTION_LENS_MODEL_HINT),
            distortion_horizontal_fov_deg=(
                float(getattr(self.args, "distortion_horizontal_fov_deg", DEFAULT_NATIVE_DISTORTION_HORIZONTAL_FOV_DEG) or 0.0) or None
            ),
            distortion_vertical_fov_deg=(
                float(getattr(self.args, "distortion_vertical_fov_deg", DEFAULT_NATIVE_DISTORTION_VERTICAL_FOV_DEG) or 0.0) or None
            ),
            distortion_camera_model=str(getattr(self.args, "distortion_camera_model", DEFAULT_NATIVE_DISTORTION_CAMERA_MODEL) or DEFAULT_NATIVE_DISTORTION_CAMERA_MODEL),
            review_required=False,
            min_matches=max(8, int(getattr(self.args, "min_matches", 8) or 8)),
            min_inliers=max(6, int(getattr(self.args, "min_inliers", 6) or 6)),
            ratio_test=float(getattr(self.args, "ratio_test", 0.75) or 0.75),
            ransac_reproj_threshold=float(getattr(self.args, "ransac_thresh", 4.0) or 4.0),
            max_features=max(500, int(getattr(self.args, "max_features", 4000) or 4000)),
        )

    def compute_assisted_calibration_candidate(self) -> tuple[np.ndarray | None, np.ndarray | None, str]:
        if self.left.frame is None or self.right.frame is None:
            raise RuntimeError("representative frames are unavailable")
        if self.calibration_pair_count() < 4:
            raise RuntimeError("at least 4 correspondence pairs are required")
        config = self._build_assisted_calibration_config()
        result = calibrate_native_homography_from_frames(
            config,
            self.left.frame,
            self.right.frame,
            left_points=list(self.left_manual_points),
            right_points=list(self.right_manual_points),
            prompt_for_points=False,
            review_required=False,
            save_outputs=False,
        )
        self.calibration_candidate_result = result
        self.calibration_candidate_config = config
        self.current_step = "calibration-review"
        return self.render_calibration_review()

    def render_calibration_review(self) -> tuple[np.ndarray | None, np.ndarray | None, str]:
        result = self.calibration_candidate_result
        if not isinstance(result, dict):
            return None, None, "### Calibration review unavailable"
        stitched = result.get("stitched_preview_frame")
        inlier_preview = result.get("inlier_preview_frame")
        payload = _build_candidate_inlier_payload(result) if self.show_calibration_inliers else None
        stitched_preview = _bgr_to_rgb(
            _draw_calibration_inlier_overlay(stitched.copy() if isinstance(stitched, np.ndarray) else None, inlier_payload=payload)
        )
        inlier_rgb = _bgr_to_rgb(inlier_preview) if isinstance(inlier_preview, np.ndarray) else None
        lines = [
            "### Calibration Review",
            f"- manual_points_count={int(result.get('manual_points_count') or 0)}",
            f"- inliers_count={int(result.get('inliers_count') or 0)}",
            f"- inlier_ratio={float(result.get('inlier_ratio') or 0.0):.3f}",
            f"- mean_reprojection_error={float(result.get('mean_reprojection_error') or 0.0):.3f}",
            f"- output_resolution={result.get('output_resolution')}",
            f"- homography reference={str(result.get('distortion_reference') or 'raw')}",
        ]
        return stitched_preview, inlier_rgb, "\n".join(lines)

    def accept_calibration_review(self) -> tuple[np.ndarray | None, str]:
        if not isinstance(self.calibration_candidate_result, dict) or self.calibration_candidate_config is None:
            return None, "### No calibration candidate to accept"
        backup_homography_file(Path(self.calibration_candidate_config.output_path))
        save_native_calibration_artifacts(self.calibration_candidate_config, self.calibration_candidate_result)
        self.homography_reference = str(self.calibration_candidate_result.get("distortion_reference") or "raw")
        self.calibration_candidate_result = None
        self.calibration_candidate_config = None
        self.current_step = "stitch-review"
        self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} assisted calibration accepted")
        preview, status = self.prepare_stitch_review()
        return preview, status

    def cancel_calibration_review(self) -> tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]:
        self.calibration_candidate_result = None
        self.calibration_candidate_config = None
        self.current_step = "assisted-calibration"
        return self.render_assisted_calibration_state()

    def line_choices(self, slot: str) -> list[tuple[str, str]]:
        camera = self._camera(slot)
        return [(_line_to_label(index, line), str(index)) for index, line in enumerate(camera.lines)]

    def render_line_hints(self, slot: str) -> tuple[np.ndarray | None, str, list[tuple[str, str]], str | None]:
        camera = self._camera(slot)
        preview = (
            _bgr_to_rgb(
                render_line_hints_preview(
                    camera.frame,
                    camera_slot=slot,
                    lines=camera.lines,
                    pending_point=camera.pending_point,
                    selected_index=camera.selected_index,
                    show_overlay=False,
                    fit_display=False,
                )
            )
            if camera.frame is not None
            else None
        )
        value = str(camera.selected_index) if camera.selected_index is not None and 0 <= camera.selected_index < len(camera.lines) else None
        return (
            preview,
            _distortion_status_markdown(
                slot=slot,
                lines=camera.lines,
                pending_point=camera.pending_point,
                selected_index=camera.selected_index,
                saved_profile=camera.saved_profile,
                auto_profile=camera.auto_profile,
                session_profile=camera.session_profile,
            ),
            self.line_choices(slot),
            value,
        )

    def add_line_click(self, slot: str, evt: Any) -> tuple[np.ndarray | None, str, list[tuple[str, str]], str | None]:
        camera = self._camera(slot)
        point = _extract_event_xy(evt)
        if point is None:
            return self.render_line_hints(slot)
        if camera.frame is not None:
            frame_height, frame_width = camera.frame.shape[:2]
            mapped_point = (
                float(np.clip(point[0], 0.0, float(max(1, frame_width - 1)))),
                float(np.clip(point[1], 0.0, float(max(1, frame_height - 1)))),
            )
        else:
            mapped_point = point
        if camera.pending_point is None:
            camera.pending_point = mapped_point
            return self.render_line_hints(slot)
        candidate = ManualLineSegment(
            start=(float(camera.pending_point[0]), float(camera.pending_point[1])),
            end=(float(mapped_point[0]), float(mapped_point[1])),
        )
        camera.pending_point = None
        if candidate.length < 20.0:
            return self.render_line_hints(slot)
        if camera.replace_index is not None and 0 <= camera.replace_index < len(camera.lines):
            camera.lines[camera.replace_index] = candidate
            camera.selected_index = camera.replace_index
            camera.replace_index = None
        else:
            camera.lines.append(candidate)
            camera.selected_index = len(camera.lines) - 1
        return self.render_line_hints(slot)

    def select_line(self, slot: str, selected_value: str | None) -> tuple[np.ndarray | None, str, list[tuple[str, str]], str | None]:
        camera = self._camera(slot)
        if selected_value is None or str(selected_value).strip() == "":
            camera.selected_index = None
        else:
            camera.selected_index = _safe_int(selected_value, default=-1)
            if camera.selected_index < 0 or camera.selected_index >= len(camera.lines):
                camera.selected_index = None
        camera.replace_index = None
        return self.render_line_hints(slot)

    def undo_line(self, slot: str) -> tuple[np.ndarray | None, str, list[tuple[str, str]], str | None]:
        camera = self._camera(slot)
        if camera.pending_point is not None:
            camera.pending_point = None
        elif camera.lines:
            camera.lines.pop()
            if camera.selected_index is not None and camera.selected_index >= len(camera.lines):
                camera.selected_index = len(camera.lines) - 1 if camera.lines else None
        camera.replace_index = None
        return self.render_line_hints(slot)

    def delete_selected_line(self, slot: str) -> tuple[np.ndarray | None, str, list[tuple[str, str]], str | None]:
        camera = self._camera(slot)
        if camera.selected_index is not None and 0 <= camera.selected_index < len(camera.lines):
            camera.lines.pop(camera.selected_index)
            if camera.lines:
                camera.selected_index = min(camera.selected_index, len(camera.lines) - 1)
            else:
                camera.selected_index = None
        camera.pending_point = None
        camera.replace_index = None
        return self.render_line_hints(slot)

    def clear_lines(self, slot: str) -> tuple[np.ndarray | None, str, list[tuple[str, str]], str | None]:
        camera = self._camera(slot)
        camera.lines = []
        camera.pending_point = None
        camera.selected_index = None
        camera.replace_index = None
        return self.render_line_hints(slot)

    def replace_selected_line(self, slot: str) -> tuple[np.ndarray | None, str, list[tuple[str, str]], str | None]:
        camera = self._camera(slot)
        if camera.selected_index is not None and 0 <= camera.selected_index < len(camera.lines):
            camera.replace_index = camera.selected_index
            camera.pending_point = None
        return self.render_line_hints(slot)

    def _baseline_profile(self, camera: CameraUiState, *, baseline_name: str, model_name: str) -> DistortionProfile:
        metadata = self._metadata()
        if camera.frame is None:
            raise RuntimeError(f"{camera.slot} frame unavailable")
        if baseline_name == "saved" and camera.saved_profile is not None:
            saved_state = distortion_profile_to_state(
                camera.saved_profile,
                model=model_name,
                width=int(camera.frame.shape[1]),
                height=int(camera.frame.shape[0]),
                lens_model_hint=str(metadata["lens_model_hint"]),
                horizontal_fov_deg=metadata["horizontal_fov_deg"],
                vertical_fov_deg=metadata["vertical_fov_deg"],
                camera_model=str(metadata["camera_model"]),
            )
            return distortion_profile_from_state(
                camera_slot=camera.slot,
                model=model_name,
                width=int(camera.frame.shape[1]),
                height=int(camera.frame.shape[0]),
                state=saved_state,
                source="saved",
                lens_model_hint=str(metadata["lens_model_hint"]),
                horizontal_fov_deg=metadata["horizontal_fov_deg"],
                vertical_fov_deg=metadata["vertical_fov_deg"],
                camera_model=str(metadata["camera_model"]),
            )
        if baseline_name == "guided" and camera.auto_profile is not None:
            guided_state = distortion_profile_to_state(
                camera.auto_profile,
                model=model_name,
                width=int(camera.frame.shape[1]),
                height=int(camera.frame.shape[0]),
                lens_model_hint=str(metadata["lens_model_hint"]),
                horizontal_fov_deg=metadata["horizontal_fov_deg"],
                vertical_fov_deg=metadata["vertical_fov_deg"],
                camera_model=str(metadata["camera_model"]),
            )
            return distortion_profile_from_state(
                camera_slot=camera.slot,
                model=model_name,
                width=int(camera.frame.shape[1]),
                height=int(camera.frame.shape[0]),
                state=guided_state,
                source="guided",
                lens_model_hint=str(metadata["lens_model_hint"]),
                horizontal_fov_deg=metadata["horizontal_fov_deg"],
                vertical_fov_deg=metadata["vertical_fov_deg"],
                camera_model=str(metadata["camera_model"]),
            )
        raw_state = distortion_profile_to_state(
            None,
            model=model_name,
            width=int(camera.frame.shape[1]),
            height=int(camera.frame.shape[0]),
            lens_model_hint=str(metadata["lens_model_hint"]),
            horizontal_fov_deg=metadata["horizontal_fov_deg"],
            vertical_fov_deg=metadata["vertical_fov_deg"],
            camera_model=str(metadata["camera_model"]),
        )
        return distortion_profile_from_state(
            camera_slot=camera.slot,
            model=model_name,
            width=int(camera.frame.shape[1]),
            height=int(camera.frame.shape[0]),
            state=raw_state,
            source="raw",
            lens_model_hint=str(metadata["lens_model_hint"]),
            horizontal_fov_deg=metadata["horizontal_fov_deg"],
            vertical_fov_deg=metadata["vertical_fov_deg"],
            camera_model=str(metadata["camera_model"]),
        )

    def _evaluate_camera_state(
        self,
        slot: str,
        *,
        baseline_name: str,
        model_name: str,
        state: dict[str, float],
    ) -> DistortionProfile | None:
        camera = self._camera(slot)
        if camera.frame is None:
            return None
        metadata = self._metadata()
        candidate = distortion_profile_from_state(
            camera_slot=slot,
            model=model_name,
            width=int(camera.frame.shape[1]),
            height=int(camera.frame.shape[0]),
            state=state,
            source="session-candidate",
            lens_model_hint=str(metadata["lens_model_hint"]),
            horizontal_fov_deg=metadata["horizontal_fov_deg"],
            vertical_fov_deg=metadata["vertical_fov_deg"],
            camera_model=str(metadata["camera_model"]),
        )
        metrics = evaluate_profile_against_guided_lines(
            camera.frames,
            camera.lines,
            profile=candidate,
            lens_model_hint=str(metadata["lens_model_hint"]),
            horizontal_fov_deg=metadata["horizontal_fov_deg"],
            vertical_fov_deg=metadata["vertical_fov_deg"],
            camera_model=str(metadata["camera_model"]),
        )
        improvement = 0.0
        if metrics:
            improvement = float(metrics.get("raw_straightness_residual") or 0.0) - float(metrics.get("straightness_residual") or 0.0)
        updated = apply_profile_metrics(
            candidate,
            metrics=metrics,
            chosen_model_reason=f"baseline={baseline_name} model={model_name} improvement={improvement:.4f}",
        )
        updated.candidate_rejected_reason = _profile_session_rejection_reason(updated)
        return updated

    def _seed_camera_tuning(self, slot: str) -> None:
        camera = self._camera(slot)
        if camera.frame is None or len(camera.lines) < MIN_MANUAL_LINE_COUNT:
            return
        metadata = self._metadata()
        camera.auto_profile = estimate_manual_guided_distortion(
            camera.frames,
            camera.slot,
            camera.lines,
            lens_model_hint=str(metadata["lens_model_hint"]),
            horizontal_fov_deg=metadata["horizontal_fov_deg"],
            vertical_fov_deg=metadata["vertical_fov_deg"],
            camera_model=str(metadata["camera_model"]),
        )
        seed_profile = camera.auto_profile or self._baseline_profile(camera, baseline_name="raw", model_name=DISTORTION_MODEL_PINHOLE)
        camera.baseline_name = "raw"
        camera.model_name = DISTORTION_MODEL_PINHOLE
        camera.session_state = distortion_profile_to_state(
            seed_profile,
            model=DISTORTION_MODEL_PINHOLE,
            width=int(camera.frame.shape[1]),
            height=int(camera.frame.shape[0]),
            lens_model_hint=str(metadata["lens_model_hint"]),
            horizontal_fov_deg=metadata["horizontal_fov_deg"],
            vertical_fov_deg=metadata["vertical_fov_deg"],
            camera_model=str(metadata["camera_model"]),
        )
        camera.session_profile = self._evaluate_camera_state(
            slot,
            baseline_name=camera.baseline_name,
            model_name=camera.model_name,
            state=camera.session_state,
        )

    def _recommended_source_for_camera(self, slot: str) -> str:
        camera = self._camera(slot)
        return _camera_recommendation_status(saved_profile=camera.saved_profile, session_profile=camera.session_profile)

    def _session_candidate_ready(self) -> bool:
        return _profile_passes_session_gate(self.left.session_profile) and _profile_passes_session_gate(self.right.session_profile)

    def _review_selection_choices(self) -> list[str]:
        choices = ["raw"]
        if self._session_candidate_ready():
            choices.append("session-candidate")
        return choices

    def _recommend_review_source(self) -> tuple[str, list[str]]:
        messages: list[str] = []
        left_recommendation = self._recommended_source_for_camera("left")
        right_recommendation = self._recommended_source_for_camera("right")
        if left_recommendation != "session-candidate":
            messages.append(f"left stayed on raw: {_profile_session_rejection_reason(self.left.session_profile)}")
        if right_recommendation != "session-candidate":
            messages.append(f"right stayed on raw: {_profile_session_rejection_reason(self.right.session_profile)}")
        if left_recommendation == "session-candidate" and right_recommendation == "session-candidate":
            return "session-candidate", messages
        return "raw", messages

    def commit_line_hints(self, slot: str) -> tuple[np.ndarray | None, str, str, str, float, float, float, float, float, float, float, float, float, float]:
        camera = self._camera(slot)
        if camera.frame is None or len(camera.lines) < MIN_MANUAL_LINE_COUNT:
            preview, status, _, _ = self.render_line_hints(slot)
            return preview, status, camera.baseline_name, DISTORTION_MODEL_PINHOLE, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        hint_path, _ = self._camera_paths(slot)
        save_line_hints(
            hint_path,
            camera_slot=slot,
            image_size=(int(camera.frame.shape[1]), int(camera.frame.shape[0])),
            lines=list(camera.lines),
        )
        self._seed_camera_tuning(slot)
        self.current_step = "left-tuning" if slot == "left" else "right-tuning"
        return self.render_tuning(slot)

    def update_tuning(
        self,
        slot: str,
        baseline_name: str,
        model_choice: str,
        fx: float,
        fy: float,
        cx: float,
        cy: float,
        k1: float,
        k2: float,
        k3: float,
        p1: float,
        p2: float,
        k4: float,
    ) -> tuple[np.ndarray | None, str]:
        camera = self._camera(slot)
        if camera.frame is None:
            return None, f"### {slot.upper()} tuning unavailable"
        model_name = DISTORTION_MODEL_PINHOLE if str(model_choice) == "auto" else str(model_choice)
        camera.baseline_name = str(baseline_name)
        camera.model_name = model_name
        specs = distortion_parameter_specs(model_name, int(camera.frame.shape[1]), int(camera.frame.shape[0]))
        state = {
            "fx": float(np.clip(_safe_float(fx), specs["fx"][0], specs["fx"][1])),
            "fy": float(np.clip(_safe_float(fy), specs["fy"][0], specs["fy"][1])),
            "cx": float(np.clip(_safe_float(cx), specs["cx"][0], specs["cx"][1])),
            "cy": float(np.clip(_safe_float(cy), specs["cy"][0], specs["cy"][1])),
            "k1": float(np.clip(_safe_float(k1), specs["k1"][0], specs["k1"][1])),
            "k2": float(np.clip(_safe_float(k2), specs["k2"][0], specs["k2"][1])),
            "k3": float(np.clip(_safe_float(k3), specs["k3"][0], specs["k3"][1])),
            "p1": float(np.clip(_safe_float(p1), specs["p1"][0], specs["p1"][1])),
            "p2": float(np.clip(_safe_float(p2), specs["p2"][0], specs["p2"][1])),
            "k4": float(np.clip(_safe_float(k4), specs["k4"][0], specs["k4"][1])),
        }
        camera.session_state = state
        camera.session_profile = self._evaluate_camera_state(slot, baseline_name=camera.baseline_name, model_name=model_name, state=state)
        return (
            _render_candidate_preview(camera.frame, slot=slot, lines=camera.lines, profile=camera.session_profile),
            _camera_tuning_markdown(
                slot=slot,
                baseline_name=camera.baseline_name,
                model_name=model_name,
                profile=camera.session_profile,
                saved_profile=camera.saved_profile,
                auto_profile=camera.auto_profile,
            ),
        )

    def reset_tuning_from_baseline(
        self,
        slot: str,
        baseline_name: str,
        model_choice: str,
    ) -> tuple[np.ndarray | None, str, str, str, float, float, float, float, float, float, float, float, float, float]:
        camera = self._camera(slot)
        model_name = DISTORTION_MODEL_PINHOLE if str(model_choice) == "auto" else str(model_choice)
        baseline_profile = self._baseline_profile(camera, baseline_name=str(baseline_name), model_name=model_name)
        metadata = self._metadata()
        camera.baseline_name = str(baseline_name)
        camera.model_name = model_name
        camera.session_state = distortion_profile_to_state(
            baseline_profile,
            model=model_name,
            width=int(camera.frame.shape[1]),
            height=int(camera.frame.shape[0]),
            lens_model_hint=str(metadata["lens_model_hint"]),
            horizontal_fov_deg=metadata["horizontal_fov_deg"],
            vertical_fov_deg=metadata["vertical_fov_deg"],
            camera_model=str(metadata["camera_model"]),
        )
        camera.session_profile = self._evaluate_camera_state(
            slot,
            baseline_name=camera.baseline_name,
            model_name=camera.model_name,
            state=camera.session_state,
        )
        return self.render_tuning(slot)

    def render_tuning(self, slot: str) -> tuple[np.ndarray | None, str, str, str, float, float, float, float, float, float, float, float, float, float]:
        camera = self._camera(slot)
        if camera.session_profile is None:
            self._seed_camera_tuning(slot)
        if camera.frame is None or camera.session_profile is None:
            return None, f"### {slot.upper()} tuning unavailable", "raw", DISTORTION_MODEL_PINHOLE, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        state = dict(camera.session_state)
        return (
            _render_candidate_preview(camera.frame, slot=slot, lines=camera.lines, profile=camera.session_profile),
            _camera_tuning_markdown(
                slot=slot,
                baseline_name=camera.baseline_name,
                model_name=camera.model_name,
                profile=camera.session_profile,
                saved_profile=camera.saved_profile,
                auto_profile=camera.auto_profile,
            ),
            str(camera.baseline_name),
            str(camera.model_name),
            float(state.get("fx", 0.0)),
            float(state.get("fy", 0.0)),
            float(state.get("cx", 0.0)),
            float(state.get("cy", 0.0)),
            float(state.get("k1", 0.0)),
            float(state.get("k2", 0.0)),
            float(state.get("k3", 0.0)),
            float(state.get("p1", 0.0)),
            float(state.get("p2", 0.0)),
            float(state.get("k4", 0.0)),
        )

    def prepare_distortion_review(self) -> tuple[np.ndarray | None, str, list[str], str]:
        if self.left.frame is None or self.right.frame is None:
            return None, "### Distortion review unavailable", ["raw"], "raw"
        self.current_step = "distortion-review"
        self.recommended_review_source, self.review_messages = self._recommend_review_source()
        choices = self._review_selection_choices()
        self.selected_review_source = self.recommended_review_source if self.recommended_review_source in choices else choices[0]
        return (
            self.render_distortion_review_preview(self.selected_review_source),
            _distortion_review_status_markdown(
                selected_source=self.selected_review_source,
                recommended_source=self.recommended_review_source,
                left_session=self.left.session_profile,
                right_session=self.right.session_profile,
                review_messages=self.review_messages,
            ),
            choices,
            self.selected_review_source,
        )

    def render_distortion_review_preview(self, source: str) -> np.ndarray | None:
        if self.left.frame is None or self.right.frame is None:
            return None
        left_profile = self.left.session_profile if source == "session-candidate" else None
        right_profile = self.right.session_profile if source == "session-candidate" else None
        left_preview_bgr = render_distortion_preview(
            self.left.frame,
            apply_distortion_profile(self.left.frame, left_profile),
            lines=self.left.lines,
            profile=left_profile,
            label="LEFT",
        )
        right_preview_bgr = render_distortion_preview(
            self.right.frame,
            apply_distortion_profile(self.right.frame, right_profile),
            lines=self.right.lines,
            profile=right_profile,
            label="RIGHT",
        )
        return _bgr_to_rgb(np.vstack([left_preview_bgr, right_preview_bgr]))

    def update_distortion_review_source(self, source: str) -> tuple[np.ndarray | None, str]:
        self.selected_review_source = str(source or "raw")
        return (
            self.render_distortion_review_preview(self.selected_review_source),
            _distortion_review_status_markdown(
                selected_source=self.selected_review_source,
                recommended_source=self.recommended_review_source,
                left_session=self.left.session_profile,
                right_session=self.right.session_profile,
                review_messages=self.review_messages,
            ),
        )

    def prepare_stitch_review(self) -> tuple[np.ndarray | None, str]:
        if self.left.frame is None or self.right.frame is None:
            return None, "### Stitch review unavailable"
        self.current_step = "stitch-review"
        return self.render_stitch_review_preview(), self.stitch_review_markdown()

    def render_stitch_review_preview(self) -> np.ndarray | None:
        if self.left.frame is None or self.right.frame is None:
            return None
        return _compose_simple_stitch(
            left_frame=self.left.frame,
            right_frame=self.right.frame,
            left_profile=None,
            right_profile=None,
            homography_file=str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH),
            inlier_payload=self.inlier_overlay_payload() if self.show_calibration_inliers else None,
        )

    def stitch_review_markdown(self) -> str:
        summary = self.active_homography_summary()
        lines = [
            "### Stitch Review",
            f"- homography reference={summary['distortion_reference']}",
            f"- manual_points_count={summary['manual_points_count']}",
            f"- inliers_count={summary['inliers_count']}",
            f"- inlier_ratio={summary['inlier_ratio']:.3f}",
            f"- mean_reprojection_error={summary['mean_reprojection_error']:.3f}",
            f"- launch ready={'yes' if summary['launch_ready'] else 'no'}",
            f"- calibration inliers={'on' if self.show_calibration_inliers and summary['inlier_sidecar_ready'] else 'off'}",
        ]
        if not summary["launch_ready"]:
            lines.append("- assisted calibration is required before runtime launch")
        return "\n".join(lines)

    def update_show_calibration_inliers(self, value: bool) -> None:
        self.show_calibration_inliers = bool(value)

    def _resolved_for_source(self, source: str) -> tuple[ResolvedDistortion, ResolvedDistortion]:
        _ = source
        return ResolvedDistortion(source="raw", status_message="raw selected"), ResolvedDistortion(source="raw", status_message="raw selected")

    def _recalibrate_homography_if_needed(
        self,
        *,
        left_distortion: ResolvedDistortion,
        right_distortion: ResolvedDistortion,
        force: bool,
    ) -> tuple[str, list[str]]:
        _ = left_distortion, right_distortion, force
        legacy = _legacy_cli()
        return legacy._ensure_runtime_homography_ready(
            self.args,
            left_distortion=ResolvedDistortion(),
            right_distortion=ResolvedDistortion(),
        )

    def _build_runtime_launch_spec(
        self,
        *,
        left_distortion: ResolvedDistortion,
        right_distortion: ResolvedDistortion,
    ) -> RuntimeLaunchSpec:
        legacy = _legacy_cli()
        probe_output, probe_explicit = legacy._resolve_output_role(self.args, alias_prefix="probe_output", legacy_prefix="output")
        transmit_output, transmit_explicit = legacy._resolve_output_role(self.args, alias_prefix="transmit_output", legacy_prefix="production_output")
        if str(self.output_standard or "").strip():
            preset = get_output_preset(str(self.output_standard))
            transmit_output = legacy._apply_output_preset(transmit_output, preset, preserve_existing=transmit_explicit)
            self.args.stitch_output_scale = float(preset.output_scale)
            self.args.sync_pair_mode = preset.sync_pair_mode
            self.args.allow_frame_reuse = bool(preset.allow_frame_reuse)
            self.args.sync_match_max_delta_ms = float(preset.sync_match_max_delta_ms)
        probe_output = legacy._inherit_probe_profile_from_transmit(
            probe_output,
            probe_explicit=probe_explicit,
            transmit_config=transmit_output,
        )
        self.probe_source = legacy._resolve_probe_source(self.args, probe_config=probe_output, transmit_config=transmit_output)
        self.probe_target_for_viewer = str(probe_output.get("target") or legacy.DEFAULT_PROBE_TARGET)
        self.transmit_target_for_display = str(transmit_output.get("target") or "")
        launch_probe_output = dict(probe_output)
        launch_transmit_output = dict(transmit_output)
        if self.probe_source == "transmit":
            launch_transmit_output = legacy._build_mirrored_transmit_output(transmit_output, probe_target=self.probe_target_for_viewer)
            launch_probe_output["runtime"] = "none"
            launch_probe_output["target"] = ""
        return RuntimeLaunchSpec(
            emit_hello=True,
            once=False,
            heartbeat_ms=max(100, int(self.args.heartbeat_ms)),
            left_rtsp=self.args.left_rtsp,
            right_rtsp=self.args.right_rtsp,
            input_runtime=self.args.input_runtime,
            ffmpeg_bin=str(self.args.ffmpeg_bin or ""),
            homography_file=str(self.args.homography_file or ""),
            distortion_mode="off",
            use_saved_distortion=False,
            distortion_auto_save=False,
            left_distortion_file="",
            right_distortion_file="",
            left_distortion_source_hint="off",
            right_distortion_source_hint="off",
            distortion_lens_model_hint=str(getattr(self.args, "distortion_lens_model_hint", DEFAULT_NATIVE_DISTORTION_LENS_MODEL_HINT) or DEFAULT_NATIVE_DISTORTION_LENS_MODEL_HINT),
            distortion_horizontal_fov_deg=float(getattr(self.args, "distortion_horizontal_fov_deg", DEFAULT_NATIVE_DISTORTION_HORIZONTAL_FOV_DEG) or 0.0),
            distortion_vertical_fov_deg=float(getattr(self.args, "distortion_vertical_fov_deg", DEFAULT_NATIVE_DISTORTION_VERTICAL_FOV_DEG) or 0.0),
            distortion_camera_model=str(getattr(self.args, "distortion_camera_model", DEFAULT_NATIVE_DISTORTION_CAMERA_MODEL) or DEFAULT_NATIVE_DISTORTION_CAMERA_MODEL),
            transport=self.args.rtsp_transport,
            input_buffer_frames=max(1, int(self.args.input_buffer_frames)),
            video_codec="h264",
            timeout_sec=max(0.1, float(self.args.rtsp_timeout_sec)),
            reconnect_cooldown_sec=max(0.1, float(self.args.reconnect_cooldown_sec)),
            output_runtime=str(launch_probe_output["runtime"] or "none"),
            output_profile=str(self.args.output_profile or "inspection"),
            output_target=str(launch_probe_output["target"] or ""),
            output_codec=str(launch_probe_output["codec"] or ""),
            output_bitrate=str(launch_probe_output["bitrate"] or ""),
            output_preset=str(launch_probe_output["preset"] or ""),
            output_muxer=str(launch_probe_output["muxer"] or ""),
            output_width=max(0, int(launch_probe_output["width"] or 0)),
            output_height=max(0, int(launch_probe_output["height"] or 0)),
            output_fps=max(0.0, float(launch_probe_output["fps"] or 0.0)),
            production_output_runtime=str(launch_transmit_output["runtime"] or "none"),
            production_output_profile=str(self.args.production_output_profile or "production-compatible"),
            production_output_target=str(launch_transmit_output["target"] or ""),
            production_output_codec=str(launch_transmit_output["codec"] or ""),
            production_output_bitrate=str(launch_transmit_output["bitrate"] or ""),
            production_output_preset=str(launch_transmit_output["preset"] or ""),
            production_output_muxer=str(launch_transmit_output["muxer"] or ""),
            production_output_width=max(0, int(launch_transmit_output["width"] or 0)),
            production_output_height=max(0, int(launch_transmit_output["height"] or 0)),
            production_output_fps=max(0.0, float(launch_transmit_output["fps"] or 0.0)),
            sync_pair_mode=str(self.args.sync_pair_mode),
            allow_frame_reuse=bool(self.args.allow_frame_reuse),
            pair_reuse_max_age_ms=max(1.0, float(self.args.pair_reuse_max_age_ms)),
            pair_reuse_max_consecutive=max(1, int(self.args.pair_reuse_max_consecutive)),
            sync_time_source=str(self.args.sync_time_source),
            sync_match_max_delta_ms=max(1.0, float(self.args.sync_match_max_delta_ms)),
            sync_manual_offset_ms=float(self.args.sync_manual_offset_ms),
            sync_auto_offset_window_sec=max(1.0, float(self.args.sync_auto_offset_window_sec)),
            sync_auto_offset_max_search_ms=max(0.0, float(self.args.sync_auto_offset_max_search_ms)),
            sync_recalibration_interval_sec=max(1.0, float(self.args.sync_recalibration_interval_sec)),
            sync_recalibration_trigger_skew_ms=max(0.0, float(self.args.sync_recalibration_trigger_skew_ms)),
            sync_recalibration_trigger_wait_ratio=max(0.0, min(1.0, float(self.args.sync_recalibration_trigger_wait_ratio))),
            sync_auto_offset_confidence_min=max(0.0, min(1.0, float(self.args.sync_auto_offset_confidence_min))),
            stitch_output_scale=max(0.1, float(self.args.stitch_output_scale)),
            stitch_every_n=max(1, int(self.args.stitch_every_n)),
            gpu_mode=str(self.args.gpu_mode),
            gpu_device=max(0, int(self.args.gpu_device)),
            headless_benchmark=bool(self.args.headless_benchmark),
        )

    def _corrected_thumbnail(self, slot: str) -> np.ndarray | None:
        camera = self._camera(slot)
        if camera.frame is None:
            return None
        return _bgr_to_rgb(camera.frame)

    def dashboard_snapshot(self) -> tuple[str, np.ndarray | None, np.ndarray | None, np.ndarray | None, str]:
        preview = None
        if self.runtime_preview_worker is not None:
            preview_frame = self.runtime_preview_worker.latest_frame
            preview = _bgr_to_rgb(
                _draw_calibration_inlier_overlay(
                    preview_frame.copy() if preview_frame is not None else None,
                    inlier_payload=self.inlier_overlay_payload() if self.show_calibration_inliers else None,
                )
            )
        if preview is None and self.left.frame is not None and self.right.frame is not None:
            preview = _bgr_to_rgb(
                _compose_simple_stitch(
                    left_frame=self.left.frame,
                    right_frame=self.right.frame,
                    left_profile=None,
                    right_profile=None,
                    homography_file=str(getattr(self.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH),
                    inlier_payload=self.inlier_overlay_payload() if self.show_calibration_inliers else None,
                )
            )
        stderr_tail = self.runtime_client.get_stderr_tail() if self.runtime_client is not None else ""
        return (
            _dashboard_markdown(
                latest_metrics=self.latest_metrics,
                recent_events=self.recent_events,
                runtime_active=self.runtime_client is not None and self.runtime_client.process.poll() is None,
                homography_reference=self.homography_reference,
                review_source=self.selected_review_source,
                left_distortion=self.left_distortion_runtime,
                right_distortion=self.right_distortion_runtime,
                stderr_tail=stderr_tail,
                workflow_summary=self.dashboard_workflow_markdown(),
                inlier_payload=self.inlier_overlay_payload(),
                show_calibration_inliers=self.show_calibration_inliers,
            ),
            preview,
            self._corrected_thumbnail("left"),
            self._corrected_thumbnail("right"),
            "\n".join(self.recent_events),
        )

    def launch_runtime(self) -> tuple[str, np.ndarray | None, np.ndarray | None, np.ndarray | None, str]:
        if not self.use_current_homography_enabled():
            self.current_step = "stitch-review"
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} runtime launch blocked: assisted calibration required")
            return self.dashboard_snapshot()
        self.current_step = "dashboard"
        self.left_distortion_runtime = ResolvedDistortion(source="off", status_message="distortion disabled")
        self.right_distortion_runtime = ResolvedDistortion(source="off", status_message="distortion disabled")
        self.homography_reference, self.homography_messages = _legacy_cli()._ensure_runtime_homography_ready(
            self.args,
            left_distortion=ResolvedDistortion(),
            right_distortion=ResolvedDistortion(),
        )
        spec = self._build_runtime_launch_spec(
            left_distortion=self.left_distortion_runtime,
            right_distortion=self.right_distortion_runtime,
        )
        self.runtime_client = RuntimeClient.launch(spec)
        try:
            hello = self.runtime_client.wait_for_hello(timeout_sec=5.0)
        except Exception as exc:
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} runtime hello failed: {type(exc).__name__}: {exc}")
            self.hello_payload = {}
        else:
            self.hello_payload = dict(hello.payload)
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} runtime hello received")
        if self.probe_source != "disabled" and self.probe_target_for_viewer.strip():
            self.runtime_preview_worker = _RuntimePreviewWorker(self.probe_target_for_viewer)
            self.runtime_preview_worker.start()
        return self.dashboard_snapshot()

    def _maybe_launch_vlc(self) -> None:
        if not self.open_vlc_low_latency or self.runtime_client is None or self.vlc_proc is not None:
            return
        if not bool(self.latest_metrics.get("transmit_active")) or int(self.latest_metrics.get("transmit_frames_written") or 0) < 8:
            return
        target = str(getattr(self.args, "vlc_target", "") or self.transmit_target_for_display or "")
        if not target.strip():
            return
        try:
            self.vlc_proc = launch_final_stream_viewer(
                FinalStreamViewerSpec(
                    target=target,
                    ffmpeg_bin=str(getattr(self.args, "ffmpeg_bin", "") or ""),
                    backend="vlc-low-latency",
                    window_title="Hogak Transmit VLC",
                    width=int(self.latest_metrics.get("transmit_width") or 0),
                    height=int(self.latest_metrics.get("transmit_height") or 0),
                    fps=float(self.latest_metrics.get("transmit_written_fps") or 0.0),
                )
            )
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} vlc launched")
        except Exception as exc:
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} vlc launch failed: {exc}")

    def poll_runtime(self) -> tuple[str, np.ndarray | None, np.ndarray | None, np.ndarray | None, str]:
        legacy = _legacy_cli()
        if self.runtime_client is None:
            return self.dashboard_snapshot()
        while True:
            event = self.runtime_client.read_event(timeout_sec=0.01)
            if event is None:
                break
            if event.type == "metrics":
                self.latest_metrics = legacy._decorate_pipeline_metrics(
                    event.payload,
                    probe_source=self.probe_source,
                    probe_target=self.probe_target_for_viewer,
                    transmit_target=self.transmit_target_for_display,
                )
                self.recent_events.appendleft(
                    f"{time.strftime('%H:%M:%S')} fps={float(self.latest_metrics.get('stitch_actual_fps') or 0.0):.2f} "
                    f"pair={float(self.latest_metrics.get('pair_skew_ms_mean') or 0.0):.2f}ms"
                )
                self._maybe_launch_vlc()
            elif event.type == "hello":
                self.hello_payload = dict(event.payload)
            else:
                self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} event={event.type}")
        return self.dashboard_snapshot()

    def open_external_viewer(self) -> tuple[str, np.ndarray | None, np.ndarray | None, np.ndarray | None, str]:
        target = str(getattr(self.args, "viewer_target", "") or self.probe_target_for_viewer or "")
        if not target.strip():
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} external viewer unavailable: probe target missing")
            return self.dashboard_snapshot()
        try:
            self.viewer_proc = launch_final_stream_viewer(
                FinalStreamViewerSpec(
                    target=target,
                    ffmpeg_bin=str(getattr(self.args, "ffmpeg_bin", "") or ""),
                    backend=str(getattr(self.args, "viewer_backend", "auto") or "auto"),
                    window_title=str(getattr(self.args, "viewer_title", "Hogak Probe Viewer")),
                    width=int(self.latest_metrics.get("probe_width") or 0),
                    height=int(self.latest_metrics.get("probe_height") or 0),
                    fps=float(self.latest_metrics.get("probe_written_fps") or 0.0),
                )
            )
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} external viewer launched")
        except Exception as exc:
            self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} external viewer launch failed: {exc}")
        return self.dashboard_snapshot()

    def save_current_distortion(self) -> tuple[str, np.ndarray | None, np.ndarray | None, np.ndarray | None, str]:
        self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} saved distortion reuse is temporarily disabled")
        return self.dashboard_snapshot()

    def stop_runtime(self) -> tuple[str, np.ndarray | None, np.ndarray | None, np.ndarray | None, str]:
        self.current_step = "dashboard"
        if self.runtime_preview_worker is not None:
            self.runtime_preview_worker.stop()
            self.runtime_preview_worker = None
        if self.viewer_proc is not None:
            try:
                self.viewer_proc.terminate()
            except Exception:
                pass
            self.viewer_proc = None
        if self.vlc_proc is not None:
            try:
                self.vlc_proc.terminate()
            except Exception:
                pass
            self.vlc_proc = None
        if self.runtime_client is not None:
            try:
                self.runtime_client.shutdown()
            except Exception:
                pass
            self.runtime_client = None
        self.recent_events.appendleft(f"{time.strftime('%H:%M:%S')} runtime stopped")
        return self.dashboard_snapshot()


def _gradio_v2_dropdown_update(gr: Any, choices: list[tuple[str, str]] | list[str], value: str | None) -> Any:
    return gr.update(choices=choices, value=value)


def _gradio_v2_tabs_update(gr: Any, selected: str) -> Any:
    return gr.update(selected=selected)


def run_native_runtime_gradio(args: argparse.Namespace) -> int:
    gr = _import_gradio()
    session = RuntimeUiSession(args)
    css_text = """
    #hogak-step-header {margin-bottom: 10px;}
    .hogak-preview {border: 1px solid #2b2b2b; border-radius: 12px; overflow: hidden;}
    .hogak-controls {min-width: 320px;}
    """
    app = gr.Blocks(
        title="Hogak Native Runtime",
        analytics_enabled=False,
    )
    with app:
        gr.Markdown("## Hogak Native Runtime")
        gr.Markdown(
            "Interactive runtime now runs in raw stitch mode only. Use assisted calibration in the web UI, "
            "review calibration inliers on the stitched preview, and launch runtime only after the active raw homography is ready."
        )
        workflow_status = gr.Markdown(value=session.workflow_markdown(), elem_id="hogak-step-header")
        with gr.Tabs(selected="start", elem_id="hogak-runtime-tabs") as workflow_tabs:
            with gr.Tab("1. Start", id="start"):
                with gr.Row():
                    with gr.Column(scale=2, min_width=420, elem_classes=["hogak-controls"]):
                        output_standard = gr.Dropdown(sorted(OUTPUT_PRESETS.keys()), value=session.output_standard, label="Output standard")
                        run_calibration_first = gr.Checkbox(value=True, label="Run calibration first")
                        open_vlc_low_latency = gr.Checkbox(value=bool(session.open_vlc_low_latency), label="Open VLC low-latency transmit")
                        distortion_disabled = gr.Checkbox(value=False, label="Distortion disabled (raw only)", interactive=False)
                        start_assisted = gr.Button("Start assisted calibration", variant="primary")
                        use_current_homography = gr.Button(
                            "Use current homography",
                            interactive=session.use_current_homography_enabled(),
                        )
                    with gr.Column(scale=5, min_width=960):
                        start_status = gr.Markdown(value=session.start_markdown())

            with gr.Tab("2. Assisted Calibration", id="assisted-calibration"):
                with gr.Row():
                    with gr.Column(scale=3, min_width=720):
                        left_calibration_image = gr.Image(
                            type="numpy",
                            label="Left frame",
                            interactive=True,
                            height=560,
                            elem_classes=["hogak-preview"],
                        )
                    with gr.Column(scale=3, min_width=720):
                        right_calibration_image = gr.Image(
                            type="numpy",
                            label="Right frame",
                            interactive=True,
                            height=560,
                            elem_classes=["hogak-preview"],
                        )
                    with gr.Column(scale=2, min_width=360, elem_classes=["hogak-controls"]):
                        calibration_status = gr.Markdown()
                        calibration_pairs = gr.Dropdown(label="Calibration pairs", choices=[], value=None)
                        undo_pair = gr.Button("Undo last pair")
                        delete_pair = gr.Button("Delete selected pair")
                        clear_pairs = gr.Button("Clear all")
                        refresh_frames = gr.Button("Refresh frames")
                        compute_calibration = gr.Button("Compute calibration", variant="primary")

            with gr.Tab("3. Calibration Review", id="calibration-review"):
                with gr.Row():
                    with gr.Column(scale=5, min_width=960):
                        calibration_review_preview = gr.Image(
                            type="numpy",
                            label="Calibration stitched preview",
                            interactive=False,
                            height=760,
                            elem_classes=["hogak-preview"],
                        )
                    with gr.Column(scale=3, min_width=520):
                        calibration_inlier_preview = gr.Image(
                            type="numpy",
                            label="Calibration inlier matches",
                            interactive=False,
                            height=360,
                            elem_classes=["hogak-preview"],
                        )
                        calibration_review_status = gr.Markdown()
                        calibration_review_show_inliers = gr.Checkbox(value=session.show_calibration_inliers, label="Show calibration inliers")
                        accept_calibration = gr.Button("Accept calibration", variant="primary")
                        back_to_calibration = gr.Button("Back to calibration")

            with gr.Tab("4. Stitch Review", id="stitch-review"):
                with gr.Row():
                    with gr.Column(scale=5, min_width=960):
                        stitch_review_preview = gr.Image(
                            type="numpy",
                            label="Stitch review preview",
                            interactive=False,
                            height=760,
                            elem_classes=["hogak-preview"],
                        )
                    with gr.Column(scale=2, min_width=360, elem_classes=["hogak-controls"]):
                        stitch_review_status = gr.Markdown()
                        stitch_review_show_inliers = gr.Checkbox(value=session.show_calibration_inliers, label="Show calibration inliers")
                        launch_runtime = gr.Button("Launch runtime", variant="primary", interactive=session.use_current_homography_enabled())

            with gr.Tab("5. Runtime Dashboard", id="dashboard"):
                with gr.Row():
                    with gr.Column(scale=2, min_width=320, elem_classes=["hogak-controls"]):
                        stop_runtime = gr.Button("Stop Runtime", variant="stop")
                        open_external_viewer = gr.Button("Open External Viewer")
                        dashboard_show_inliers = gr.Checkbox(value=session.show_calibration_inliers, label="Show calibration inliers")
                        runtime_events = gr.Textbox(label="Recent events", lines=14)
                    with gr.Column(scale=3, min_width=420):
                        runtime_status = gr.Markdown()
                    with gr.Column(scale=5, min_width=760):
                        runtime_preview = gr.Image(
                            type="numpy",
                            label="Runtime stitched preview",
                            interactive=False,
                            height=560,
                            elem_classes=["hogak-preview"],
                        )
                        with gr.Row():
                            runtime_left_thumb = gr.Image(type="numpy", label="Left raw", interactive=False, height=180)
                            runtime_right_thumb = gr.Image(type="numpy", label="Right raw", interactive=False, height=180)
                timer = gr.Timer(1.0)

        def _start_common(output_standard_value: str, run_calibration_value: bool, open_vlc_value: bool):
            start_md = session.prepare_start(output_standard_value, run_calibration_value, open_vlc_value, False)
            use_button = gr.update(interactive=session.use_current_homography_enabled())
            return start_md, use_button

        def _start_assisted_begin_action(output_standard_value: str, run_calibration_value: bool, open_vlc_value: bool):
            start_md = session.begin_assisted_calibration_start(
                output_standard_value,
                run_calibration_value,
                open_vlc_value,
                False,
            )
            left_img, right_img, _, choices, value = session.render_assisted_calibration_state()
            status_lines = [
                "### Assisted Calibration",
                "- ready",
                "- automatic frame loading is disabled here to avoid long RTSP blocking on button click",
                "- click `Refresh frames` to load the current left/right representative frames",
                "- then click left point -> right point to create correspondence pairs",
            ]
            return (
                _gradio_v2_tabs_update(gr, "assisted-calibration"),
                session.workflow_markdown(),
                start_md,
                gr.update(interactive=session.use_current_homography_enabled()),
                left_img,
                right_img,
                "\n".join(status_lines),
                gr.update(choices=choices, value=value),
                gr.update(interactive=False),
            )

        def _use_current_homography_action(output_standard_value: str, run_calibration_value: bool, open_vlc_value: bool):
            start_md, use_button = _start_common(output_standard_value, run_calibration_value, open_vlc_value)
            if not session.use_current_homography_enabled():
                return (
                    _gradio_v2_tabs_update(gr, "start"),
                    session.workflow_markdown(),
                    start_md,
                    use_button,
                    None,
                    "### Current homography is not launch-ready\n- run assisted calibration first",
                    gr.update(interactive=False),
                )
            preview, status = session.prepare_stitch_review()
            return (
                _gradio_v2_tabs_update(gr, "stitch-review"),
                session.workflow_markdown(),
                start_md,
                use_button,
                preview,
                status,
                gr.update(interactive=session.use_current_homography_enabled()),
            )

        def _assisted_state_payload():
            left_img, right_img, status, choices, value = session.render_assisted_calibration_state()
            return (
                session.workflow_markdown(),
                left_img,
                right_img,
                status,
                gr.update(choices=choices, value=value),
                gr.update(interactive=session.calibration_pair_count() >= 4),
            )

        def _left_calibration_click(evt: GradioSelectData):
            return _assisted_state_payload_after(session.add_calibration_click("left", evt))

        def _right_calibration_click(evt: GradioSelectData):
            return _assisted_state_payload_after(session.add_calibration_click("right", evt))

        def _assisted_state_payload_after(payload: tuple[np.ndarray | None, np.ndarray | None, str, list[tuple[str, str]], str | None]):
            left_img, right_img, status, choices, value = payload
            return (
                session.workflow_markdown(),
                left_img,
                right_img,
                status,
                gr.update(choices=choices, value=value),
                gr.update(interactive=session.calibration_pair_count() >= 4),
            )

        def _select_calibration_pair_action(value: str | None):
            return _assisted_state_payload_after(session.select_calibration_pair(value))

        def _undo_calibration_pair_action():
            return _assisted_state_payload_after(session.undo_last_calibration_pair())

        def _delete_calibration_pair_action():
            return _assisted_state_payload_after(session.delete_selected_calibration_pair())

        def _clear_calibration_pairs_action():
            return _assisted_state_payload_after(session.clear_calibration_pairs())

        def _refresh_calibration_frames_action():
            try:
                return _assisted_state_payload_after(session.refresh_calibration_frames())
            except Exception as exc:
                left_img, right_img, status, choices, value = session.render_assisted_calibration_state()
                status = "\n".join([status, "", f"- refresh failed={type(exc).__name__}: {exc}"])
                return (
                    session.workflow_markdown(),
                    left_img,
                    right_img,
                    status,
                    gr.update(choices=choices, value=value),
                    gr.update(interactive=session.calibration_pair_count() >= 4),
                )

        def _compute_calibration_action():
            try:
                stitched_preview, inlier_preview, status = session.compute_assisted_calibration_candidate()
            except Exception as exc:
                left_img, right_img, assisted_status, choices, value = session.render_assisted_calibration_state()
                assisted_status = "\n".join([assisted_status, "", f"- compute error={type(exc).__name__}: {exc}"])
                return (
                    _gradio_v2_tabs_update(gr, "assisted-calibration"),
                    session.workflow_markdown(),
                    left_img,
                    right_img,
                    assisted_status,
                    gr.update(choices=choices, value=value),
                    gr.update(interactive=session.calibration_pair_count() >= 4),
                    None,
                    None,
                    "",
                )
            return (
                _gradio_v2_tabs_update(gr, "calibration-review"),
                session.workflow_markdown(),
                None,
                None,
                None,
                None,
                None,
                stitched_preview,
                inlier_preview,
                status,
            )

        def _update_calibration_review_inliers(value: bool):
            session.update_show_calibration_inliers(value)
            stitched_preview, inlier_preview, status = session.render_calibration_review()
            return session.workflow_markdown(), stitched_preview, inlier_preview, status

        def _accept_calibration_action():
            preview, status = session.accept_calibration_review()
            start_md = session.start_markdown()
            launch_update = gr.update(interactive=session.use_current_homography_enabled())
            use_button = gr.update(interactive=session.use_current_homography_enabled())
            return (
                _gradio_v2_tabs_update(gr, "stitch-review"),
                session.workflow_markdown(),
                start_md,
                use_button,
                preview,
                status,
                session.show_calibration_inliers,
                launch_update,
            )

        def _back_to_calibration_action():
            return (
                _gradio_v2_tabs_update(gr, "assisted-calibration"),
                *_assisted_state_payload_after(session.cancel_calibration_review()),
            )

        def _update_stitch_review_inliers(value: bool):
            session.update_show_calibration_inliers(value)
            preview, status = session.prepare_stitch_review()
            return session.workflow_markdown(), preview, status

        def _runtime_action(action: str):
            if action == "launch":
                payload = session.launch_runtime()
            elif action == "stop":
                payload = session.stop_runtime()
            elif action == "viewer":
                payload = session.open_external_viewer()
            else:
                payload = session.poll_runtime()
            status, preview, left_thumb, right_thumb, events = payload
            return session.workflow_markdown(), status, preview, left_thumb, right_thumb, events, session.show_calibration_inliers

        def _launch_runtime_action():
            if not session.use_current_homography_enabled():
                preview, status = session.prepare_stitch_review()
                return (
                    _gradio_v2_tabs_update(gr, "stitch-review"),
                    session.workflow_markdown(),
                    status,
                    preview,
                    session._corrected_thumbnail("left"),
                    session._corrected_thumbnail("right"),
                    "\n".join(session.recent_events),
                    session.show_calibration_inliers,
                )
            return (_gradio_v2_tabs_update(gr, "dashboard"), *_runtime_action("launch"))

        def _stop_runtime_action():
            return _runtime_action("stop")

        def _open_external_viewer_action():
            return _runtime_action("viewer")

        def _poll_runtime_action():
            return _runtime_action("poll")

        def _update_dashboard_inliers(value: bool):
            session.update_show_calibration_inliers(value)
            status, preview, left_thumb, right_thumb, events = session.dashboard_snapshot()
            return session.workflow_markdown(), status, preview, left_thumb, right_thumb, events, session.show_calibration_inliers

        start_assisted.click(
            fn=_start_assisted_begin_action,
            inputs=[output_standard, run_calibration_first, open_vlc_low_latency],
            outputs=[
                workflow_tabs,
                workflow_status,
                start_status,
                use_current_homography,
                left_calibration_image,
                right_calibration_image,
                calibration_status,
                calibration_pairs,
                compute_calibration,
            ],
        )
        use_current_homography.click(
            fn=_use_current_homography_action,
            inputs=[output_standard, run_calibration_first, open_vlc_low_latency],
            outputs=[
                workflow_tabs,
                workflow_status,
                start_status,
                use_current_homography,
                stitch_review_preview,
                stitch_review_status,
                launch_runtime,
            ],
        )
        left_calibration_image.select(
            fn=_left_calibration_click,
            outputs=[workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        right_calibration_image.select(
            fn=_right_calibration_click,
            outputs=[workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        calibration_pairs.change(
            fn=_select_calibration_pair_action,
            inputs=[calibration_pairs],
            outputs=[workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        undo_pair.click(
            fn=_undo_calibration_pair_action,
            outputs=[workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        delete_pair.click(
            fn=_delete_calibration_pair_action,
            outputs=[workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        clear_pairs.click(
            fn=_clear_calibration_pairs_action,
            outputs=[workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        refresh_frames.click(
            fn=_refresh_calibration_frames_action,
            outputs=[workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        compute_calibration.click(
            fn=_compute_calibration_action,
            outputs=[
                workflow_tabs,
                workflow_status,
                left_calibration_image,
                right_calibration_image,
                calibration_status,
                calibration_pairs,
                compute_calibration,
                calibration_review_preview,
                calibration_inlier_preview,
                calibration_review_status,
            ],
        )
        calibration_review_show_inliers.change(
            fn=_update_calibration_review_inliers,
            inputs=[calibration_review_show_inliers],
            outputs=[workflow_status, calibration_review_preview, calibration_inlier_preview, calibration_review_status],
        )
        accept_calibration.click(
            fn=_accept_calibration_action,
            outputs=[
                workflow_tabs,
                workflow_status,
                start_status,
                use_current_homography,
                stitch_review_preview,
                stitch_review_status,
                stitch_review_show_inliers,
                launch_runtime,
            ],
        )
        back_to_calibration.click(
            fn=_back_to_calibration_action,
            outputs=[workflow_tabs, workflow_status, left_calibration_image, right_calibration_image, calibration_status, calibration_pairs, compute_calibration],
        )
        stitch_review_show_inliers.change(
            fn=_update_stitch_review_inliers,
            inputs=[stitch_review_show_inliers],
            outputs=[workflow_status, stitch_review_preview, stitch_review_status],
        )
        launch_runtime.click(fn=_launch_runtime_action, outputs=[workflow_tabs, workflow_status, runtime_status, runtime_preview, runtime_left_thumb, runtime_right_thumb, runtime_events, dashboard_show_inliers])
        stop_runtime.click(fn=_stop_runtime_action, outputs=[workflow_status, runtime_status, runtime_preview, runtime_left_thumb, runtime_right_thumb, runtime_events, dashboard_show_inliers])
        open_external_viewer.click(fn=_open_external_viewer_action, outputs=[workflow_status, runtime_status, runtime_preview, runtime_left_thumb, runtime_right_thumb, runtime_events, dashboard_show_inliers])
        dashboard_show_inliers.change(fn=_update_dashboard_inliers, inputs=[dashboard_show_inliers], outputs=[workflow_status, runtime_status, runtime_preview, runtime_left_thumb, runtime_right_thumb, runtime_events, dashboard_show_inliers])
        timer.tick(fn=_poll_runtime_action, outputs=[workflow_status, runtime_status, runtime_preview, runtime_left_thumb, runtime_right_thumb, runtime_events, dashboard_show_inliers])

    preferred_port = max(1, int(getattr(args, "ui_port", DEFAULT_UI_PORT) or DEFAULT_UI_PORT))
    resolved_port = _pick_available_local_port(preferred_port)
    if resolved_port <= 0:
        raise OSError(f"Could not find an available local Gradio port near {preferred_port}.")
    if resolved_port != preferred_port:
        print(f"[native-runtime] Port {preferred_port} is busy, using available Gradio port {resolved_port}.")

    app.launch(
        server_name="127.0.0.1",
        server_port=resolved_port,
        inbrowser=not bool(getattr(args, "no_browser", False)),
        share=False,
        prevent_thread_lock=True,
        quiet=True,
        css=css_text,
    )
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        session.stop_runtime()
    return 0
