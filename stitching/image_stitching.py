from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

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
class StitchConfig:
    min_matches: int = 80
    min_inliers: int = 30
    ratio_test: float = 0.75
    ransac_reproj_threshold: float = 5.0
    max_features: int = 4000
    max_output_scale: float = 4.0
    max_output_pixels: int = 40_000_000
    ghost_diff_threshold: float = 18.0
    seam_transition_px: int = 40


class StitchingFailure(RuntimeError):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class WarpPlan:
    homography_adjusted: np.ndarray
    width: int
    height: int
    tx: int
    ty: int


def _read_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise StitchingFailure(ErrorCode.PROBE_FAIL, f"cannot read image: {path}")
    return image


def _detect_and_match(
    left: np.ndarray, right: np.ndarray, config: StitchConfig
) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch]]:
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
    left_h, left_w = left_shape
    right_h, right_w = right_shape

    corners_left = np.float32([[0, 0], [left_w, 0], [left_w, left_h], [0, left_h]]).reshape(-1, 1, 2)
    corners_right = np.float32([[0, 0], [right_w, 0], [right_w, right_h], [0, right_h]]).reshape(-1, 1, 2)
    warped_right_corners = cv2.perspectiveTransform(corners_right, homography)

    all_corners = np.vstack((corners_left, warped_right_corners)).reshape(-1, 2)
    min_x, min_y = np.floor(all_corners.min(axis=0)).astype(int)
    max_x, max_y = np.ceil(all_corners.max(axis=0)).astype(int)

    # Always shift by min corner so the composed canvas starts at (0, 0).
    # Using only negative minima can offset content incorrectly when min corner is positive.
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

    result = np.clip(result, 0, 255).astype(np.uint8)
    return result


def _compute_overlap_diff_mean(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    overlap: np.ndarray,
) -> float:
    if not np.any(overlap):
        return 0.0
    gray_left = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray_left, gray_right).astype(np.float32)
    return float(diff[overlap].mean())


def _find_vertical_seam_x(
    overlap: np.ndarray,
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
) -> int:
    ys, xs = np.where(overlap)
    if len(xs) == 0:
        return canvas_left.shape[1] // 2
    x_min, x_max = int(xs.min()), int(xs.max())
    gray_left = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray_left, gray_right).astype(np.float32)

    best_x = x_min
    best_cost = float("inf")
    for x in range(x_min, x_max + 1):
        col_mask = overlap[:, x]
        if not np.any(col_mask):
            continue
        col_cost = float(diff[:, x][col_mask].mean())
        if col_cost < best_cost:
            best_cost = col_cost
            best_x = x
    return best_x


def _blend_seam_cut(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
    transition_px: int,
) -> tuple[np.ndarray, int]:
    left_valid = left_mask > 0
    right_valid = right_mask > 0
    overlap = left_valid & right_valid
    only_left = left_valid & ~right_valid
    only_right = right_valid & ~left_valid

    result = np.zeros_like(canvas_left, dtype=np.float32)
    result[only_left] = canvas_left[only_left]
    result[only_right] = warped_right[only_right]

    seam_x = _find_vertical_seam_x(overlap, canvas_left, warped_right)
    if np.any(overlap):
        transition = max(2, int(transition_px))
        x_coords = np.arange(canvas_left.shape[1], dtype=np.float32)[None, :]
        right_w = np.clip((x_coords - (seam_x - transition / 2.0)) / transition, 0.0, 1.0)
        left_w = 1.0 - right_w
        blended = (
            canvas_left.astype(np.float32) * left_w[..., None]
            + warped_right.astype(np.float32) * right_w[..., None]
        )
        result[overlap] = blended[overlap]

    return np.clip(result, 0, 255).astype(np.uint8), seam_x


def _crop_to_valid(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        return image
    x_min, x_max = xs.min(), xs.max()
    y_min, y_max = ys.min(), ys.max()
    return image[y_min : y_max + 1, x_min : x_max + 1]


def _save_debug_matches(
    path: Path,
    left: np.ndarray,
    right: np.ndarray,
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    mask: np.ndarray | None = None,
) -> None:
    if not matches:
        return
    display_matches = matches[:300]
    draw_mask: list[int] | None = None
    if mask is not None:
        mask_values = [int(v) for v in mask.ravel().tolist()]
        draw_mask = mask_values[: len(display_matches)]
    visual = cv2.drawMatches(
        left,
        keypoints_left,
        right,
        keypoints_right,
        display_matches,
        None,
        matchesMask=draw_mask,
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), visual)


def stitch_images(
    left_path: Path,
    right_path: Path,
    output_path: Path,
    report_path: Path,
    debug_dir: Path,
    config: StitchConfig | None = None,
    job_id: str | None = None,
    status_hook: Callable[[str], None] | None = None,
) -> dict:
    config = config or StitchConfig()
    started_at = time.perf_counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    report = base_report(
        pipeline="image",
        inputs={"left": str(left_path), "right": str(right_path)},
        job_id=job_id,
    )
    stage_times: dict[str, float] = {}
    report["metrics"]["processing_time_sec"] = stage_times
    debug_dir.mkdir(parents=True, exist_ok=True)

    try:
        if status_hook is not None:
            status_hook("probing")
        with StageTimer(stage_times, "probe"):
            left = _read_image(left_path)
            right = _read_image(right_path)

        if status_hook is not None:
            status_hook("feature_match")
        with StageTimer(stage_times, "feature_match"):
            keypoints_left, keypoints_right, matches = _detect_and_match(left, right, config)
            report["metrics"]["matches_count"] = len(matches)
            _save_debug_matches(
                debug_dir / "matches.jpg",
                left,
                right,
                keypoints_left,
                keypoints_right,
                matches,
            )

        if status_hook is not None:
            status_hook("homography")
        with StageTimer(stage_times, "homography"):
            used_fallback = False
            try:
                homography, inlier_mask = _estimate_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    config,
                )
                plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
            except StitchingFailure as exc:
                if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
                    raise
                homography, inlier_mask = _estimate_affine_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    config,
                )
                plan = _prepare_warp_plan(left.shape[:2], right.shape[:2], homography, config)
                used_fallback = True

            report["metrics"]["inliers_count"] = int(inlier_mask.ravel().sum())
            if used_fallback:
                report["warnings"].append("homography_unstable_fallback_affine")
            _save_debug_matches(
                debug_dir / "inliers.jpg",
                left,
                right,
                keypoints_left,
                keypoints_right,
                matches,
                inlier_mask,
            )

        if status_hook is not None:
            status_hook("stitching")
        with StageTimer(stage_times, "warp_blend"):
            warped_right = cv2.warpPerspective(right, plan.homography_adjusted, (plan.width, plan.height))
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

            overlap = (left_mask > 0) & (right_mask > 0)
            overlap_diff_mean = _compute_overlap_diff_mean(canvas_left, warped_right, overlap)
            use_seam_cut = used_fallback or overlap_diff_mean >= config.ghost_diff_threshold
            if use_seam_cut:
                stitched, seam_x = _blend_seam_cut(
                    canvas_left,
                    warped_right,
                    left_mask,
                    right_mask,
                    transition_px=config.seam_transition_px,
                )
                report["metrics"]["blend_mode"] = "seam_cut"
                report["metrics"]["seam_x"] = int(seam_x)
                if "homography_unstable_fallback_affine" not in report["warnings"]:
                    report["warnings"].append("high_overlap_difference_seam_cut")
            else:
                stitched = _blend_feather(canvas_left, warped_right, left_mask, right_mask)
                report["metrics"]["blend_mode"] = "feather"
                report["metrics"]["seam_x"] = None
            report["metrics"]["overlap_diff_mean"] = round(overlap_diff_mean, 3)
            union_mask = ((left_mask > 0) | (right_mask > 0)).astype(np.uint8) * 255
            stitched = _crop_to_valid(stitched, union_mask)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            ok = cv2.imwrite(str(output_path), stitched)
            if not ok:
                raise StitchingFailure(ErrorCode.ENCODE_FAIL, f"failed to write output image: {output_path}")

            cv2.imwrite(str(debug_dir / "warp_overlay.png"), stitched)
            out_h, out_w = stitched.shape[:2]
            report["metrics"]["output_resolution"] = [int(out_w), int(out_h)]

        mark_succeeded(report)

    except StitchingFailure as exc:
        mark_failed(report, exc.code, exc.detail)
    except Exception as exc:  # pragma: no cover - defensive path
        mark_failed(report, ErrorCode.INTERNAL_ERROR, f"unexpected error: {exc}")
    finally:
        finalize_total_time(report, started_at)
        write_report(report_path, report)

    return report
