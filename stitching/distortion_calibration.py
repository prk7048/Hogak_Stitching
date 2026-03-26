from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, cast

try:
    import numpy as _np  # type: ignore
except ModuleNotFoundError:
    _np = None

if TYPE_CHECKING:
    import cv2 as cv2_types

    CvVideoCapture = cv2_types.VideoCapture
else:
    CvVideoCapture = Any

try:
    import cv2 as _cv2  # type: ignore
except ModuleNotFoundError:
    _cv2 = None

cv2 = cast(Any, _cv2)
np = cast(Any, _np)


DISTORTION_SCHEMA_VERSION = 2
DISTORTION_MODEL_PINHOLE = "opencv_pinhole"
DISTORTION_MODEL_FISHEYE = "opencv_fisheye"
DISTORTION_SOURCE_MANUAL = "manual_line_selection"
DISTORTION_SOURCE_MANUAL_GUIDED = "manual_line_guided_auto_fit"
DISTORTION_SOURCE_EXTERNAL = "external_calibration"
MIN_MANUAL_LINE_CONFIDENCE = 0.32
MIN_MANUAL_LINE_COUNT = 4
RECOMMENDED_MANUAL_LINE_COUNT = 6
MAX_MANUAL_ESTIMATION_WIDTH = 1280
MAX_SUPPORT_POINTS_PER_LINE = 128
DEFAULT_GUIDED_FRAME_COUNT = 6


@dataclass(slots=True)
class DistortionLensMetadata:
    lens_model_hint: str = "auto"
    horizontal_fov_deg: float | None = None
    vertical_fov_deg: float | None = None
    camera_model: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "lens_model_hint": str(self.lens_model_hint or "auto"),
        }
        if self.horizontal_fov_deg is not None:
            payload["horizontal_fov_deg"] = float(self.horizontal_fov_deg)
        if self.vertical_fov_deg is not None:
            payload["vertical_fov_deg"] = float(self.vertical_fov_deg)
        if self.camera_model:
            payload["camera_model"] = str(self.camera_model)
        return payload


@dataclass(slots=True)
class DistortionProfile:
    camera_slot: str
    model: str
    image_size: tuple[int, int]
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    source: str
    confidence: float
    saved_at_epoch_sec: int
    line_count: int = 0
    frame_count_used: int = 0
    fit_score: float = 0.0
    support_points: int = 0
    lens_metadata_used: dict[str, Any] = field(default_factory=dict)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "version": DISTORTION_SCHEMA_VERSION,
            "camera_slot": str(self.camera_slot),
            "model": str(self.model),
            "image_size": [int(self.image_size[0]), int(self.image_size[1])],
            "camera_matrix": self.camera_matrix.tolist(),
            "dist_coeffs": self.dist_coeffs.reshape(-1).tolist(),
            "source": str(self.source),
            "confidence": float(self.confidence),
            "saved_at_epoch_sec": int(self.saved_at_epoch_sec),
            "line_count": int(self.line_count),
            "frame_count_used": int(self.frame_count_used),
            "fit_score": float(self.fit_score),
            "support_points": int(self.support_points),
            "lens_metadata_used": dict(self.lens_metadata_used or {}),
        }


@dataclass(slots=True)
class ResolvedDistortion:
    enabled: bool = False
    source: str = "off"
    confidence: float = 0.0
    active_path: str = ""
    profile: DistortionProfile | None = None
    line_count: int = 0
    frame_count_used: int = 0
    fit_score: float = 0.0
    lens_model: str = DISTORTION_MODEL_PINHOLE
    status_message: str = ""


@dataclass(slots=True)
class ManualLineSegment:
    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def length(self) -> float:
        return float(
            np.hypot(
                float(self.end[0]) - float(self.start[0]),
                float(self.end[1]) - float(self.start[1]),
            )
        )

    def scaled(self, scale: float) -> ManualLineSegment:
        return ManualLineSegment(
            start=(float(self.start[0]) * scale, float(self.start[1]) * scale),
            end=(float(self.end[0]) * scale, float(self.end[1]) * scale),
        )

    def midpoint(self) -> tuple[float, float]:
        return (
            float((self.start[0] + self.end[0]) * 0.5),
            float((self.start[1] + self.end[1]) * 0.5),
        )

    def direction(self) -> tuple[float, float]:
        dx = float(self.end[0]) - float(self.start[0])
        dy = float(self.end[1]) - float(self.start[1])
        length = max(1e-6, float(np.hypot(dx, dy)))
        return (dx / length, dy / length)


class _FfmpegCaptureEnv:
    def __init__(self, transport: str, timeout_sec: float) -> None:
        self._transport = str(transport or "tcp")
        self._timeout_sec = float(timeout_sec)
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


def cv2_available() -> bool:
    return cv2 is not None


def _distortion_deps_available() -> bool:
    return cv2 is not None and np is not None


def build_lens_metadata(
    *,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> DistortionLensMetadata:
    return DistortionLensMetadata(
        lens_model_hint=str(lens_model_hint or "auto").strip().lower() or "auto",
        horizontal_fov_deg=float(horizontal_fov_deg) if horizontal_fov_deg is not None else None,
        vertical_fov_deg=float(vertical_fov_deg) if vertical_fov_deg is not None else None,
        camera_model=str(camera_model or "").strip(),
    )


def saved_distortion_available(left_path: str | Path, right_path: str | Path) -> bool:
    return Path(left_path).expanduser().exists() and Path(right_path).expanduser().exists()


def load_distortion_profile(path: str | Path) -> DistortionProfile | None:
    if np is None:
        return None
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return None
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        image_size_raw = payload.get("image_size") or [0, 0]
        image_size = (int(image_size_raw[0]), int(image_size_raw[1]))
        camera_matrix = np.asarray(payload.get("camera_matrix"), dtype=np.float64).reshape(3, 3)
        dist_coeffs = np.asarray(payload.get("dist_coeffs"), dtype=np.float64).reshape(1, -1)
    except (TypeError, ValueError, IndexError):
        return None
    lens_metadata_used = payload.get("lens_metadata_used")
    if not isinstance(lens_metadata_used, dict):
        lens_metadata_used = {}
    return DistortionProfile(
        camera_slot=str(payload.get("camera_slot") or ""),
        model=str(payload.get("model") or DISTORTION_MODEL_PINHOLE),
        image_size=image_size,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        source=str(payload.get("source") or DISTORTION_SOURCE_EXTERNAL),
        confidence=float(payload.get("confidence") or 0.0),
        saved_at_epoch_sec=int(payload.get("saved_at_epoch_sec") or 0),
        line_count=int(payload.get("line_count") or 0),
        frame_count_used=int(payload.get("frame_count_used") or 0),
        fit_score=float(payload.get("fit_score") or 0.0),
        support_points=int(payload.get("support_points") or 0),
        lens_metadata_used=cast(dict[str, Any], lens_metadata_used),
    )


def save_distortion_profile(path: str | Path, profile: DistortionProfile) -> None:
    file_path = Path(path).expanduser()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        json.dumps(profile.to_json_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_homography_distortion_reference(path: str | Path) -> str:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return "missing"
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    if not isinstance(payload, dict):
        return "unknown"
    reference = str(payload.get("distortion_reference") or "").strip().lower()
    if reference in {"raw", "undistorted"}:
        return reference
    return "raw"


def capture_representative_frame(
    rtsp_url: str,
    *,
    transport: str,
    timeout_sec: float,
    warmup_frames: int = 18,
) -> np.ndarray | None:
    frames = capture_representative_frames(
        rtsp_url,
        transport=transport,
        timeout_sec=timeout_sec,
        warmup_frames=warmup_frames,
        sample_frames=1,
    )
    return frames[-1] if frames else None


def capture_representative_frames(
    rtsp_url: str,
    *,
    transport: str,
    timeout_sec: float,
    warmup_frames: int = 18,
    sample_frames: int = DEFAULT_GUIDED_FRAME_COUNT,
) -> list[np.ndarray]:
    if not _distortion_deps_available():
        return []
    with _FfmpegCaptureEnv(transport=transport, timeout_sec=timeout_sec):
        capture: CvVideoCapture = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        return []
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    frames: list[np.ndarray] = []
    frame: np.ndarray | None = None
    deadline = time.time() + max(1.0, float(timeout_sec))
    try:
        count = 0
        target = max(1, int(warmup_frames))
        while time.time() < deadline and count < target:
            ok, current = capture.read()
            if not ok:
                continue
            frame = current
            count += 1
        if frame is not None:
            frames.append(frame)
        target_samples = max(1, int(sample_frames))
        idle_loops = 0
        while time.time() < deadline and len(frames) < target_samples:
            ok, current = capture.read()
            if not ok:
                idle_loops += 1
                if idle_loops > target_samples * 8:
                    break
                continue
            idle_loops = 0
            frames.append(current)
    finally:
        capture.release()
    if not frames and frame is not None:
        frames.append(frame)
    if not frames:
        return []
    if len(frames) <= target_samples:
        return list(frames)
    indexes = np.linspace(0, len(frames) - 1, target_samples).astype(int)
    return [frames[int(index)] for index in indexes]


def _resize_for_estimation(
    frame: np.ndarray,
    *,
    max_width: int = MAX_MANUAL_ESTIMATION_WIDTH,
) -> tuple[np.ndarray, float]:
    if not _distortion_deps_available():
        raise RuntimeError("distortion dependencies unavailable")
    height, width = frame.shape[:2]
    if width <= max_width:
        return frame, 1.0
    scale = max_width / float(max(1, width))
    resized = cv2.resize(
        frame,
        (max(2, int(round(width * scale))), max(2, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, scale


def _fov_to_focal(length_px: float, fov_deg: float | None) -> float | None:
    if fov_deg is None:
        return None
    angle = float(fov_deg)
    if not np.isfinite(angle) or angle <= 1.0 or angle >= 179.0:
        return None
    tangent = math.tan(math.radians(angle) * 0.5)
    if abs(tangent) <= 1e-6:
        return None
    return float(length_px / (2.0 * tangent))


def _initial_camera_matrix(width: int, height: int, metadata: DistortionLensMetadata) -> np.ndarray:
    focal_default = float(max(width, height))
    fx = _fov_to_focal(float(width), metadata.horizontal_fov_deg) or focal_default
    fy = _fov_to_focal(float(height), metadata.vertical_fov_deg) or fx
    if metadata.horizontal_fov_deg is None and metadata.vertical_fov_deg is not None:
        fx = fy
    if metadata.vertical_fov_deg is None and metadata.horizontal_fov_deg is not None:
        fy = fx
    return np.asarray(
        [
            [float(fx), 0.0, float(width) * 0.5],
            [0.0, float(fy), float(height) * 0.5],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _undistort_frame(frame: np.ndarray, camera_matrix: np.ndarray, dist_coeffs: np.ndarray, model: str) -> np.ndarray:
    if not _distortion_deps_available():
        raise RuntimeError("distortion dependencies unavailable")
    height, width = frame.shape[:2]
    if str(model or DISTORTION_MODEL_PINHOLE) == DISTORTION_MODEL_FISHEYE:
        map_x, map_y = cv2.fisheye.initUndistortRectifyMap(
            camera_matrix,
            dist_coeffs.reshape(-1, 1).astype(np.float64),
            np.eye(3, dtype=np.float64),
            camera_matrix,
            (width, height),
            cv2.CV_32FC1,
        )
    else:
        map_x, map_y = cv2.initUndistortRectifyMap(
            camera_matrix,
            dist_coeffs,
            None,
            camera_matrix,
            (width, height),
            cv2.CV_32FC1,
        )
    return cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def _distance_to_line(points: np.ndarray, line: ManualLineSegment) -> tuple[np.ndarray, np.ndarray]:
    p1 = np.asarray(line.start, dtype=np.float32)
    p2 = np.asarray(line.end, dtype=np.float32)
    direction = p2 - p1
    length = float(np.hypot(float(direction[0]), float(direction[1])))
    if length <= 1.0:
        return np.zeros((points.shape[0],), dtype=np.float32), np.zeros((points.shape[0],), dtype=np.float32)
    unit = direction / length
    relative = points - p1
    projection = relative @ unit
    perpendicular = np.abs((relative[:, 0] * unit[1]) - (relative[:, 1] * unit[0]))
    return projection.astype(np.float32), perpendicular.astype(np.float32)


def _collect_line_support_points(
    edges: np.ndarray,
    line: ManualLineSegment,
) -> np.ndarray:
    height, width = edges.shape[:2]
    line_length = max(1.0, line.length)
    band_px = float(np.clip(line_length * 0.035, 6.0, 18.0))
    margin_px = band_px * 1.5

    x_coords = [float(line.start[0]), float(line.end[0])]
    y_coords = [float(line.start[1]), float(line.end[1])]
    x1 = max(0, int(np.floor(min(x_coords) - band_px - margin_px)))
    y1 = max(0, int(np.floor(min(y_coords) - band_px - margin_px)))
    x2 = min(width, int(np.ceil(max(x_coords) + band_px + margin_px)))
    y2 = min(height, int(np.ceil(max(y_coords) + band_px + margin_px)))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return np.empty((0, 2), dtype=np.float32)

    roi = edges[y1:y2, x1:x2]
    ys, xs = np.nonzero(roi)
    if xs.size <= 0:
        return np.empty((0, 2), dtype=np.float32)
    points = np.column_stack((xs + x1, ys + y1)).astype(np.float32)
    projection, perpendicular = _distance_to_line(points, line)
    keep = (
        (projection >= -margin_px)
        & (projection <= line_length + margin_px)
        & (perpendicular <= band_px)
    )
    filtered = points[keep]
    if filtered.shape[0] < 12:
        return np.empty((0, 2), dtype=np.float32)

    projection, _ = _distance_to_line(filtered, line)
    order = np.argsort(projection)
    filtered = filtered[order]
    if filtered.shape[0] > MAX_SUPPORT_POINTS_PER_LINE:
        indexes = np.linspace(0, filtered.shape[0] - 1, MAX_SUPPORT_POINTS_PER_LINE).astype(np.int32)
        filtered = filtered[indexes]
    return filtered.astype(np.float32)


def _angle_distance_deg(a_deg: float, b_deg: float) -> float:
    diff = abs(float(a_deg) - float(b_deg)) % 180.0
    return min(diff, 180.0 - diff)


def _line_angle_deg(line: ManualLineSegment) -> float:
    dx = float(line.end[0]) - float(line.start[0])
    dy = float(line.end[1]) - float(line.start[1])
    return float((math.degrees(math.atan2(dy, dx)) + 180.0) % 180.0)


def _line_midpoint_distance(a: ManualLineSegment, b: ManualLineSegment) -> float:
    ax, ay = a.midpoint()
    bx, by = b.midpoint()
    return float(np.hypot(ax - bx, ay - by))


def _select_guided_line(edges: np.ndarray, hint_line: ManualLineSegment) -> ManualLineSegment:
    height, width = edges.shape[:2]
    margin = max(12, int(round(hint_line.length * 0.20)))
    x_coords = [float(hint_line.start[0]), float(hint_line.end[0])]
    y_coords = [float(hint_line.start[1]), float(hint_line.end[1])]
    x1 = max(0, int(np.floor(min(x_coords) - margin)))
    y1 = max(0, int(np.floor(min(y_coords) - margin)))
    x2 = min(width, int(np.ceil(max(x_coords) + margin)))
    y2 = min(height, int(np.ceil(max(y_coords) + margin)))
    if x2 - x1 < 12 or y2 - y1 < 12:
        return hint_line

    roi = edges[y1:y2, x1:x2]
    min_line_length = max(24, int(round(hint_line.length * 0.45)))
    try:
        segments = cv2.HoughLinesP(
            roi,
            1.0,
            np.pi / 180.0,
            threshold=28,
            minLineLength=min_line_length,
            maxLineGap=14,
        )
    except cv2.error:
        segments = None
    if segments is None or len(segments) <= 0:
        return hint_line

    hint_angle = _line_angle_deg(hint_line)
    hint_length = max(1.0, hint_line.length)
    best_line = hint_line
    best_score = -1e9
    for candidate in segments.reshape(-1, 4):
        line = ManualLineSegment(
            start=(float(candidate[0] + x1), float(candidate[1] + y1)),
            end=(float(candidate[2] + x1), float(candidate[3] + y1)),
        )
        if line.length < 18.0:
            continue
        angle_distance = _angle_distance_deg(hint_angle, _line_angle_deg(line))
        if angle_distance > 18.0:
            continue
        midpoint_distance = _line_midpoint_distance(hint_line, line)
        score = (
            (line.length / hint_length)
            - (angle_distance * 0.035)
            - (midpoint_distance / max(14.0, hint_length * 0.35))
        )
        if score > best_score:
            best_score = score
            best_line = line
    return best_line


def _undistort_points_model(
    model: str,
    points: np.ndarray,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> np.ndarray:
    if points.size == 0:
        return np.empty((0, 2), dtype=np.float32)
    if model == DISTORTION_MODEL_FISHEYE:
        undistorted = cv2.fisheye.undistortPoints(
            points.reshape(-1, 1, 2).astype(np.float32),
            camera_matrix,
            dist_coeffs.reshape(4, 1).astype(np.float64),
            P=camera_matrix,
        )
    else:
        undistorted = cv2.undistortPoints(
            points.reshape(-1, 1, 2).astype(np.float32),
            camera_matrix,
            dist_coeffs.astype(np.float64),
            P=camera_matrix,
        )
    return undistorted.reshape(-1, 2).astype(np.float32)


def _fit_line_error(points: np.ndarray) -> float:
    if points.shape[0] < 2:
        return 1e9
    vx, vy, x0, y0 = cv2.fitLine(points, cv2.DIST_L2, 0, 0.01, 0.01).reshape(-1)
    distances = np.abs((points[:, 0] - x0) * vy - (points[:, 1] - y0) * vx)
    return float(np.mean(distances))


def _score_support_sets_for_model(
    support_sets: list[np.ndarray],
    *,
    model: str,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> tuple[float, int, int]:
    total_error = 0.0
    total_points = 0
    usable_lines = 0
    for support in support_sets:
        if support.shape[0] < 6:
            continue
        corrected = _undistort_points_model(model, support, camera_matrix, dist_coeffs)
        line_error = _fit_line_error(corrected)
        total_error += line_error * float(support.shape[0])
        total_points += int(support.shape[0])
        usable_lines += 1
    if usable_lines <= 0 or total_points <= 0:
        return 1e9, 0, 0
    return total_error / float(total_points), usable_lines, total_points


def _candidate_penalty(
    model: str,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    width: int,
    height: int,
) -> float:
    fx = float(camera_matrix[0, 0])
    fy = float(camera_matrix[1, 1])
    cx = float(camera_matrix[0, 2])
    cy = float(camera_matrix[1, 2])
    focal_ref = float(max(width, height))
    focal_penalty = abs(math.log(max(1e-6, fx / focal_ref))) + abs(math.log(max(1e-6, fy / focal_ref)))
    center_penalty = (
        abs(cx - (width * 0.5)) / max(1.0, width * 0.20)
        + abs(cy - (height * 0.5)) / max(1.0, height * 0.20)
    )
    coeff_penalty = float(np.mean(np.abs(dist_coeffs.reshape(-1)))) * (0.55 if model == DISTORTION_MODEL_FISHEYE else 0.45)
    return float((focal_penalty * 0.10) + (center_penalty * 0.08) + coeff_penalty)


def _build_camera_matrix_from_state(state: dict[str, float]) -> np.ndarray:
    return np.asarray(
        [
            [float(state["fx"]), 0.0, float(state["cx"])],
            [0.0, float(state["fy"]), float(state["cy"])],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _build_dist_coeffs_from_state(model: str, state: dict[str, float]) -> np.ndarray:
    if model == DISTORTION_MODEL_FISHEYE:
        return np.asarray(
            [[float(state["k1"]), float(state["k2"]), float(state["k3"]), float(state["k4"])]],
            dtype=np.float64,
        )
    return np.asarray(
        [[float(state["k1"]), float(state["k2"]), float(state["p1"]), float(state["p2"]), float(state["k3"])]],
        dtype=np.float64,
    )


def _evaluate_candidate_state(
    model: str,
    state: dict[str, float],
    support_sets: list[np.ndarray],
    width: int,
    height: int,
) -> dict[str, Any]:
    camera_matrix = _build_camera_matrix_from_state(state)
    dist_coeffs = _build_dist_coeffs_from_state(model, state)
    fit_error, usable_lines, total_points = _score_support_sets_for_model(
        support_sets,
        model=model,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
    )
    penalty = _candidate_penalty(model, camera_matrix, dist_coeffs, width, height)
    return {
        "fit_error": float(fit_error),
        "usable_lines": int(usable_lines),
        "total_points": int(total_points),
        "camera_matrix": camera_matrix,
        "dist_coeffs": dist_coeffs,
        "penalty": float(penalty),
        "objective": float(fit_error + penalty),
    }


def _clamp_state_for_model(model: str, state: dict[str, float], width: int, height: int) -> dict[str, float]:
    clamped = dict(state)
    focal_ref = float(max(width, height))
    clamped["fx"] = float(np.clip(clamped["fx"], focal_ref * 0.35, focal_ref * 3.5))
    clamped["fy"] = float(np.clip(clamped["fy"], focal_ref * 0.35, focal_ref * 3.5))
    clamped["cx"] = float(np.clip(clamped["cx"], width * 0.30, width * 0.70))
    clamped["cy"] = float(np.clip(clamped["cy"], height * 0.30, height * 0.70))
    if model == DISTORTION_MODEL_FISHEYE:
        for key in ("k1", "k2", "k3", "k4"):
            clamped[key] = float(np.clip(clamped[key], -1.8, 1.8))
    else:
        for key in ("k1", "k2"):
            clamped[key] = float(np.clip(clamped[key], -1.6, 1.6))
        clamped["k3"] = float(np.clip(clamped["k3"], -0.8, 0.8))
        clamped["p1"] = float(np.clip(clamped["p1"], -0.10, 0.10))
        clamped["p2"] = float(np.clip(clamped["p2"], -0.10, 0.10))
    return clamped


def _initial_state_for_model(
    model: str,
    width: int,
    height: int,
    metadata: DistortionLensMetadata,
) -> dict[str, float]:
    camera_matrix = _initial_camera_matrix(width, height, metadata)
    state = {
        "fx": float(camera_matrix[0, 0]),
        "fy": float(camera_matrix[1, 1]),
        "cx": float(camera_matrix[0, 2]),
        "cy": float(camera_matrix[1, 2]),
        "k1": 0.0,
        "k2": 0.0,
        "k3": 0.0,
    }
    if model == DISTORTION_MODEL_FISHEYE:
        state["k4"] = 0.0
        if metadata.lens_model_hint == "fisheye":
            state["k1"] = 0.10
    else:
        state["p1"] = 0.0
        state["p2"] = 0.0
        if metadata.lens_model_hint == "pinhole":
            state["k1"] = 0.04
    return state


def _coordinate_descent_steps(model: str, width: int, height: int) -> list[dict[str, float]]:
    width_step = float(max(4.0, width * 0.05))
    height_step = float(max(4.0, height * 0.05))
    if model == DISTORTION_MODEL_FISHEYE:
        return [
            {"fx": width_step, "fy": height_step, "cx": width * 0.035, "cy": height * 0.035, "k1": 0.24, "k2": 0.12, "k3": 0.08, "k4": 0.05},
            {"fx": width_step * 0.45, "fy": height_step * 0.45, "cx": width * 0.018, "cy": height * 0.018, "k1": 0.10, "k2": 0.06, "k3": 0.03, "k4": 0.02},
            {"fx": width_step * 0.16, "fy": height_step * 0.16, "cx": width * 0.008, "cy": height * 0.008, "k1": 0.035, "k2": 0.020, "k3": 0.010, "k4": 0.008},
        ]
    return [
        {"fx": width_step, "fy": height_step, "cx": width * 0.030, "cy": height * 0.030, "k1": 0.20, "k2": 0.10, "k3": 0.04, "p1": 0.015, "p2": 0.015},
        {"fx": width_step * 0.40, "fy": height_step * 0.40, "cx": width * 0.015, "cy": height * 0.015, "k1": 0.08, "k2": 0.04, "k3": 0.02, "p1": 0.008, "p2": 0.008},
        {"fx": width_step * 0.14, "fy": height_step * 0.14, "cx": width * 0.006, "cy": height * 0.006, "k1": 0.025, "k2": 0.015, "k3": 0.008, "p1": 0.0035, "p2": 0.0035},
    ]


def _optimize_model_state(
    model: str,
    support_sets: list[np.ndarray],
    width: int,
    height: int,
    metadata: DistortionLensMetadata,
) -> dict[str, Any]:
    state = _clamp_state_for_model(model, _initial_state_for_model(model, width, height, metadata), width, height)
    best = _evaluate_candidate_state(model, state, support_sets, width, height)
    best["state"] = dict(state)
    param_names = list(state.keys())
    for step_map in _coordinate_descent_steps(model, width, height):
        improved = True
        while improved:
            improved = False
            for param_name in param_names:
                if param_name not in step_map:
                    continue
                delta = float(step_map[param_name])
                for direction in (-1.0, 1.0):
                    candidate_state = dict(best["state"])
                    candidate_state[param_name] = float(candidate_state[param_name]) + (direction * delta)
                    candidate_state = _clamp_state_for_model(model, candidate_state, width, height)
                    candidate = _evaluate_candidate_state(model, candidate_state, support_sets, width, height)
                    if float(candidate["objective"]) + 1e-6 < float(best["objective"]):
                        candidate["state"] = dict(candidate_state)
                        best = candidate
                        improved = True
    return best


def _guided_line_support_sets(
    frames: list[np.ndarray],
    lines: list[ManualLineSegment],
) -> tuple[list[np.ndarray], int]:
    reduced_frames: list[np.ndarray] = []
    scale = 1.0
    for index, frame in enumerate(frames):
        reduced, frame_scale = _resize_for_estimation(frame, max_width=MAX_MANUAL_ESTIMATION_WIDTH)
        if index == 0:
            scale = frame_scale
        reduced_frames.append(reduced)
    scaled_lines = [line.scaled(scale) for line in lines]
    aggregated_support: list[list[np.ndarray]] = [[] for _ in scaled_lines]
    frame_support_hits = 0
    for frame in reduced_frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame.copy()
        gray = cv2.GaussianBlur(gray, (5, 5), 0.0)
        edges = cv2.Canny(gray, 48, 152, apertureSize=3, L2gradient=True)
        frame_has_support = False
        for line_index, hint_line in enumerate(scaled_lines):
            snapped_line = _select_guided_line(edges, hint_line)
            support = _collect_line_support_points(edges, snapped_line)
            if support.shape[0] < 12:
                support = _collect_line_support_points(edges, hint_line)
            if support.shape[0] >= 12:
                aggregated_support[line_index].append(support)
                frame_has_support = True
        if frame_has_support:
            frame_support_hits += 1
    support_sets: list[np.ndarray] = []
    for line_support in aggregated_support:
        if not line_support:
            continue
        merged = np.vstack(line_support).astype(np.float32)
        if merged.shape[0] > MAX_SUPPORT_POINTS_PER_LINE:
            indexes = np.linspace(0, merged.shape[0] - 1, MAX_SUPPORT_POINTS_PER_LINE).astype(np.int32)
            merged = merged[indexes]
        support_sets.append(merged)
    return support_sets, frame_support_hits


def estimate_manual_guided_distortion(
    frames: list[np.ndarray],
    camera_slot: str,
    lines: list[ManualLineSegment],
    *,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> DistortionProfile | None:
    if not _distortion_deps_available() or not frames:
        return None
    usable_lines = [line for line in lines if line.length >= 40.0]
    if len(usable_lines) < MIN_MANUAL_LINE_COUNT:
        return None
    metadata = build_lens_metadata(
        lens_model_hint=lens_model_hint,
        horizontal_fov_deg=horizontal_fov_deg,
        vertical_fov_deg=vertical_fov_deg,
        camera_model=camera_model,
    )
    reference_frame = frames[-1]
    reduced_reference, scale = _resize_for_estimation(reference_frame, max_width=MAX_MANUAL_ESTIMATION_WIDTH)
    scaled_lines = [line.scaled(scale) for line in usable_lines]
    reduced_frames = [_resize_for_estimation(frame, max_width=MAX_MANUAL_ESTIMATION_WIDTH)[0] for frame in frames]
    support_sets, frame_support_hits = _guided_line_support_sets(reduced_frames, scaled_lines)
    if len(support_sets) < MIN_MANUAL_LINE_COUNT:
        return None

    height, width = reduced_reference.shape[:2]
    baseline_camera_matrix = _initial_camera_matrix(width, height, metadata)
    baseline_dist_coeffs = np.zeros((1, 5), dtype=np.float64)
    baseline_error, baseline_lines, baseline_support_points = _score_support_sets_for_model(
        support_sets,
        model=DISTORTION_MODEL_PINHOLE,
        camera_matrix=baseline_camera_matrix,
        dist_coeffs=baseline_dist_coeffs,
    )
    if baseline_lines < MIN_MANUAL_LINE_COUNT or baseline_support_points < 64 or not np.isfinite(baseline_error):
        return None

    lens_hint = str(metadata.lens_model_hint or "auto").strip().lower() or "auto"
    candidate_models = [DISTORTION_MODEL_PINHOLE, DISTORTION_MODEL_FISHEYE]
    if lens_hint == "fisheye":
        candidate_models = [DISTORTION_MODEL_FISHEYE, DISTORTION_MODEL_PINHOLE]

    best_result: dict[str, Any] | None = None
    for model in candidate_models:
        candidate = _optimize_model_state(model, support_sets, width, height, metadata)
        usable_lines_count = int(candidate["usable_lines"])
        support_points = int(candidate["total_points"])
        if usable_lines_count < MIN_MANUAL_LINE_COUNT or support_points < 64:
            continue
        fit_error = float(candidate["fit_error"])
        penalty = float(candidate["penalty"])
        improvement = max(0.0, baseline_error - fit_error)
        improvement_ratio = improvement / max(1e-6, baseline_error)
        line_factor = min(1.0, float(usable_lines_count) / max(float(RECOMMENDED_MANUAL_LINE_COUNT), 1.0))
        frame_factor = min(1.0, float(frame_support_hits) / max(1.0, float(len(reduced_frames))))
        support_factor = min(1.0, float(support_points) / 220.0)
        confidence = (improvement_ratio * 0.68) + (line_factor * 0.12) + (frame_factor * 0.10) + (support_factor * 0.10) - min(0.18, penalty * 0.10)
        if lens_hint == "pinhole" and model == DISTORTION_MODEL_PINHOLE:
            confidence += 0.015
        if lens_hint == "fisheye" and model == DISTORTION_MODEL_FISHEYE:
            confidence += 0.015
        candidate["model"] = model
        candidate["confidence"] = float(max(0.0, min(0.99, confidence)))
        candidate["line_count"] = usable_lines_count
        candidate["support_points"] = support_points
        candidate["frame_count_used"] = int(frame_support_hits)
        candidate["fit_score"] = float(max(0.0, min(1.0, improvement_ratio)))
        if best_result is None:
            best_result = candidate
            continue
        current_key = (float(candidate["confidence"]), float(candidate["fit_score"]), -float(candidate["objective"]))
        best_key = (float(best_result["confidence"]), float(best_result["fit_score"]), -float(best_result["objective"]))
        if current_key > best_key:
            best_result = candidate

    if best_result is None or float(best_result["confidence"]) < MIN_MANUAL_LINE_CONFIDENCE or float(best_result["fit_score"]) < 0.05:
        return None

    camera_matrix = np.asarray(best_result["camera_matrix"], dtype=np.float64).reshape(3, 3).copy()
    if abs(scale - 1.0) > 1e-6:
        inv_scale = 1.0 / scale
        camera_matrix[0, 0] *= inv_scale
        camera_matrix[1, 1] *= inv_scale
        camera_matrix[0, 2] *= inv_scale
        camera_matrix[1, 2] *= inv_scale

    original_height, original_width = reference_frame.shape[:2]
    return DistortionProfile(
        camera_slot=str(camera_slot),
        model=str(best_result["model"]),
        image_size=(int(original_width), int(original_height)),
        camera_matrix=camera_matrix,
        dist_coeffs=np.asarray(best_result["dist_coeffs"], dtype=np.float64).reshape(1, -1),
        source=DISTORTION_SOURCE_MANUAL_GUIDED,
        confidence=float(best_result["confidence"]),
        saved_at_epoch_sec=int(time.time()),
        line_count=int(best_result["line_count"]),
        frame_count_used=int(best_result["frame_count_used"]),
        fit_score=float(best_result["fit_score"]),
        support_points=int(best_result["support_points"]),
        lens_metadata_used=metadata.to_json_dict(),
    )


def estimate_manual_line_distortion(
    frame: np.ndarray,
    camera_slot: str,
    lines: list[ManualLineSegment],
    *,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> DistortionProfile | None:
    if frame is None:
        return None
    return estimate_manual_guided_distortion(
        [frame],
        camera_slot,
        lines,
        lens_model_hint=lens_model_hint,
        horizontal_fov_deg=horizontal_fov_deg,
        vertical_fov_deg=vertical_fov_deg,
        camera_model=camera_model,
    )


def prompt_manual_line_segments(
    frame: np.ndarray,
    *,
    camera_slot: str,
    min_lines: int = MIN_MANUAL_LINE_COUNT,
) -> list[ManualLineSegment] | None:
    if not _distortion_deps_available() or frame is None or frame.size == 0:
        return None

    window_name = f"Hogak Distortion Lines - {camera_slot}"
    lines: list[ManualLineSegment] = []
    pending_point: tuple[int, int] | None = None
    cancelled = False

    def redraw() -> None:
        canvas = frame.copy()
        for index, line in enumerate(lines, start=1):
            start = (int(round(line.start[0])), int(round(line.start[1])))
            end = (int(round(line.end[0])), int(round(line.end[1])))
            cv2.line(canvas, start, end, (80, 255, 120), 2, cv2.LINE_AA)
            cv2.circle(canvas, start, 4, (60, 220, 255), -1, cv2.LINE_AA)
            cv2.circle(canvas, end, 4, (60, 220, 255), -1, cv2.LINE_AA)
            mid = (int(round((start[0] + end[0]) * 0.5)), int(round((start[1] + end[1]) * 0.5)))
            cv2.putText(
                canvas,
                str(index),
                mid,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        if pending_point is not None:
            cv2.circle(canvas, pending_point, 5, (0, 200, 255), -1, cv2.LINE_AA)

        overlay_height = 118
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], overlay_height), (12, 12, 12), cv2.FILLED)
        text_lines = [
            f"{camera_slot.upper()} distortion guide lines",
            f"Lines: {len(lines)} / min {int(min_lines)} (recommended {RECOMMENDED_MANUAL_LINE_COUNT}+)",
            "Left click twice to add a line. Right click to undo.",
            "Lines are hints: the fitter will snap to nearby real edges automatically.",
            "Enter = confirm when minimum is met. Esc = cancel.",
        ]
        y = 24
        for index, text in enumerate(text_lines):
            cv2.putText(
                canvas,
                text,
                (14, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.60 if index == 0 else 0.52,
                (120, 255, 140) if index == 0 else (230, 230, 230),
                1,
                cv2.LINE_AA,
            )
            y += 21
        cv2.imshow(window_name, canvas)

    def on_mouse(event: int, x: int, y: int, _flags: int, _userdata: Any) -> None:
        nonlocal pending_point
        if event == cv2.EVENT_LBUTTONDOWN:
            if pending_point is None:
                pending_point = (int(x), int(y))
            else:
                candidate = ManualLineSegment(
                    start=(float(pending_point[0]), float(pending_point[1])),
                    end=(float(x), float(y)),
                )
                if candidate.length >= 20.0:
                    lines.append(candidate)
                pending_point = None
            redraw()
        elif event == cv2.EVENT_RBUTTONDOWN:
            if pending_point is not None:
                pending_point = None
            elif lines:
                lines.pop()
            redraw()

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, on_mouse)
    redraw()
    try:
        while True:
            key = int(cv2.waitKeyEx(30))
            if key < 0:
                continue
            if key in {13, 10}:
                if len(lines) >= int(min_lines):
                    break
                redraw()
                continue
            if key == 27:
                cancelled = True
                break
        cv2.destroyWindow(window_name)
    except Exception:
        try:
            cv2.destroyWindow(window_name)
        except Exception:
            pass
        raise

    if cancelled:
        return None
    if len(lines) < int(min_lines):
        return None
    return list(lines)


def resolve_distortion_profile(
    frame: np.ndarray | None,
    *,
    camera_slot: str,
    saved_path: str | Path,
    use_saved_distortion: bool,
    distortion_auto_save: bool,
    distortion_mode: str,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> ResolvedDistortion:
    _ = frame
    _ = camera_slot
    _ = distortion_auto_save
    _ = lens_model_hint
    _ = horizontal_fov_deg
    _ = vertical_fov_deg
    _ = camera_model
    if str(distortion_mode).strip().lower() == "off":
        return ResolvedDistortion()
    saved_profile = (
        load_distortion_profile(saved_path)
        if use_saved_distortion and Path(saved_path).expanduser().exists()
        else None
    )
    if saved_profile is not None:
        return ResolvedDistortion(
            enabled=True,
            source="saved",
            confidence=float(saved_profile.confidence),
            active_path=str(Path(saved_path).expanduser()),
            profile=saved_profile,
            line_count=int(saved_profile.line_count),
            frame_count_used=int(saved_profile.frame_count_used),
            fit_score=float(saved_profile.fit_score),
            lens_model=str(saved_profile.model or DISTORTION_MODEL_PINHOLE),
        )
    return ResolvedDistortion()


def apply_distortion_profile(frame: np.ndarray, profile: DistortionProfile | None) -> np.ndarray:
    if not _distortion_deps_available() or profile is None or frame is None or frame.size == 0:
        return frame
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        return frame
    camera_matrix = np.asarray(profile.camera_matrix, dtype=np.float64).reshape(3, 3).copy()
    dist_coeffs = np.asarray(profile.dist_coeffs, dtype=np.float64).reshape(1, -1)
    source_width = max(1, int(profile.image_size[0]))
    source_height = max(1, int(profile.image_size[1]))
    if (source_width, source_height) != (width, height):
        scale_x = width / float(source_width)
        scale_y = height / float(source_height)
        camera_matrix[0, 0] *= scale_x
        camera_matrix[0, 2] *= scale_x
        camera_matrix[1, 1] *= scale_y
        camera_matrix[1, 2] *= scale_y
    return _undistort_frame(
        frame,
        camera_matrix,
        dist_coeffs,
        str(profile.model or DISTORTION_MODEL_PINHOLE),
    )
