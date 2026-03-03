from __future__ import annotations

import cv2
import numpy as np

from stitching.core.config import StitchConfig, StitchingFailure
from stitching.errors import ErrorCode


def _detect_and_match(
    left: np.ndarray,
    right: np.ndarray,
    config: StitchConfig,
) -> tuple[list[cv2.KeyPoint], list[cv2.KeyPoint], list[cv2.DMatch]]:
    """ORB + ratio-test로 두 영상의 특징점을 매칭한다."""

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
