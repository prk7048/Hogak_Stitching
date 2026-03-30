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


DISTORTION_SCHEMA_VERSION = 3
DISTORTION_HINTS_SCHEMA_VERSION = 1
DISTORTION_MODEL_PINHOLE = "opencv_pinhole"
DISTORTION_MODEL_FISHEYE = "opencv_fisheye"
DISTORTION_SOURCE_MANUAL = "manual_line_selection"
DISTORTION_SOURCE_MANUAL_GUIDED = "manual_line_guided_auto_fit"
DISTORTION_SOURCE_EXTERNAL = "external_calibration"
MIN_MANUAL_LINE_CONFIDENCE = 0.32
MIN_MANUAL_LINE_COUNT = 4
RECOMMENDED_MANUAL_LINE_COUNT = 6
MAX_MANUAL_ESTIMATION_WIDTH = 1280
MAX_SUPPORT_POINTS_PER_LINE = 192
DEFAULT_GUIDED_FRAME_COUNT = 8
MIN_ACCEPTABLE_IMPROVEMENT_RATIO = 0.08
MIN_CLEAR_IMPROVEMENT_RATIO = 0.14
MIN_RESIDUAL_DELTA_PX = 0.18
MIN_GUIDED_SUPPORT_POINTS = 96
MIN_GUIDED_FIT_SCORE = 0.10
MIN_GUIDED_EDGE_COVERAGE = 0.25
MIN_GUIDED_SUPPORT_DISTRIBUTION = 0.25
MIN_GUIDED_RELATIVE_IMPROVEMENT = 0.07
MIN_GUIDED_ABSOLUTE_IMPROVEMENT = 0.02
MIN_PINHOLE_CONFIDENCE = 0.40
MIN_FISHEYE_OBJECTIVE_ADVANTAGE = 0.05
MIN_FISHEYE_FIT_ADVANTAGE = 0.05
DEFAULT_PROJECTION_ALPHA = 0.04
MAX_BLACK_BORDER_RATIO = 0.16
MIN_USABLE_AREA_RATIO = 0.80
MAX_SESSION_BLACK_BORDER_RATIO = 0.18
MIN_SESSION_USABLE_AREA_RATIO = 0.78
PREVIEW_MAX_WIDTH = 1820
PREVIEW_MAX_HEIGHT = 980
CONTROL_PANEL_WIDTH = 1560


@dataclass(slots=True)
class DistortionLensMetadata:
    lens_model_hint: str = "auto"
    horizontal_fov_deg: float | None = None
    vertical_fov_deg: float | None = None
    camera_model: str = ""
    lens_type: str = ""
    focal_length_range_mm: tuple[float, float] | None = None
    horizontal_fov_range_deg: tuple[float, float] | None = None
    vertical_fov_range_deg: tuple[float, float] | None = None
    diagonal_fov_range_deg: tuple[float, float] | None = None
    metadata_source: str = "user"

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
        if self.lens_type:
            payload["lens_type"] = str(self.lens_type)
        if self.focal_length_range_mm is not None:
            payload["focal_length_range_mm"] = [
                float(self.focal_length_range_mm[0]),
                float(self.focal_length_range_mm[1]),
            ]
        if self.horizontal_fov_range_deg is not None:
            payload["horizontal_fov_range_deg"] = [
                float(self.horizontal_fov_range_deg[0]),
                float(self.horizontal_fov_range_deg[1]),
            ]
        if self.vertical_fov_range_deg is not None:
            payload["vertical_fov_range_deg"] = [
                float(self.vertical_fov_range_deg[0]),
                float(self.vertical_fov_range_deg[1]),
            ]
        if self.diagonal_fov_range_deg is not None:
            payload["diagonal_fov_range_deg"] = [
                float(self.diagonal_fov_range_deg[0]),
                float(self.diagonal_fov_range_deg[1]),
            ]
        if self.metadata_source:
            payload["metadata_source"] = str(self.metadata_source)
        return payload


KNOWN_CAMERA_SPECS: dict[str, dict[str, Any]] = {
    "DH-IPC-HFW4841T-ZAS": {
        "lens_model_hint": "pinhole",
        "lens_type": "motorized_vari_focal",
        "focal_length_range_mm": (2.7, 13.5),
        "horizontal_fov_range_deg": (31.0, 113.0),
        "vertical_fov_range_deg": (18.0, 58.0),
        "diagonal_fov_range_deg": (36.0, 138.0),
        # Zoom position is unknown at runtime, so use a midpoint prior only for initialization.
        "default_horizontal_fov_deg": 72.0,
        "default_vertical_fov_deg": 38.0,
    },
}


@dataclass(slots=True)
class DistortionProfile:
    camera_slot: str
    model: str
    image_size: tuple[int, int]
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    projection_matrix: np.ndarray
    source: str
    confidence: float
    saved_at_epoch_sec: int
    line_count: int = 0
    frame_count_used: int = 0
    fit_score: float = 0.0
    support_points: int = 0
    lens_metadata_used: dict[str, Any] = field(default_factory=dict)
    straightness_residual: float = 0.0
    raw_straightness_residual: float = 0.0
    edge_coverage: float = 0.0
    support_distribution: float = 0.0
    black_border_ratio: float = 0.0
    usable_area_ratio: float = 1.0
    projection_alpha: float = DEFAULT_PROJECTION_ALPHA
    chosen_projection_mode: str = "camera-matrix"
    chosen_model_reason: str = ""
    candidate_rejected_reason: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "version": DISTORTION_SCHEMA_VERSION,
            "camera_slot": str(self.camera_slot),
            "model": str(self.model),
            "image_size": [int(self.image_size[0]), int(self.image_size[1])],
            "camera_matrix": self.camera_matrix.tolist(),
            "dist_coeffs": self.dist_coeffs.reshape(-1).tolist(),
            "projection_matrix": self.projection_matrix.tolist(),
            "source": str(self.source),
            "confidence": float(self.confidence),
            "saved_at_epoch_sec": int(self.saved_at_epoch_sec),
            "line_count": int(self.line_count),
            "frame_count_used": int(self.frame_count_used),
            "fit_score": float(self.fit_score),
            "support_points": int(self.support_points),
            "lens_metadata_used": dict(self.lens_metadata_used or {}),
            "straightness_residual": float(self.straightness_residual),
            "raw_straightness_residual": float(self.raw_straightness_residual),
            "edge_coverage": float(self.edge_coverage),
            "support_distribution": float(self.support_distribution),
            "black_border_ratio": float(self.black_border_ratio),
            "usable_area_ratio": float(self.usable_area_ratio),
            "projection_alpha": float(self.projection_alpha),
            "chosen_projection_mode": str(self.chosen_projection_mode or ""),
            "chosen_model_reason": str(self.chosen_model_reason or ""),
            "candidate_rejected_reason": str(self.candidate_rejected_reason or ""),
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
    straightness_residual: float = 0.0
    raw_straightness_residual: float = 0.0
    edge_coverage: float = 0.0
    support_distribution: float = 0.0
    black_border_ratio: float = 0.0
    usable_area_ratio: float = 1.0
    chosen_projection_mode: str = ""
    chosen_model_reason: str = ""
    candidate_rejected_reason: str = ""


@dataclass(slots=True)
class SupportSetEvidence:
    points: np.ndarray
    edge_radius: float = 0.0
    edge_weight: float = 1.0


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
    normalized_camera_model = str(camera_model or "").strip()
    known_spec = KNOWN_CAMERA_SPECS.get(normalized_camera_model)
    normalized_hint = str(lens_model_hint or "auto").strip().lower() or "auto"
    resolved_hint = str(known_spec.get("lens_model_hint")) if known_spec is not None and normalized_hint == "auto" else normalized_hint
    resolved_horizontal_fov_deg = float(horizontal_fov_deg) if horizontal_fov_deg is not None else None
    resolved_vertical_fov_deg = float(vertical_fov_deg) if vertical_fov_deg is not None else None
    metadata_source = "user"
    if known_spec is not None:
        if resolved_horizontal_fov_deg is None:
            default_horizontal = known_spec.get("default_horizontal_fov_deg")
            if default_horizontal is not None:
                resolved_horizontal_fov_deg = float(default_horizontal)
                metadata_source = "camera-spec-midpoint"
        if resolved_vertical_fov_deg is None:
            default_vertical = known_spec.get("default_vertical_fov_deg")
            if default_vertical is not None:
                resolved_vertical_fov_deg = float(default_vertical)
                metadata_source = "camera-spec-midpoint"
    return DistortionLensMetadata(
        lens_model_hint=resolved_hint,
        horizontal_fov_deg=resolved_horizontal_fov_deg,
        vertical_fov_deg=resolved_vertical_fov_deg,
        camera_model=normalized_camera_model,
        lens_type=str(known_spec.get("lens_type") or "") if known_spec is not None else "",
        focal_length_range_mm=tuple(known_spec["focal_length_range_mm"]) if known_spec is not None and known_spec.get("focal_length_range_mm") else None,
        horizontal_fov_range_deg=tuple(known_spec["horizontal_fov_range_deg"]) if known_spec is not None and known_spec.get("horizontal_fov_range_deg") else None,
        vertical_fov_range_deg=tuple(known_spec["vertical_fov_range_deg"]) if known_spec is not None and known_spec.get("vertical_fov_range_deg") else None,
        diagonal_fov_range_deg=tuple(known_spec["diagonal_fov_range_deg"]) if known_spec is not None and known_spec.get("diagonal_fov_range_deg") else None,
        metadata_source=metadata_source,
    )


def saved_distortion_available(left_path: str | Path, right_path: str | Path) -> bool:
    return Path(left_path).expanduser().exists() and Path(right_path).expanduser().exists()


def load_line_hints(path: str | Path) -> list[ManualLineSegment]:
    file_path = Path(path).expanduser()
    if not file_path.exists():
        return []
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        return []
    lines: list[ManualLineSegment] = []
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        try:
            start_xy = (float(start[0]), float(start[1]))
            end_xy = (float(end[0]), float(end[1]))
        except (TypeError, ValueError, IndexError):
            continue
        candidate = ManualLineSegment(start=start_xy, end=end_xy)
        if candidate.length >= 4.0:
            lines.append(candidate)
    return lines


def save_line_hints(
    path: str | Path,
    *,
    camera_slot: str,
    image_size: tuple[int, int],
    lines: list[ManualLineSegment],
) -> None:
    file_path = Path(path).expanduser()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": DISTORTION_HINTS_SCHEMA_VERSION,
        "camera_slot": str(camera_slot),
        "image_size": [int(image_size[0]), int(image_size[1])],
        "saved_at_epoch_sec": int(time.time()),
        "lines": [
            {
                "start": [float(line.start[0]), float(line.start[1])],
                "end": [float(line.end[0]), float(line.end[1])],
            }
            for line in lines
        ],
    }
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
        projection_matrix_payload = payload.get("projection_matrix")
        if projection_matrix_payload is None:
            projection_matrix = camera_matrix.copy()
        else:
            projection_matrix = np.asarray(projection_matrix_payload, dtype=np.float64).reshape(3, 3)
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
        projection_matrix=projection_matrix,
        source=str(payload.get("source") or DISTORTION_SOURCE_EXTERNAL),
        confidence=float(payload.get("confidence") or 0.0),
        saved_at_epoch_sec=int(payload.get("saved_at_epoch_sec") or 0),
        line_count=int(payload.get("line_count") or 0),
        frame_count_used=int(payload.get("frame_count_used") or 0),
        fit_score=float(payload.get("fit_score") or 0.0),
        support_points=int(payload.get("support_points") or 0),
        lens_metadata_used=cast(dict[str, Any], lens_metadata_used),
        straightness_residual=float(payload.get("straightness_residual") or 0.0),
        raw_straightness_residual=float(payload.get("raw_straightness_residual") or 0.0),
        edge_coverage=float(payload.get("edge_coverage") or 0.0),
        support_distribution=float(payload.get("support_distribution") or 0.0),
        black_border_ratio=float(payload.get("black_border_ratio") or 0.0),
        usable_area_ratio=float(payload.get("usable_area_ratio") or 1.0),
        projection_alpha=float(payload.get("projection_alpha") or DEFAULT_PROJECTION_ALPHA),
        chosen_projection_mode=str(payload.get("chosen_projection_mode") or "camera-matrix"),
        chosen_model_reason=str(payload.get("chosen_model_reason") or ""),
        candidate_rejected_reason=str(payload.get("candidate_rejected_reason") or ""),
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


def _resolve_projection_matrix(
    *,
    model: str,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    width: int,
    height: int,
    alpha: float = DEFAULT_PROJECTION_ALPHA,
) -> tuple[np.ndarray, str]:
    projection_alpha = float(np.clip(alpha, 0.0, 1.0))
    image_size = (max(1, int(width)), max(1, int(height)))
    try:
        if str(model or DISTORTION_MODEL_PINHOLE) == DISTORTION_MODEL_FISHEYE:
            projection = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
                camera_matrix.astype(np.float64),
                dist_coeffs.reshape(-1, 1).astype(np.float64),
                image_size,
                np.eye(3, dtype=np.float64),
                balance=projection_alpha,
                new_size=image_size,
            )
            return np.asarray(projection, dtype=np.float64).reshape(3, 3), f"fisheye-balance:{projection_alpha:.2f}"
        projection, _ = cv2.getOptimalNewCameraMatrix(
            camera_matrix.astype(np.float64),
            dist_coeffs.astype(np.float64),
            image_size,
            projection_alpha,
            image_size,
            centerPrincipalPoint=True,
        )
        return np.asarray(projection, dtype=np.float64).reshape(3, 3), f"optimal-alpha:{projection_alpha:.2f}"
    except Exception:
        return np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3).copy(), "camera-matrix"


def _visual_quality_metrics(
    *,
    model: str,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    projection_matrix: np.ndarray,
    width: int,
    height: int,
) -> dict[str, float]:
    mask = np.full((max(1, int(height)), max(1, int(width))), 255, dtype=np.uint8)
    if str(model or DISTORTION_MODEL_PINHOLE) == DISTORTION_MODEL_FISHEYE:
        map_x, map_y = cv2.fisheye.initUndistortRectifyMap(
            camera_matrix.astype(np.float64),
            dist_coeffs.reshape(-1, 1).astype(np.float64),
            np.eye(3, dtype=np.float64),
            projection_matrix.astype(np.float64),
            (int(width), int(height)),
            cv2.CV_32FC1,
        )
    else:
        map_x, map_y = cv2.initUndistortRectifyMap(
            camera_matrix.astype(np.float64),
            dist_coeffs.astype(np.float64),
            None,
            projection_matrix.astype(np.float64),
            (int(width), int(height)),
            cv2.CV_32FC1,
        )
    remapped = cv2.remap(mask, map_x, map_y, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    valid_mask = remapped > 0
    valid_ratio = float(np.count_nonzero(valid_mask)) / float(max(1, valid_mask.size))
    usable_area_ratio = valid_ratio
    if np.any(valid_mask):
        ys, xs = np.where(valid_mask)
        usable_area_ratio = float(((int(xs.max()) - int(xs.min()) + 1) * (int(ys.max()) - int(ys.min()) + 1))) / float(max(1, valid_mask.size))
    return {
        "black_border_ratio": float(np.clip(1.0 - valid_ratio, 0.0, 1.0)),
        "usable_area_ratio": float(np.clip(usable_area_ratio, 0.0, 1.0)),
    }


def _undistort_frame(
    frame: np.ndarray,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    model: str,
    *,
    projection_matrix: np.ndarray | None = None,
) -> np.ndarray:
    if not _distortion_deps_available():
        raise RuntimeError("distortion dependencies unavailable")
    height, width = frame.shape[:2]
    projection = (
        np.asarray(projection_matrix, dtype=np.float64).reshape(3, 3)
        if projection_matrix is not None
        else np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)
    )
    if str(model or DISTORTION_MODEL_PINHOLE) == DISTORTION_MODEL_FISHEYE:
        map_x, map_y = cv2.fisheye.initUndistortRectifyMap(
            camera_matrix,
            dist_coeffs.reshape(-1, 1).astype(np.float64),
            np.eye(3, dtype=np.float64),
            projection,
            (width, height),
            cv2.CV_32FC1,
        )
    else:
        map_x, map_y = cv2.initUndistortRectifyMap(
            camera_matrix,
            dist_coeffs,
            None,
            projection,
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


def _line_edge_radius(line: ManualLineSegment, width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        return 0.0
    mx, my = line.midpoint()
    dx = float(mx - (width * 0.5))
    dy = float(my - (height * 0.5))
    max_radius = max(1.0, float(np.hypot(width * 0.5, height * 0.5)))
    return float(max(0.0, min(1.0, np.hypot(dx, dy) / max_radius)))


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
    projection_matrix: np.ndarray | None = None,
) -> np.ndarray:
    if points.size == 0:
        return np.empty((0, 2), dtype=np.float32)
    projection = (
        np.asarray(projection_matrix, dtype=np.float64).reshape(3, 3)
        if projection_matrix is not None
        else np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)
    )
    if model == DISTORTION_MODEL_FISHEYE:
        undistorted = cv2.fisheye.undistortPoints(
            points.reshape(-1, 1, 2).astype(np.float32),
            camera_matrix,
            dist_coeffs.reshape(4, 1).astype(np.float64),
            P=projection,
        )
    else:
        undistorted = cv2.undistortPoints(
            points.reshape(-1, 1, 2).astype(np.float32),
            camera_matrix,
            dist_coeffs.astype(np.float64),
            P=projection,
        )
    return undistorted.reshape(-1, 2).astype(np.float32)


def _fit_line_error(points: np.ndarray) -> float:
    if points.shape[0] < 2:
        return 1e9
    vx, vy, x0, y0 = cv2.fitLine(points, cv2.DIST_L2, 0, 0.01, 0.01).reshape(-1)
    distances = np.abs((points[:, 0] - x0) * vy - (points[:, 1] - y0) * vx)
    return float(np.mean(distances))


def _score_support_sets_for_model(
    support_sets: list[SupportSetEvidence],
    *,
    model: str,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    projection_matrix: np.ndarray | None = None,
) -> tuple[float, int, int, float, float]:
    total_error = 0.0
    total_weight = 0.0
    total_points = 0
    usable_lines = 0
    edge_lines = 0
    edge_radius_sum = 0.0
    for support in support_sets:
        points = np.asarray(support.points, dtype=np.float32)
        if points.shape[0] < 6:
            continue
        corrected = _undistort_points_model(
            model,
            points,
            camera_matrix,
            dist_coeffs,
            projection_matrix=projection_matrix,
        )
        line_error = _fit_line_error(corrected)
        point_weight = max(1.0, float(points.shape[0]) / 48.0)
        edge_weight = max(1.0, float(support.edge_weight))
        line_weight = point_weight * edge_weight
        total_error += line_error * line_weight
        total_weight += line_weight
        total_points += int(points.shape[0])
        usable_lines += 1
        edge_radius_sum += float(support.edge_radius)
        if float(support.edge_radius) >= 0.52:
            edge_lines += 1
    if usable_lines <= 0 or total_points <= 0 or total_weight <= 0.0:
        return 1e9, 0, 0, 0.0, 0.0
    return (
        total_error / total_weight,
        usable_lines,
        total_points,
        float(edge_lines) / float(max(1, usable_lines)),
        edge_radius_sum / float(max(1, usable_lines)),
    )


def _candidate_penalty(
    model: str,
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    projection_matrix: np.ndarray,
    width: int,
    height: int,
    black_border_ratio: float,
    usable_area_ratio: float,
) -> float:
    fx = float(camera_matrix[0, 0])
    fy = float(camera_matrix[1, 1])
    cx = float(camera_matrix[0, 2])
    cy = float(camera_matrix[1, 2])
    focal_ref = float(max(width, height))
    proj_fx = float(projection_matrix[0, 0])
    proj_fy = float(projection_matrix[1, 1])
    proj_cx = float(projection_matrix[0, 2])
    proj_cy = float(projection_matrix[1, 2])
    focal_penalty = abs(math.log(max(1e-6, fx / focal_ref))) + abs(math.log(max(1e-6, fy / focal_ref)))
    zoom_penalty = abs(math.log(max(1e-6, proj_fx / max(1e-6, fx)))) + abs(math.log(max(1e-6, proj_fy / max(1e-6, fy))))
    center_penalty = (
        abs(cx - (width * 0.5)) / max(1.0, width * 0.20)
        + abs(cy - (height * 0.5)) / max(1.0, height * 0.20)
    )
    projection_center_penalty = (
        abs(proj_cx - (width * 0.5)) / max(1.0, width * 0.16)
        + abs(proj_cy - (height * 0.5)) / max(1.0, height * 0.16)
    )
    coeffs = np.abs(dist_coeffs.reshape(-1))
    coeff_penalty = float(np.mean(coeffs)) * (0.32 if model == DISTORTION_MODEL_FISHEYE else 0.26)
    if model != DISTORTION_MODEL_FISHEYE and coeffs.size >= 5:
        coeff_penalty += float(coeffs[4]) * 0.65
        if coeffs.size >= 4:
            coeff_penalty += float(coeffs[2] + coeffs[3]) * 1.2
    black_border_penalty = max(0.0, black_border_ratio - 0.02) * 8.0
    area_penalty = max(0.0, 0.94 - usable_area_ratio) * 6.2
    return float(
        (focal_penalty * 0.18)
        + (zoom_penalty * 0.82)
        + (center_penalty * 0.18)
        + (projection_center_penalty * 0.30)
        + coeff_penalty
        + black_border_penalty
        + area_penalty
    )


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
    support_sets: list[SupportSetEvidence],
    width: int,
    height: int,
) -> dict[str, Any]:
    camera_matrix = _build_camera_matrix_from_state(state)
    dist_coeffs = _build_dist_coeffs_from_state(model, state)
    projection_matrix, projection_mode = _resolve_projection_matrix(
        model=model,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        width=width,
        height=height,
    )
    visual_quality = _visual_quality_metrics(
        model=model,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        projection_matrix=projection_matrix,
        width=width,
        height=height,
    )
    fit_error, usable_lines, total_points, edge_coverage, support_distribution = _score_support_sets_for_model(
        support_sets,
        model=model,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        projection_matrix=projection_matrix,
    )
    penalty = _candidate_penalty(
        model,
        camera_matrix,
        dist_coeffs,
        projection_matrix,
        width,
        height,
        float(visual_quality.get("black_border_ratio") or 0.0),
        float(visual_quality.get("usable_area_ratio") or 1.0),
    )
    return {
        "fit_error": float(fit_error),
        "usable_lines": int(usable_lines),
        "total_points": int(total_points),
        "camera_matrix": camera_matrix,
        "dist_coeffs": dist_coeffs,
        "projection_matrix": projection_matrix,
        "projection_alpha": float(DEFAULT_PROJECTION_ALPHA),
        "chosen_projection_mode": str(projection_mode),
        "penalty": float(penalty),
        "objective": float(fit_error + penalty),
        "edge_coverage": float(edge_coverage),
        "support_distribution": float(support_distribution),
        "black_border_ratio": float(visual_quality.get("black_border_ratio") or 0.0),
        "usable_area_ratio": float(visual_quality.get("usable_area_ratio") or 1.0),
    }


def _clamp_state_for_model(model: str, state: dict[str, float], width: int, height: int) -> dict[str, float]:
    clamped = dict(state)
    focal_ref = float(max(width, height))
    clamped["fx"] = float(np.clip(clamped["fx"], focal_ref * 0.82, focal_ref * 1.22))
    clamped["fy"] = float(np.clip(clamped["fy"], focal_ref * 0.82, focal_ref * 1.22))
    clamped["cx"] = float(np.clip(clamped["cx"], width * 0.47, width * 0.53))
    clamped["cy"] = float(np.clip(clamped["cy"], height * 0.47, height * 0.53))
    if model == DISTORTION_MODEL_FISHEYE:
        for key in ("k1", "k2", "k3", "k4"):
            clamped[key] = float(np.clip(clamped[key], -0.40, 0.40))
    else:
        for key in ("k1", "k2"):
            clamped[key] = float(np.clip(clamped[key], -0.28, 0.28))
        clamped["k3"] = float(np.clip(clamped["k3"], -0.05, 0.05))
        clamped["p1"] = float(np.clip(clamped["p1"], -0.008, 0.008))
        clamped["p2"] = float(np.clip(clamped["p2"], -0.008, 0.008))
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
            {"k1": 0.08, "k2": 0.05},
            {"fx": width_step * 0.14, "fy": height_step * 0.14, "cx": width * 0.005, "cy": height * 0.005, "k1": 0.03, "k2": 0.025, "k3": 0.02},
            {"fx": width_step * 0.06, "fy": height_step * 0.06, "cx": width * 0.002, "cy": height * 0.002, "k1": 0.012, "k2": 0.012, "k3": 0.008, "k4": 0.006},
        ]
    return [
        {"k1": 0.08, "k2": 0.05},
        {"fx": width_step * 0.14, "fy": height_step * 0.14, "cx": width * 0.005, "cy": height * 0.005, "k1": 0.025, "k2": 0.018},
        {"fx": width_step * 0.06, "fy": height_step * 0.06, "cx": width * 0.002, "cy": height * 0.002, "k1": 0.010, "k2": 0.008, "k3": 0.004},
        {"fx": width_step * 0.03, "fy": height_step * 0.03, "cx": width * 0.001, "cy": height * 0.001, "k1": 0.004, "k2": 0.003, "k3": 0.002, "p1": 0.0006, "p2": 0.0006},
    ]


def _optimize_model_state(
    model: str,
    support_sets: list[SupportSetEvidence],
    width: int,
    height: int,
    metadata: DistortionLensMetadata,
) -> dict[str, Any]:
    state = _clamp_state_for_model(model, _initial_state_for_model(model, width, height, metadata), width, height)
    best = _evaluate_candidate_state(model, state, support_sets, width, height)
    best["state"] = dict(state)
    staged_param_names: list[list[str]]
    if model == DISTORTION_MODEL_FISHEYE:
        staged_param_names = [
            ["k1", "k2"],
            ["fx", "fy", "cx", "cy", "k1", "k2", "k3"],
            ["fx", "fy", "cx", "cy", "k1", "k2", "k3", "k4"],
        ]
    else:
        staged_param_names = [
            ["k1", "k2"],
            ["fx", "fy", "cx", "cy", "k1", "k2"],
            ["fx", "fy", "cx", "cy", "k1", "k2", "k3"],
            ["fx", "fy", "cx", "cy", "k1", "k2", "k3", "p1", "p2"],
        ]
    for step_index, step_map in enumerate(_coordinate_descent_steps(model, width, height)):
        param_names = staged_param_names[min(step_index, len(staged_param_names) - 1)]
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


def _scale_camera_matrix_for_size(
    camera_matrix: np.ndarray,
    *,
    source_size: tuple[int, int],
    target_width: int,
    target_height: int,
) -> np.ndarray:
    scaled = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3).copy()
    source_width = max(1, int(source_size[0]))
    source_height = max(1, int(source_size[1]))
    scale_x = float(target_width) / float(source_width)
    scale_y = float(target_height) / float(source_height)
    scaled[0, 0] *= scale_x
    scaled[0, 2] *= scale_x
    scaled[1, 1] *= scale_y
    scaled[1, 2] *= scale_y
    return scaled


def evaluate_profile_against_guided_lines(
    frames: list[np.ndarray],
    lines: list[ManualLineSegment],
    *,
    profile: DistortionProfile,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> dict[str, Any] | None:
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
    raw_residual, baseline_lines, baseline_support_points, _, _ = _score_support_sets_for_model(
        support_sets,
        model=DISTORTION_MODEL_PINHOLE,
        camera_matrix=baseline_camera_matrix,
        dist_coeffs=baseline_dist_coeffs,
    )
    if baseline_lines < MIN_MANUAL_LINE_COUNT or baseline_support_points < 64 or not np.isfinite(raw_residual):
        return None
    scaled_camera_matrix = _scale_camera_matrix_for_size(
        profile.camera_matrix,
        source_size=profile.image_size,
        target_width=width,
        target_height=height,
    )
    scaled_projection_matrix = _scale_camera_matrix_for_size(
        profile.projection_matrix if profile.projection_matrix is not None else profile.camera_matrix,
        source_size=profile.image_size,
        target_width=width,
        target_height=height,
    )
    dist_coeffs = np.asarray(profile.dist_coeffs, dtype=np.float64).reshape(1, -1)
    straightness_residual, usable_line_count, support_points, edge_coverage, support_distribution = _score_support_sets_for_model(
        support_sets,
        model=str(profile.model or DISTORTION_MODEL_PINHOLE),
        camera_matrix=scaled_camera_matrix,
        dist_coeffs=dist_coeffs,
        projection_matrix=scaled_projection_matrix,
    )
    visual_quality = _visual_quality_metrics(
        model=str(profile.model or DISTORTION_MODEL_PINHOLE),
        camera_matrix=scaled_camera_matrix,
        dist_coeffs=dist_coeffs,
        projection_matrix=scaled_projection_matrix,
        width=width,
        height=height,
    )
    penalty = _candidate_penalty(
        str(profile.model or DISTORTION_MODEL_PINHOLE),
        scaled_camera_matrix,
        dist_coeffs,
        scaled_projection_matrix,
        width,
        height,
        float(visual_quality.get("black_border_ratio") or 0.0),
        float(visual_quality.get("usable_area_ratio") or 1.0),
    )
    improvement = max(0.0, float(raw_residual) - float(straightness_residual))
    improvement_ratio = improvement / max(1e-6, float(raw_residual))
    return {
        "raw_straightness_residual": float(raw_residual),
        "straightness_residual": float(straightness_residual),
        "fit_score": float(max(0.0, min(1.0, improvement_ratio))),
        "line_count": int(usable_line_count),
        "support_points": int(support_points),
        "frame_count_used": int(frame_support_hits),
        "edge_coverage": float(edge_coverage),
        "support_distribution": float(support_distribution),
        "black_border_ratio": float(visual_quality.get("black_border_ratio") or 0.0),
        "usable_area_ratio": float(visual_quality.get("usable_area_ratio") or 1.0),
        "projection_alpha": float(profile.projection_alpha or DEFAULT_PROJECTION_ALPHA),
        "chosen_projection_mode": str(profile.chosen_projection_mode or "camera-matrix"),
        "projection_matrix": scaled_projection_matrix,
        "penalty": float(penalty),
        "objective": float(straightness_residual + penalty),
    }


def _guided_line_support_sets(
    frames: list[np.ndarray],
    lines: list[ManualLineSegment],
) -> tuple[list[SupportSetEvidence], int]:
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
    support_sets: list[SupportSetEvidence] = []
    reference_height, reference_width = reduced_frames[-1].shape[:2]
    for line_index, line_support in enumerate(aggregated_support):
        if not line_support:
            continue
        merged = np.vstack(line_support).astype(np.float32)
        if merged.shape[0] > MAX_SUPPORT_POINTS_PER_LINE:
            indexes = np.linspace(0, merged.shape[0] - 1, MAX_SUPPORT_POINTS_PER_LINE).astype(np.int32)
            merged = merged[indexes]
        edge_radius = _line_edge_radius(scaled_lines[line_index], reference_width, reference_height)
        support_sets.append(
            SupportSetEvidence(
                points=merged,
                edge_radius=edge_radius,
                edge_weight=1.0 + (edge_radius * 1.15),
            )
        )
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
    baseline_error, baseline_lines, baseline_support_points, baseline_edge_coverage, baseline_support_distribution = _score_support_sets_for_model(
        support_sets,
        model=DISTORTION_MODEL_PINHOLE,
        camera_matrix=baseline_camera_matrix,
        dist_coeffs=baseline_dist_coeffs,
    )
    if baseline_lines < MIN_MANUAL_LINE_COUNT or baseline_support_points < MIN_GUIDED_SUPPORT_POINTS or not np.isfinite(baseline_error):
        return None

    lens_hint = str(metadata.lens_model_hint or "auto").strip().lower() or "auto"
    candidate_models = [DISTORTION_MODEL_PINHOLE]
    if lens_hint == "fisheye":
        candidate_models = [DISTORTION_MODEL_FISHEYE, DISTORTION_MODEL_PINHOLE]

    best_result: dict[str, Any] | None = None
    model_results: dict[str, dict[str, Any]] = {}
    for model in candidate_models:
        candidate = _optimize_model_state(model, support_sets, width, height, metadata)
        usable_lines_count = int(candidate["usable_lines"])
        support_points = int(candidate["total_points"])
        if usable_lines_count < MIN_MANUAL_LINE_COUNT or support_points < MIN_GUIDED_SUPPORT_POINTS:
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
        candidate["raw_straightness_residual"] = float(baseline_error)
        candidate["straightness_residual"] = float(fit_error)
        candidate["residual_delta"] = float(improvement)
        candidate["baseline_edge_coverage"] = float(baseline_edge_coverage)
        candidate["edge_coverage"] = float(candidate.get("edge_coverage") or 0.0)
        candidate["support_distribution"] = float(candidate.get("support_distribution") or 0.0)
        candidate["black_border_ratio"] = float(candidate.get("black_border_ratio") or 0.0)
        candidate["usable_area_ratio"] = float(candidate.get("usable_area_ratio") or 1.0)
        candidate["chosen_model_reason"] = (
            f"model={model} residual={fit_error:.4f} raw={baseline_error:.4f} "
            f"delta={improvement:.4f} fit={candidate['fit_score']:.3f} "
            f"coverage={float(candidate.get('edge_coverage') or 0.0):.2f} "
            f"distribution={float(candidate.get('support_distribution') or 0.0):.2f} "
            f"border={float(candidate.get('black_border_ratio') or 0.0):.2f} "
            f"usable={float(candidate.get('usable_area_ratio') or 1.0):.2f} "
            f"penalty={penalty:.4f}"
        )
        model_results[model] = dict(candidate)
        if best_result is None:
            best_result = candidate
            continue
        current_key = (float(candidate["confidence"]), float(candidate["fit_score"]), -float(candidate["objective"]))
        best_key = (float(best_result["confidence"]), float(best_result["fit_score"]), -float(best_result["objective"]))
        if current_key > best_key:
            best_result = candidate

    if (
        best_result is not None
        and best_result.get("model") == DISTORTION_MODEL_FISHEYE
        and DISTORTION_MODEL_PINHOLE in model_results
    ):
        pinhole_result = model_results[DISTORTION_MODEL_PINHOLE]
        fisheye_result = best_result
        objective_advantage = float(pinhole_result["objective"]) - float(fisheye_result["objective"])
        fit_advantage = float(fisheye_result["fit_score"]) - float(pinhole_result["fit_score"])
        if objective_advantage < MIN_FISHEYE_OBJECTIVE_ADVANTAGE and fit_advantage < MIN_FISHEYE_FIT_ADVANTAGE:
            best_result = pinhole_result

    if best_result is None:
        return None

    residual_delta = float(best_result.get("residual_delta") or 0.0)
    required_delta = max(
        MIN_GUIDED_ABSOLUTE_IMPROVEMENT,
        float(best_result.get("raw_straightness_residual") or 0.0) * MIN_GUIDED_RELATIVE_IMPROVEMENT,
    )
    if (
        float(best_result["confidence"]) < max(MIN_MANUAL_LINE_CONFIDENCE, MIN_PINHOLE_CONFIDENCE)
        or float(best_result["fit_score"]) < MIN_GUIDED_FIT_SCORE
        or int(best_result.get("support_points") or 0) < MIN_GUIDED_SUPPORT_POINTS
        or float(best_result.get("edge_coverage") or 0.0) < MIN_GUIDED_EDGE_COVERAGE
        or float(best_result.get("support_distribution") or 0.0) < MIN_GUIDED_SUPPORT_DISTRIBUTION
        or float(best_result.get("black_border_ratio") or 0.0) > MAX_BLACK_BORDER_RATIO
        or float(best_result.get("usable_area_ratio") or 0.0) < MIN_USABLE_AREA_RATIO
        or residual_delta < required_delta
    ):
        return None

    camera_matrix = np.asarray(best_result["camera_matrix"], dtype=np.float64).reshape(3, 3).copy()
    projection_matrix = np.asarray(best_result["projection_matrix"], dtype=np.float64).reshape(3, 3).copy()
    if abs(scale - 1.0) > 1e-6:
        inv_scale = 1.0 / scale
        camera_matrix[0, 0] *= inv_scale
        camera_matrix[1, 1] *= inv_scale
        camera_matrix[0, 2] *= inv_scale
        camera_matrix[1, 2] *= inv_scale
        projection_matrix[0, 0] *= inv_scale
        projection_matrix[1, 1] *= inv_scale
        projection_matrix[0, 2] *= inv_scale
        projection_matrix[1, 2] *= inv_scale

    original_height, original_width = reference_frame.shape[:2]
    return DistortionProfile(
        camera_slot=str(camera_slot),
        model=str(best_result["model"]),
        image_size=(int(original_width), int(original_height)),
        camera_matrix=camera_matrix,
        dist_coeffs=np.asarray(best_result["dist_coeffs"], dtype=np.float64).reshape(1, -1),
        projection_matrix=projection_matrix,
        source=DISTORTION_SOURCE_MANUAL_GUIDED,
        confidence=float(best_result["confidence"]),
        saved_at_epoch_sec=int(time.time()),
        line_count=int(best_result["line_count"]),
        frame_count_used=int(best_result["frame_count_used"]),
        fit_score=float(best_result["fit_score"]),
        support_points=int(best_result["support_points"]),
        lens_metadata_used=metadata.to_json_dict(),
        straightness_residual=float(best_result["straightness_residual"]),
        raw_straightness_residual=float(best_result["raw_straightness_residual"]),
        edge_coverage=float(best_result["edge_coverage"]),
        support_distribution=float(best_result["support_distribution"]),
        black_border_ratio=float(best_result.get("black_border_ratio") or 0.0),
        usable_area_ratio=float(best_result.get("usable_area_ratio") or 1.0),
        projection_alpha=float(best_result.get("projection_alpha") or DEFAULT_PROJECTION_ALPHA),
        chosen_projection_mode=str(best_result.get("chosen_projection_mode") or "camera-matrix"),
        chosen_model_reason=str(best_result["chosen_model_reason"] or ""),
        candidate_rejected_reason="",
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


def _clone_profile(profile: DistortionProfile, *, source: str | None = None) -> DistortionProfile:
    return DistortionProfile(
        camera_slot=str(profile.camera_slot),
        model=str(profile.model),
        image_size=(int(profile.image_size[0]), int(profile.image_size[1])),
        camera_matrix=np.asarray(profile.camera_matrix, dtype=np.float64).reshape(3, 3).copy(),
        dist_coeffs=np.asarray(profile.dist_coeffs, dtype=np.float64).reshape(1, -1).copy(),
        projection_matrix=np.asarray(profile.projection_matrix, dtype=np.float64).reshape(3, 3).copy(),
        source=str(source if source is not None else profile.source),
        confidence=float(profile.confidence),
        saved_at_epoch_sec=int(time.time()),
        line_count=int(profile.line_count),
        frame_count_used=int(profile.frame_count_used),
        fit_score=float(profile.fit_score),
        support_points=int(profile.support_points),
        lens_metadata_used=dict(profile.lens_metadata_used or {}),
        straightness_residual=float(profile.straightness_residual),
        raw_straightness_residual=float(profile.raw_straightness_residual),
        edge_coverage=float(profile.edge_coverage),
        support_distribution=float(profile.support_distribution),
        black_border_ratio=float(profile.black_border_ratio),
        usable_area_ratio=float(profile.usable_area_ratio),
        projection_alpha=float(profile.projection_alpha),
        chosen_projection_mode=str(profile.chosen_projection_mode or ""),
        chosen_model_reason=str(profile.chosen_model_reason or ""),
        candidate_rejected_reason=str(profile.candidate_rejected_reason or ""),
    )


def clone_distortion_profile(profile: DistortionProfile | None, *, source: str | None = None) -> DistortionProfile | None:
    if profile is None:
        return None
    return _clone_profile(profile, source=source)


def _profile_to_state(
    profile: DistortionProfile | None,
    *,
    model: str,
    width: int,
    height: int,
    metadata: DistortionLensMetadata,
) -> dict[str, float]:
    state = _initial_state_for_model(model, width, height, metadata)
    if profile is None:
        return state
    camera_matrix = _scale_camera_matrix_for_size(
        np.asarray(profile.camera_matrix, dtype=np.float64).reshape(3, 3),
        source_size=profile.image_size,
        target_width=width,
        target_height=height,
    )
    state["fx"] = float(camera_matrix[0, 0])
    state["fy"] = float(camera_matrix[1, 1])
    state["cx"] = float(camera_matrix[0, 2])
    state["cy"] = float(camera_matrix[1, 2])
    dist = np.asarray(profile.dist_coeffs, dtype=np.float64).reshape(-1)
    if model == DISTORTION_MODEL_FISHEYE:
        state["k1"] = float(dist[0]) if dist.size >= 1 else 0.0
        state["k2"] = float(dist[1]) if dist.size >= 2 else 0.0
        state["k3"] = float(dist[2]) if dist.size >= 3 else 0.0
        state["k4"] = float(dist[3]) if dist.size >= 4 else 0.0
    else:
        state["k1"] = float(dist[0]) if dist.size >= 1 else 0.0
        state["k2"] = float(dist[1]) if dist.size >= 2 else 0.0
        state["p1"] = float(dist[2]) if dist.size >= 3 else 0.0
        state["p2"] = float(dist[3]) if dist.size >= 4 else 0.0
        state["k3"] = float(dist[4]) if dist.size >= 5 else 0.0
    return _clamp_state_for_model(model, state, width, height)


def distortion_profile_to_state(
    profile: DistortionProfile | None,
    *,
    model: str,
    width: int,
    height: int,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> dict[str, float]:
    metadata = build_lens_metadata(
        lens_model_hint=lens_model_hint,
        horizontal_fov_deg=horizontal_fov_deg,
        vertical_fov_deg=vertical_fov_deg,
        camera_model=camera_model,
    )
    return _profile_to_state(
        profile,
        model=str(model or DISTORTION_MODEL_PINHOLE),
        width=int(width),
        height=int(height),
        metadata=metadata,
    )


def _profile_from_state(
    *,
    camera_slot: str,
    model: str,
    width: int,
    height: int,
    state: dict[str, float],
    metadata: DistortionLensMetadata,
    source: str,
) -> DistortionProfile:
    clamped = _clamp_state_for_model(model, state, width, height)
    camera_matrix = _build_camera_matrix_from_state(clamped)
    dist_coeffs = _build_dist_coeffs_from_state(model, clamped)
    projection_matrix, projection_mode = _resolve_projection_matrix(
        model=model,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        width=width,
        height=height,
    )
    return DistortionProfile(
        camera_slot=str(camera_slot),
        model=str(model),
        image_size=(int(width), int(height)),
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        projection_matrix=projection_matrix,
        source=str(source),
        confidence=0.0,
        saved_at_epoch_sec=int(time.time()),
        line_count=0,
        frame_count_used=0,
        fit_score=0.0,
        support_points=0,
        lens_metadata_used=metadata.to_json_dict(),
        straightness_residual=0.0,
        raw_straightness_residual=0.0,
        edge_coverage=0.0,
        support_distribution=0.0,
        black_border_ratio=0.0,
        usable_area_ratio=1.0,
        projection_alpha=DEFAULT_PROJECTION_ALPHA,
        chosen_projection_mode=str(projection_mode),
        chosen_model_reason="",
        candidate_rejected_reason="",
    )


def distortion_profile_from_state(
    *,
    camera_slot: str,
    model: str,
    width: int,
    height: int,
    state: dict[str, float],
    source: str = DISTORTION_SOURCE_MANUAL_GUIDED,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> DistortionProfile:
    metadata = build_lens_metadata(
        lens_model_hint=lens_model_hint,
        horizontal_fov_deg=horizontal_fov_deg,
        vertical_fov_deg=vertical_fov_deg,
        camera_model=camera_model,
    )
    return _profile_from_state(
        camera_slot=str(camera_slot),
        model=str(model or DISTORTION_MODEL_PINHOLE),
        width=int(width),
        height=int(height),
        state=dict(state),
        metadata=metadata,
        source=str(source or DISTORTION_SOURCE_MANUAL_GUIDED),
    )


def _apply_metrics_to_profile(
    profile: DistortionProfile,
    *,
    metrics: dict[str, Any] | None,
    chosen_model_reason: str = "",
) -> DistortionProfile:
    updated = _clone_profile(profile)
    if metrics:
        updated.confidence = float(max(0.0, min(0.99, metrics.get("fit_score") or 0.0)))
        updated.fit_score = float(metrics.get("fit_score") or 0.0)
        updated.line_count = int(metrics.get("line_count") or 0)
        updated.frame_count_used = int(metrics.get("frame_count_used") or 0)
        updated.support_points = int(metrics.get("support_points") or 0)
        updated.straightness_residual = float(metrics.get("straightness_residual") or 0.0)
        updated.raw_straightness_residual = float(metrics.get("raw_straightness_residual") or 0.0)
        updated.edge_coverage = float(metrics.get("edge_coverage") or 0.0)
        updated.support_distribution = float(metrics.get("support_distribution") or 0.0)
        updated.black_border_ratio = float(metrics.get("black_border_ratio") or 0.0)
        updated.usable_area_ratio = float(metrics.get("usable_area_ratio") or 1.0)
        updated.projection_alpha = float(metrics.get("projection_alpha") or DEFAULT_PROJECTION_ALPHA)
        updated.chosen_projection_mode = str(metrics.get("chosen_projection_mode") or updated.chosen_projection_mode or "camera-matrix")
        projection_matrix = metrics.get("projection_matrix")
        if projection_matrix is not None:
            updated.projection_matrix = np.asarray(projection_matrix, dtype=np.float64).reshape(3, 3).copy()
        rejected_reason = str(metrics.get("candidate_rejected_reason") or "").strip()
        if rejected_reason:
            updated.candidate_rejected_reason = rejected_reason
    updated.chosen_model_reason = str(chosen_model_reason or updated.chosen_model_reason or "")
    return updated


def apply_profile_metrics(
    profile: DistortionProfile,
    *,
    metrics: dict[str, Any] | None,
    chosen_model_reason: str = "",
) -> DistortionProfile:
    return _apply_metrics_to_profile(profile, metrics=metrics, chosen_model_reason=chosen_model_reason)


def _undistort_line_for_profile(
    line: ManualLineSegment,
    *,
    profile: DistortionProfile | None,
    width: int,
    height: int,
) -> ManualLineSegment:
    if profile is None:
        return line
    points = np.asarray([line.start, line.end], dtype=np.float32)
    camera_matrix = _scale_camera_matrix_for_size(
        np.asarray(profile.camera_matrix, dtype=np.float64).reshape(3, 3),
        source_size=profile.image_size,
        target_width=width,
        target_height=height,
    )
    projection_matrix = _scale_camera_matrix_for_size(
        np.asarray(profile.projection_matrix, dtype=np.float64).reshape(3, 3),
        source_size=profile.image_size,
        target_width=width,
        target_height=height,
    )
    corrected = _undistort_points_model(
        str(profile.model or DISTORTION_MODEL_PINHOLE),
        points,
        camera_matrix,
        np.asarray(profile.dist_coeffs, dtype=np.float64).reshape(1, -1),
        projection_matrix=projection_matrix,
    )
    if corrected.shape[0] < 2:
        return line
    return ManualLineSegment(
        start=(float(corrected[0, 0]), float(corrected[0, 1])),
        end=(float(corrected[1, 0]), float(corrected[1, 1])),
    )


def _preview_strip(
    raw_frame: np.ndarray,
    corrected_frame: np.ndarray,
    *,
    lines: list[ManualLineSegment],
    profile: DistortionProfile | None,
    label: str,
) -> np.ndarray:
    height, width = raw_frame.shape[:2]
    raw_canvas = raw_frame.copy()
    corrected_canvas = corrected_frame.copy()
    for index, line in enumerate(lines[:12], start=1):
        raw_start = (int(round(line.start[0])), int(round(line.start[1])))
        raw_end = (int(round(line.end[0])), int(round(line.end[1])))
        cv2.line(raw_canvas, raw_start, raw_end, (30, 210, 255), 2, cv2.LINE_AA)
        corrected_line = _undistort_line_for_profile(line, profile=profile, width=width, height=height)
        corrected_start = (int(round(corrected_line.start[0])), int(round(corrected_line.start[1])))
        corrected_end = (int(round(corrected_line.end[0])), int(round(corrected_line.end[1])))
        cv2.line(corrected_canvas, corrected_start, corrected_end, (90, 255, 120), 2, cv2.LINE_AA)
        cv2.putText(
            raw_canvas,
            str(index),
            raw_start,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            corrected_canvas,
            str(index),
            corrected_start,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    strip = np.hstack([raw_canvas, corrected_canvas])
    cv2.putText(strip, f"{label} | raw", (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(strip, f"{label} | corrected", (width + 14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (255, 255, 255), 2, cv2.LINE_AA)
    return strip


def render_line_hints_preview(
    frame: np.ndarray,
    *,
    camera_slot: str,
    lines: list[ManualLineSegment],
    pending_point: tuple[float, float] | None = None,
    selected_index: int | None = None,
    show_overlay: bool = True,
    fit_display: bool = True,
) -> np.ndarray:
    if frame is None or frame.size == 0:
        return frame
    canvas = frame.copy()
    for index, line in enumerate(lines, start=1):
        start = (int(round(line.start[0])), int(round(line.start[1])))
        end = (int(round(line.end[0])), int(round(line.end[1])))
        color = (30, 210, 255) if selected_index == index - 1 else (80, 255, 120)
        thickness = 3 if selected_index == index - 1 else 2
        cv2.line(canvas, start, end, color, thickness, cv2.LINE_AA)
        cv2.circle(canvas, start, 4, (60, 220, 255), -1, cv2.LINE_AA)
        cv2.circle(canvas, end, 4, (60, 220, 255), -1, cv2.LINE_AA)
        cv2.putText(
            canvas,
            str(index),
            (int(round((start[0] + end[0]) * 0.5)), int(round((start[1] + end[1]) * 0.5))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    if pending_point is not None:
        cv2.circle(
            canvas,
            (int(round(float(pending_point[0]))), int(round(float(pending_point[1])))),
            5,
            (0, 200, 255),
            -1,
            cv2.LINE_AA,
        )
    if show_overlay:
        header_lines = [
            f"{camera_slot.upper()} line hints",
            f"Lines: {len(lines)} / min {MIN_MANUAL_LINE_COUNT} (recommended {RECOMMENDED_MANUAL_LINE_COUNT}+)",
            "Click twice to add a line. Use Replace/Delete/Undo controls to refine the set.",
        ]
        overlay = _render_text_panel(header_lines, width=max(680, int(canvas.shape[1])), background=12)
        overlay = overlay[: min(overlay.shape[0], canvas.shape[0]), : canvas.shape[1]]
        canvas[: overlay.shape[0], : overlay.shape[1]] = overlay
    return _fit_canvas_for_display(canvas) if fit_display else canvas


def _fit_canvas_for_display(
    canvas: np.ndarray,
    *,
    max_width: int = PREVIEW_MAX_WIDTH,
    max_height: int = PREVIEW_MAX_HEIGHT,
) -> np.ndarray:
    if canvas is None or canvas.size <= 0:
        return canvas
    height, width = canvas.shape[:2]
    if width <= 0 or height <= 0:
        return canvas
    scale = min(
        1.0,
        float(max_width) / float(width),
        float(max_height) / float(height),
    )
    if scale >= 0.999:
        return canvas
    return cv2.resize(
        canvas,
        (
            max(2, int(round(width * scale))),
            max(2, int(round(height * scale))),
        ),
        interpolation=cv2.INTER_AREA,
    )


def _render_text_panel(
    lines: list[str],
    *,
    width: int = CONTROL_PANEL_WIDTH,
    background: int = 16,
) -> np.ndarray:
    line_height = 24
    top_padding = 18
    bottom_padding = 12
    height = max(140, top_padding + bottom_padding + (line_height * max(1, len(lines))))
    panel = np.zeros((height, width, 3), dtype=np.uint8)
    panel[:, :, :] = int(background)
    y = top_padding + 8
    for index, text in enumerate(lines):
        cv2.putText(
            panel,
            text,
            (14, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.60 if index == 0 else 0.50,
            (120, 255, 140) if index == 0 else (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
        y += line_height
    return panel


def render_distortion_preview(
    raw_frame: np.ndarray,
    corrected_frame: np.ndarray,
    *,
    lines: list[ManualLineSegment],
    profile: DistortionProfile | None,
    label: str,
) -> np.ndarray:
    return _fit_canvas_for_display(
        _preview_strip(raw_frame, corrected_frame, lines=lines, profile=profile, label=label)
    )


def _trackbar_specs_for_model(model: str, width: int, height: int) -> dict[str, tuple[float, float]]:
    focal_ref = float(max(width, height))
    specs = {
        "fx": (focal_ref * 0.70, focal_ref * 1.40),
        "fy": (focal_ref * 0.70, focal_ref * 1.40),
        "cx": (width * 0.42, width * 0.58),
        "cy": (height * 0.42, height * 0.58),
        "k1": (-0.55, 0.55),
        "k2": (-0.55, 0.55),
        "k3": (-0.25, 0.25),
        "p1": (-0.03, 0.03),
        "p2": (-0.03, 0.03),
        "k4": (-0.55, 0.55),
    }
    if model != DISTORTION_MODEL_FISHEYE:
        specs["k3"] = (-0.10, 0.10)
        specs["p1"] = (-0.01, 0.01)
        specs["p2"] = (-0.01, 0.01)
    return specs


def distortion_parameter_specs(model: str, width: int, height: int) -> dict[str, tuple[float, float]]:
    return dict(_trackbar_specs_for_model(str(model or DISTORTION_MODEL_PINHOLE), int(width), int(height)))


def _encode_trackbar_value(value: float, lo: float, hi: float) -> int:
    if hi - lo <= 1e-9:
        return 0
    clamped = float(np.clip(value, lo, hi))
    return int(round(((clamped - lo) / (hi - lo)) * 1000.0))


def _decode_trackbar_value(position: int, lo: float, hi: float) -> float:
    if hi - lo <= 1e-9:
        return float(lo)
    ratio = float(np.clip(position, 0, 1000)) / 1000.0
    return float(lo + ((hi - lo) * ratio))


def tune_manual_guided_distortion(
    frames: list[np.ndarray],
    camera_slot: str,
    lines: list[ManualLineSegment],
    *,
    auto_profile: DistortionProfile | None,
    saved_profile: DistortionProfile | None = None,
    use_saved_as_starting_point: bool = True,
    lens_model_hint: str = "auto",
    horizontal_fov_deg: float | None = None,
    vertical_fov_deg: float | None = None,
    camera_model: str = "",
) -> DistortionProfile | None:
    if not _distortion_deps_available() or not frames:
        return auto_profile
    preview_frame = frames[-1]
    height, width = preview_frame.shape[:2]
    default_baseline_name = "raw"
    default_model_name = DISTORTION_MODEL_PINHOLE
    metadata = build_lens_metadata(
        lens_model_hint=lens_model_hint,
        horizontal_fov_deg=horizontal_fov_deg,
        vertical_fov_deg=vertical_fov_deg,
        camera_model=camera_model,
    )

    def raw_profile_for(model: str) -> DistortionProfile:
        return _profile_from_state(
            camera_slot=str(camera_slot),
            model=model,
            width=width,
            height=height,
            state=_initial_state_for_model(model, width, height, metadata),
            metadata=metadata,
            source="raw",
        )

    auto_model_default = str(auto_profile.model if auto_profile is not None else default_model_name)
    baseline_profiles: dict[str, DistortionProfile | None] = {
        "raw": raw_profile_for(default_model_name),
        "saved": _clone_profile(saved_profile) if saved_profile is not None else None,
        "guided": _clone_profile(auto_profile) if auto_profile is not None else None,
    }
    baseline_order = ["raw", "saved", "guided"]
    model_order = ["auto", DISTORTION_MODEL_PINHOLE, DISTORTION_MODEL_FISHEYE]
    baseline_index = baseline_order.index(default_baseline_name)
    active_model_choice = 1

    window_name = f"Hogak Distortion Tune - {camera_slot}"
    preview_window_name = f"{window_name} Preview"
    controls_window_name = f"{window_name} Controls"
    cv2.namedWindow(preview_window_name, cv2.WINDOW_NORMAL)
    cv2.namedWindow(controls_window_name, cv2.WINDOW_NORMAL)
    cv2.createTrackbar("baseline", controls_window_name, baseline_index, len(baseline_order) - 1, lambda _value: None)
    cv2.createTrackbar("model", controls_window_name, active_model_choice, len(model_order) - 1, lambda _value: None)
    for name in ("fx", "fy", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "k4"):
        cv2.createTrackbar(name, controls_window_name, 500, 1000, lambda _value: None)

    last_reset_signature: tuple[int, int] | None = None
    last_render_signature: tuple[Any, ...] | None = None
    accepted_profile: DistortionProfile | None = None

    def baseline_profile_for(name: str, model_choice: str) -> DistortionProfile:
        profile = baseline_profiles.get(name)
        if profile is None:
            return raw_profile_for(model_choice)
        if str(profile.model or model_choice) == model_choice:
            return _clone_profile(profile)
        state = _profile_to_state(profile, model=model_choice, width=width, height=height, metadata=metadata)
        return _profile_from_state(
            camera_slot=str(camera_slot),
            model=model_choice,
            width=width,
            height=height,
            state=state,
            metadata=metadata,
            source=str(profile.source or name),
        )

    def active_model_from_choice(choice_index: int, baseline_name: str) -> str:
        if choice_index == 1:
            return DISTORTION_MODEL_PINHOLE
        if choice_index == 2:
            return DISTORTION_MODEL_FISHEYE
        baseline_profile = baseline_profiles.get(baseline_name)
        if baseline_profile is not None and str(baseline_profile.model or "") in {DISTORTION_MODEL_PINHOLE, DISTORTION_MODEL_FISHEYE}:
            return str(baseline_profile.model)
        if auto_profile is not None and str(auto_profile.model or "") in {DISTORTION_MODEL_PINHOLE, DISTORTION_MODEL_FISHEYE}:
            return str(auto_profile.model)
        return default_model_name

    def reset_trackbars_from_baseline(name: str, model_choice: str) -> None:
        profile = baseline_profile_for(name, model_choice)
        if (
            name == default_baseline_name
            and model_choice == default_model_name
            and auto_profile is not None
        ):
            profile = baseline_profile_for("guided", model_choice)
        state = _profile_to_state(profile, model=model_choice, width=width, height=height, metadata=metadata)
        specs = _trackbar_specs_for_model(model_choice, width, height)
        for key in ("fx", "fy", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "k4"):
            lo, hi = specs[key]
            cv2.setTrackbarPos(key, controls_window_name, _encode_trackbar_value(float(state.get(key, 0.0)), lo, hi))

    reset_trackbars_from_baseline(default_baseline_name, active_model_from_choice(active_model_choice, default_baseline_name))
    last_reset_signature = (baseline_index, active_model_choice)

    try:
        while True:
            current_baseline_index = int(cv2.getTrackbarPos("baseline", controls_window_name))
            current_baseline_index = max(0, min(len(baseline_order) - 1, current_baseline_index))
            current_baseline_name = baseline_order[current_baseline_index]
            current_model_choice = int(cv2.getTrackbarPos("model", controls_window_name))
            current_model_choice = max(0, min(len(model_order) - 1, current_model_choice))
            active_model = active_model_from_choice(current_model_choice, current_baseline_name)
            reset_signature = (current_baseline_index, current_model_choice)
            if reset_signature != last_reset_signature:
                reset_trackbars_from_baseline(current_baseline_name, active_model)
                last_reset_signature = reset_signature

            specs = _trackbar_specs_for_model(active_model, width, height)
            current_state: dict[str, float] = {}
            for key in ("fx", "fy", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "k4"):
                lo, hi = specs[key]
                current_state[key] = _decode_trackbar_value(int(cv2.getTrackbarPos(key, controls_window_name)), lo, hi)
            current_state = _clamp_state_for_model(active_model, current_state, width, height)
            render_signature = (
                current_baseline_name,
                active_model,
                tuple(round(float(current_state[key]), 6) for key in ("fx", "fy", "cx", "cy", "k1", "k2", "k3", "p1", "p2", "k4")),
            )
            if render_signature != last_render_signature:
                candidate = _profile_from_state(
                    camera_slot=str(camera_slot),
                    model=active_model,
                    width=width,
                    height=height,
                    state=current_state,
                    metadata=metadata,
                    source=DISTORTION_SOURCE_MANUAL_GUIDED,
                )
                metrics = evaluate_profile_against_guided_lines(
                    frames,
                    lines,
                    profile=candidate,
                    lens_model_hint=lens_model_hint,
                    horizontal_fov_deg=horizontal_fov_deg,
                    vertical_fov_deg=vertical_fov_deg,
                    camera_model=camera_model,
                )
                improvement = 0.0
                if metrics:
                    improvement = float(metrics.get("raw_straightness_residual") or 0.0) - float(metrics.get("straightness_residual") or 0.0)
                chosen_model_reason = (
                    f"baseline={current_baseline_name} model={active_model} "
                    f"improvement={improvement:.4f}"
                )
                candidate = _apply_metrics_to_profile(candidate, metrics=metrics, chosen_model_reason=chosen_model_reason)
                corrected = apply_distortion_profile(preview_frame, candidate)
                strip = _preview_strip(preview_frame, corrected, lines=lines, profile=candidate, label=str(camera_slot).upper())
                preview_canvas = _fit_canvas_for_display(strip)
                status_lines = [
                    f"{camera_slot.upper()} tuning | baseline={current_baseline_name} | model={active_model}",
                    f"residual={candidate.straightness_residual:.4f} raw={candidate.raw_straightness_residual:.4f} delta={candidate.raw_straightness_residual - candidate.straightness_residual:.4f}",
                    f"coverage={candidate.edge_coverage:.2f} distribution={candidate.support_distribution:.2f} fit={candidate.fit_score:.3f}",
                    f"reason={candidate.chosen_model_reason[:120]}",
                    "Default start = raw baseline + pinhole model. Guided auto-fit seeds the sliders when available.",
                    "Preview is resized to fit the screen. Controls stay in a separate window.",
                    "Trackbars: fx fy cx cy k1 k2 k3 p1 p2 k4 | baseline(0=raw 1=saved 2=guided) | model(0=auto 1=pinhole 2=fisheye)",
                    "Enter=accept current tuning  Esc=cancel",
                ]
                cv2.imshow(preview_window_name, preview_canvas)
                cv2.imshow(controls_window_name, _render_text_panel(status_lines, background=16))
                accepted_profile = candidate
                last_render_signature = render_signature

            key = int(cv2.waitKeyEx(30))
            if key < 0:
                continue
            if key in {13, 10}:
                break
            if key == 27:
                accepted_profile = None
                break
        cv2.destroyWindow(preview_window_name)
        cv2.destroyWindow(controls_window_name)
    except Exception:
        try:
            cv2.destroyWindow(preview_window_name)
        except Exception:
            pass
        try:
            cv2.destroyWindow(controls_window_name)
        except Exception:
            pass
        raise
    return accepted_profile


def review_distortion_candidates(
    *,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    left_saved: DistortionProfile | None,
    right_saved: DistortionProfile | None,
    left_session: DistortionProfile | None,
    right_session: DistortionProfile | None,
    homography_reference: str,
    recommended_source: str = "saved",
) -> str | None:
    if not _distortion_deps_available() or left_frame is None or right_frame is None:
        return recommended_source
    options: dict[str, tuple[DistortionProfile | None, DistortionProfile | None]] = {
        "raw": (None, None),
        "saved": (left_saved, right_saved),
        "session-candidate": (left_session, right_session),
    }
    available = [name for name, (left_profile, right_profile) in options.items() if name == "raw" or (left_profile is not None and right_profile is not None)]
    if not available:
        return "raw"
    selected = recommended_source if recommended_source in available else available[0]
    order = ["raw", "saved", "session-candidate"]
    preview_window_name = "Hogak Distortion Stitch Review Preview"
    controls_window_name = "Hogak Distortion Stitch Review Controls"
    cv2.namedWindow(preview_window_name, cv2.WINDOW_NORMAL)
    cv2.namedWindow(controls_window_name, cv2.WINDOW_NORMAL)
    try:
        while True:
            left_profile, right_profile = options.get(selected, (None, None))
            left_corrected = apply_distortion_profile(left_frame, left_profile)
            right_corrected = apply_distortion_profile(right_frame, right_profile)
            left_strip = _preview_strip(left_frame, left_corrected, lines=[], profile=left_profile, label="LEFT")
            right_strip = _preview_strip(right_frame, right_corrected, lines=[], profile=right_profile, label="RIGHT")
            body = np.vstack([left_strip, right_strip])
            preview_canvas = _fit_canvas_for_display(body)
            profile_texts = []
            if left_profile is not None and right_profile is not None:
                profile_texts.append(
                    f"left residual={left_profile.straightness_residual:.4f} raw={left_profile.raw_straightness_residual:.4f} coverage={left_profile.edge_coverage:.2f} model={left_profile.model}"
                )
                profile_texts.append(
                    f"right residual={right_profile.straightness_residual:.4f} raw={right_profile.raw_straightness_residual:.4f} coverage={right_profile.edge_coverage:.2f} model={right_profile.model}"
                )
            else:
                profile_texts.append("raw selected: no distortion correction")
            info_lines = [
                f"Stitch review | selected={selected} | recommended={recommended_source}",
                f"homography reference={homography_reference}",
                "1=raw  2=saved  3=session-candidate  Enter=launch runtime  Esc=cancel",
                "Preview shows corrected camera feeds; runtime stitch uses the selected profile and current homography path.",
            ]
            if selected != "raw" and homography_reference != "undistorted":
                info_lines.append("Runtime will auto-regenerate an undistorted-compatible homography before launch.")
            info_lines.extend(profile_texts)
            cv2.imshow(preview_window_name, preview_canvas)
            cv2.imshow(controls_window_name, _render_text_panel(info_lines, background=14))

            key = int(cv2.waitKeyEx(30))
            if key < 0:
                continue
            if key == 27:
                selected = ""
                break
            if key in {13, 10}:
                break
            if key in {ord("1"), ord("2"), ord("3")}:
                candidate = order[int(chr(key)) - 1]
                if candidate in available:
                    selected = candidate
        cv2.destroyWindow(preview_window_name)
        cv2.destroyWindow(controls_window_name)
    except Exception:
        try:
            cv2.destroyWindow(preview_window_name)
        except Exception:
            pass
        try:
            cv2.destroyWindow(controls_window_name)
        except Exception:
            pass
        raise
    return selected or None


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
            straightness_residual=float(saved_profile.straightness_residual),
            raw_straightness_residual=float(saved_profile.raw_straightness_residual),
            edge_coverage=float(saved_profile.edge_coverage),
            support_distribution=float(saved_profile.support_distribution),
            black_border_ratio=float(saved_profile.black_border_ratio),
            usable_area_ratio=float(saved_profile.usable_area_ratio),
            chosen_projection_mode=str(saved_profile.chosen_projection_mode or ""),
            chosen_model_reason=str(saved_profile.chosen_model_reason or ""),
            candidate_rejected_reason=str(saved_profile.candidate_rejected_reason or ""),
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
    projection_matrix = np.asarray(profile.projection_matrix, dtype=np.float64).reshape(3, 3).copy()
    source_width = max(1, int(profile.image_size[0]))
    source_height = max(1, int(profile.image_size[1]))
    if (source_width, source_height) != (width, height):
        scale_x = width / float(source_width)
        scale_y = height / float(source_height)
        camera_matrix[0, 0] *= scale_x
        camera_matrix[0, 2] *= scale_x
        camera_matrix[1, 1] *= scale_y
        camera_matrix[1, 2] *= scale_y
        projection_matrix[0, 0] *= scale_x
        projection_matrix[0, 2] *= scale_x
        projection_matrix[1, 1] *= scale_y
        projection_matrix[1, 2] *= scale_y
    return _undistort_frame(
        frame,
        camera_matrix,
        dist_coeffs,
        str(profile.model or DISTORTION_MODEL_PINHOLE),
        projection_matrix=projection_matrix,
    )
