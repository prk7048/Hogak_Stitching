from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from stitching.errors import ErrorCode


@dataclass(slots=True)
class StitchConfig:
    """스티칭 품질/안정성을 제어하는 공통 설정값."""

    # 특징점 매칭 관련 기본 임계값
    min_matches: int = 80
    min_inliers: int = 30
    ratio_test: float = 0.75
    ransac_reproj_threshold: float = 5.0
    max_features: int = 4000

    # 비정상적으로 큰 캔버스가 만들어지는 것을 막는 안전장치
    max_output_scale: float = 4.0
    max_output_pixels: int = 40_000_000

    # 블렌딩/노출 보정 관련 설정
    seam_transition_px: int = 40
    exposure_compensation: bool = True
    exposure_gain_min: float = 0.7
    exposure_gain_max: float = 1.4
    exposure_bias_abs_max: float = 35.0
    seam_smoothness_penalty: float = 4.0


class StitchingFailure(RuntimeError):
    """리포트에 에러 코드를 남기기 위한 도메인 예외."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class WarpPlan:
    """우측 영상을 좌측 좌표계로 옮기기 위한 워프 계획."""

    homography_adjusted: np.ndarray
    width: int
    height: int
    tx: int
    ty: int


def _detect_and_match(
    left: np.ndarray, right: np.ndarray, config: StitchConfig
) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch]]:
    """ORB + ratio-test로 두 영상의 대응점을 찾는다."""

    gray_left = cv2.cvtColor(left, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(right, cv2.COLOR_BGR2GRAY)

    detector = cv2.ORB_create(nfeatures=config.max_features)
    keypoints_left, descriptors_left = detector.detectAndCompute(gray_left, None)
    keypoints_right, descriptors_right = detector.detectAndCompute(gray_right, None)

    if descriptors_left is None or descriptors_right is None:
        raise StitchingFailure(ErrorCode.OVERLAP_LOW, "descriptor extraction failed")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn_matches = matcher.knnMatch(descriptors_left, descriptors_right, k=2)

    good_matches: list[cv2.DMatch] = []
    for pair in knn_matches:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < config.ratio_test * n.distance:
            good_matches.append(m)

    if len(good_matches) < config.min_matches:
        raise StitchingFailure(
            ErrorCode.OVERLAP_LOW,
            f"matches below threshold: {len(good_matches)} < {config.min_matches}",
        )

    return keypoints_left, keypoints_right, good_matches


def _estimate_homography(
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    config: StitchConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """RANSAC으로 호모그래피를 추정한다."""

    src_points = np.float32([keypoints_right[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst_points = np.float32([keypoints_left[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    homography, inlier_mask = cv2.findHomography(
        src_points,
        dst_points,
        cv2.RANSAC,
        config.ransac_reproj_threshold,
    )

    if homography is None or inlier_mask is None:
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "homography estimation returned null")

    inliers = int(inlier_mask.ravel().sum())
    if inliers < config.min_inliers:
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"inliers below threshold: {inliers} < {config.min_inliers}",
        )
    return homography, inlier_mask


def _estimate_affine_homography(
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    config: StitchConfig,
) -> tuple[np.ndarray, np.ndarray]:
    """
    호모그래피가 불안정할 때 쓰는 보수적 대안.
    완전한 투영 변환 대신 2D affine을 추정한 뒤 3x3 행렬로 확장한다.
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
    min_affine_inliers = max(12, int(config.min_inliers * 0.6))
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
    좌/우 프레임이 모두 들어가는 출력 캔버스를 계산한다.
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


def _blend_feather(canvas_left: np.ndarray, warped_right: np.ndarray, left_mask: np.ndarray, right_mask: np.ndarray) -> np.ndarray:
    """겹치는 영역을 거리 기반 가중치로 부드럽게 섞는다."""

    left_valid = left_mask > 0
    right_valid = right_mask > 0
    overlap = left_valid & right_valid
    only_left = left_valid & ~right_valid
    only_right = right_valid & ~left_valid

    result = np.zeros_like(canvas_left, dtype=np.float32)
    result[only_left] = canvas_left[only_left]
    result[only_right] = warped_right[only_right]

    if np.any(overlap):
        dist_left = cv2.distanceTransform(left_valid.astype(np.uint8), cv2.DIST_L2, 3)
        dist_right = cv2.distanceTransform(right_valid.astype(np.uint8), cv2.DIST_L2, 3)
        denom = dist_left + dist_right + 1e-6
        weight_left = dist_left / denom
        weight_right = dist_right / denom
        w_l = weight_left[overlap][:, None]
        w_r = weight_right[overlap][:, None]
        result[overlap] = canvas_left[overlap] * w_l + warped_right[overlap] * w_r

    return np.clip(result, 0, 255).astype(np.uint8)


def _apply_gain_bias(image: np.ndarray, gain: float, bias: float, mask: np.ndarray | None = None) -> np.ndarray:
    """노출 보정 결과를 영상 전체 또는 마스크 영역에만 반영한다."""

    adjusted = image.astype(np.float32) * float(gain) + float(bias)
    adjusted = np.clip(adjusted, 0, 255).astype(np.uint8)
    if mask is None:
        return adjusted
    out = image.copy()
    valid = mask > 0
    out[valid] = adjusted[valid]
    return out


def _compensate_exposure(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    overlap: np.ndarray,
    right_mask: np.ndarray,
    config: StitchConfig,
) -> tuple[np.ndarray, float, float]:
    """겹치는 영역의 평균/표준편차를 기준으로 우측 영상 노출을 맞춘다."""

    if not np.any(overlap):
        return warped_right, 1.0, 0.0
    gray_left = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray_right = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY).astype(np.float32)
    left_vals = gray_left[overlap]
    right_vals = gray_right[overlap]
    if left_vals.size < 64 or right_vals.size < 64:
        return warped_right, 1.0, 0.0

    mean_left = float(left_vals.mean())
    mean_right = float(right_vals.mean())
    std_left = float(left_vals.std())
    std_right = float(right_vals.std())

    gain = 1.0
    if std_right > 1e-3:
        gain = std_left / std_right
    gain = float(np.clip(gain, config.exposure_gain_min, config.exposure_gain_max))

    bias = mean_left - gain * mean_right
    bias = float(np.clip(bias, -config.exposure_bias_abs_max, config.exposure_bias_abs_max))

    compensated = _apply_gain_bias(warped_right, gain=gain, bias=bias, mask=right_mask)
    return compensated, gain, bias


def _compute_seam_cost_map(canvas_left: np.ndarray, warped_right: np.ndarray, overlap: np.ndarray) -> np.ndarray:
    """
    seam-cut용 비용맵.
    밝기 차이 + 경계(gradient) 패널티를 합쳐 사람이 눈에 띄는 경로를 피한다.
    """

    gray_left = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray_right = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY).astype(np.float32)
    diff = cv2.absdiff(gray_left, gray_right)

    grad_x = cv2.Sobel(gray_left, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray_left, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = cv2.magnitude(grad_x, grad_y)

    cost = diff + 0.25 * grad_mag
    cost[~overlap] = 1e9
    return cost


def _find_seam_path(
    overlap: np.ndarray,
    cost_map: np.ndarray,
    smoothness_penalty: float,
) -> np.ndarray:
    """동적 계획법으로 위->아래 seam 경로를 찾는다."""

    h, w = overlap.shape
    seam_path = np.full(h, -1, dtype=np.int32)
    ys, xs = np.where(overlap)
    if len(xs) == 0 or len(ys) == 0:
        seam_path[:] = w // 2
        return seam_path

    x_min, x_max = int(xs.min()), int(xs.max())
    seam_w = x_max - x_min + 1
    inf = 1e18
    dp = np.full((h, seam_w), inf, dtype=np.float64)
    prev_idx = np.full((h, seam_w), -1, dtype=np.int32)

    first_valid_row = int(ys.min())
    valid0 = overlap[first_valid_row, x_min : x_max + 1]
    row0_cost = cost_map[first_valid_row, x_min : x_max + 1]
    dp[first_valid_row, valid0] = row0_cost[valid0]

    for y in range(first_valid_row + 1, h):
        valid = overlap[y, x_min : x_max + 1]
        if not np.any(valid):
            continue
        prev_cost = dp[y - 1]
        row_cost = cost_map[y, x_min : x_max + 1]
        for x in np.where(valid)[0]:
            candidates = []
            for step in (-1, 0, 1):
                px = x + step
                if px < 0 or px >= seam_w:
                    continue
                p_cost = prev_cost[px]
                if p_cost >= inf * 0.5:
                    continue
                penalty = smoothness_penalty * abs(step)
                candidates.append((p_cost + penalty, px))
            if not candidates:
                candidates.append((row_cost[x], x))
            best_cost, best_prev = min(candidates, key=lambda t: t[0])
            dp[y, x] = row_cost[x] + best_cost
            prev_idx[y, x] = best_prev

    last_row = int(ys.max())
    valid_last = overlap[last_row, x_min : x_max + 1]
    if not np.any(valid_last):
        seam_path[:] = (x_min + x_max) // 2
        return seam_path

    last_candidates = np.where(valid_last)[0]
    best_x_local = int(last_candidates[np.argmin(dp[last_row, last_candidates])])
    seam_path[last_row] = x_min + best_x_local

    for y in range(last_row, first_valid_row, -1):
        prev_local = prev_idx[y, seam_path[y] - x_min]
        seam_path[y - 1] = seam_path[y] if prev_local < 0 else x_min + int(prev_local)

    for y in range(first_valid_row):
        seam_path[y] = seam_path[first_valid_row]
    for y in range(first_valid_row, h):
        if seam_path[y] < 0:
            seam_path[y] = seam_path[y - 1]
    return seam_path


def _blend_seam_path(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
    seam_path: np.ndarray,
    transition_px: int,
) -> np.ndarray:
    """seam 경로를 중심으로 좌/우 영상을 선형 전이로 섞는다."""

    left_valid = left_mask > 0
    right_valid = right_mask > 0
    overlap = left_valid & right_valid
    only_left = left_valid & ~right_valid
    only_right = right_valid & ~left_valid

    result = np.zeros_like(canvas_left, dtype=np.float32)
    result[only_left] = canvas_left[only_left]
    result[only_right] = warped_right[only_right]

    if np.any(overlap):
        transition = max(2, int(transition_px))
        h, w = overlap.shape
        x_coords = np.arange(w, dtype=np.float32)
        for y in range(h):
            row_overlap = overlap[y]
            if not np.any(row_overlap):
                continue
            seam_x = float(seam_path[y])
            right_w = np.clip((x_coords - (seam_x - transition / 2.0)) / transition, 0.0, 1.0)
            left_w = 1.0 - right_w
            blend_row = (
                canvas_left[y].astype(np.float32) * left_w[:, None]
                + warped_right[y].astype(np.float32) * right_w[:, None]
            )
            result[y, row_overlap] = blend_row[row_overlap]

    return np.clip(result, 0, 255).astype(np.uint8)


def _compute_overlap_diff_mean(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    overlap: np.ndarray,
) -> float:
    """겹치는 영역의 평균 밝기 차이(작을수록 자연스러움)."""

    if not np.any(overlap):
        return 0.0
    gray_left = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray_left, gray_right).astype(np.float32)
    return float(diff[overlap].mean())
