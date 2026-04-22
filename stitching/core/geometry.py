from __future__ import annotations

import cv2
import numpy as np

from stitching.core.config import StitchConfig, StitchingFailure, WarpPlan
from stitching.errors import ErrorCode


def _estimate_homography(
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    config: StitchConfig,
    *,
    left_shape: tuple[int, int] | None = None,
    right_shape: tuple[int, int] | None = None,
    method_candidates: list[tuple[str, int]] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """RANSAC으로 호모그래피를 추정한다."""

    src_points = np.float32([keypoints_right[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst_points = np.float32([keypoints_left[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    if method_candidates is None:
        method_candidates = []
        seen_methods: set[int] = set()
        for method_name, method_value in (
            ("usac_magsac", getattr(cv2, "USAC_MAGSAC", None)),
            ("usac_accurate", getattr(cv2, "USAC_ACCURATE", None)),
            ("usac_default", getattr(cv2, "USAC_DEFAULT", None)),
            ("ransac", cv2.RANSAC),
            ("lmeds", cv2.LMEDS),
        ):
            if method_value is None:
                continue
            method_int = int(method_value)
            if method_int in seen_methods:
                continue
            seen_methods.add(method_int)
            method_candidates.append((str(method_name), method_int))

    best_candidate: tuple[np.ndarray, np.ndarray] | None = None
    best_rank: tuple[int, int, float, float] | None = None
    last_null_method: str | None = None
    last_inlier_failure: tuple[str, int] | None = None

    for method_name, method_value in method_candidates:
        try:
            homography, inlier_mask = cv2.findHomography(
                src_points,
                dst_points,
                int(method_value),
                config.ransac_reproj_threshold,
            )
        except cv2.error:
            continue

        if homography is None or inlier_mask is None:
            last_null_method = str(method_name)
            continue

        inliers = int(inlier_mask.ravel().sum())
        if inliers < config.min_inliers:
            last_inlier_failure = (str(method_name), inliers)
            continue

        projected_points = cv2.perspectiveTransform(src_points, homography)
        errors = np.linalg.norm(projected_points.reshape(-1, 2) - dst_points.reshape(-1, 2), axis=1)
        inlier_values = inlier_mask.ravel().astype(bool)
        mean_error = float(np.mean(errors[inlier_values])) if np.any(inlier_values) else float(np.mean(errors))

        geometry_ok = 0
        geometry_area = float("inf")
        if left_shape is not None and right_shape is not None:
            try:
                plan = _prepare_warp_plan(left_shape, right_shape, homography, config)
            except StitchingFailure:
                geometry_ok = 0
            else:
                geometry_ok = 1
                geometry_area = float(plan.width * plan.height)

        candidate_rank = (
            geometry_ok,
            inliers,
            -mean_error,
            -geometry_area,
        )
        if best_rank is None or candidate_rank > best_rank:
            best_rank = candidate_rank
            best_candidate = (
                np.asarray(homography, dtype=np.float64),
                np.asarray(inlier_mask, dtype=np.uint8).reshape(-1, 1),
            )

    if best_candidate is None:
        if last_inlier_failure is not None:
            method_name, inliers = last_inlier_failure
            raise StitchingFailure(
                ErrorCode.HOMOGRAPHY_FAIL,
                f"{method_name} inliers below threshold: {inliers} < {config.min_inliers}",
            )
        if last_null_method is not None:
            raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, f"{last_null_method} homography estimation returned null")
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "homography estimation returned null")
    return best_candidate


def _estimate_affine_homography(
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    config: StitchConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """
    호모그래피가 불안정할 때 사용하는 보수적 대안.
    회전/이동 변환 중심의 2D affine을 추정하고 3x3 행렬로 확장한다.
    """

    src_points = np.float32([keypoints_right[m.trainIdx].pt for m in matches]).reshape(-1, 2)
    dst_points = np.float32([keypoints_left[m.queryIdx].pt for m in matches]).reshape(-1, 2)
    affine, inlier_mask = cv2.estimateAffinePartial2D(
        src_points,
        dst_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=config.ransac_reproj_threshold,
    )
    if affine is None or inlier_mask is None:
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "affine fallback estimation returned null")

    inliers = int(inlier_mask.ravel().sum())
    affine_floor = max(0, int(getattr(config, "min_affine_inliers_floor", 12)))
    min_affine_inliers = max(affine_floor, int(config.min_inliers * 0.6))
    if inliers < min_affine_inliers:
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"affine inliers below threshold: {inliers} < {min_affine_inliers}",
        )

    homography = np.eye(3, dtype=np.float64)
    homography[:2, :] = affine
    return homography, inlier_mask.reshape(-1, 1)


def _prepare_warp_plan(
    left_shape: tuple[int, int],
    right_shape: tuple[int, int],
    homography: np.ndarray,
    config: StitchConfig,
) -> WarpPlan:
    """
    두 프레임이 모두 들어가는 출력 캔버스를 계산한다.
    캔버스가 비정상적으로 커지면 즉시 실패시켜 메모리 폭주를 막는다.
    """

    left_h, left_w = left_shape
    right_h, right_w = right_shape

    corners_left = np.float32([[0, 0], [left_w, 0], [left_w, left_h], [0, left_h]]).reshape(-1, 1, 2)
    corners_right = np.float32([[0, 0], [right_w, 0], [right_w, right_h], [0, right_h]]).reshape(-1, 1, 2)
    warped_right_corners = cv2.perspectiveTransform(corners_right, homography)

    all_corners = np.vstack((corners_left, warped_right_corners)).reshape(-1, 2)
    min_x, min_y = np.floor(all_corners.min(axis=0)).astype(int)
    max_x, max_y = np.ceil(all_corners.max(axis=0)).astype(int)

    tx = -min_x
    ty = -min_y
    width = int(max_x - min_x)
    height = int(max_y - min_y)
    if width <= 0 or height <= 0:
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "invalid output geometry")

    max_dim = int(max(left_w, right_w, left_h, right_h) * config.max_output_scale)
    if width > max_dim or height > max_dim:
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"output geometry too large: {width}x{height}",
        )
    if width * height > config.max_output_pixels:
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"output pixel count too large: {width * height}",
        )

    translation = np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1]], dtype=np.float64)
    homography_adjusted = translation @ homography
    return WarpPlan(homography_adjusted=homography_adjusted, width=width, height=height, tx=tx, ty=ty)
