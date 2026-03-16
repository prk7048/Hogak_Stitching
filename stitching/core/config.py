from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from stitching.errors import ErrorCode


@dataclass(slots=True)
class StitchConfig:
    """스티칭 매칭/정합/합성에 공통으로 사용하는 설정값."""

    min_matches: int = 80
    min_inliers: int = 30
    ratio_test: float = 0.75
    ransac_reproj_threshold: float = 5.0
    max_features: int = 4000

    max_output_scale: float = 4.0
    max_output_pixels: int = 40_000_000

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
    """오른쪽 영상을 좌측 좌표계로 보내기 위한 워프 계획."""

    homography_adjusted: np.ndarray
    width: int
    height: int
    tx: int
    ty: int
