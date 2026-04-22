from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, cast

import numpy as np

if TYPE_CHECKING:
    import cv2 as cv2_types
    CvDMatch = cv2_types.DMatch
    CvKeyPoint = cv2_types.KeyPoint
    CvVideoCapture = cv2_types.VideoCapture
else:
    CvDMatch = Any
    CvKeyPoint = Any
    CvVideoCapture = Any

try:
    import cv2 as _cv2  # type: ignore
except ModuleNotFoundError:
    _cv2 = None

cv2 = cast(Any, _cv2)

from stitching.core.blend import _blend_feather
from stitching.core.config import StitchConfig, StitchingFailure
from stitching.core.geometry import (
    _estimate_affine_homography,
    _estimate_homography,
    _prepare_warp_plan,
)
from stitching.domain.geometry.virtual_center import (
    VirtualCenterRectilinearSolution as _VirtualCenterRectilinearSolution,
    point_to_rectilinear_ray as _domain_point_to_rectilinear_ray,
    project_ray_to_virtual_rectilinear as _domain_project_ray_to_virtual_rectilinear,
    score_virtual_center_candidate as _domain_score_virtual_center_candidate,
    solve_virtual_center_rectilinear as _domain_solve_virtual_center_rectilinear,
    should_use_virtual_center_runtime_geometry as _shared_should_use_virtual_center_runtime_geometry,
)
from stitching.errors import ErrorCode
from stitching.domain.runtime.defaults import (
    DEFAULT_CALIBRATION_DEBUG_DIR,
    DEFAULT_CALIBRATION_INLIERS_FILE,
    DEFAULT_HOMOGRAPHY_PATH,
)
from stitching.domain.calibration.native.artifacts import (
    save_native_calibration_artifacts as _save_native_calibration_artifacts_impl,
)
from stitching.domain.calibration.native.pipeline import (
    calibrate_native_homography_from_frames as _calibrate_native_homography_from_frames_impl,
)
from stitching.domain.calibration.native.runner import (
    backup_homography_file as _backup_homography_file_impl,
    run_native_calibration as _run_native_calibration_impl,
)
from stitching.domain.calibration.native.capture import (
    FfmpegCaptureEnv as _FfmpegCaptureEnvImpl,
    capture_pair as _capture_pair_impl,
    open_capture as _open_capture_impl,
    resize_frame as _resize_frame_impl,
    resize_to_match as _resize_to_match_impl,
)
from stitching.domain.calibration.native.ui import (
    AssistedCalibrationUi as _AssistedCalibrationUiImpl,
    CalibrationReviewUi as _CalibrationReviewUiImpl,
)
from stitching.domain.calibration.native.matching import (
    assisted_min_matches as _assisted_min_matches_impl,
    auto_match_variant_configs as _auto_match_variant_configs_impl,
    backend_display_name as _backend_display_name_impl,
    backend_match_cache_entry as _backend_match_cache_entry_impl,
    build_assisted_matches as _build_assisted_matches_impl,
    build_manual_matches as _build_manual_matches_impl,
    clamp_overlap_rect as _clamp_overlap_rect_impl,
    create_feature_match_session as _create_feature_match_session_impl,
    detect_and_match_classic_raw as _detect_and_match_classic_raw_impl,
    detect_and_match_feature_raw as _detect_and_match_feature_raw_impl,
    detect_auto_matches as _detect_auto_matches_impl,
    detect_matches_for_backend_raw as _detect_matches_for_backend_raw_impl,
    estimate_overlap_hints as _estimate_overlap_hints_impl,
    guidance_threshold_px as _guidance_threshold_px_impl,
    manual_candidate_config as _manual_candidate_config_impl,
    match_backend_priority as _match_backend_priority_impl,
    robust_overlap_rect as _robust_overlap_rect_impl,
)
from stitching.domain.geometry.common import (
    VirtualCenterArtifactBuildSpec,
    apply_virtual_center_metrics as _apply_virtual_center_metrics,
    build_virtual_center_runtime_artifact,
)
from stitching.domain.geometry.selection import (
    calibration_transform_rank,
    choose_preferred_calibration_candidate,
)
from stitching.domain.geometry.artifact import (
    RUNTIME_GEOMETRY_SCHEMA_VERSION,
    runtime_geometry_artifact_path,
    save_runtime_geometry_artifact,
)
from stitching.domain.geometry.workflow import (
    build_native_calibration_metadata,
    build_native_calibration_result,
)
from stitching.domain.runtime.site_config import require_configured_rtsp_urls


@dataclass(slots=True)
class NativeCalibrationConfig(StitchConfig):
    left_rtsp: str = ""
    right_rtsp: str = ""
    output_path: Path = Path(DEFAULT_HOMOGRAPHY_PATH)
    inliers_output_path: Path = Path(DEFAULT_CALIBRATION_INLIERS_FILE)
    debug_dir: Path = Path(DEFAULT_CALIBRATION_DEBUG_DIR)
    rtsp_transport: str = "tcp"
    rtsp_timeout_sec: float = 10.0
    warmup_frames: int = 12
    process_scale: float = 1.0
    calibration_mode: str = "assisted"
    assisted_reproj_threshold: float = 12.0
    assisted_max_auto_matches: int = 600
    match_backend: str = "classic"
    review_required: bool = True


@dataclass(slots=True)
class _CalibrationCandidate:
    homography: np.ndarray
    inlier_mask: np.ndarray
    keypoints_left: list[CvKeyPoint]
    keypoints_right: list[CvKeyPoint]
    matches: list[CvDMatch]
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


@dataclass(slots=True)
class _BackendMatchCacheEntry:
    keypoints_left: list[CvKeyPoint]
    keypoints_right: list[CvKeyPoint]
    knn_matches: list[list[CvDMatch]]


@dataclass(slots=True)
class _FeatureMatchSession:
    gray_left: np.ndarray
    gray_right: np.ndarray
    backend_matches: dict[tuple[str, int], _BackendMatchCacheEntry]


class _AssistedCalibrationUi(_AssistedCalibrationUiImpl):
    def __init__(
        self,
        left: np.ndarray,
        right: np.ndarray,
        *,
        status: str = "",
        left_overlap_hint: tuple[int, int, int, int] | None = None,
        right_overlap_hint: tuple[int, int, int, int] | None = None,
    ) -> None:
        super().__init__(
            left,
            right,
            status=status,
            left_overlap_hint=left_overlap_hint,
            right_overlap_hint=right_overlap_hint,
            cv2_module=cv2,
        )


class _CalibrationReviewUi(_CalibrationReviewUiImpl):
    def __init__(
        self,
        *,
        inlier_preview: np.ndarray,
        stitched_preview: np.ndarray,
        summary_lines: list[str],
    ) -> None:
        super().__init__(
            inlier_preview=inlier_preview,
            stitched_preview=stitched_preview,
            summary_lines=summary_lines,
            cv2_module=cv2,
        )


class _FfmpegCaptureEnv(_FfmpegCaptureEnvImpl):
    pass


def _open_capture(url: str, transport: str, timeout_sec: float) -> CvVideoCapture:
    return _open_capture_impl(
        url,
        transport,
        timeout_sec,
        ffmpeg_capture_env_cls=_FfmpegCaptureEnv,
        cv2_module=cv2,
        stitching_failure_cls=StitchingFailure,
    )


def _resize_frame(frame: np.ndarray, scale: float) -> np.ndarray:
    return _resize_frame_impl(frame, scale, cv2_module=cv2)


def _resize_to_match(frame: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    return _resize_to_match_impl(frame, target_shape, cv2_module=cv2)


def _capture_pair(config: NativeCalibrationConfig) -> tuple[np.ndarray, np.ndarray]:
    return _capture_pair_impl(
        config,
        open_capture_func=_open_capture,
        resize_frame_func=_resize_frame,
        resize_to_match_func=_resize_to_match,
        time_module=time,
        stitching_failure_cls=StitchingFailure,
    )


def _save_homography_file(
    path: Path,
    homography: np.ndarray,
    metadata: dict,
) -> None:
    payload = {
        "version": 1,
        "saved_at_epoch_sec": int(time.time()),
        "homography": homography.tolist(),
        "metadata": metadata,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_inlier_points(
    keypoints_left: list[CvKeyPoint],
    keypoints_right: list[CvKeyPoint],
    matches: list[CvDMatch],
    inlier_mask: np.ndarray,
) -> tuple[list[list[float]], list[list[float]]]:
    left_points: list[list[float]] = []
    right_points: list[list[float]] = []
    flat_mask = inlier_mask.ravel().tolist()
    for match, is_inlier in zip(matches, flat_mask):
        if not bool(is_inlier):
            continue
        try:
            left_point = keypoints_left[int(match.queryIdx)].pt
            right_point = keypoints_right[int(match.trainIdx)].pt
        except Exception:
            continue
        left_points.append([float(left_point[0]), float(left_point[1])])
        right_points.append([float(right_point[0]), float(right_point[1])])
    return left_points, right_points


def _save_calibration_inliers_file(
    path: Path,
    *,
    homography: np.ndarray,
    left_resolution: tuple[int, int],
    right_resolution: tuple[int, int],
    output_resolution: tuple[int, int],
    inliers_count: int,
    inlier_ratio: float,
    left_points: list[list[float]],
    right_points: list[list[float]],
) -> None:
    payload = {
        "version": 1,
        "saved_at_epoch_sec": int(time.time()),
        "homography": homography.tolist(),
        "left_resolution": [int(left_resolution[0]), int(left_resolution[1])],
        "right_resolution": [int(right_resolution[0]), int(right_resolution[1])],
        "output_resolution": [int(output_resolution[0]), int(output_resolution[1])],
        "inliers_count": int(inliers_count),
        "inlier_ratio": float(inlier_ratio),
        "left_inlier_points": left_points,
        "right_inlier_points": right_points,
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


def _default_runtime_projection_params(frame_shape: tuple[int, int] | tuple[int, int, int]) -> tuple[float, tuple[float, float]]:
    height = max(1, int(frame_shape[0]))
    width = max(1, int(frame_shape[1]))
    return float(max(width, height) * 0.90), (float(width) / 2.0, float(height) / 2.0)


def _point_to_rectilinear_ray(
    point: list[float] | tuple[float, float],
    *,
    focal_px: float,
    center_x: float,
    center_y: float,
) -> np.ndarray:
    focal = max(1.0, float(focal_px))
    x = (float(point[0]) - float(center_x)) / focal
    y = (float(point[1]) - float(center_y)) / focal
    ray = np.asarray([x, y, 1.0], dtype=np.float64)
    norm = float(np.linalg.norm(ray))
    if norm <= 1e-9:
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
    return ray / norm


def _project_point_to_cylindrical(
    point: list[float] | tuple[float, float],
    *,
    focal_px: float,
    center_x: float,
    center_y: float,
) -> tuple[float, float]:
    x = float(point[0])
    y = float(point[1])
    focal = max(1.0, float(focal_px))
    cx = float(center_x)
    cy = float(center_y)
    dx = x - cx
    dy = y - cy
    theta = np.arctan(dx / focal)
    cylindrical_x = (focal * theta) + cx
    cylindrical_y = (focal * dy) / np.sqrt((dx * dx) + (focal * focal)) + cy
    return float(cylindrical_x), float(cylindrical_y)


def _solve_rotation_kabsch(source_rays: np.ndarray, target_rays: np.ndarray) -> np.ndarray:
    source = np.asarray(source_rays, dtype=np.float64).reshape(-1, 3)
    target = np.asarray(target_rays, dtype=np.float64).reshape(-1, 3)
    if source.shape[0] != target.shape[0] or source.shape[0] < 2:
        raise ValueError("virtual-center rotation solve requires at least two paired rays")
    covariance = source.T @ target
    u, _s, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1, :] *= -1.0
        rotation = vt.T @ u.T
    return rotation


def _midpoint_virtual_rotations(right_to_left_rotation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rotation = np.asarray(right_to_left_rotation, dtype=np.float64).reshape(3, 3)
    rvec, _ = cv2.Rodrigues(rotation)
    rvec = np.asarray(rvec, dtype=np.float64).reshape(3, 1)
    left_to_virtual, _ = cv2.Rodrigues((-0.5 * rvec).reshape(3, 1))
    right_to_virtual, _ = cv2.Rodrigues((0.5 * rvec).reshape(3, 1))
    return (
        np.asarray(left_to_virtual, dtype=np.float64).reshape(3, 3),
        np.asarray(right_to_virtual, dtype=np.float64).reshape(3, 3),
    )


def _lock_virtual_roll(
    left_to_virtual_rotation: np.ndarray,
    right_to_virtual_rotation: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    left_rotation = np.asarray(left_to_virtual_rotation, dtype=np.float64).reshape(3, 3)
    right_rotation = np.asarray(right_to_virtual_rotation, dtype=np.float64).reshape(3, 3)
    projected_right_axis = left_rotation[:, 0]
    roll_rad = float(np.arctan2(projected_right_axis[1], projected_right_axis[0]))
    if abs(roll_rad) <= 1e-9:
        return left_rotation, right_rotation, 0.0
    cos_value = float(np.cos(-roll_rad))
    sin_value = float(np.sin(-roll_rad))
    roll_correction = np.asarray(
        [
            [cos_value, -sin_value, 0.0],
            [sin_value, cos_value, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return (
        np.asarray(roll_correction @ left_rotation, dtype=np.float64).reshape(3, 3),
        np.asarray(roll_correction @ right_rotation, dtype=np.float64).reshape(3, 3),
        float(np.degrees(roll_rad)),
    )


def _project_ray_to_virtual_rectilinear(
    ray: np.ndarray,
    *,
    focal_px: float,
    center_x: float,
    center_y: float,
) -> tuple[float, float] | None:
    direction = np.asarray(ray, dtype=np.float64).reshape(3)
    z = float(direction[2])
    if z <= 1e-6:
        return None
    focal = max(1.0, float(focal_px))
    x = (focal * float(direction[0]) / z) + float(center_x)
    y = (focal * float(direction[1]) / z) + float(center_y)
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    return float(x), float(y)

def _largest_valid_rect(mask: np.ndarray) -> tuple[int, int, int, int]:
    binary = (np.asarray(mask, dtype=np.uint8) > 0).astype(np.uint8)
    height, width = binary.shape[:2]
    heights = np.zeros(width, dtype=np.int32)
    best_area = 0
    best_rect = (0, 0, width, height)
    for y in range(height):
        heights = np.where(binary[y] > 0, heights + 1, 0)
        stack: list[int] = []
        for x in range(width + 1):
            current = heights[x] if x < width else 0
            while stack and current < heights[stack[-1]]:
                h = heights[stack.pop()]
                x0 = stack[-1] + 1 if stack else 0
                rect_width = x - x0
                area = int(h * rect_width)
                if area > best_area and h > 0 and rect_width > 0:
                    best_area = area
                    best_rect = (x0, y - h + 1, rect_width, h)
            stack.append(x)
    return best_rect

def _valid_mask_tilt_deg(mask: np.ndarray) -> float:
    ys, xs = np.where(np.asarray(mask, dtype=np.uint8) > 0)
    if xs.size < 8:
        return 0.0
    centered_x = xs.astype(np.float64) - float(np.mean(xs))
    centered_y = ys.astype(np.float64) - float(np.mean(ys))
    covariance = np.cov(np.stack([centered_x, centered_y], axis=0))
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    principal = eigenvectors[:, int(np.argmax(eigenvalues))]
    angle_rad = float(np.arctan2(principal[1], principal[0]))
    angle_deg = abs(float(np.degrees(angle_rad)))
    if angle_deg > 90.0:
        angle_deg = 180.0 - angle_deg
    return float(angle_deg)


def _estimate_runtime_alignment_affine(
    right_points: list[list[float]] | list[tuple[float, float]],
    left_points: list[list[float]] | list[tuple[float, float]],
    homography: np.ndarray,
    *,
    left_projection_focal_px: float,
    left_projection_center: tuple[float, float],
    right_projection_focal_px: float,
    right_projection_center: tuple[float, float],
) -> np.ndarray:
    if len(right_points) >= 3 and len(left_points) >= 3:
        src_points = np.float32(
            [
                _project_point_to_cylindrical(
                    point,
                    focal_px=right_projection_focal_px,
                    center_x=right_projection_center[0],
                    center_y=right_projection_center[1],
                )
                for point in right_points
            ]
        ).reshape(-1, 2)
        dst_points = np.float32(
            [
                _project_point_to_cylindrical(
                    point,
                    focal_px=left_projection_focal_px,
                    center_x=left_projection_center[0],
                    center_y=left_projection_center[1],
                )
                for point in left_points
            ]
        ).reshape(-1, 2)
        affine, _ = cv2.estimateAffinePartial2D(
            src_points,
            dst_points,
            method=cv2.LMEDS,
        )
        if affine is not None:
            return np.asarray(affine, dtype=np.float64).reshape(2, 3)

    homography_array = np.asarray(homography, dtype=np.float64).reshape(3, 3)
    return homography_array[:2, :].copy()


def _estimate_virtual_center_rigid(
    source_points: np.ndarray,
    target_points: np.ndarray,
) -> tuple[np.ndarray, float, float, float]:
    source = np.asarray(source_points, dtype=np.float64).reshape(-1, 2)
    target = np.asarray(target_points, dtype=np.float64).reshape(-1, 2)
    if source.shape[0] != target.shape[0] or source.shape[0] < 1:
        raise ValueError("virtual-center rigid solve requires paired points")
    if source.shape[0] == 1:
        translation = target[0] - source[0]
        rigid = np.asarray(
            [
                [1.0, 0.0, float(translation[0])],
                [0.0, 1.0, float(translation[1])],
            ],
            dtype=np.float64,
        )
        return rigid, 0.0, float(np.linalg.norm(translation)), 1.0

    source_mean = np.mean(source, axis=0)
    target_mean = np.mean(target, axis=0)
    source_centered = source - source_mean
    target_centered = target - target_mean
    covariance = source_centered.T @ target_centered
    u, _s, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1, :] *= -1.0
        rotation = vt.T @ u.T
    translation = target_mean - (rotation @ source_mean)
    rigid = np.zeros((2, 3), dtype=np.float64)
    rigid[:2, :2] = rotation
    rigid[:, 2] = translation
    rotation_deg = float(np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0])))
    translation_px = float(np.linalg.norm(translation))
    return rigid, rotation_deg, translation_px, 1.0


def _match_backend_priority(match_backend: str) -> list[str]:
    return _match_backend_priority_impl(match_backend)


def _create_feature_match_session(left: np.ndarray, right: np.ndarray) -> _FeatureMatchSession:
    return _create_feature_match_session_impl(
        left,
        right,
        cv2_module=cv2,
        feature_match_session_cls=_FeatureMatchSession,
    )


def _backend_display_name(match_backend: str, backend: str) -> str:
    return _backend_display_name_impl(match_backend, backend)


def _backend_match_cache_entry(
    session: _FeatureMatchSession,
    config: NativeCalibrationConfig,
    backend: str,
) -> _BackendMatchCacheEntry:
    return _backend_match_cache_entry_impl(
        session,
        config,
        backend,
        cv2_module=cv2,
        backend_match_cache_entry_cls=_BackendMatchCacheEntry,
        stitching_failure_cls=StitchingFailure,
    )


def _detect_matches_for_backend_raw(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
    backend: str,
    *,
    feature_session: _FeatureMatchSession | None = None,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str]:
    return _detect_matches_for_backend_raw_impl(
        left,
        right,
        config,
        backend,
        feature_session=feature_session,
        create_feature_match_session_func=_create_feature_match_session,
        backend_match_cache_entry_func=_backend_match_cache_entry,
        backend_display_name_func=_backend_display_name,
        stitching_failure_cls=StitchingFailure,
    )


def _detect_and_match_feature_raw(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
    *,
    minimum_match_count: int | None = None,
    feature_session: _FeatureMatchSession | None = None,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str]:
    return _detect_and_match_feature_raw_impl(
        left,
        right,
        config,
        minimum_match_count=minimum_match_count,
        feature_session=feature_session,
        match_backend_priority_func=_match_backend_priority,
        detect_matches_for_backend_raw_func=_detect_matches_for_backend_raw,
        stitching_failure_cls=StitchingFailure,
    )


def _detect_and_match_classic_raw(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
    *,
    feature_session: _FeatureMatchSession | None = None,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch]]:
    return _detect_and_match_classic_raw_impl(
        left,
        right,
        config,
        feature_session=feature_session,
        detect_and_match_feature_raw_func=_detect_and_match_feature_raw,
    )


def _guidance_threshold_px(config: NativeCalibrationConfig, seed_model: str) -> float:
    return _guidance_threshold_px_impl(config, seed_model)


def _assisted_min_matches(config: NativeCalibrationConfig) -> int:
    return _assisted_min_matches_impl(config)


def _build_assisted_matches(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
    left_points: list[tuple[float, float]],
    right_points: list[tuple[float, float]],
    *,
    feature_session: _FeatureMatchSession | None = None,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str, str, str]:
    return _build_assisted_matches_impl(
        left,
        right,
        config,
        left_points,
        right_points,
        feature_session=feature_session,
        detect_auto_matches_func=_detect_auto_matches,
        estimate_seed_guidance_transform_func=_estimate_seed_guidance_transform,
        guidance_threshold_px_func=_guidance_threshold_px,
        reprojection_error_func=_reprojection_error,
        assisted_min_matches_func=_assisted_min_matches,
        stitching_failure_cls=StitchingFailure,
    )


def _build_manual_matches(
    left_points: list[tuple[float, float]],
    right_points: list[tuple[float, float]],
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str, str, str]:
    return _build_manual_matches_impl(
        left_points,
        right_points,
        cv2_module=cv2,
        stitching_failure_cls=StitchingFailure,
    )


def _manual_candidate_config(
    config: NativeCalibrationConfig,
    *,
    pair_count: int,
) -> NativeCalibrationConfig:
    return _manual_candidate_config_impl(config, pair_count=pair_count)


def _detect_auto_matches(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
    *,
    feature_session: _FeatureMatchSession | None = None,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str]:
    return _detect_auto_matches_impl(
        left,
        right,
        config,
        feature_session=feature_session,
        detect_and_match_feature_raw_func=_detect_and_match_feature_raw,
    )


def _auto_match_variant_configs(config: NativeCalibrationConfig) -> list[tuple[str, NativeCalibrationConfig]]:
    return _auto_match_variant_configs_impl(config)


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
    return _clamp_overlap_rect_impl(x, y, w, h, image_width, image_height)


def _robust_overlap_rect(
    points: np.ndarray,
    *,
    image_width: int,
    image_height: int,
    prefer_side: str,
) -> tuple[int, int, int, int] | None:
    return _robust_overlap_rect_impl(
        points,
        image_width=image_width,
        image_height=image_height,
        prefer_side=prefer_side,
        clamp_overlap_rect_func=_clamp_overlap_rect,
    )


def _estimate_overlap_hints(
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
    *,
    feature_session: _FeatureMatchSession | None = None,
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    return _estimate_overlap_hints_impl(
        left,
        right,
        config,
        feature_session=feature_session,
        detect_and_match_classic_raw_func=_detect_and_match_classic_raw,
        robust_overlap_rect_func=_robust_overlap_rect,
        stitching_failure_cls=StitchingFailure,
    )


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
    keypoints_left: list[CvKeyPoint],
    keypoints_right: list[CvKeyPoint],
    matches: list[CvDMatch],
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
    width_ratio, height_ratio = _plan_geometry_ratios(
        plan_width,
        plan_height,
        left_shape=left.shape[:2],
        right_shape=right.shape[:2],
    )
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


def _is_output_geometry_plan_failure(exc: StitchingFailure) -> bool:
    if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
        return False
    detail = str(exc.detail or "").strip().lower()
    return any(
        token in detail
        for token in (
            "output geometry too large",
            "output pixel count too large",
            "invalid output geometry",
        )
    )


def _plan_geometry_ratios(
    plan_width: int,
    plan_height: int,
    *,
    left_shape: tuple[int, int],
    right_shape: tuple[int, int],
) -> tuple[float, float]:
    left_h, left_w = left_shape
    right_h, right_w = right_shape
    max_input_w = max(left_w, right_w)
    max_input_h = max(left_h, right_h)
    width_ratio = plan_width / float(max(1, max_input_w))
    height_ratio = plan_height / float(max(1, max_input_h))
    return float(width_ratio), float(height_ratio)


def _regularize_homography_for_output_plan(
    homography: np.ndarray,
    *,
    left_shape: tuple[int, int],
    right_shape: tuple[int, int],
    config: NativeCalibrationConfig,
) -> np.ndarray | None:
    base = np.asarray(homography, dtype=np.float64).reshape(3, 3)
    if abs(float(base[2, 2])) <= 1e-9:
        return None
    base = base / float(base[2, 2])
    if abs(float(base[2, 0])) <= 1e-9 and abs(float(base[2, 1])) <= 1e-9:
        return None

    best_candidate: np.ndarray | None = None
    best_rank: tuple[int, float, float] | None = None
    for perspective_scale in (0.75, 0.55, 0.40, 0.28, 0.18, 0.10, 0.0):
        candidate = base.copy()
        candidate[2, 0] *= float(perspective_scale)
        candidate[2, 1] *= float(perspective_scale)
        candidate[2, 2] = 1.0
        try:
            plan = _prepare_warp_plan(left_shape, right_shape, candidate, config)
        except StitchingFailure:
            continue
        width_ratio, height_ratio = _plan_geometry_ratios(
            plan.width,
            plan.height,
            left_shape=left_shape,
            right_shape=right_shape,
        )
        if width_ratio > 1.95 or height_ratio > 1.55:
            continue
        candidate_rank = (
            int(width_ratio <= 1.85 and height_ratio <= 1.45),
            float(perspective_scale),
            -float(plan.width * plan.height),
        )
        if best_rank is None or candidate_rank > best_rank:
            best_rank = candidate_rank
            best_candidate = candidate
    return best_candidate


def _regularized_candidate_requires_affine_fallback(
    *,
    plan_width: int,
    plan_height: int,
    left_shape: tuple[int, int],
    right_shape: tuple[int, int],
    geometry_score: float,
    mean_reprojection_error: float,
    config: NativeCalibrationConfig,
) -> bool:
    width_ratio, height_ratio = _plan_geometry_ratios(
        plan_width,
        plan_height,
        left_shape=left_shape,
        right_shape=right_shape,
    )
    reprojection_limit = max(24.0, float(config.ransac_reproj_threshold) * 5.0)
    return bool(
        width_ratio > 1.95
        or height_ratio > 1.55
        or float(geometry_score) < 0.42
        or float(mean_reprojection_error) > reprojection_limit
    )


def _build_candidate(
    *,
    left: np.ndarray,
    right: np.ndarray,
    keypoints_left: list[CvKeyPoint],
    keypoints_right: list[CvKeyPoint],
    matches: list[CvDMatch],
    calibration_mode: str,
    seed_guidance_model: str,
    backend_name: str,
    config: NativeCalibrationConfig,
    enforce_quality_gate: bool,
) -> _CalibrationCandidate:
    transform_model = "homography"
    try:
        homography, inlier_mask = _estimate_homography(
            keypoints_left,
            keypoints_right,
            matches,
            config,
            left_shape=left.shape[:2],
            right_shape=right.shape[:2],
        )
    except StitchingFailure as exc:
        if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
            raise
        homography, inlier_mask = _estimate_affine_homography(keypoints_left, keypoints_right, matches, config)
        transform_model = "affine_fallback"
    try:
        plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
    except StitchingFailure as exc:
        if transform_model != "homography" or not _is_output_geometry_plan_failure(exc):
            raise
        regularized_homography = _regularize_homography_for_output_plan(
            homography,
            left_shape=left.shape[:2],
            right_shape=right.shape[:2],
            config=config,
        )
        if regularized_homography is not None:
            homography = regularized_homography
            transform_model = "homography_geometry_regularized"
            plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
        else:
            homography, inlier_mask = _estimate_affine_homography(keypoints_left, keypoints_right, matches, config)
            transform_model = "affine_geometry_fallback"
            plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
    inliers_count = int(inlier_mask.ravel().sum())
    match_count = int(len(matches))
    inlier_ratio = float(inliers_count / float(max(1, match_count)))
    mean_reprojection_error = _mean_reprojection_error(homography, keypoints_left, keypoints_right, matches, inlier_mask)
    if enforce_quality_gate:
        _validate_calibration_quality(left, right, plan.width, plan.height, inliers_count, config)

    geometry_score, _distortion_penalty = _compute_geometry_score(
        plan_width=plan.width,
        plan_height=plan.height,
        left=left,
        right=right,
    )
    if transform_model == "homography_geometry_regularized" and _regularized_candidate_requires_affine_fallback(
        plan_width=plan.width,
        plan_height=plan.height,
        left_shape=left.shape[:2],
        right_shape=right.shape[:2],
        geometry_score=geometry_score,
        mean_reprojection_error=mean_reprojection_error,
        config=config,
    ):
        homography, inlier_mask = _estimate_affine_homography(keypoints_left, keypoints_right, matches, config)
        transform_model = "affine_geometry_fallback"
        plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
        inliers_count = int(inlier_mask.ravel().sum())
        match_count = int(len(matches))
        inlier_ratio = float(inliers_count / float(max(1, match_count)))
        mean_reprojection_error = _mean_reprojection_error(homography, keypoints_left, keypoints_right, matches, inlier_mask)
        geometry_score, _distortion_penalty = _compute_geometry_score(
            plan_width=plan.width,
            plan_height=plan.height,
            left=left,
            right=right,
        )
    match_score = _compute_match_score(
        inliers_count=inliers_count,
        match_count=match_count,
        inlier_ratio=inlier_ratio,
        mean_reprojection_error=mean_reprojection_error,
        config=config,
    )
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
    keypoints_left: list[CvKeyPoint],
    keypoints_right: list[CvKeyPoint],
    matches: list[CvDMatch],
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


def _should_collect_overlap_hints(
    *,
    requested_mode: str,
    prompt_for_points: bool,
    left_points: list[tuple[float, float]],
) -> bool:
    return bool(prompt_for_points and requested_mode in {"assisted", "manual"} and not left_points)


def _is_high_confidence_auto_candidate(
    candidate: _CalibrationCandidate,
    *,
    left: np.ndarray,
    right: np.ndarray,
    config: NativeCalibrationConfig,
) -> bool:
    if calibration_transform_rank(candidate.transform_model) < calibration_transform_rank("homography"):
        return False
    if float(candidate.score) < 0.60:
        return False
    if float(candidate.inlier_ratio) < 0.45:
        return False
    if float(candidate.mean_reprojection_error) > max(6.0, float(config.ransac_reproj_threshold) * 1.5):
        return False
    try:
        _validate_calibration_quality(
            left,
            right,
            int(candidate.output_width),
            int(candidate.output_height),
            int(candidate.inliers_count),
            config,
        )
    except StitchingFailure:
        return False
    return True


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


def _should_use_virtual_center_runtime_geometry(
    result: dict[str, Any],
    virtual_center_solution: _VirtualCenterRectilinearSolution,
    *,
    requested_residual_model: str = "rigid",
    effective_residual_model: str | None = None,
) -> tuple[bool, str]:
    return _shared_should_use_virtual_center_runtime_geometry(
        result,
        virtual_center_solution,
        requested_residual_model=requested_residual_model,
        effective_residual_model=effective_residual_model,
    )


_point_to_rectilinear_ray = _domain_point_to_rectilinear_ray
_project_ray_to_virtual_rectilinear = _domain_project_ray_to_virtual_rectilinear
_score_virtual_center_candidate = _domain_score_virtual_center_candidate
_solve_virtual_center_rectilinear = _domain_solve_virtual_center_rectilinear


def save_native_calibration_artifacts(
    config: NativeCalibrationConfig,
    result: dict[str, Any],
) -> dict[str, Any]:
    return _save_native_calibration_artifacts_impl(
        config,
        result,
        write_debug_outputs_func=_write_debug_outputs,
        save_homography_file_func=_save_homography_file,
        solve_virtual_center_rectilinear_func=_solve_virtual_center_rectilinear,
        apply_virtual_center_metrics_func=_apply_virtual_center_metrics,
        should_use_virtual_center_runtime_geometry_func=_should_use_virtual_center_runtime_geometry,
        save_calibration_inliers_file_func=_save_calibration_inliers_file,
    )


def calibrate_native_homography_from_frames(
    config: NativeCalibrationConfig,
    left_raw: np.ndarray,
    right_raw: np.ndarray,
    *,
    left_points: list[tuple[float, float]] | None = None,
    right_points: list[tuple[float, float]] | None = None,
    prompt_for_points: bool = False,
    review_required: bool | None = None,
    save_outputs: bool = False,
) -> dict[str, Any]:
    return _calibrate_native_homography_from_frames_impl(
        config,
        left_raw,
        right_raw,
        left_points=left_points,
        right_points=right_points,
        prompt_for_points=prompt_for_points,
        review_required=review_required,
        save_outputs=save_outputs,
        create_feature_match_session_func=_create_feature_match_session,
        should_collect_overlap_hints_func=_should_collect_overlap_hints,
        estimate_overlap_hints_func=_estimate_overlap_hints,
        assisted_ui_cls=_AssistedCalibrationUi,
        auto_match_variant_configs_func=_auto_match_variant_configs,
        detect_auto_matches_func=_detect_auto_matches,
        build_candidate_func=_build_candidate,
        is_high_confidence_auto_candidate_func=_is_high_confidence_auto_candidate,
        build_manual_matches_func=_build_manual_matches,
        manual_candidate_config_func=_manual_candidate_config,
        build_assisted_matches_func=_build_assisted_matches,
        choose_preferred_calibration_candidate_func=choose_preferred_calibration_candidate,
        prepare_warp_plan_func=_prepare_warp_plan,
        blend_feather_func=_blend_feather,
        draw_inlier_preview_func=_draw_inlier_preview,
        review_ui_cls=_CalibrationReviewUi,
        extract_inlier_points_func=_extract_inlier_points,
        build_native_calibration_metadata_func=build_native_calibration_metadata,
        build_native_calibration_result_func=build_native_calibration_result,
        save_native_calibration_artifacts_func=save_native_calibration_artifacts,
        cv2_module=cv2,
    )


def calibrate_native_homography(config: NativeCalibrationConfig) -> dict:
    left_raw, right_raw = _capture_pair(config)
    return calibrate_native_homography_from_frames(
        config,
        left_raw,
        right_raw,
        prompt_for_points=True,
        review_required=bool(config.review_required),
        save_outputs=True,
    )


def backup_homography_file(path: Path) -> Path | None:
    return _backup_homography_file_impl(path)


def run_native_calibration(args: argparse.Namespace) -> int:
    return _run_native_calibration_impl(
        args,
        cv2_module=_cv2,
        require_configured_rtsp_urls_func=require_configured_rtsp_urls,
        native_calibration_config_cls=NativeCalibrationConfig,
        default_calibration_inliers_file=DEFAULT_CALIBRATION_INLIERS_FILE,
        calibrate_native_homography_func=calibrate_native_homography,
        stitching_failure_cls=StitchingFailure,
    )
