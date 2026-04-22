from __future__ import annotations

from typing import Any, Sequence


def calibration_transform_rank(transform_model: Any) -> int:
    normalized = str(transform_model or "").strip().lower().replace("_", "-")
    if normalized == "homography":
        return 2
    if normalized == "affine-fallback":
        return 1
    if normalized == "affine-geometry-fallback":
        return 0
    return -1


def calibration_result_sort_key(result: dict[str, Any]) -> tuple[int, float, int, int]:
    return (
        calibration_transform_rank(result.get("transform_model")),
        float(result.get("candidate_score") or 0.0),
        int(result.get("inliers_count") or 0),
        int(result.get("matches_count") or 0),
    )


def choose_preferred_calibration_candidate(
    candidates: Sequence[Any],
    *,
    requested_mode: str,
    manual_points_present: bool,
    score_margin: float = 0.03,
) -> Any:
    if not candidates:
        raise ValueError("candidates must not be empty")
    auto_candidates = [item for item in candidates if str(getattr(item, "calibration_mode", "")) == "auto"]
    auto_candidate = max(auto_candidates, key=lambda item: float(getattr(item, "score", 0.0))) if auto_candidates else None
    manual_candidate = next((item for item in candidates if str(getattr(item, "calibration_mode", "")) == "manual"), None)
    assisted_candidate = next((item for item in candidates if str(getattr(item, "calibration_mode", "")) == "assisted"), None)
    best_candidate = max(candidates, key=lambda item: float(getattr(item, "score", 0.0)))
    normalized_mode = str(requested_mode or "").strip().lower()
    if normalized_mode in {"assisted", "manual"} and manual_points_present:
        if manual_candidate is not None:
            return manual_candidate
        if assisted_candidate is not None:
            return assisted_candidate
        return best_candidate
    if auto_candidate is not None and str(getattr(best_candidate, "calibration_mode", "")) != "auto":
        if float(getattr(best_candidate, "score", 0.0)) < float(getattr(auto_candidate, "score", 0.0)) + float(score_margin):
            return auto_candidate
    return best_candidate
