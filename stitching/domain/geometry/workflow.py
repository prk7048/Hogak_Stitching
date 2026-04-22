from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np


def apply_capture_summary_metadata(
    metadata: dict[str, Any],
    capture_summary: dict[str, Any],
) -> dict[str, Any]:
    metadata["capture_source"] = str(capture_summary.get("capture_source") or "")
    metadata["capture_manifest_path"] = str(capture_summary.get("capture_manifest_path") or "")
    metadata["capture_pairing_mode"] = str(capture_summary.get("capture_pairing_mode") or "")
    metadata["capture_pairing_time_domain"] = str(capture_summary.get("capture_pairing_time_domain") or "")
    metadata["capture_pairing_requested_time_source"] = str(
        capture_summary.get("capture_pairing_requested_time_source") or ""
    )
    metadata["capture_pairing_mean_delta_ms"] = float(capture_summary.get("capture_pairing_mean_delta_ms") or 0.0)
    metadata["capture_pairing_worst_delta_ms"] = float(capture_summary.get("capture_pairing_worst_delta_ms") or 0.0)
    metadata["capture_fallback_reason"] = str(capture_summary.get("capture_fallback_reason") or "")
    return metadata


def build_native_calibration_metadata(
    *,
    config: Any,
    left: np.ndarray,
    right: np.ndarray,
    left_points_local: list[tuple[float, float]],
    right_points_local: list[tuple[float, float]],
    failures: list[str],
    candidates: list[Any],
    best_candidate: Any,
    matches_count: int,
    inliers_count: int,
    transform_model: str,
    output_resolution: tuple[int, int],
) -> dict[str, Any]:
    return {
        "source": "native_runtime_calibration",
        "calibration_mode_requested": str(config.calibration_mode),
        "calibration_mode_effective": str(best_candidate.calibration_mode),
        "left_rtsp": str(config.left_rtsp),
        "right_rtsp": str(config.right_rtsp),
        "rtsp_transport": str(config.rtsp_transport),
        "process_scale": float(config.process_scale),
        "manual_points_count": int(min(len(left_points_local), len(right_points_local))),
        "match_backend_requested": str(config.match_backend),
        "match_backend_effective": str(best_candidate.backend_name),
        "selected_candidate": str(best_candidate.calibration_mode),
        "seed_guidance_model": str(best_candidate.seed_guidance_model),
        "candidate_failures": list(failures),
        "candidate_count": int(len(candidates)),
        "candidate_score": float(best_candidate.score),
        "match_score": float(best_candidate.match_score),
        "geometry_score": float(best_candidate.geometry_score),
        "visual_score": float(best_candidate.visual_score),
        "matches_count": int(matches_count),
        "inliers_count": int(inliers_count),
        "inlier_ratio": float(best_candidate.inlier_ratio),
        "mean_reprojection_error": float(best_candidate.mean_reprojection_error),
        "overlap_luma_diff": float(best_candidate.overlap_luma_diff),
        "overlap_edge_diff": float(best_candidate.overlap_edge_diff),
        "ghosting_score": float(best_candidate.ghosting_score),
        "transform_model": str(transform_model),
        "left_resolution": [int(left.shape[1]), int(left.shape[0])],
        "right_resolution": [int(right.shape[1]), int(right.shape[0])],
        "output_resolution": [int(output_resolution[0]), int(output_resolution[1])],
        "debug_dir": str(config.debug_dir),
        "candidates": [
            {
                "name": str(item.calibration_mode),
                "seed_guidance_model": str(item.seed_guidance_model),
                "backend_name": str(item.backend_name),
                "transform_model": str(item.transform_model),
                "score": float(item.score),
                "match_score": float(item.match_score),
                "geometry_score": float(item.geometry_score),
                "visual_score": float(item.visual_score),
                "matches_count": int(item.match_count),
                "inliers_count": int(item.inliers_count),
                "inlier_ratio": float(item.inlier_ratio),
                "mean_reprojection_error": float(item.mean_reprojection_error),
                "output_resolution": [int(item.output_width), int(item.output_height)],
                "overlap_luma_diff": float(item.overlap_luma_diff),
                "overlap_edge_diff": float(item.overlap_edge_diff),
                "ghosting_score": float(item.ghosting_score),
                "accepted": bool(item is best_candidate),
            }
            for item in list(candidates)
        ],
    }


def build_native_calibration_result(
    *,
    config: Any,
    failures: list[str],
    best_candidate: Any,
    transform_model: str,
    matches_count: int,
    inliers_count: int,
    homography: np.ndarray,
    metadata: dict[str, Any],
    left: np.ndarray,
    right: np.ndarray,
    stitched: np.ndarray,
    inlier_preview: np.ndarray,
    left_inlier_points: list[list[float]],
    right_inlier_points: list[list[float]],
    review_lines: list[str],
    output_resolution: tuple[int, int],
) -> dict[str, Any]:
    return {
        "homography_file": str(config.output_path),
        "inliers_file": str(config.inliers_output_path),
        "debug_dir": str(config.debug_dir),
        "matches_count": int(matches_count),
        "inliers_count": int(inliers_count),
        "manual_points_count": int(metadata.get("manual_points_count") or 0),
        "calibration_mode": str(best_candidate.calibration_mode),
        "seed_guidance_model": str(best_candidate.seed_guidance_model),
        "candidate_failures": list(failures),
        "transform_model": str(transform_model),
        "candidate_score": float(best_candidate.score),
        "match_score": float(best_candidate.match_score),
        "geometry_score": float(best_candidate.geometry_score),
        "visual_score": float(best_candidate.visual_score),
        "inlier_ratio": float(best_candidate.inlier_ratio),
        "mean_reprojection_error": float(best_candidate.mean_reprojection_error),
        "output_resolution": [int(output_resolution[0]), int(output_resolution[1])],
        "match_backend": str(best_candidate.backend_name),
        "homography_matrix": homography,
        "metadata": metadata,
        "left_frame": left,
        "right_frame": right,
        "stitched_preview_frame": stitched,
        "inlier_preview_frame": inlier_preview,
        "left_inlier_points": left_inlier_points,
        "right_inlier_points": right_inlier_points,
        "review_lines": list(review_lines),
    }


def build_mesh_refresh_manifest(
    *,
    refresh_dir: Path,
    artifact_info: dict[str, Any],
    rollout_truth: dict[str, Any],
    rollout: dict[str, Any],
    capture_summary: dict[str, Any],
    representative_index: int,
    clip_frame_count: int,
    calibration_result: dict[str, Any],
    spec: dict[str, Any],
    outputs: dict[str, Any],
    mesh_refresh_model: str,
    created_at_epoch_sec: int | None = None,
) -> dict[str, Any]:
    return {
        "status": "ready",
        "session_id": refresh_dir.name,
        "refresh_dir": str(refresh_dir),
        "runtime_active_artifact_path": str(artifact_info["active_geometry_path"]),
        "mesh_refresh_model": str(mesh_refresh_model),
        "geometry_artifact_model": str(rollout.get("geometry_model") or ""),
        "geometry_residual_model": str(rollout.get("geometry_residual_model") or ""),
        **dict(rollout_truth),
        "capture_source": str(capture_summary.get("capture_source") or ""),
        "capture_manifest_path": str(capture_summary.get("capture_manifest_path") or ""),
        "capture_pairing_mode": str(capture_summary.get("capture_pairing_mode") or ""),
        "capture_pairing_time_domain": str(capture_summary.get("capture_pairing_time_domain") or ""),
        "capture_pairing_requested_time_source": str(
            capture_summary.get("capture_pairing_requested_time_source") or ""
        ),
        "capture_pairing_max_delta_ms": float(capture_summary.get("capture_pairing_max_delta_ms") or 0.0),
        "capture_pairing_mean_delta_ms": float(capture_summary.get("capture_pairing_mean_delta_ms") or 0.0),
        "capture_pairing_worst_delta_ms": float(capture_summary.get("capture_pairing_worst_delta_ms") or 0.0),
        "capture_fallback_reason": str(capture_summary.get("capture_fallback_reason") or ""),
        "representative_frame_index": int(representative_index),
        "clip_frame_count": int(clip_frame_count),
        "mesh_refresh_calibration_mode": str(
            calibration_result.get("mesh_refresh_calibration_mode") or "single-path-rigid"
        ),
        "calibration_total_ms": float(calibration_result.get("mesh_refresh_calibration_total_ms") or 0.0),
        "preview_rollout_total_ms": float(calibration_result.get("mesh_refresh_preview_total_ms") or 0.0),
        "calibration_attempt_count": int(calibration_result.get("mesh_refresh_attempt_count") or 0),
        "calibration_attempted_frame_indices": [
            int(value) for value in list(calibration_result.get("mesh_refresh_attempted_frame_indices") or [])
        ],
        "selected_attempt_ordinal": int(calibration_result.get("mesh_refresh_selected_attempt_ordinal") or 0),
        "good_match_count": int(calibration_result.get("matches_count") or 0),
        "inlier_count": int(calibration_result.get("inliers_count") or 0),
        "inlier_ratio": float(calibration_result.get("inlier_ratio") or 0.0),
        "right_edge_scale_drift": float(spec.get("right_edge_scale_drift") or 0.0),
        "fallback_used": bool(spec.get("fallback_used")),
        "status_detail": str(spec.get("status") or "ready"),
        "crop_rect": [
            int(value) for value in outputs.get("crop_rect") or [0, 0, calibration_result["output_resolution"][0], calibration_result["output_resolution"][1]]
        ],
        "active_homography_path": str(artifact_info["active_homography_path"]),
        "snapshot_homography_path": str(artifact_info["snapshot_homography_path"]),
        "snapshot_geometry_path": str(artifact_info["snapshot_geometry_path"]),
        "created_at_epoch_sec": int(created_at_epoch_sec if created_at_epoch_sec is not None else time.time()),
    }
