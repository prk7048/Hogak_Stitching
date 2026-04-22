from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from stitching.core.config import StitchingFailure
from stitching.core.exposure import _compute_overlap_diff_mean
from stitching.domain.geometry.virtual_center import (
    point_to_rectilinear_ray as _point_to_rectilinear_ray,
    project_ray_to_virtual_rectilinear as _project_ray_to_virtual_rectilinear,
    should_use_virtual_center_runtime_geometry as _should_use_virtual_center_runtime_geometry,
    solve_virtual_center_rectilinear as _solve_virtual_center_rectilinear,
)
from stitching.domain.calibration.native.calibration import (
    NativeCalibrationConfig,
    _save_homography_file,
    calibrate_native_homography_from_frames,
)
from stitching.domain.geometry.capture import capture_clip as _capture_clip_impl
from stitching.domain.geometry.artifact import save_runtime_geometry_artifact
from stitching.domain.geometry.common import (
    VirtualCenterArtifactBuildSpec,
    apply_virtual_center_metrics as _apply_virtual_center_metrics,
    build_rectilinear_remap as _build_rectilinear_remap,
    build_virtual_center_runtime_artifact,
    compose_affine_inverse_map as _compose_affine_inverse_map,
    requested_residual_model as _requested_residual_model,
    right_edge_scale_drift as _right_edge_scale_drift,
    rollout_truth_fields as _rollout_truth_fields,
    stamp_runtime_artifact_metadata as _stamp_runtime_artifact_metadata,
)
from stitching.domain.geometry.policy import geometry_rollout_metadata
from stitching.domain.geometry.workflow import (
    apply_capture_summary_metadata,
    build_mesh_refresh_manifest,
)
from stitching.domain.runtime.service.launcher import run_native_capture
from stitching.domain.geometry.refresh_service import (
    DEFAULT_CLIP_FRAMES,
    INTERNAL_MESH_REFRESH_MODEL,
    ProgressCallback,
    _mesh_refresh_session_dir,
    _resolve_active_runtime_paths,
)
from stitching.domain.geometry.render import (
    prepare_virtual_center_spec as _prepare_virtual_center_spec_impl,
    render_virtual_center_from_spec as _render_virtual_center_from_spec_impl,
)


@dataclass(slots=True)
class _SelectedRigidCalibration:
    frame_index: int
    left_frame: np.ndarray
    right_frame: np.ndarray
    calibration_result: dict[str, Any]
    virtual_solution: Any
    spec: dict[str, Any]
    outputs: dict[str, Any]
    rollout: dict[str, Any]


def _capture_clip(
    config: NativeCalibrationConfig,
    *,
    clip_frames: int,
    session_dir: Path,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], dict[str, Any]]:
    return _capture_clip_impl(
        config,
        clip_frames=clip_frames,
        session_dir=session_dir,
        run_native_capture_func=run_native_capture,
    )


def _rectilinear_points_for_solution(
    points: list[list[float]],
    *,
    focal_px: float,
    center: tuple[float, float],
    rotation: np.ndarray,
    virtual_focal_px: float,
    virtual_center: tuple[float, float],
) -> np.ndarray:
    out: list[list[float]] = []
    for point in points:
        ray = _point_to_rectilinear_ray(
            point,
            focal_px=float(focal_px),
            center_x=float(center[0]),
            center_y=float(center[1]),
        )
        projected = _project_ray_to_virtual_rectilinear(
            np.asarray(rotation, dtype=np.float64).reshape(3, 3) @ ray,
            focal_px=float(virtual_focal_px),
            center_x=float(virtual_center[0]),
            center_y=float(virtual_center[1]),
        )
        if projected is None:
            continue
        out.append([float(projected[0]), float(projected[1])])
    return np.asarray(out, dtype=np.float64).reshape(-1, 2)


def _unused_mesh_field(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("mesh helpers must not be used on the rigid-only mesh-refresh path")


def _unused_mesh_apply(*args: Any, **kwargs: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    raise RuntimeError("mesh helpers must not be used on the rigid-only mesh-refresh path")


def _unused_mesh_quality_reason(*args: Any, **kwargs: Any) -> str:
    raise RuntimeError("mesh helpers must not be used on the rigid-only mesh-refresh path")


def _largest_valid_rect(mask: np.ndarray) -> tuple[int, int, int, int]:
    binary = (mask > 0).astype(np.uint8)
    height, width = binary.shape[:2]
    heights = np.zeros(width, dtype=np.int32)
    best_area = 0
    best_rect = (0, 0, width, height)
    for y in range(height):
        heights = np.where(binary[y] > 0, heights + 1, 0)
        stack: list[int] = []
        for x in range(width + 1):
            current = heights[x] if x < width else 0
            while stack and current < heights[stack[-1]]:
                h = heights[stack.pop()]
                x0 = stack[-1] + 1 if stack else 0
                rect_width = x - x0
                area = int(h * rect_width)
                if area > best_area and h > 0 and rect_width > 0:
                    best_area = area
                    best_rect = (x0, y - h + 1, rect_width, h)
            stack.append(x)
    return best_rect


def _extract_overlap_crop(frame: np.ndarray, overlap_mask: np.ndarray) -> np.ndarray:
    if not np.any(overlap_mask):
        return frame
    ys, xs = np.where(overlap_mask)
    x1 = max(0, int(xs.min()) - 24)
    x2 = min(frame.shape[1], int(xs.max()) + 25)
    y1 = max(0, int(ys.min()) - 24)
    y2 = min(frame.shape[0], int(ys.max()) + 25)
    return frame[y1:y2, x1:x2].copy()


def _draw_seam_debug(frame: np.ndarray, seam_path: np.ndarray, overlap_mask: np.ndarray) -> np.ndarray:
    debug = frame.copy()
    valid_rows = np.where(np.any(overlap_mask, axis=1))[0]
    for y in valid_rows.tolist():
        x = int(seam_path[y])
        if 0 <= x < debug.shape[1]:
            cv2.circle(debug, (x, int(y)), 1, (255, 80, 220), -1, cv2.LINE_AA)
    return debug


def _compose_rigid_outputs(
    left_canvas: np.ndarray,
    right_canvas: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
    *,
    instability: np.ndarray | None = None,
    transition_px: int = 36,
) -> dict[str, Any]:
    del instability, transition_px
    overlap = (left_mask > 0) & (right_mask > 0)
    stitched_uncropped = np.zeros_like(left_canvas)
    stitched_uncropped[left_mask > 0] = left_canvas[left_mask > 0]
    stitched_uncropped[(right_mask > 0) & ~overlap] = right_canvas[(right_mask > 0) & ~overlap]

    seam_path = np.full(left_canvas.shape[0], left_canvas.shape[1] // 2, dtype=np.int32)
    seam_visibility = 0.0
    if np.any(overlap):
        overlap_u8 = overlap.astype(np.uint8)
        x, y, width, height = cv2.boundingRect(overlap_u8)
        seam_center_x = int(x + (width // 2))
        seam_path[:] = seam_center_x
        band_width = int(np.clip(round(float(width) * 0.14), 48, max(48, min(192, width))))
        half_band = max(1, band_width // 2)
        transition_start_x = max(x, seam_center_x - half_band)
        transition_end_x = min(x + width - 1, seam_center_x + half_band)
        denom = max(1.0, float(transition_end_x - transition_start_x))

        roi = np.s_[y : y + height, x : x + width]
        overlap_roi = overlap[roi]
        left_roi = left_canvas[roi].astype(np.float32)
        right_roi = right_canvas[roi].astype(np.float32)
        roi_width = left_roi.shape[1]
        weights = np.zeros((left_roi.shape[0], roi_width), dtype=np.float32)
        for offset_x in range(roi_width):
            absolute_x = x + offset_x
            if absolute_x <= transition_start_x:
                alpha = 0.0
            elif absolute_x >= transition_end_x:
                alpha = 1.0
            else:
                alpha = float(absolute_x - transition_start_x) / denom
            weights[:, offset_x] = alpha
        weights = np.where(overlap_roi, weights, 0.0)
        left_weights = (1.0 - weights)[..., None]
        right_weights = weights[..., None]
        blended_roi = np.clip(left_roi * left_weights + right_roi * right_weights, 0.0, 255.0).astype(np.uint8)
        stitched_roi = stitched_uncropped[roi]
        stitched_roi[overlap_roi] = blended_roi[overlap_roi]
        stitched_uncropped[roi] = stitched_roi

        left_gray = cv2.cvtColor(left_canvas, cv2.COLOR_BGR2GRAY)
        right_gray = cv2.cvtColor(right_canvas, cv2.COLOR_BGR2GRAY)
        abs_diff = cv2.absdiff(left_gray, right_gray)
        seam_band = np.zeros_like(overlap, dtype=bool)
        seam_band[:, max(0, transition_start_x) : min(stitched_uncropped.shape[1], transition_end_x + 1)] = True
        seam_values = abs_diff[overlap & seam_band]
        seam_visibility = float(np.mean(seam_values)) if seam_values.size > 0 else 0.0

    valid_mask = ((left_mask > 0) | (right_mask > 0)).astype(np.uint8)
    crop_x, crop_y, crop_w, crop_h = _largest_valid_rect(valid_mask)
    stitched_cropped = stitched_uncropped[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w].copy()
    overlap_crop = _extract_overlap_crop(stitched_uncropped, overlap)
    seam_debug = _draw_seam_debug(stitched_uncropped, seam_path, overlap)
    crop_ratio = float((crop_w * crop_h) / max(1, stitched_uncropped.shape[0] * stitched_uncropped.shape[1]))
    return {
        "stitched_uncropped": stitched_uncropped,
        "stitched_cropped": stitched_cropped,
        "overlap_crop": overlap_crop,
        "seam_debug": seam_debug,
        "seam_visibility_score": seam_visibility,
        "overlap_luma_diff": _compute_overlap_diff_mean(left_canvas, right_canvas, overlap),
        "crop_ratio": crop_ratio,
        "crop_rect": (int(crop_x), int(crop_y), int(crop_w), int(crop_h)),
        "gain": 1.0,
        "bias": 0.0,
        "overlap_mask": overlap,
    }


def _prepare_virtual_center_spec(
    *,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    left_inlier_points: np.ndarray,
    right_inlier_points: np.ndarray,
    output_resolution: tuple[int, int],
    virtual_solution: Any,
) -> dict[str, Any]:
    return _prepare_virtual_center_spec_impl(
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        left_frame=left_frame,
        right_frame=right_frame,
        left_inlier_points=left_inlier_points,
        right_inlier_points=right_inlier_points,
        output_resolution=output_resolution,
        virtual_solution=virtual_solution,
        build_rectilinear_remap_func=_build_rectilinear_remap,
        rectilinear_points_for_solution_func=_rectilinear_points_for_solution,
        compose_affine_inverse_map_func=_compose_affine_inverse_map,
        fit_mesh_field_func=_unused_mesh_field,
        apply_mesh_to_canvas_func=_unused_mesh_apply,
        right_edge_scale_drift_func=_right_edge_scale_drift,
        virtual_center_mesh_quality_reason_func=_unused_mesh_quality_reason,
    )


def _render_virtual_center_from_spec(
    spec: dict[str, Any],
    left_frame: np.ndarray,
    right_frame: np.ndarray,
) -> dict[str, Any]:
    return _render_virtual_center_from_spec_impl(
        spec,
        left_frame,
        right_frame,
        compose_candidate_outputs_func=_compose_rigid_outputs,
    )


def _ordered_representative_indices(clip_size: int) -> list[int]:
    if clip_size <= 0:
        return []
    center = (float(clip_size) - 1.0) * 0.5
    return sorted(range(clip_size), key=lambda index: (abs(float(index) - center), index))


def _record_single_path_metadata(
    calibration_result: dict[str, Any],
    *,
    attempted_indices: list[int],
    calibration_total_ms: float,
    preview_total_ms: float,
    selected_attempt_ordinal: int,
) -> dict[str, Any]:
    result = dict(calibration_result)
    metadata = dict(result.get("metadata") or {})
    payload = {
        "mesh_refresh_calibration_mode": "single-path-rigid",
        "mesh_refresh_attempt_count": int(len(attempted_indices)),
        "mesh_refresh_attempted_frame_indices": [int(value) for value in attempted_indices],
        "mesh_refresh_calibration_total_ms": float(calibration_total_ms),
        "mesh_refresh_preview_total_ms": float(preview_total_ms),
        "mesh_refresh_selected_attempt_ordinal": int(selected_attempt_ordinal),
    }
    result.update(payload)
    metadata.update(payload)
    result["metadata"] = metadata
    return result


def _preview_rigid_calibration_rollout(
    calibration_result: dict[str, Any],
    *,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    output_resolution = (
        int(calibration_result["output_resolution"][0]),
        int(calibration_result["output_resolution"][1]),
    )
    virtual_solution = _solve_virtual_center_rectilinear(
        left_points=list(calibration_result.get("left_inlier_points") or []),
        right_points=list(calibration_result.get("right_inlier_points") or []),
        left_shape=left_frame.shape,
        right_shape=right_frame.shape,
        output_resolution=output_resolution,
    )
    left_inlier_points = np.asarray(calibration_result.get("left_inlier_points") or [], dtype=np.float64).reshape(-1, 2)
    right_inlier_points = np.asarray(calibration_result.get("right_inlier_points") or [], dtype=np.float64).reshape(-1, 2)
    spec = _prepare_virtual_center_spec(
        left_frame=left_frame,
        right_frame=right_frame,
        left_inlier_points=left_inlier_points,
        right_inlier_points=right_inlier_points,
        output_resolution=output_resolution,
        virtual_solution=virtual_solution,
    )
    outputs = _render_virtual_center_from_spec(spec, left_frame, right_frame)

    preview_metrics = _apply_virtual_center_metrics(
        dict(calibration_result.get("metadata") or {}),
        virtual_solution,
    )
    preview_metrics["virtual_center_right_edge_scale_drift"] = float(
        spec.get("right_edge_scale_drift") or preview_metrics.get("virtual_center_right_edge_scale_drift") or 0.0
    )
    requested_residual_model = _requested_residual_model(INTERNAL_MESH_REFRESH_MODEL)
    use_virtual_center_geometry, fallback_reason = _should_use_virtual_center_runtime_geometry(
        calibration_result,
        virtual_solution,
        requested_residual_model=requested_residual_model,
        effective_residual_model=requested_residual_model,
    )
    if not use_virtual_center_geometry:
        raise ValueError(fallback_reason or "virtual-center runtime geometry preview rejected")

    artifact_preview = build_virtual_center_runtime_artifact(
        VirtualCenterArtifactBuildSpec(
            source_homography_file="preview_homography.json",
            geometry_file="preview_geometry.json",
            homography=np.asarray(calibration_result.get("homography_matrix"), dtype=np.float64),
            metadata=preview_metrics,
            left_resolution=(int(left_frame.shape[1]), int(left_frame.shape[0])),
            right_resolution=(int(right_frame.shape[1]), int(right_frame.shape[0])),
            output_resolution=output_resolution,
            inliers_count=int(calibration_result.get("inliers_count") or 0),
            inlier_ratio=float(calibration_result.get("inlier_ratio") or 0.0),
            left_inlier_points=list(calibration_result.get("left_inlier_points") or []),
            right_inlier_points=list(calibration_result.get("right_inlier_points") or []),
            virtual_solution=virtual_solution,
            candidate_model=INTERNAL_MESH_REFRESH_MODEL,
            fallback_used=False,
            status_detail=str(spec.get("status") or "ready"),
            mesh=None,
            seam_mode="fixed-seam",
            exposure_enabled=False,
            crop_rect=tuple(
                int(value)
                for value in outputs.get("crop_rect")
                or [0, 0, output_resolution[0], output_resolution[1]]
            ),
        )
    )
    rollout = geometry_rollout_metadata(artifact_preview.artifact)
    return virtual_solution, spec, outputs, rollout


def _select_single_path_rigid_calibration(
    config: NativeCalibrationConfig,
    clip: list[tuple[np.ndarray, np.ndarray]],
    *,
    progress: ProgressCallback | None = None,
) -> _SelectedRigidCalibration:
    candidate_indices = _ordered_representative_indices(len(clip))
    attempted_indices: list[int] = []
    failures: list[str] = []
    selection_started = time.perf_counter()
    preview_total_ms = 0.0
    total_candidates = max(1, len(candidate_indices))

    for attempt_ordinal, index in enumerate(candidate_indices, start=1):
        attempted_indices.append(index)
        if progress is not None:
            progress(
                "match_features",
                (
                    "Matching features on the representative rigid frame "
                    f"({attempt_ordinal}/{total_candidates}; captured {index + 1}/{len(clip)})."
                ),
            )
        left_frame, right_frame = clip[index]
        try:
            calibration_result = calibrate_native_homography_from_frames(
                config,
                left_frame,
                right_frame,
                prompt_for_points=False,
                review_required=False,
                save_outputs=False,
            )
        except StitchingFailure as exc:
            failures.append(f"frame#{index}:{exc.code.value}:{exc.detail}")
            continue
        except Exception as exc:
            failures.append(f"frame#{index}:{exc}")
            continue

        calibration_result = dict(calibration_result)
        preview_started = time.perf_counter()
        try:
            virtual_solution, spec, outputs, rollout = _preview_rigid_calibration_rollout(
                calibration_result,
                left_frame=left_frame,
                right_frame=right_frame,
            )
        except Exception as exc:
            preview_total_ms += (time.perf_counter() - preview_started) * 1000.0
            failures.append(f"frame#{index}:rigid-preview:{exc}")
            continue
        preview_total_ms += (time.perf_counter() - preview_started) * 1000.0

        if bool(rollout.get("launch_ready")) and bool(rollout.get("geometry_operator_visible")):
            calibration_result = _record_single_path_metadata(
                calibration_result,
                attempted_indices=attempted_indices,
                calibration_total_ms=(time.perf_counter() - selection_started) * 1000.0,
                preview_total_ms=preview_total_ms,
                selected_attempt_ordinal=attempt_ordinal,
            )
            return _SelectedRigidCalibration(
                frame_index=index,
                left_frame=left_frame,
                right_frame=right_frame,
                calibration_result=calibration_result,
                virtual_solution=virtual_solution,
                spec=spec,
                outputs=outputs,
                rollout=rollout,
            )

        failures.append(
            f"frame#{index}:rollout:{str(rollout.get('launch_ready_reason') or 'launch blocked').strip() or 'launch blocked'}"
        )

    detail = " | ".join(failures[:3]) if failures else "unknown"
    raise ValueError(f"mesh-refresh rigid calibration failed ({detail})")


def _build_active_rigid_runtime_artifact(
    *,
    session_dir: Path,
    calibration_result: dict[str, Any],
    homography: np.ndarray,
    output_resolution: tuple[int, int],
    virtual_solution: Any,
    status_detail: str = "",
    crop_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    active_homography_path, active_geometry_path = _resolve_active_runtime_paths()
    active_homography_path.parent.mkdir(parents=True, exist_ok=True)
    active_geometry_path.parent.mkdir(parents=True, exist_ok=True)

    requested_residual_model = _requested_residual_model(INTERNAL_MESH_REFRESH_MODEL)
    use_virtual_center_geometry, fallback_reason = _should_use_virtual_center_runtime_geometry(
        calibration_result,
        virtual_solution,
        requested_residual_model=requested_residual_model,
        effective_residual_model=requested_residual_model,
    )
    if not use_virtual_center_geometry:
        raise ValueError(fallback_reason or "virtual-center runtime geometry artifact rejected")

    artifact_build = build_virtual_center_runtime_artifact(
        VirtualCenterArtifactBuildSpec(
            source_homography_file=active_homography_path,
            geometry_file=active_geometry_path,
            homography=np.asarray(homography, dtype=np.float64),
            metadata=dict(calibration_result.get("metadata") or {}),
            left_resolution=(int(calibration_result["left_frame"].shape[1]), int(calibration_result["left_frame"].shape[0])),
            right_resolution=(int(calibration_result["right_frame"].shape[1]), int(calibration_result["right_frame"].shape[0])),
            output_resolution=output_resolution,
            inliers_count=int(calibration_result.get("inliers_count") or 0),
            inlier_ratio=float(calibration_result.get("inlier_ratio") or 0.0),
            left_inlier_points=list(calibration_result.get("left_inlier_points") or []),
            right_inlier_points=list(calibration_result.get("right_inlier_points") or []),
            virtual_solution=virtual_solution,
            candidate_model=INTERNAL_MESH_REFRESH_MODEL,
            fallback_used=False,
            status_detail=status_detail,
            mesh=None,
            seam_mode="fixed-seam",
            exposure_enabled=False,
            crop_rect=crop_rect,
        )
    )
    artifact_metadata = artifact_build.metadata
    _save_homography_file(
        active_homography_path,
        np.asarray(homography, dtype=np.float64),
        artifact_metadata,
    )

    artifact = artifact_build.artifact
    save_runtime_geometry_artifact(active_geometry_path, artifact)

    snapshot_homography_path = session_dir / "runtime_homography.json"
    snapshot_geometry_path = session_dir / "runtime_geometry.json"
    _save_homography_file(
        snapshot_homography_path,
        np.asarray(homography, dtype=np.float64),
        artifact_metadata,
    )
    artifact_snapshot = dict(artifact)
    source = artifact_snapshot.get("source", {})
    if isinstance(source, dict):
        source["homography_file"] = str(snapshot_homography_path)
        source["geometry_file"] = str(snapshot_geometry_path)
    save_runtime_geometry_artifact(snapshot_geometry_path, artifact_snapshot)

    rollout = geometry_rollout_metadata(artifact)
    _stamp_runtime_artifact_metadata(
        active_geometry_path,
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        fallback_used=False,
        status_detail=status_detail,
        rollout=rollout,
    )
    _stamp_runtime_artifact_metadata(
        snapshot_geometry_path,
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        fallback_used=False,
        status_detail=status_detail,
        rollout=rollout,
    )
    return {
        "artifact": artifact,
        "rollout": rollout,
        "active_homography_path": str(active_homography_path),
        "active_geometry_path": str(active_geometry_path),
        "snapshot_homography_path": str(snapshot_homography_path),
        "snapshot_geometry_path": str(snapshot_geometry_path),
    }


def run_mesh_refresh(
    config: NativeCalibrationConfig,
    *,
    session_dir: Path | None = None,
    clip_frames: int = DEFAULT_CLIP_FRAMES,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    refresh_dir = Path(session_dir).expanduser() if session_dir is not None else _mesh_refresh_session_dir()
    refresh_dir.mkdir(parents=True, exist_ok=True)
    if progress is not None:
        progress("connect_inputs", "Connecting to the camera streams.")
        progress("capture_frames", "Capturing paired frames for mesh-refresh.")
    clip, capture_summary = _capture_clip(
        config,
        clip_frames=max(3, int(clip_frames)),
        session_dir=refresh_dir,
    )
    if progress is not None and capture_summary.get("capture_source") != "native_paired_capture":
        progress(
            "capture_frames",
            "Native paired-capture was unavailable; using OpenCV fallback for this mesh-refresh run.",
        )

    selected = _select_single_path_rigid_calibration(config, clip, progress=progress)
    representative_index = int(selected.frame_index)
    calibration_result = dict(selected.calibration_result)
    virtual_solution = selected.virtual_solution
    spec = dict(selected.spec)
    outputs = dict(selected.outputs)
    homography = np.asarray(calibration_result["homography_matrix"], dtype=np.float64).reshape(3, 3)
    output_resolution = (
        int(calibration_result["output_resolution"][0]),
        int(calibration_result["output_resolution"][1]),
    )

    if progress is not None:
        progress("solve_geometry", "Finalizing the selected rigid virtual-center geometry.")
    calibration_metadata = calibration_result.get("metadata")
    if isinstance(calibration_metadata, dict):
        apply_capture_summary_metadata(calibration_metadata, capture_summary)
        _apply_virtual_center_metrics(calibration_metadata, virtual_solution)
        calibration_metadata["virtual_center_right_edge_scale_drift"] = float(
            spec.get("right_edge_scale_drift") or calibration_metadata.get("virtual_center_right_edge_scale_drift") or 0.0
        )

    if progress is not None:
        progress("build_artifact", "Writing the active rigid runtime artifact.")
    artifact_info = _build_active_rigid_runtime_artifact(
        session_dir=refresh_dir,
        calibration_result=calibration_result,
        homography=homography,
        output_resolution=output_resolution,
        virtual_solution=virtual_solution,
        status_detail=str(spec.get("status") or "ready"),
        crop_rect=tuple(
            int(value)
            for value in outputs.get("crop_rect")
            or [0, 0, output_resolution[0], output_resolution[1]]
        ),
    )
    rollout = dict(artifact_info["rollout"])
    rollout_truth = _rollout_truth_fields(
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        fallback_used=False,
        status_detail=str(spec.get("status") or "ready"),
        rollout=rollout,
    )

    manifest = build_mesh_refresh_manifest(
        refresh_dir=refresh_dir,
        artifact_info=artifact_info,
        rollout_truth=rollout_truth,
        rollout=rollout,
        capture_summary=capture_summary,
        representative_index=representative_index,
        clip_frame_count=len(clip),
        calibration_result=calibration_result,
        spec=spec,
        outputs=outputs,
        mesh_refresh_model=INTERNAL_MESH_REFRESH_MODEL,
    )
    (refresh_dir / "mesh_refresh.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if progress is not None:
        progress("artifact_ready", "Rigid runtime artifact is ready.")
    return manifest
