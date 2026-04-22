from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np

from stitching.errors import ErrorCode

if TYPE_CHECKING:
    import cv2 as cv2_types

    CvDMatch = cv2_types.DMatch
    CvKeyPoint = cv2_types.KeyPoint
else:
    CvDMatch = Any
    CvKeyPoint = Any


def match_backend_priority(match_backend: str) -> list[str]:
    requested = str(match_backend or "").strip().lower()
    if requested in {"orb", "orb-only"}:
        return ["orb"]
    if requested in {"sift-only"}:
        return ["sift"]
    if requested in {"sift", "sift-primary", "classic", "auto", ""}:
        return ["sift", "orb"]
    return ["sift", "orb"]


def create_feature_match_session(
    left: np.ndarray,
    right: np.ndarray,
    *,
    cv2_module: Any,
    feature_match_session_cls: Any,
) -> Any:
    return feature_match_session_cls(
        gray_left=cv2_module.cvtColor(left, cv2_module.COLOR_BGR2GRAY),
        gray_right=cv2_module.cvtColor(right, cv2_module.COLOR_BGR2GRAY),
        backend_matches={},
    )


def backend_display_name(match_backend: str, backend: str) -> str:
    requested_backend = str(match_backend or "").strip().lower()
    if backend == "sift":
        return "sift-primary"
    return "orb" if requested_backend in {"orb", "orb-only"} else "orb-fallback"


def backend_match_cache_entry(
    session: Any,
    config: Any,
    backend: str,
    *,
    cv2_module: Any,
    backend_match_cache_entry_cls: Any,
    stitching_failure_cls: Any,
) -> Any:
    cache_key = (str(backend).strip().lower(), max(0, int(config.max_features)))
    cached = session.backend_matches.get(cache_key)
    if cached is not None:
        return cached

    if backend == "sift":
        sift_factory = getattr(cv2_module, "SIFT_create", None)
        if sift_factory is None:
            raise stitching_failure_cls(ErrorCode.OVERLAP_LOW, "sift detector unavailable")
        detector = sift_factory(nfeatures=cache_key[1])
        norm_type = cv2_module.NORM_L2
    else:
        detector = cv2_module.ORB_create(nfeatures=cache_key[1])
        norm_type = cv2_module.NORM_HAMMING

    keypoints_left, descriptors_left = detector.detectAndCompute(session.gray_left, None)
    keypoints_right, descriptors_right = detector.detectAndCompute(session.gray_right, None)
    if descriptors_left is None or descriptors_right is None:
        raise stitching_failure_cls(ErrorCode.OVERLAP_LOW, "descriptor extraction failed")

    matcher = cv2_module.BFMatcher(norm_type, crossCheck=False)
    knn_matches_raw = matcher.knnMatch(descriptors_left, descriptors_right, k=2)
    knn_matches = [list(pair[:2]) for pair in knn_matches_raw if len(pair) >= 2]
    cached = backend_match_cache_entry_cls(
        keypoints_left=keypoints_left,
        keypoints_right=keypoints_right,
        knn_matches=knn_matches,
    )
    session.backend_matches[cache_key] = cached
    return cached


def detect_matches_for_backend_raw(
    left: np.ndarray,
    right: np.ndarray,
    config: Any,
    backend: str,
    *,
    feature_session: Any | None = None,
    create_feature_match_session_func: Any,
    backend_match_cache_entry_func: Any,
    backend_display_name_func: Any,
    stitching_failure_cls: Any,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str]:
    _ = (left, right)
    match_session = feature_session if feature_session is not None else create_feature_match_session_func(left, right)
    cached = backend_match_cache_entry_func(match_session, config, backend)
    backend_name = backend_display_name_func(config.match_backend, backend)
    good_matches: list[CvDMatch] = []
    for pair in cached.knn_matches:
        m, n = pair
        if m.distance < config.ratio_test * n.distance:
            good_matches.append(m)
    if not good_matches:
        raise stitching_failure_cls(ErrorCode.OVERLAP_LOW, f"{backend_name} produced no matches")
    return cached.keypoints_left, cached.keypoints_right, good_matches, backend_name


def detect_and_match_feature_raw(
    left: np.ndarray,
    right: np.ndarray,
    config: Any,
    *,
    minimum_match_count: int | None = None,
    feature_session: Any | None = None,
    match_backend_priority_func: Any,
    detect_matches_for_backend_raw_func: Any,
    stitching_failure_cls: Any,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str]:
    for backend in match_backend_priority_func(config.match_backend):
        try:
            keypoints_left, keypoints_right, good_matches, backend_name = detect_matches_for_backend_raw_func(
                left,
                right,
                config,
                backend,
                feature_session=feature_session,
            )
        except stitching_failure_cls:
            continue
        if minimum_match_count is None or len(good_matches) >= int(minimum_match_count):
            return keypoints_left, keypoints_right, good_matches, backend_name

    raise stitching_failure_cls(ErrorCode.OVERLAP_LOW, "feature matching failed with SIFT and ORB backends")


def detect_and_match_classic_raw(
    left: np.ndarray,
    right: np.ndarray,
    config: Any,
    *,
    feature_session: Any | None = None,
    detect_and_match_feature_raw_func: Any,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch]]:
    keypoints_left, keypoints_right, matches, _backend_name = detect_and_match_feature_raw_func(
        left,
        right,
        config,
        minimum_match_count=8,
        feature_session=feature_session,
    )
    return keypoints_left, keypoints_right, matches


def guidance_threshold_px(config: Any, seed_model: str) -> float:
    base = float(config.assisted_reproj_threshold)
    if seed_model == "translation":
        return max(80.0, base * 8.0)
    if seed_model == "affine_seed":
        return max(40.0, base * 4.0)
    if seed_model == "homography_seed":
        return max(20.0, base * 2.0)
    return max(20.0, base * 2.0)


def assisted_min_matches(config: Any) -> int:
    return max(8, min(int(config.min_matches), 20))


def build_assisted_matches(
    left: np.ndarray,
    right: np.ndarray,
    config: Any,
    left_points: list[tuple[float, float]],
    right_points: list[tuple[float, float]],
    *,
    feature_session: Any | None = None,
    detect_auto_matches_func: Any,
    estimate_seed_guidance_transform_func: Any,
    guidance_threshold_px_func: Any,
    reprojection_error_func: Any,
    assisted_min_matches_func: Any,
    stitching_failure_cls: Any,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str, str, str]:
    if not left_points:
        keypoints_left, keypoints_right, matches, backend_name = detect_auto_matches_func(
            left,
            right,
            config,
            feature_session=feature_session,
        )
        return keypoints_left, keypoints_right, matches, "auto", "none", backend_name

    keypoints_left_auto, keypoints_right_auto, auto_matches, backend_name = detect_auto_matches_func(
        left,
        right,
        config,
        feature_session=feature_session,
    )
    seed_transform, seed_model = estimate_seed_guidance_transform_func(left_points, right_points, config)
    threshold_px = guidance_threshold_px_func(config, seed_model)
    filtered_auto_matches = []
    scored_matches: list[tuple[float, CvDMatch]] = []
    for match in auto_matches:
        left_pt = keypoints_left_auto[match.queryIdx].pt
        right_pt = keypoints_right_auto[match.trainIdx].pt
        reproj_error = reprojection_error_func(seed_transform, right_pt, left_pt)
        scored_matches.append((reproj_error, match))
        if reproj_error <= threshold_px:
            filtered_auto_matches.append(match)
    if len(filtered_auto_matches) < assisted_min_matches_func(config):
        scored_matches.sort(key=lambda item: item[0])
        filtered_auto_matches = [
            match for _, match in scored_matches[: min(len(scored_matches), config.assisted_max_auto_matches)]
        ]
    filtered_auto_matches = filtered_auto_matches[: max(0, int(config.assisted_max_auto_matches))]
    if len(filtered_auto_matches) < assisted_min_matches_func(config):
        raise stitching_failure_cls(
            ErrorCode.OVERLAP_LOW,
            f"seed-guided matches below threshold: {len(filtered_auto_matches)} < {assisted_min_matches_func(config)}",
        )
    return keypoints_left_auto, keypoints_right_auto, filtered_auto_matches, "assisted", seed_model, backend_name


def build_manual_matches(
    left_points: list[tuple[float, float]],
    right_points: list[tuple[float, float]],
    *,
    cv2_module: Any,
    stitching_failure_cls: Any,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str, str, str]:
    pair_count = min(len(left_points), len(right_points))
    if pair_count < 4:
        raise stitching_failure_cls(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"manual correspondence pairs below threshold: {pair_count} < 4",
        )

    keypoints_left: list[CvKeyPoint] = []
    keypoints_right: list[CvKeyPoint] = []
    matches: list[CvDMatch] = []
    for index, (left_pt, right_pt) in enumerate(zip(left_points[:pair_count], right_points[:pair_count])):
        keypoints_left.append(cv2_module.KeyPoint(float(left_pt[0]), float(left_pt[1]), 1.0))
        keypoints_right.append(cv2_module.KeyPoint(float(right_pt[0]), float(right_pt[1]), 1.0))
        matches.append(cv2_module.DMatch(index, index, 0, 0.0))
    return keypoints_left, keypoints_right, matches, "manual", "manual_pairs", "manual_points"


def manual_candidate_config(config: Any, *, pair_count: int) -> Any:
    manual_min_inliers = max(4, min(int(pair_count), 6))
    manual_min_matches = max(4, min(int(pair_count), int(config.min_matches)))
    return replace(
        config,
        min_inliers=manual_min_inliers,
        min_matches=manual_min_matches,
    )


def detect_auto_matches(
    left: np.ndarray,
    right: np.ndarray,
    config: Any,
    *,
    feature_session: Any | None = None,
    detect_and_match_feature_raw_func: Any,
) -> tuple[list[CvKeyPoint], list[CvKeyPoint], list[CvDMatch], str]:
    keypoints_left, keypoints_right, matches, backend_name = detect_and_match_feature_raw_func(
        left,
        right,
        config,
        minimum_match_count=int(config.min_matches),
        feature_session=feature_session,
    )
    return keypoints_left, keypoints_right, matches, backend_name


def auto_match_variant_configs(config: Any) -> list[tuple[str, Any]]:
    requested = str(config.match_backend or "").strip().lower()
    variants: list[tuple[str, Any]] = [("primary", config)]
    if requested in {"classic", "auto", "sift", "sift-primary", ""}:
        variants.append(
            (
                "sift-tight",
                replace(
                    config,
                    match_backend="sift-only",
                    ratio_test=max(0.64, float(config.ratio_test) - 0.07),
                    min_matches=max(8, int(round(float(config.min_matches) * 0.60))),
                ),
            )
        )
        variants.append(
            (
                "orb-fallback",
                replace(
                    config,
                    match_backend="orb-only",
                    ratio_test=min(0.82, float(config.ratio_test) + 0.03),
                    min_matches=max(8, int(round(float(config.min_matches) * 0.60))),
                    max_features=max(int(config.max_features), 5000),
                ),
            )
        )
    return variants


def clamp_overlap_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int] | None:
    x1 = max(0, int(np.floor(x)))
    y1 = max(0, int(np.floor(y)))
    x2 = min(image_width, int(np.ceil(x + w)))
    y2 = min(image_height, int(np.ceil(y + h)))
    if x2 - x1 < 12 or y2 - y1 < 12:
        return None
    return x1, y1, x2 - x1, y2 - y1


def robust_overlap_rect(
    points: np.ndarray,
    *,
    image_width: int,
    image_height: int,
    prefer_side: str,
    clamp_overlap_rect_func: Any,
) -> tuple[int, int, int, int] | None:
    if points.size == 0 or len(points) < 6:
        return None
    xs = points[:, 0]
    ys = points[:, 1]
    x1 = float(np.percentile(xs, 8))
    x2 = float(np.percentile(xs, 92))
    y1 = float(np.percentile(ys, 8))
    y2 = float(np.percentile(ys, 92))

    width = max(24.0, x2 - x1)
    height = max(24.0, y2 - y1)

    x_pad = width * 0.24
    y_pad = height * 0.22
    x1 -= x_pad
    x2 += x_pad
    y1 -= y_pad
    y2 += y_pad

    min_width = image_width * 0.42
    current_width = x2 - x1
    if current_width < min_width:
        expand = (min_width - current_width) * 0.5
        x1 -= expand
        x2 += expand

    max_width = image_width * 0.78
    current_width = x2 - x1
    if current_width > max_width:
        if prefer_side == "right":
            x2 = min(float(image_width), x2)
            x1 = x2 - max_width
        else:
            x1 = max(0.0, x1)
            x2 = x1 + max_width

    min_height = image_height * 0.34
    current_height = y2 - y1
    if current_height < min_height:
        expand = (min_height - current_height) * 0.5
        y1 -= expand
        y2 += expand

    max_height = image_height * 0.86
    current_height = y2 - y1
    if current_height > max_height:
        center_y = (y1 + y2) * 0.5
        y1 = center_y - max_height * 0.5
        y2 = center_y + max_height * 0.5

    return clamp_overlap_rect_func(x1, y1, x2 - x1, y2 - y1, image_width, image_height)


def estimate_overlap_hints(
    left: np.ndarray,
    right: np.ndarray,
    config: Any,
    *,
    feature_session: Any | None = None,
    detect_and_match_classic_raw_func: Any,
    robust_overlap_rect_func: Any,
    stitching_failure_cls: Any,
) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
    try:
        keypoints_left, keypoints_right, matches = detect_and_match_classic_raw_func(
            left,
            right,
            config,
            feature_session=feature_session,
        )
    except stitching_failure_cls:
        return None, None
    if len(matches) < 8:
        return None, None
    sorted_matches = sorted(matches, key=lambda match: float(match.distance))
    limit = min(len(sorted_matches), 60)
    left_pts = np.float32([keypoints_left[m.queryIdx].pt for m in sorted_matches[:limit]])
    right_pts = np.float32([keypoints_right[m.trainIdx].pt for m in sorted_matches[:limit]])
    left_rect = robust_overlap_rect_func(
        left_pts,
        image_width=left.shape[1],
        image_height=left.shape[0],
        prefer_side="right",
    )
    right_rect = robust_overlap_rect_func(
        right_pts,
        image_width=right.shape[1],
        image_height=right.shape[0],
        prefer_side="left",
    )
    return left_rect, right_rect
