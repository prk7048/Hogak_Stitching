from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

try:
    import cv2 as _cv2  # type: ignore
except ModuleNotFoundError:
    _cv2 = None

cv2 = cast(Any, _cv2)

from stitching.runtime_geometry_artifact import (
    RUNTIME_GEOMETRY_SCHEMA_VERSION,
    build_runtime_geometry_artifact,
    load_runtime_geometry_artifact,
    save_runtime_geometry_artifact,
)


@dataclass(slots=True)
class VirtualCenterArtifactBuildSpec:
    source_homography_file: Path | str
    geometry_file: Path | str
    homography: np.ndarray
    metadata: dict[str, Any] | None
    left_resolution: tuple[int, int]
    right_resolution: tuple[int, int]
    output_resolution: tuple[int, int]
    inliers_count: int
    inlier_ratio: float
    left_inlier_points: list[list[float]]
    right_inlier_points: list[list[float]]
    virtual_solution: Any
    candidate_model: str = "virtual-center-rectilinear"
    fallback_used: bool = False
    status_detail: str = ""
    mesh: dict[str, Any] | None = None
    seam_mode: str | None = None
    seam_transition_px: int = 64
    exposure_enabled: bool = True
    crop_rect: tuple[int, int, int, int] | list[int] | None = None


@dataclass(slots=True)
class VirtualCenterArtifactBuildResult:
    artifact: dict[str, Any]
    metadata: dict[str, Any]
    effective_candidate_model: str
    effective_residual_model: str


def apply_virtual_center_metrics(metadata: dict[str, Any], virtual_solution: Any) -> dict[str, Any]:
    metadata["virtual_center_mean_error_px"] = float(virtual_solution.mean_error_px)
    metadata["virtual_center_p95_error_px"] = float(virtual_solution.p95_error_px)
    metadata["virtual_center_scale"] = float(virtual_solution.rigid_scale)
    metadata["virtual_center_rotation_deg"] = float(virtual_solution.rigid_rotation_deg)
    metadata["virtual_center_translation_px"] = float(virtual_solution.rigid_translation_px)
    metadata["virtual_center_candidate_score"] = float(virtual_solution.candidate_score)
    metadata["virtual_center_crop_ratio"] = float(virtual_solution.crop_ratio)
    metadata["virtual_center_right_edge_scale_drift"] = float(virtual_solution.right_edge_scale_drift)
    metadata["virtual_center_roll_correction_deg"] = float(virtual_solution.virtual_roll_correction_deg)
    metadata["virtual_center_mask_tilt_deg"] = float(virtual_solution.mask_tilt_deg)
    return metadata


def build_rectilinear_inverse_map(
    *,
    source_shape: tuple[int, int] | tuple[int, int, int],
    output_resolution: tuple[int, int],
    focal_px: float,
    center: tuple[float, float],
    virtual_focal_px: float,
    virtual_center: tuple[float, float],
    virtual_to_source_rotation: np.ndarray | list[list[float]],
) -> tuple[np.ndarray, np.ndarray]:
    output_width = max(1, int(output_resolution[0]))
    output_height = max(1, int(output_resolution[1]))
    grid_x, grid_y = np.meshgrid(
        np.arange(output_width, dtype=np.float64),
        np.arange(output_height, dtype=np.float64),
    )
    ray_x = (grid_x - float(virtual_center[0])) / max(1.0, float(virtual_focal_px))
    ray_y = (grid_y - float(virtual_center[1])) / max(1.0, float(virtual_focal_px))
    rays = np.stack([ray_x, ray_y, np.ones_like(ray_x)], axis=-1)
    norms = np.linalg.norm(rays, axis=-1, keepdims=True)
    norms = np.where(norms <= 1e-9, 1.0, norms)
    normalized = rays / norms
    source = np.einsum(
        "ij,hwj->hwi",
        np.asarray(virtual_to_source_rotation, dtype=np.float64).reshape(3, 3),
        normalized,
    )
    z = np.where(np.abs(source[..., 2]) < 1e-6, 1e-6, source[..., 2])
    map_x = ((float(focal_px) * source[..., 0] / z) + float(center[0])).astype(np.float32)
    map_y = ((float(focal_px) * source[..., 1] / z) + float(center[1])).astype(np.float32)
    source_height = max(1, int(source_shape[0]))
    source_width = max(1, int(source_shape[1]))
    invalid = (
        ~np.isfinite(map_x)
        | ~np.isfinite(map_y)
        | (source[..., 2] <= 1e-6)
        | (map_x < 0.0)
        | (map_y < 0.0)
        | (map_x > float(source_width - 1))
        | (map_y > float(source_height - 1))
    )
    map_x[invalid] = -1.0
    map_y[invalid] = -1.0
    return map_x, map_y


def build_rectilinear_remap(
    side_projection: dict[str, Any],
    *,
    source_shape: tuple[int, int] | tuple[int, int, int],
    output_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    output_width = max(1, int(output_size[0]))
    output_height = max(1, int(output_size[1]))
    src_focal = float(side_projection.get("focal_px") or 1.0)
    src_center = (
        side_projection.get("center")
        if isinstance(side_projection.get("center"), (list, tuple))
        else [0.0, 0.0]
    )
    virtual_focal = float(side_projection.get("virtual_focal_px") or side_projection.get("focal_px") or 1.0)
    virtual_center = (
        side_projection.get("virtual_center")
        if isinstance(side_projection.get("virtual_center"), (list, tuple))
        else [output_width / 2.0, output_height / 2.0]
    )
    rotation_raw = side_projection.get("virtual_to_source_rotation")
    rotation = np.eye(3, dtype=np.float64)
    if isinstance(rotation_raw, (list, tuple, np.ndarray)):
        try:
            rotation = np.asarray(rotation_raw, dtype=np.float64).reshape(3, 3)
        except Exception:
            rotation = np.eye(3, dtype=np.float64)
    return build_rectilinear_inverse_map(
        source_shape=source_shape,
        output_resolution=output_size,
        focal_px=src_focal,
        center=(float(src_center[0]), float(src_center[1])),
        virtual_focal_px=virtual_focal,
        virtual_center=(float(virtual_center[0]), float(virtual_center[1])),
        virtual_to_source_rotation=rotation,
    )


def compose_affine_inverse_map(
    base_map_x: np.ndarray,
    base_map_y: np.ndarray,
    alignment_matrix: np.ndarray | list[list[float]],
) -> tuple[np.ndarray, np.ndarray]:
    affine = np.asarray(alignment_matrix, dtype=np.float64).reshape(2, 3)
    inverse = cv2.invertAffineTransform(affine.astype(np.float32))
    height, width = base_map_x.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    sample_x = inverse[0, 0] * grid_x + inverse[0, 1] * grid_y + inverse[0, 2]
    sample_y = inverse[1, 0] * grid_x + inverse[1, 1] * grid_y + inverse[1, 2]
    remapped_x = cv2.remap(
        base_map_x,
        sample_x,
        sample_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=-1,
    )
    remapped_y = cv2.remap(
        base_map_y,
        sample_x,
        sample_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=-1,
    )
    return remapped_x, remapped_y


def right_edge_scale_drift(map_x: np.ndarray, map_y: np.ndarray) -> float:
    valid = np.isfinite(map_x) & np.isfinite(map_y) & (map_x >= 0.0) & (map_y >= 0.0)
    if not np.any(valid):
        return 0.0
    grad_x = cv2.Sobel(np.asarray(map_x, dtype=np.float32), cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(np.asarray(map_y, dtype=np.float32), cv2.CV_32F, 1, 0, ksize=3)
    local_scale = np.sqrt(grad_x * grad_x + grad_y * grad_y)
    valid_cols = np.where(np.any(valid, axis=0))[0]
    if valid_cols.size < 10:
        return 0.0
    width = int(valid_cols[-1] - valid_cols[0] + 1)
    center_start = int(valid_cols[0] + width * 0.40)
    center_end = int(valid_cols[0] + width * 0.60)
    right_start = int(valid_cols[-1] - max(2, int(width * 0.15)))
    center_band = np.zeros_like(valid, dtype=bool)
    right_band = np.zeros_like(valid, dtype=bool)
    center_band[:, max(0, center_start) : min(valid.shape[1], center_end)] = True
    right_band[:, max(0, right_start) : valid.shape[1]] = True
    center_values = local_scale[valid & center_band]
    right_values = local_scale[valid & right_band]
    if center_values.size == 0 or right_values.size == 0:
        return 0.0
    center_median = float(np.median(center_values))
    right_median = float(np.median(right_values))
    if right_median <= 1e-6:
        return 0.0
    return float(center_median / right_median)


def requested_residual_model(candidate_model: str) -> str:
    candidate_model = str(candidate_model or "").strip()
    if candidate_model.endswith("-mesh"):
        return "mesh"
    if candidate_model == "virtual-center-rectilinear-rigid":
        return "rigid"
    return "none"


def effective_residual_model(candidate_model: str, *, fallback_used: bool) -> str:
    requested = requested_residual_model(candidate_model)
    if requested == "mesh" and bool(fallback_used):
        return "rigid"
    return requested


def effective_candidate_model(candidate_model: str, *, fallback_used: bool) -> str:
    effective = effective_residual_model(candidate_model, fallback_used=fallback_used)
    if effective == "mesh":
        return "virtual-center-rectilinear-mesh"
    if effective == "rigid":
        return "virtual-center-rectilinear-rigid"
    return "virtual-center-rectilinear"


def residual_truth_fields(
    *,
    candidate_model: str,
    fallback_used: bool,
    status_detail: str = "",
) -> dict[str, Any]:
    requested = requested_residual_model(candidate_model)
    effective = effective_residual_model(candidate_model, fallback_used=fallback_used)
    degraded_to_rigid = requested == "mesh" and effective == "rigid"
    detail = str(status_detail or "")
    if not detail:
        detail = "degraded-to-rigid" if degraded_to_rigid else "ready"
    return {
        "requested_residual_model": requested,
        "effective_residual_model": effective,
        "degraded_to_rigid": bool(degraded_to_rigid),
        "fallback_used": bool(fallback_used),
        "status_detail": detail,
    }


def rollout_truth_fields(
    *,
    candidate_model: str,
    fallback_used: bool,
    status_detail: str = "",
    rollout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = residual_truth_fields(
        candidate_model=candidate_model,
        fallback_used=fallback_used,
        status_detail=status_detail,
    )
    rollout_payload = rollout or {}
    degraded_reason = ""
    if payload["degraded_to_rigid"]:
        degraded_reason = "requested mesh degraded to rigid during geometry solve"
    launch_ready = bool(rollout_payload.get("launch_ready"))
    launch_ready_reason = str(rollout_payload.get("launch_ready_reason") or "").strip()
    if degraded_reason:
        launch_ready_reason = (
            f"{launch_ready_reason}; {degraded_reason}" if launch_ready_reason else degraded_reason
        )
    geometry_rollout_status = str(rollout_payload.get("geometry_rollout_status") or "").strip()
    payload.update(
        {
            "runtime_launch_ready": launch_ready,
            "runtime_launch_ready_reason": launch_ready_reason,
            "geometry_rollout_status": geometry_rollout_status,
            "launch_compatible": launch_ready,
            "launch_compatibility_reason": launch_ready_reason,
        }
    )
    return payload


def build_artifact_metadata(
    metadata: dict[str, Any] | None,
    *,
    candidate_model: str,
    fallback_used: bool,
    status_detail: str = "",
    rollout: dict[str, Any] | None = None,
    geometry_file: Path | str | None = None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    if rollout is None:
        payload.update(
            residual_truth_fields(
                candidate_model=candidate_model,
                fallback_used=fallback_used,
                status_detail=status_detail,
            )
        )
    else:
        payload.update(
            rollout_truth_fields(
                candidate_model=candidate_model,
                fallback_used=fallback_used,
                status_detail=status_detail,
                rollout=rollout,
            )
        )
    payload["runtime_geometry_schema_version"] = RUNTIME_GEOMETRY_SCHEMA_VERSION
    if geometry_file is not None:
        payload["runtime_geometry_file"] = str(geometry_file)
    payload["runtime_geometry_model"] = "virtual-center-rectilinear"
    payload["runtime_geometry_warp_model"] = "virtual-center-remap"
    return payload


def stamp_runtime_artifact_metadata(
    artifact_path: Path | None,
    *,
    candidate_model: str,
    fallback_used: bool,
    status_detail: str = "",
    rollout: dict[str, Any] | None = None,
) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    if not path.exists():
        return
    artifact = load_runtime_geometry_artifact(path)
    if not isinstance(artifact, dict):
        return
    artifact["metadata"] = build_artifact_metadata(
        artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {},
        candidate_model=candidate_model,
        fallback_used=fallback_used,
        status_detail=status_detail,
        rollout=rollout,
        geometry_file=path,
    )
    save_runtime_geometry_artifact(path, artifact)


def build_virtual_center_runtime_artifact(
    spec: VirtualCenterArtifactBuildSpec,
) -> VirtualCenterArtifactBuildResult:
    effective_model = effective_candidate_model(
        spec.candidate_model,
        fallback_used=spec.fallback_used,
    )
    resolved_residual_model = requested_residual_model(effective_model)
    artifact_metadata = build_artifact_metadata(
        spec.metadata,
        candidate_model=spec.candidate_model,
        fallback_used=spec.fallback_used,
        status_detail=spec.status_detail,
        geometry_file=spec.geometry_file,
    )
    left_projection_focal_px = getattr(spec.virtual_solution, "left_projection_focal_px", None)
    left_projection_center = getattr(spec.virtual_solution, "left_projection_center", None)
    right_projection_focal_px = getattr(spec.virtual_solution, "right_projection_focal_px", None)
    right_projection_center = getattr(spec.virtual_solution, "right_projection_center", None)
    virtual_camera: dict[str, Any] = {"model": "rectilinear"}
    if hasattr(spec.virtual_solution, "virtual_focal_px"):
        virtual_camera["focal_px"] = float(spec.virtual_solution.virtual_focal_px)
    if hasattr(spec.virtual_solution, "virtual_center"):
        virtual_center = spec.virtual_solution.virtual_center
        virtual_camera["center"] = [
            float(virtual_center[0]),
            float(virtual_center[1]),
        ]
    if hasattr(spec.virtual_solution, "midpoint_alpha"):
        virtual_camera["midpoint_alpha"] = float(spec.virtual_solution.midpoint_alpha)
    if hasattr(spec.virtual_solution, "left_to_virtual_rotation"):
        virtual_camera["left_to_virtual_rotation"] = np.asarray(
            spec.virtual_solution.left_to_virtual_rotation,
            dtype=np.float64,
        ).reshape(3, 3).tolist()
    if hasattr(spec.virtual_solution, "right_to_virtual_rotation"):
        virtual_camera["right_to_virtual_rotation"] = np.asarray(
            spec.virtual_solution.right_to_virtual_rotation,
            dtype=np.float64,
        ).reshape(3, 3).tolist()
    artifact = build_runtime_geometry_artifact(
        source_homography_file=spec.source_homography_file,
        geometry_file=spec.geometry_file,
        homography=np.asarray(spec.homography, dtype=np.float64),
        metadata=artifact_metadata,
        left_resolution=spec.left_resolution,
        right_resolution=spec.right_resolution,
        output_resolution=spec.output_resolution,
        inliers_count=int(spec.inliers_count),
        inlier_ratio=float(spec.inlier_ratio),
        left_inlier_points=list(spec.left_inlier_points),
        right_inlier_points=list(spec.right_inlier_points),
        geometry_model="virtual-center-rectilinear",
        warp_model="virtual-center-remap",
        alignment_model="rigid",
        alignment_matrix=np.asarray(spec.virtual_solution.rigid_matrix, dtype=np.float64),
        residual_model=resolved_residual_model,
        mesh=dict(spec.mesh or {}) if resolved_residual_model == "mesh" else None,
        projection_model="rectilinear",
        projection_left_focal_px=None if left_projection_focal_px is None else float(left_projection_focal_px),
        projection_left_center=None if left_projection_center is None else tuple(left_projection_center),
        projection_right_focal_px=None if right_projection_focal_px is None else float(right_projection_focal_px),
        projection_right_center=None if right_projection_center is None else tuple(right_projection_center),
        virtual_camera=virtual_camera,
        seam_mode=spec.seam_mode,
        seam_transition_px=int(spec.seam_transition_px),
        exposure_enabled=bool(spec.exposure_enabled),
        crop_rect=spec.crop_rect,
    )
    return VirtualCenterArtifactBuildResult(
        artifact=artifact,
        metadata=artifact_metadata,
        effective_candidate_model=effective_model,
        effective_residual_model=resolved_residual_model,
    )
