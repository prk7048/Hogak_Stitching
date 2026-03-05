"""
Backward-compatible facade for stitching core helpers.

이 파일은 기존 import 경로(`stitching.stitch_core`)를 유지하기 위한 호환 레이어다.
실제 구현은 `stitching/core/` 하위 모듈로 분리했다.
"""

from stitching.core import (
    StitchConfig,
    StitchingFailure,
    WarpPlan,
    _apply_gain_bias,
    _blend_feather,
    _blend_seam_path,
    _compensate_exposure,
    _compute_overlap_diff_mean,
    _compute_seam_cost_map,
    _detect_and_match,
    _estimate_affine_homography,
    _estimate_homography,
    _find_seam_path,
    _prepare_warp_plan,
)

__all__ = [
    "StitchConfig",
    "StitchingFailure",
    "WarpPlan",
    "_detect_and_match",
    "_estimate_homography",
    "_estimate_affine_homography",
    "_prepare_warp_plan",
    "_blend_feather",
    "_blend_seam_path",
    "_compute_seam_cost_map",
    "_find_seam_path",
    "_apply_gain_bias",
    "_compensate_exposure",
    "_compute_overlap_diff_mean",
]

