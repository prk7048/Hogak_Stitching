from __future__ import annotations

import cv2
import numpy as np

from stitching.core.config import StitchConfig


def _apply_gain_bias(
    image: np.ndarray,
    gain: float,
    bias: float,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    """노출 보정 결과를 전체 또는 마스크 영역에만 적용한다."""

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
    """겹침 영역 평균/표준편차를 기준으로 우측 영상 노출을 보정한다."""

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


def _compute_overlap_diff_mean(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    overlap: np.ndarray,
) -> float:
    """겹침 영역 평균 밝기 차이(작을수록 자연스러움)."""

    if not np.any(overlap):
        return 0.0
    gray_left = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(gray_left, gray_right).astype(np.float32)
    return float(diff[overlap].mean())
