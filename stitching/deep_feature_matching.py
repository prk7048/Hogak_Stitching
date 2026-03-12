from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from stitching.core.config import StitchConfig, StitchingFailure
from stitching.errors import ErrorCode


@dataclass(slots=True)
class DeepMatchResult:
    keypoints_left: list[cv2.KeyPoint]
    keypoints_right: list[cv2.KeyPoint]
    matches: list[cv2.DMatch]
    backend_name: str


def detect_and_match_deep(
    left: np.ndarray,
    right: np.ndarray,
    config: StitchConfig,
) -> DeepMatchResult:
    # Placeholder hook for future SuperPoint/LightGlue or ONNX backend.
    # We intentionally fail explicitly so callers can cleanly fall back.
    raise StitchingFailure(
        ErrorCode.INTERNAL_ERROR,
        "deep matcher backend is not installed; use classic backend or install a supported model runtime",
    )
