from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

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
    requested = str(getattr(config, "deep_backend", "auto")).lower().strip() or "auto"
    failures: list[str] = []

    if requested in {"auto", "lightglue"}:
        try:
            return _detect_and_match_lightglue(left, right, config)
        except StitchingFailure as exc:
            failures.append(f"lightglue:{exc.detail}")
            if requested == "lightglue":
                raise

    if requested in {"auto", "loftr"}:
        try:
            return _detect_and_match_loftr(left, right, config)
        except StitchingFailure as exc:
            failures.append(f"loftr:{exc.detail}")
            if requested == "loftr":
                raise

    detail = " | ".join(failures) if failures else "no deep matcher backend is available"
    raise StitchingFailure(
        ErrorCode.INTERNAL_ERROR,
        f"deep matcher backend is not installed or could not run ({detail})",
    )


def _import_torch() -> Any:
    try:
        import torch
    except ModuleNotFoundError as exc:
        raise StitchingFailure(ErrorCode.INTERNAL_ERROR, f"torch not installed: {exc}") from exc
    return torch


def _select_device(torch: Any) -> str:
    return "cuda" if bool(torch.cuda.is_available()) else "cpu"


def _to_rgb_tensor(image: np.ndarray, torch: Any, device: str) -> Any:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    tensor = torch.from_numpy(rgb).float().permute(2, 0, 1) / 255.0
    return tensor.to(device)


def _to_gray_tensor(image: np.ndarray, torch: Any, device: str) -> Any:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    tensor = torch.from_numpy(gray).float()[None, None, ...] / 255.0
    return tensor.to(device)


def _to_keypoints(points: np.ndarray) -> list[cv2.KeyPoint]:
    return [cv2.KeyPoint(float(x), float(y), 1.0) for x, y in points]


def _to_index_matches(count: int, confidences: np.ndarray | None = None) -> list[cv2.DMatch]:
    output: list[cv2.DMatch] = []
    for idx in range(count):
        confidence = float(confidences[idx]) if confidences is not None and idx < len(confidences) else 1.0
        distance = max(0.0, 1.0 - confidence)
        output.append(cv2.DMatch(_queryIdx=idx, _trainIdx=idx, _distance=distance))
    return output


@lru_cache(maxsize=4)
def _load_lightglue_bundle(max_features: int, device: str) -> tuple[Any, Any, Any]:
    torch = _import_torch()
    try:
        from lightglue import LightGlue, SuperPoint
        from lightglue.utils import rbd
    except ModuleNotFoundError as exc:
        raise StitchingFailure(ErrorCode.INTERNAL_ERROR, f"LightGlue not installed: {exc}") from exc

    extractor = SuperPoint(max_num_keypoints=max(256, min(int(max_features), 4096))).eval().to(device)
    matcher = LightGlue(features="superpoint").eval().to(device)
    return extractor, matcher, rbd


def _detect_and_match_lightglue(left: np.ndarray, right: np.ndarray, config: StitchConfig) -> DeepMatchResult:
    torch = _import_torch()
    device = _select_device(torch)
    extractor, matcher, rbd = _load_lightglue_bundle(int(getattr(config, "max_features", 2048)), device)

    with torch.inference_mode():
        left_tensor = _to_rgb_tensor(left, torch, device)
        right_tensor = _to_rgb_tensor(right, torch, device)
        feats_left = extractor.extract(left_tensor)
        feats_right = extractor.extract(right_tensor)
        matches_pack = matcher({"image0": feats_left, "image1": feats_right})
        feats_left, feats_right, matches_pack = [rbd(x) for x in (feats_left, feats_right, matches_pack)]

        match_indices = matches_pack["matches"]
        if int(match_indices.shape[0]) < max(8, int(getattr(config, "min_matches", 20))):
            raise StitchingFailure(
                ErrorCode.OVERLAP_LOW,
                f"LightGlue matches below threshold: {int(match_indices.shape[0])} < {max(8, int(getattr(config, 'min_matches', 20)))}",
            )

        keypoints_left = feats_left["keypoints"][match_indices[:, 0]].detach().cpu().numpy().astype(np.float32)
        keypoints_right = feats_right["keypoints"][match_indices[:, 1]].detach().cpu().numpy().astype(np.float32)
        scores = matches_pack.get("scores")
        confidences = None if scores is None else scores.detach().cpu().numpy().astype(np.float32)

    return DeepMatchResult(
        keypoints_left=_to_keypoints(keypoints_left),
        keypoints_right=_to_keypoints(keypoints_right),
        matches=_to_index_matches(len(keypoints_left), confidences),
        backend_name=f"lightglue_superpoint_{device}",
    )


@lru_cache(maxsize=2)
def _load_loftr_bundle(device: str) -> Any:
    torch = _import_torch()
    try:
        from kornia.feature import LoFTR
    except ModuleNotFoundError as exc:
        raise StitchingFailure(ErrorCode.INTERNAL_ERROR, f"LoFTR/Kornia not installed: {exc}") from exc
    matcher = LoFTR(pretrained="outdoor").eval().to(device)
    return matcher


def _detect_and_match_loftr(left: np.ndarray, right: np.ndarray, config: StitchConfig) -> DeepMatchResult:
    torch = _import_torch()
    device = _select_device(torch)
    matcher = _load_loftr_bundle(device)

    with torch.inference_mode():
        left_tensor = _to_gray_tensor(left, torch, device)
        right_tensor = _to_gray_tensor(right, torch, device)
        correspondences = matcher({"image0": left_tensor, "image1": right_tensor})
        keypoints_left = correspondences["keypoints0"].detach().cpu().numpy().astype(np.float32)
        keypoints_right = correspondences["keypoints1"].detach().cpu().numpy().astype(np.float32)
        confidences = correspondences.get("confidence")
        confidence_values = None if confidences is None else confidences.detach().cpu().numpy().astype(np.float32)

    match_count = int(len(keypoints_left))
    if match_count < max(8, int(getattr(config, "min_matches", 20))):
        raise StitchingFailure(
            ErrorCode.OVERLAP_LOW,
            f"LoFTR matches below threshold: {match_count} < {max(8, int(getattr(config, 'min_matches', 20)))}",
        )

    return DeepMatchResult(
        keypoints_left=_to_keypoints(keypoints_left),
        keypoints_right=_to_keypoints(keypoints_right),
        matches=_to_index_matches(match_count, confidence_values),
        backend_name=f"loftr_{device}",
    )
