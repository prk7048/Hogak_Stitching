from __future__ import annotations

from typing import Any, Callable

import numpy as np

from stitching.domain.geometry.compositor import (
    render_virtual_center_from_spec as _render_virtual_center_from_spec_impl,
)
from stitching.domain.geometry.spec import (
    prepare_virtual_center_spec as _prepare_virtual_center_spec_impl,
)


def mesh_payload(mesh_field: Any | None) -> dict[str, Any]:
    if mesh_field is None:
        return {}
    return {
        "grid_cols": int(mesh_field.grid_cols),
        "grid_rows": int(mesh_field.grid_rows),
        "control_displacement_x": np.asarray(mesh_field.control_displacement_x, dtype=np.float32).tolist(),
        "control_displacement_y": np.asarray(mesh_field.control_displacement_y, dtype=np.float32).tolist(),
        "max_displacement_px": float(mesh_field.max_displacement_px),
        "max_local_scale_drift": float(mesh_field.max_local_scale_drift),
        "max_local_rotation_drift": float(mesh_field.max_local_rotation_drift),
        "fallback_used": bool(mesh_field.fallback_used),
    }


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
    return _prepare_virtual_center_spec_impl(
        candidate_model=candidate_model,
        left_frame=left_frame,
        right_frame=right_frame,
        left_inlier_points=left_inlier_points,
        right_inlier_points=right_inlier_points,
        output_resolution=output_resolution,
        virtual_solution=virtual_solution,
        build_rectilinear_remap_func=build_rectilinear_remap_func,
        rectilinear_points_for_solution_func=rectilinear_points_for_solution_func,
        compose_affine_inverse_map_func=compose_affine_inverse_map_func,
        fit_mesh_field_func=fit_mesh_field_func,
        apply_mesh_to_canvas_func=apply_mesh_to_canvas_func,
        right_edge_scale_drift_func=right_edge_scale_drift_func,
        virtual_center_mesh_quality_reason_func=virtual_center_mesh_quality_reason_func,
    )


def render_virtual_center_from_spec(
    spec: dict[str, Any],
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    *,
    compose_candidate_outputs_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    return _render_virtual_center_from_spec_impl(
        spec,
        left_frame,
        right_frame,
        compose_candidate_outputs_func=compose_candidate_outputs_func,
    )
