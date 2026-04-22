from __future__ import annotations

from typing import Any, Callable

import cv2
import numpy as np


def render_virtual_center_from_spec(
    spec: dict[str, Any],
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    *,
    compose_candidate_outputs_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    output_size = tuple(spec["output_size"])
    left_projected = cv2.remap(
        left_frame,
        spec["left_map_x"],
        spec["left_map_y"],
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    right_projected = cv2.remap(
        right_frame,
        spec["right_map_x"],
        spec["right_map_y"],
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    right_projected = cv2.warpAffine(
        right_projected,
        np.asarray(spec["rigid_affine"], dtype=np.float32),
        output_size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    left_mask = np.asarray(spec["left_mask_template"], dtype=np.uint8)
    right_mask = np.asarray(spec["right_mask_template"], dtype=np.uint8)
    final_right = right_projected
    final_mask = right_mask
    mesh_field = spec.get("mesh_field")
    mesh_remap_x = spec.get("mesh_remap_x")
    mesh_remap_y = spec.get("mesh_remap_y")
    if mesh_field is not None and mesh_remap_x is not None and mesh_remap_y is not None:
        final_right = cv2.remap(
            right_projected,
            mesh_remap_x,
            mesh_remap_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
        final_mask = cv2.remap(
            right_mask,
            mesh_remap_x,
            mesh_remap_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
    return compose_candidate_outputs_func(
        left_projected,
        final_right,
        left_mask,
        final_mask,
        instability=None if mesh_field is None else mesh_field.instability,
    )
