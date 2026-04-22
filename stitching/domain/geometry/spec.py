from __future__ import annotations

from typing import Any, Callable

import cv2
import numpy as np


def prepare_virtual_center_spec(
    *,
    candidate_model: str,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    left_inlier_points: np.ndarray,
    right_inlier_points: np.ndarray,
    output_resolution: tuple[int, int],
    virtual_solution: Any,
    build_rectilinear_remap_func: Callable[..., tuple[np.ndarray, np.ndarray]],
    rectilinear_points_for_solution_func: Callable[..., np.ndarray],
    compose_affine_inverse_map_func: Callable[..., tuple[np.ndarray, np.ndarray]],
    fit_mesh_field_func: Callable[..., Any],
    apply_mesh_to_canvas_func: Callable[..., tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
    right_edge_scale_drift_func: Callable[[np.ndarray, np.ndarray], float],
    virtual_center_mesh_quality_reason_func: Callable[..., str],
) -> dict[str, Any]:
    output_size = (int(output_resolution[0]), int(output_resolution[1]))
    left_projection = {
        "focal_px": float(virtual_solution.left_projection_focal_px),
        "center": [float(virtual_solution.left_projection_center[0]), float(virtual_solution.left_projection_center[1])],
        "virtual_focal_px": float(virtual_solution.virtual_focal_px),
        "virtual_center": [float(virtual_solution.virtual_center[0]), float(virtual_solution.virtual_center[1])],
        "virtual_to_source_rotation": np.linalg.inv(np.asarray(virtual_solution.left_to_virtual_rotation, dtype=np.float64)).tolist(),
    }
    right_projection = {
        "focal_px": float(virtual_solution.right_projection_focal_px),
        "center": [float(virtual_solution.right_projection_center[0]), float(virtual_solution.right_projection_center[1])],
        "virtual_focal_px": float(virtual_solution.virtual_focal_px),
        "virtual_center": [float(virtual_solution.virtual_center[0]), float(virtual_solution.virtual_center[1])],
        "virtual_to_source_rotation": np.linalg.inv(np.asarray(virtual_solution.right_to_virtual_rotation, dtype=np.float64)).tolist(),
    }
    left_map_x, left_map_y = build_rectilinear_remap_func(
        left_projection,
        source_shape=left_frame.shape,
        output_size=output_size,
    )
    right_map_x, right_map_y = build_rectilinear_remap_func(
        right_projection,
        source_shape=right_frame.shape,
        output_size=output_size,
    )
    left_mask = cv2.remap(
        np.full(left_frame.shape[:2], 255, dtype=np.uint8),
        left_map_x,
        left_map_y,
        interpolation=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    right_mask = cv2.remap(
        np.full(right_frame.shape[:2], 255, dtype=np.uint8),
        right_map_x,
        right_map_y,
        interpolation=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    affine = np.asarray(virtual_solution.rigid_matrix, dtype=np.float32).reshape(2, 3)
    right_mask = cv2.warpAffine(
        right_mask,
        affine,
        output_size,
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )

    left_virtual_points = rectilinear_points_for_solution_func(
        left_inlier_points.tolist(),
        focal_px=float(virtual_solution.left_projection_focal_px),
        center=tuple(virtual_solution.left_projection_center),
        rotation=np.asarray(virtual_solution.left_to_virtual_rotation, dtype=np.float64),
        virtual_focal_px=float(virtual_solution.virtual_focal_px),
        virtual_center=tuple(virtual_solution.virtual_center),
    )
    right_virtual_points = rectilinear_points_for_solution_func(
        right_inlier_points.tolist(),
        focal_px=float(virtual_solution.right_projection_focal_px),
        center=tuple(virtual_solution.right_projection_center),
        rotation=np.asarray(virtual_solution.right_to_virtual_rotation, dtype=np.float64),
        virtual_focal_px=float(virtual_solution.virtual_focal_px),
        virtual_center=tuple(virtual_solution.virtual_center),
    )
    right_virtual_h = np.concatenate(
        [right_virtual_points, np.ones((right_virtual_points.shape[0], 1), dtype=np.float64)],
        axis=1,
    )
    right_aligned_points = (np.asarray(virtual_solution.rigid_matrix, dtype=np.float64) @ right_virtual_h.T).T
    base_map_x, base_map_y = compose_affine_inverse_map_func(
        right_map_x,
        right_map_y,
        np.asarray(virtual_solution.rigid_matrix, dtype=np.float64),
    )
    mesh_field = None
    mesh_remap_x = None
    mesh_remap_y = None
    final_map_x = base_map_x
    final_map_y = base_map_y
    status = "ready"
    fallback_used = False
    mesh_quality_reason = ""
    if candidate_model.endswith("-mesh"):
        try:
            mesh_field = fit_mesh_field_func(
                left_virtual_points,
                left_virtual_points - right_aligned_points,
                canvas_shape=(output_size[1], output_size[0]),
                overlap_mask=((left_mask > 0) & (right_mask > 0)).astype(np.uint8),
            )
            mesh_quality_reason = virtual_center_mesh_quality_reason_func(
                max_local_scale_drift=mesh_field.max_local_scale_drift,
                max_local_rotation_drift=mesh_field.max_local_rotation_drift,
            )
            fallback_used = bool(mesh_field.fallback_used)
            if fallback_used or mesh_quality_reason:
                status = "degraded-to-rigid"
                mesh_remap_x = None
                mesh_remap_y = None
            else:
                _, _, mesh_remap_x, mesh_remap_y = apply_mesh_to_canvas_func(
                    np.zeros((output_size[1], output_size[0], 3), dtype=np.uint8),
                    right_mask,
                    mesh_field,
                )
                final_map_x = cv2.remap(
                    base_map_x,
                    mesh_remap_x,
                    mesh_remap_y,
                    interpolation=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=-1,
                )
                final_map_y = cv2.remap(
                    base_map_y,
                    mesh_remap_x,
                    mesh_remap_y,
                    interpolation=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=-1,
                )
        except Exception:
            mesh_field = None
            mesh_remap_x = None
            mesh_remap_y = None
            fallback_used = True
            status = "degraded-to-rigid"

    return {
        "kind": "virtual-center",
        "candidate_model": candidate_model,
        "output_size": output_size,
        "left_map_x": left_map_x,
        "left_map_y": left_map_y,
        "right_map_x": right_map_x,
        "right_map_y": right_map_y,
        "rigid_affine": affine,
        "left_mask_template": left_mask,
        "right_mask_template": right_mask,
        "left_virtual_points": left_virtual_points,
        "right_aligned_points": right_aligned_points,
        "mesh_field": mesh_field,
        "mesh_remap_x": mesh_remap_x,
        "mesh_remap_y": mesh_remap_y,
        "right_edge_scale_drift": right_edge_scale_drift_func(final_map_x, final_map_y),
        "status": status,
        "fallback_used": fallback_used,
        "mesh_max_displacement_px": 0.0 if mesh_field is None else float(mesh_field.max_displacement_px),
        "mesh_max_local_scale_drift": 0.0 if mesh_field is None else float(mesh_field.max_local_scale_drift),
        "mesh_max_local_rotation_drift": 0.0 if mesh_field is None else float(mesh_field.max_local_rotation_drift),
        "mesh_quality_reason": mesh_quality_reason,
    }
