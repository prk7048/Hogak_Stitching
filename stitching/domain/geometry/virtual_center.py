from dataclasses import dataclass
from typing import Any, cast

import numpy as np

try:
    import cv2 as _cv2  # type: ignore
except ModuleNotFoundError:
    _cv2 = None

cv2 = cast(Any, _cv2)

from stitching.domain.geometry.common import (
    build_rectilinear_inverse_map,
    compose_affine_inverse_map,
    right_edge_scale_drift,
)


@dataclass(slots=True)
class VirtualCenterRectilinearSolution:
    left_projection_focal_px: float
    left_projection_center: tuple[float, float]
    right_projection_focal_px: float
    right_projection_center: tuple[float, float]
    virtual_focal_px: float
    virtual_center: tuple[float, float]
    left_to_virtual_rotation: np.ndarray
    right_to_virtual_rotation: np.ndarray
    rigid_matrix: np.ndarray
    mean_error_px: float
    p95_error_px: float
    rigid_rotation_deg: float
    rigid_translation_px: float
    rigid_scale: float
    candidate_score: float
    crop_ratio: float = 0.0
    right_edge_scale_drift: float = 0.0
    virtual_roll_correction_deg: float = 0.0
    mask_tilt_deg: float = 0.0
    midpoint_alpha: float = 0.5


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def default_runtime_projection_params(
    frame_shape: tuple[int, int] | tuple[int, int, int],
) -> tuple[float, tuple[float, float]]:
    height = max(1, int(frame_shape[0]))
    width = max(1, int(frame_shape[1]))
    return float(max(width, height) * 0.90), (float(width) / 2.0, float(height) / 2.0)


def point_to_rectilinear_ray(
    point: list[float] | tuple[float, float],
    *,
    focal_px: float,
    center_x: float,
    center_y: float,
) -> np.ndarray:
    focal = max(1.0, float(focal_px))
    x = (float(point[0]) - float(center_x)) / focal
    y = (float(point[1]) - float(center_y)) / focal
    ray = np.asarray([x, y, 1.0], dtype=np.float64)
    norm = float(np.linalg.norm(ray))
    if norm <= 1e-9:
        return np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
    return ray / norm


def solve_rotation_kabsch(source_rays: np.ndarray, target_rays: np.ndarray) -> np.ndarray:
    source = np.asarray(source_rays, dtype=np.float64).reshape(-1, 3)
    target = np.asarray(target_rays, dtype=np.float64).reshape(-1, 3)
    if source.shape[0] != target.shape[0] or source.shape[0] < 2:
        raise ValueError("virtual-center rotation solve requires at least two paired rays")
    covariance = source.T @ target
    u, _s, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1, :] *= -1.0
        rotation = vt.T @ u.T
    return rotation


def midpoint_virtual_rotations(right_to_left_rotation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rotation = np.asarray(right_to_left_rotation, dtype=np.float64).reshape(3, 3)
    rvec, _ = cv2.Rodrigues(rotation)
    rvec = np.asarray(rvec, dtype=np.float64).reshape(3, 1)
    left_to_virtual, _ = cv2.Rodrigues((-0.5 * rvec).reshape(3, 1))
    right_to_virtual, _ = cv2.Rodrigues((0.5 * rvec).reshape(3, 1))
    return (
        np.asarray(left_to_virtual, dtype=np.float64).reshape(3, 3),
        np.asarray(right_to_virtual, dtype=np.float64).reshape(3, 3),
    )


def lock_virtual_roll(
    left_to_virtual_rotation: np.ndarray,
    right_to_virtual_rotation: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    left_rotation = np.asarray(left_to_virtual_rotation, dtype=np.float64).reshape(3, 3)
    right_rotation = np.asarray(right_to_virtual_rotation, dtype=np.float64).reshape(3, 3)
    projected_right_axis = left_rotation[:, 0]
    roll_rad = float(np.arctan2(projected_right_axis[1], projected_right_axis[0]))
    if abs(roll_rad) <= 1e-9:
        return left_rotation, right_rotation, 0.0
    cos_value = float(np.cos(-roll_rad))
    sin_value = float(np.sin(-roll_rad))
    roll_correction = np.asarray(
        [
            [cos_value, -sin_value, 0.0],
            [sin_value, cos_value, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return (
        np.asarray(roll_correction @ left_rotation, dtype=np.float64).reshape(3, 3),
        np.asarray(roll_correction @ right_rotation, dtype=np.float64).reshape(3, 3),
        float(np.degrees(roll_rad)),
    )


def project_ray_to_virtual_rectilinear(
    ray: np.ndarray,
    *,
    focal_px: float,
    center_x: float,
    center_y: float,
) -> tuple[float, float] | None:
    direction = np.asarray(ray, dtype=np.float64).reshape(3)
    z = float(direction[2])
    if z <= 1e-6:
        return None
    focal = max(1.0, float(focal_px))
    x = (focal * float(direction[0]) / z) + float(center_x)
    y = (focal * float(direction[1]) / z) + float(center_y)
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    return float(x), float(y)


def largest_valid_rect(mask: np.ndarray) -> tuple[int, int, int, int]:
    binary = (np.asarray(mask, dtype=np.uint8) > 0).astype(np.uint8)
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


def valid_mask_tilt_deg(mask: np.ndarray) -> float:
    ys, xs = np.where(np.asarray(mask, dtype=np.uint8) > 0)
    if xs.size < 8:
        return 0.0
    centered_x = xs.astype(np.float64) - float(np.mean(xs))
    centered_y = ys.astype(np.float64) - float(np.mean(ys))
    covariance = np.cov(np.stack([centered_x, centered_y], axis=0))
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    principal = eigenvectors[:, int(np.argmax(eigenvalues))]
    angle_rad = float(np.arctan2(principal[1], principal[0]))
    angle_deg = abs(float(np.degrees(angle_rad)))
    if angle_deg > 90.0:
        angle_deg = 180.0 - angle_deg
    return float(angle_deg)


def estimate_virtual_center_rigid(
    source_points: np.ndarray,
    target_points: np.ndarray,
) -> tuple[np.ndarray, float, float, float]:
    source = np.asarray(source_points, dtype=np.float64).reshape(-1, 2)
    target = np.asarray(target_points, dtype=np.float64).reshape(-1, 2)
    if source.shape[0] != target.shape[0] or source.shape[0] < 1:
        raise ValueError("virtual-center rigid solve requires paired points")
    if source.shape[0] == 1:
        translation = target[0] - source[0]
        rigid = np.asarray(
            [
                [1.0, 0.0, float(translation[0])],
                [0.0, 1.0, float(translation[1])],
            ],
            dtype=np.float64,
        )
        return rigid, 0.0, float(np.linalg.norm(translation)), 1.0

    source_mean = np.mean(source, axis=0)
    target_mean = np.mean(target, axis=0)
    source_centered = source - source_mean
    target_centered = target - target_mean
    covariance = source_centered.T @ target_centered
    u, _s, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0.0:
        vt[-1, :] *= -1.0
        rotation = vt.T @ u.T
    translation = target_mean - (rotation @ source_mean)
    rigid = np.zeros((2, 3), dtype=np.float64)
    rigid[:2, :2] = rotation
    rigid[:, 2] = translation
    rotation_deg = float(np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0])))
    translation_px = float(np.linalg.norm(translation))
    return rigid, rotation_deg, translation_px, 1.0


def score_virtual_center_candidate(
    *,
    mean_error_px: float,
    p95_error_px: float,
    rigid_rotation_deg: float,
    rigid_translation_px: float,
    crop_ratio: float,
    right_edge_scale_drift: float,
    mask_tilt_deg: float,
    virtual_focal_px: float,
    virtual_default_focal_px: float,
    output_resolution: tuple[int, int],
) -> float:
    width = max(1, int(output_resolution[0]))
    height = max(1, int(output_resolution[1]))
    translation_limit = max(8.0, float(max(width, height)) * 0.015)
    mean_term = _clamp_unit(1.0 - (float(mean_error_px) / 6.0))
    p95_term = _clamp_unit(1.0 - (float(p95_error_px) / 12.0))
    rotation_term = _clamp_unit(1.0 - (abs(float(rigid_rotation_deg)) / 5.0))
    translation_term = _clamp_unit(1.0 - (float(rigid_translation_px) / translation_limit))
    crop_term = _clamp_unit((float(crop_ratio) - 0.45) / 0.30)
    scale_term = _clamp_unit(1.0 - (abs(float(right_edge_scale_drift) - 1.0) / 0.20))
    tilt_term = _clamp_unit(1.0 - (abs(float(mask_tilt_deg)) / 8.0))
    default_focal = max(1.0, float(virtual_default_focal_px))
    focal_ratio = float(virtual_focal_px) / default_focal
    zoom_in_penalty = max(0.0, focal_ratio - 1.0) / 0.30
    zoom_out_penalty = max(0.0, 0.82 - focal_ratio) / 0.18
    fov_term = _clamp_unit(1.0 - zoom_in_penalty - (zoom_out_penalty * 0.35))
    residual_term = (rotation_term * 0.50) + (translation_term * 0.50)
    visual_term = (crop_term * 0.30) + (scale_term * 0.25) + (tilt_term * 0.15) + (fov_term * 0.30)
    return float((mean_term * 0.50) + (p95_term * 0.15) + (residual_term * 0.10) + (visual_term * 0.25))


def should_use_virtual_center_runtime_geometry(
    result: dict[str, Any],
    virtual_center_solution: VirtualCenterRectilinearSolution,
    *,
    requested_residual_model: str = "rigid",
    effective_residual_model: str | None = None,
) -> tuple[bool, str]:
    requested = str(requested_residual_model or "rigid").strip().lower().replace("_", "-")
    effective = str(effective_residual_model or requested).strip().lower().replace("_", "-")
    if requested == "mesh" and effective == "mesh":
        return True, ""
    if float(virtual_center_solution.mean_error_px) > 30.0 or float(virtual_center_solution.p95_error_px) > 60.0:
        return False, "virtual-center rigid preview error is too high for fixed-seam runtime"
    return True, ""


def solve_virtual_center_rectilinear(
    *,
    left_points: list[list[float]] | list[tuple[float, float]],
    right_points: list[list[float]] | list[tuple[float, float]],
    left_shape: tuple[int, int] | tuple[int, int, int],
    right_shape: tuple[int, int] | tuple[int, int, int],
    output_resolution: tuple[int, int],
) -> VirtualCenterRectilinearSolution:
    if len(left_points) != len(right_points) or len(left_points) < 3:
        raise ValueError("virtual-center solve requires at least three paired inlier points")

    left_default_focal_px, left_projection_center = default_runtime_projection_params(left_shape)
    right_default_focal_px, right_projection_center = default_runtime_projection_params(right_shape)
    output_height = max(1, int(output_resolution[1]))
    output_width = max(1, int(output_resolution[0]))
    virtual_default_focal_px, virtual_center = default_runtime_projection_params((output_height, output_width))
    scale_candidates = (0.70, 0.80, 0.90, 1.00, 1.10, 1.25, 1.40)
    best_solution: VirtualCenterRectilinearSolution | None = None

    for scale in scale_candidates:
        left_focal_px = float(left_default_focal_px * scale)
        right_focal_px = float(right_default_focal_px * scale)
        virtual_focal_px = float(virtual_default_focal_px * scale)
        left_rays = np.asarray(
            [
                point_to_rectilinear_ray(
                    point,
                    focal_px=left_focal_px,
                    center_x=left_projection_center[0],
                    center_y=left_projection_center[1],
                )
                for point in left_points
            ],
            dtype=np.float64,
        ).reshape(-1, 3)
        right_rays = np.asarray(
            [
                point_to_rectilinear_ray(
                    point,
                    focal_px=right_focal_px,
                    center_x=right_projection_center[0],
                    center_y=right_projection_center[1],
                )
                for point in right_points
            ],
            dtype=np.float64,
        ).reshape(-1, 3)
        right_to_left_rotation = solve_rotation_kabsch(right_rays, left_rays)
        left_to_virtual_rotation, right_to_virtual_rotation = midpoint_virtual_rotations(right_to_left_rotation)
        left_to_virtual_rotation, right_to_virtual_rotation, virtual_roll_correction_deg = lock_virtual_roll(
            left_to_virtual_rotation,
            right_to_virtual_rotation,
        )

        left_virtual_points: list[tuple[float, float]] = []
        right_virtual_points: list[tuple[float, float]] = []
        for left_ray, right_ray in zip(left_rays, right_rays, strict=False):
            left_virtual = project_ray_to_virtual_rectilinear(
                left_to_virtual_rotation @ left_ray,
                focal_px=virtual_focal_px,
                center_x=virtual_center[0],
                center_y=virtual_center[1],
            )
            right_virtual = project_ray_to_virtual_rectilinear(
                right_to_virtual_rotation @ right_ray,
                focal_px=virtual_focal_px,
                center_x=virtual_center[0],
                center_y=virtual_center[1],
            )
            if left_virtual is None or right_virtual is None:
                continue
            left_virtual_points.append(left_virtual)
            right_virtual_points.append(right_virtual)

        if len(left_virtual_points) < 3:
            continue

        left_virtual_array = np.asarray(left_virtual_points, dtype=np.float64).reshape(-1, 2)
        right_virtual_array = np.asarray(right_virtual_points, dtype=np.float64).reshape(-1, 2)
        rigid_matrix, rigid_rotation_deg, rigid_translation_px, rigid_scale = estimate_virtual_center_rigid(
            right_virtual_array,
            left_virtual_array,
        )
        right_virtual_h = np.concatenate(
            [right_virtual_array, np.ones((right_virtual_array.shape[0], 1), dtype=np.float64)],
            axis=1,
        )
        aligned_right_virtual = (rigid_matrix @ right_virtual_h.T).T
        errors = np.linalg.norm(aligned_right_virtual - left_virtual_array, axis=1)
        mean_error_px = float(np.mean(errors)) if errors.size else 9999.0
        p95_error_px = float(np.percentile(errors, 95.0)) if errors.size else 9999.0
        left_map_x, left_map_y = build_rectilinear_inverse_map(
            source_shape=left_shape,
            output_resolution=(output_width, output_height),
            focal_px=left_focal_px,
            center=(float(left_projection_center[0]), float(left_projection_center[1])),
            virtual_focal_px=virtual_focal_px,
            virtual_center=(float(virtual_center[0]), float(virtual_center[1])),
            virtual_to_source_rotation=np.linalg.inv(left_to_virtual_rotation),
        )
        right_map_x, right_map_y = build_rectilinear_inverse_map(
            source_shape=right_shape,
            output_resolution=(output_width, output_height),
            focal_px=right_focal_px,
            center=(float(right_projection_center[0]), float(right_projection_center[1])),
            virtual_focal_px=virtual_focal_px,
            virtual_center=(float(virtual_center[0]), float(virtual_center[1])),
            virtual_to_source_rotation=np.linalg.inv(right_to_virtual_rotation),
        )
        left_mask = cv2.remap(
            np.full((int(left_shape[0]), int(left_shape[1])), 255, dtype=np.uint8),
            left_map_x,
            left_map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        right_mask = cv2.remap(
            np.full((int(right_shape[0]), int(right_shape[1])), 255, dtype=np.uint8),
            right_map_x,
            right_map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        right_mask = cv2.warpAffine(
            right_mask,
            np.asarray(rigid_matrix, dtype=np.float32).reshape(2, 3),
            (output_width, output_height),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        final_map_x, final_map_y = compose_affine_inverse_map(
            right_map_x,
            right_map_y,
            np.asarray(rigid_matrix, dtype=np.float64),
        )
        valid_mask = ((left_mask > 0) | (right_mask > 0)).astype(np.uint8)
        crop_x, crop_y, crop_w, crop_h = largest_valid_rect(valid_mask)
        crop_ratio = float((crop_w * crop_h) / max(1, output_width * output_height))
        edge_scale_drift = right_edge_scale_drift(final_map_x, final_map_y)
        mask_tilt = valid_mask_tilt_deg(valid_mask)
        candidate_score = score_virtual_center_candidate(
            mean_error_px=mean_error_px,
            p95_error_px=p95_error_px,
            rigid_rotation_deg=rigid_rotation_deg,
            rigid_translation_px=rigid_translation_px,
            crop_ratio=crop_ratio,
            right_edge_scale_drift=edge_scale_drift,
            mask_tilt_deg=mask_tilt,
            virtual_focal_px=virtual_focal_px,
            virtual_default_focal_px=virtual_default_focal_px,
            output_resolution=(output_width, output_height),
        )
        candidate = VirtualCenterRectilinearSolution(
            left_projection_focal_px=left_focal_px,
            left_projection_center=(float(left_projection_center[0]), float(left_projection_center[1])),
            right_projection_focal_px=right_focal_px,
            right_projection_center=(float(right_projection_center[0]), float(right_projection_center[1])),
            virtual_focal_px=virtual_focal_px,
            virtual_center=(float(virtual_center[0]), float(virtual_center[1])),
            left_to_virtual_rotation=np.asarray(left_to_virtual_rotation, dtype=np.float64).reshape(3, 3),
            right_to_virtual_rotation=np.asarray(right_to_virtual_rotation, dtype=np.float64).reshape(3, 3),
            rigid_matrix=np.asarray(rigid_matrix, dtype=np.float64).reshape(2, 3),
            mean_error_px=mean_error_px,
            p95_error_px=p95_error_px,
            rigid_rotation_deg=rigid_rotation_deg,
            rigid_translation_px=rigid_translation_px,
            rigid_scale=rigid_scale,
            candidate_score=candidate_score,
            crop_ratio=crop_ratio,
            right_edge_scale_drift=edge_scale_drift,
            virtual_roll_correction_deg=virtual_roll_correction_deg,
            mask_tilt_deg=mask_tilt,
        )
        if (
            best_solution is None
            or candidate.candidate_score > best_solution.candidate_score
            or (
                abs(candidate.candidate_score - best_solution.candidate_score) <= 1e-9
                and (
                    candidate.virtual_focal_px < (best_solution.virtual_focal_px - 1e-9)
                    or (
                        abs(candidate.virtual_focal_px - best_solution.virtual_focal_px) <= 1e-9
                        and candidate.mean_error_px < best_solution.mean_error_px
                    )
                )
            )
        ):
            best_solution = candidate

    if best_solution is None:
        raise ValueError("virtual-center rectilinear solve failed to produce a valid candidate")
    return best_solution
