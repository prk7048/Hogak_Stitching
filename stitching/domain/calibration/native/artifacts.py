from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

from stitching.core.config import StitchingFailure
from stitching.errors import ErrorCode
from stitching.domain.geometry.artifact import (
    RUNTIME_GEOMETRY_SCHEMA_VERSION,
    runtime_geometry_artifact_path,
    save_runtime_geometry_artifact,
)
from stitching.domain.geometry.common import (
    VirtualCenterArtifactBuildSpec,
    build_virtual_center_runtime_artifact,
)


def save_native_calibration_artifacts(
    config: Any,
    result: dict[str, Any],
    *,
    write_debug_outputs_func: Callable[..., None],
    save_homography_file_func: Callable[..., None],
    solve_virtual_center_rectilinear_func: Callable[..., Any],
    apply_virtual_center_metrics_func: Callable[..., Any],
    should_use_virtual_center_runtime_geometry_func: Callable[..., tuple[bool, str]],
    save_calibration_inliers_file_func: Callable[..., None],
) -> dict[str, Any]:
    homography = np.asarray(result.get("homography_matrix"), dtype=np.float64).reshape(3, 3)
    left = np.asarray(result.get("left_frame"), dtype=np.uint8)
    right = np.asarray(result.get("right_frame"), dtype=np.uint8)
    stitched = np.asarray(result.get("stitched_preview_frame"), dtype=np.uint8)
    inlier_preview = np.asarray(result.get("inlier_preview_frame"), dtype=np.uint8)
    metadata = dict(result.get("metadata") or {})
    left_inlier_points = list(result.get("left_inlier_points") or [])
    right_inlier_points = list(result.get("right_inlier_points") or [])
    geometry_file = runtime_geometry_artifact_path(config.output_path)
    output_resolution = (int(result["output_resolution"][0]), int(result["output_resolution"][1]))
    seam_transition_px = max(48, int(round(min(output_resolution[0], output_resolution[1]) * 0.04)))

    write_debug_outputs_func(config, left, right, stitched, inlier_preview)
    save_homography_file_func(
        config.output_path,
        homography,
        metadata,
    )
    metadata["runtime_geometry_schema_version"] = RUNTIME_GEOMETRY_SCHEMA_VERSION
    metadata["runtime_geometry_file"] = str(geometry_file)

    try:
        virtual_center_solution = solve_virtual_center_rectilinear_func(
            left_points=left_inlier_points,
            right_points=right_inlier_points,
            left_shape=left.shape,
            right_shape=right.shape,
            output_resolution=output_resolution,
        )
    except Exception as exc:
        raise StitchingFailure(
            ErrorCode.HOMOGRAPHY_FAIL,
            f"virtual-center runtime geometry solve failed: {exc}",
        ) from exc

    apply_virtual_center_metrics_func(metadata, virtual_center_solution)
    use_virtual_center_geometry, fallback_reason = should_use_virtual_center_runtime_geometry_func(
        result,
        virtual_center_solution,
    )
    if not use_virtual_center_geometry:
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, fallback_reason)

    geometry_artifact_build = build_virtual_center_runtime_artifact(
        VirtualCenterArtifactBuildSpec(
            source_homography_file=config.output_path,
            geometry_file=geometry_file,
            homography=homography,
            metadata=metadata,
            left_resolution=(int(left.shape[1]), int(left.shape[0])),
            right_resolution=(int(right.shape[1]), int(right.shape[0])),
            output_resolution=output_resolution,
            inliers_count=int(result["inliers_count"]),
            inlier_ratio=float(result["inlier_ratio"]),
            left_inlier_points=left_inlier_points,
            right_inlier_points=right_inlier_points,
            virtual_solution=virtual_center_solution,
            seam_transition_px=seam_transition_px,
        )
    )
    geometry_artifact: dict[str, Any] = geometry_artifact_build.artifact
    metadata.update(geometry_artifact_build.metadata)
    save_homography_file_func(
        config.output_path,
        homography,
        metadata,
    )
    save_runtime_geometry_artifact(geometry_file, geometry_artifact)
    save_calibration_inliers_file_func(
        Path(config.inliers_output_path),
        homography=homography,
        left_resolution=(int(left.shape[1]), int(left.shape[0])),
        right_resolution=(int(right.shape[1]), int(right.shape[0])),
        output_resolution=output_resolution,
        inliers_count=int(result["inliers_count"]),
        inlier_ratio=float(result["inlier_ratio"]),
        left_points=left_inlier_points,
        right_points=right_inlier_points,
    )
    result["homography_file"] = str(config.output_path)
    result["geometry_file"] = str(geometry_file)
    result["geometry_schema_version"] = RUNTIME_GEOMETRY_SCHEMA_VERSION
    result["geometry_model"] = str(geometry_artifact.get("geometry", {}).get("model") or "virtual-center-rectilinear")
    result["inliers_file"] = str(config.inliers_output_path)
    result["debug_dir"] = str(config.debug_dir)
    result["runtime_geometry_file"] = str(geometry_file)
    return result
