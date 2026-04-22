from __future__ import annotations

from typing import Any, Callable

import numpy as np

from stitching.core.config import StitchingFailure
from stitching.errors import ErrorCode


def calibrate_native_homography_from_frames(
    config: Any,
    left_raw: np.ndarray,
    right_raw: np.ndarray,
    *,
    left_points: list[tuple[float, float]] | None = None,
    right_points: list[tuple[float, float]] | None = None,
    prompt_for_points: bool = False,
    review_required: bool | None = None,
    save_outputs: bool = False,
    create_feature_match_session_func: Callable[..., Any],
    should_collect_overlap_hints_func: Callable[..., bool],
    estimate_overlap_hints_func: Callable[..., tuple[Any, Any]],
    assisted_ui_cls: type,
    auto_match_variant_configs_func: Callable[..., list[tuple[str, Any]]],
    detect_auto_matches_func: Callable[..., tuple[Any, Any, Any, str]],
    build_candidate_func: Callable[..., Any],
    is_high_confidence_auto_candidate_func: Callable[..., bool],
    build_manual_matches_func: Callable[..., tuple[Any, Any, Any, str, str, str]],
    manual_candidate_config_func: Callable[..., Any],
    build_assisted_matches_func: Callable[..., tuple[Any, Any, Any, str, str, str]],
    choose_preferred_calibration_candidate_func: Callable[..., Any],
    prepare_warp_plan_func: Callable[..., Any],
    blend_feather_func: Callable[..., np.ndarray],
    draw_inlier_preview_func: Callable[..., np.ndarray],
    review_ui_cls: type,
    extract_inlier_points_func: Callable[..., tuple[Any, Any]],
    build_native_calibration_metadata_func: Callable[..., dict[str, Any]],
    build_native_calibration_result_func: Callable[..., dict[str, Any]],
    save_native_calibration_artifacts_func: Callable[..., dict[str, Any]],
    cv2_module: Any,
) -> dict[str, Any]:
    left = left_raw
    right = right_raw
    requested_mode = str(config.calibration_mode).lower().strip()
    left_points_local = list(left_points or [])
    right_points_local = list(right_points or [])
    feature_session = create_feature_match_session_func(left, right)
    collect_overlap_hints = should_collect_overlap_hints_func(
        requested_mode=requested_mode,
        prompt_for_points=prompt_for_points,
        left_points=left_points_local,
    )
    if collect_overlap_hints:
        left_overlap_hint, right_overlap_hint = estimate_overlap_hints_func(
            left,
            right,
            config,
            feature_session=feature_session,
        )
    else:
        left_overlap_hint, right_overlap_hint = None, None
    if prompt_for_points and requested_mode in {"assisted", "manual"} and not left_points_local:
        left_points_local, right_points_local = assisted_ui_cls(
            left,
            right,
            left_overlap_hint=left_overlap_hint,
            right_overlap_hint=right_overlap_hint,
        ).run()
    if len(left_points_local) != len(right_points_local):
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "manual point counts must match")

    candidates: list[Any] = []
    failures: list[str] = []

    auto_variant_succeeded = False
    for variant_name, variant_config in auto_match_variant_configs_func(config):
        try:
            auto_kp_left, auto_kp_right, auto_matches, auto_backend_name = detect_auto_matches_func(
                left,
                right,
                variant_config,
                feature_session=feature_session,
            )
            auto_candidate = build_candidate_func(
                left=left,
                right=right,
                keypoints_left=auto_kp_left,
                keypoints_right=auto_kp_right,
                matches=auto_matches,
                calibration_mode="auto",
                seed_guidance_model="none",
                backend_name=f"{auto_backend_name}:{variant_name}",
                config=variant_config,
                enforce_quality_gate=False,
            )
            candidates.append(auto_candidate)
            auto_variant_succeeded = True
            if is_high_confidence_auto_candidate_func(
                auto_candidate,
                left=left,
                right=right,
                config=variant_config,
            ):
                break
        except StitchingFailure as exc:
            failures.append(f"auto[{variant_name}]:{exc.code.value}:{exc.detail}")
    if not auto_variant_succeeded:
        failures.append("auto:no_auto_variant_succeeded")

    if left_points_local:
        try:
            manual_kp_left, manual_kp_right, manual_matches, manual_mode, manual_seed_model, manual_backend_name = (
                build_manual_matches_func(
                    left_points_local,
                    right_points_local,
                )
            )
            candidates.append(
                build_candidate_func(
                    left=left,
                    right=right,
                    keypoints_left=manual_kp_left,
                    keypoints_right=manual_kp_right,
                    matches=manual_matches,
                    calibration_mode=manual_mode,
                    seed_guidance_model=manual_seed_model,
                    backend_name=manual_backend_name,
                    config=manual_candidate_config_func(config, pair_count=len(manual_matches)),
                    enforce_quality_gate=False,
                )
            )
        except StitchingFailure as exc:
            failures.append(f"manual:{exc.code.value}:{exc.detail}")

    if left_points_local:
        try:
            assisted_kp_left, assisted_kp_right, assisted_matches, assisted_mode, seed_guidance_model, assisted_backend_name = (
                build_assisted_matches_func(
                    left,
                    right,
                    config,
                    left_points_local,
                    right_points_local,
                    feature_session=feature_session,
                )
            )
            candidates.append(
                build_candidate_func(
                    left=left,
                    right=right,
                    keypoints_left=assisted_kp_left,
                    keypoints_right=assisted_kp_right,
                    matches=assisted_matches,
                    calibration_mode=assisted_mode,
                    seed_guidance_model=seed_guidance_model,
                    backend_name=assisted_backend_name,
                    config=config,
                    enforce_quality_gate=True,
                )
            )
        except StitchingFailure as exc:
            failures.append(f"assisted:{exc.code.value}:{exc.detail}")

    if not candidates:
        if failures:
            detail = " | ".join(failures)
            raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, f"no valid calibration candidate ({detail})")
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "no valid calibration candidate")

    best_candidate = choose_preferred_calibration_candidate_func(
        candidates,
        requested_mode=requested_mode,
        manual_points_present=bool(left_points_local),
    )
    homography = best_candidate.homography
    inlier_mask = best_candidate.inlier_mask
    keypoints_left = best_candidate.keypoints_left
    keypoints_right = best_candidate.keypoints_right
    matches = best_candidate.matches
    calibration_mode_effective = best_candidate.calibration_mode
    transform_model = best_candidate.transform_model
    seed_guidance_model = best_candidate.seed_guidance_model

    plan = prepare_warp_plan_func(left.shape[:2], right.shape[:2], homography, config)
    warped_right = cv2_module.warpPerspective(
        right,
        plan.homography_adjusted,
        (plan.width, plan.height),
    )
    right_mask = cv2_module.warpPerspective(
        np.ones(right.shape[:2], dtype=np.uint8) * 255,
        plan.homography_adjusted,
        (plan.width, plan.height),
    )
    canvas_left = np.zeros((plan.height, plan.width, 3), dtype=np.uint8)
    left_mask = np.zeros((plan.height, plan.width), dtype=np.uint8)
    left_h, left_w = left.shape[:2]
    canvas_left[plan.ty : plan.ty + left_h, plan.tx : plan.tx + left_w] = left
    left_mask[plan.ty : plan.ty + left_h, plan.tx : plan.tx + left_w] = 255
    stitched = blend_feather_func(canvas_left, warped_right, left_mask, right_mask)
    inlier_preview = draw_inlier_preview_func(left, right, keypoints_left, keypoints_right, matches, inlier_mask)
    inliers_count = int(inlier_mask.ravel().sum())
    review_lines = [
        f"mode={calibration_mode_effective}  seed={seed_guidance_model}  model={transform_model}",
        f"score={best_candidate.score:.3f}  match={best_candidate.match_score:.3f}  geom={best_candidate.geometry_score:.3f}  visual={best_candidate.visual_score:.3f}",
        f"matches={len(matches)}  inliers={inliers_count}  inlier_ratio={best_candidate.inlier_ratio:.3f}  repr_err={best_candidate.mean_reprojection_error:.2f}px",
        f"output={plan.width}x{plan.height}  luma_diff={best_candidate.overlap_luma_diff:.3f}  edge_diff={best_candidate.overlap_edge_diff:.3f}  ghost={best_candidate.ghosting_score:.3f}",
        f"manual_points={min(len(left_points_local), len(right_points_local))}  backend={best_candidate.backend_name}",
        "CONFIRM saves this homography and launches runtime. CANCEL stops here.",
    ]
    use_review = bool(config.review_required) if review_required is None else bool(review_required)
    if use_review:
        if not review_ui_cls(
            inlier_preview=inlier_preview,
            stitched_preview=stitched,
            summary_lines=review_lines,
        ).run():
            raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "calibration review cancelled by user")

    left_inlier_points, right_inlier_points = extract_inlier_points_func(
        keypoints_left,
        keypoints_right,
        matches,
        inlier_mask,
    )
    metadata = build_native_calibration_metadata_func(
        config=config,
        left=left,
        right=right,
        left_points_local=left_points_local,
        right_points_local=right_points_local,
        failures=failures,
        candidates=candidates,
        best_candidate=best_candidate,
        matches_count=int(len(matches)),
        inliers_count=inliers_count,
        transform_model=transform_model,
        output_resolution=(int(plan.width), int(plan.height)),
    )
    result = build_native_calibration_result_func(
        config=config,
        failures=failures,
        best_candidate=best_candidate,
        transform_model=transform_model,
        matches_count=int(len(matches)),
        inliers_count=inliers_count,
        homography=homography,
        metadata=metadata,
        left=left,
        right=right,
        stitched=stitched,
        inlier_preview=inlier_preview,
        left_inlier_points=left_inlier_points,
        right_inlier_points=right_inlier_points,
        review_lines=review_lines,
        output_resolution=(int(plan.width), int(plan.height)),
    )
    if save_outputs:
        save_native_calibration_artifacts_func(config, result)
    return result
