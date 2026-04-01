from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from stitching.core import (
    StitchingFailure,
    _blend_seam_path,
    _compensate_exposure,
    _compute_overlap_diff_mean,
    _compute_seam_cost_map,
    _find_seam_path,
)
from stitching.native_calibration import (
    NativeCalibrationConfig,
    _open_capture,
    _point_to_rectilinear_ray,
    _project_ray_to_virtual_rectilinear,
    _resize_frame,
    _resize_to_match,
    _save_homography_file,
    _solve_virtual_center_rectilinear,
    calibrate_native_homography_from_frames,
)
from stitching.project_defaults import DEFAULT_NATIVE_HOMOGRAPHY_PATH
from stitching.runtime_contract import geometry_rollout_metadata
from stitching.runtime_geometry_artifact import (
    build_runtime_geometry_artifact,
    load_runtime_geometry_artifact,
    runtime_geometry_artifact_path,
    save_runtime_geometry_artifact,
)
from stitching.runtime_site_config import load_runtime_site_config


DEFAULT_MESH_REFRESH_ROOT = Path("data/mesh_refresh")
DEFAULT_CLIP_FRAMES = 12
DEFAULT_GRID_COLS = 16
DEFAULT_GRID_ROWS = 8
INTERNAL_MESH_REFRESH_MODEL = "virtual-center-rectilinear-rigid"
ProgressCallback = Callable[[str, str], None]


@dataclass(slots=True)
class MeshField:
    grid_cols: int
    grid_rows: int
    control_displacement_x: np.ndarray
    control_displacement_y: np.ndarray
    displacement_x: np.ndarray
    displacement_y: np.ndarray
    instability: np.ndarray
    max_displacement_px: float
    max_local_scale_drift: float
    max_local_rotation_drift: float
    fallback_used: bool


def _session_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def _capture_clip(config: NativeCalibrationConfig, *, clip_frames: int) -> list[tuple[np.ndarray, np.ndarray]]:
    left_cap = _open_capture(config.left_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    right_cap = _open_capture(config.right_rtsp, config.rtsp_transport, config.rtsp_timeout_sec)
    captured: list[tuple[np.ndarray, np.ndarray]] = []
    deadline = time.time() + max(4.0, float(config.rtsp_timeout_sec) * 2.0)
    warmup_remaining = max(1, int(config.warmup_frames))
    try:
        while time.time() < deadline and warmup_remaining > 0:
            ok_left, frame_left = left_cap.read()
            ok_right, frame_right = right_cap.read()
            if ok_left and frame_left is not None and ok_right and frame_right is not None:
                warmup_remaining -= 1
        while time.time() < deadline and len(captured) < max(1, int(clip_frames)):
            ok_left, frame_left = left_cap.read()
            ok_right, frame_right = right_cap.read()
            if not ok_left or frame_left is None or not ok_right or frame_right is None:
                continue
            left_frame = _resize_frame(frame_left, config.process_scale)
            right_frame = _resize_frame(frame_right, config.process_scale)
            right_frame = _resize_to_match(right_frame, left_frame.shape[:2])
            captured.append((left_frame, right_frame))
    finally:
        left_cap.release()
        right_cap.release()
    if not captured:
        raise ValueError("failed to capture a synchronized mesh-refresh clip")
    return captured


def _select_best_clip_calibration(
    config: NativeCalibrationConfig,
    clip: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[int, np.ndarray, np.ndarray, dict[str, Any]]:
    def _attempt(
        attempt_name: str,
        attempt_config: NativeCalibrationConfig,
    ) -> tuple[
        list[tuple[tuple[float, int, int], int, np.ndarray, np.ndarray, dict[str, Any]]],
        list[str],
    ]:
        successes: list[tuple[tuple[float, int, int], int, np.ndarray, np.ndarray, dict[str, Any]]] = []
        failures: list[str] = []
        for index, (left_frame, right_frame) in enumerate(clip):
            try:
                result = calibrate_native_homography_from_frames(
                    attempt_config,
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
            result = dict(result)
            result["bakeoff_calibration_mode"] = attempt_name
            result["bakeoff_effective_min_matches"] = int(attempt_config.min_matches)
            result["bakeoff_effective_min_inliers"] = int(attempt_config.min_inliers)
            result["bakeoff_effective_min_affine_inliers"] = int(
                max(
                    int(getattr(attempt_config, "min_affine_inliers_floor", 12)),
                    int(attempt_config.min_inliers * 0.6),
                )
            )
            score = (
                float(result.get("candidate_score") or 0.0),
                int(result.get("inliers_count") or 0),
                int(result.get("matches_count") or 0),
            )
            successes.append((score, index, left_frame, right_frame, result))
        return successes, failures

    relaxed_config = replace(
        config,
        min_matches=max(8, min(int(config.min_matches), 12)),
        min_inliers=max(4, min(int(config.min_inliers), 4)),
        min_affine_inliers_floor=4,
        ratio_test=max(float(config.ratio_test), 0.90),
        ransac_reproj_threshold=max(float(config.ransac_reproj_threshold), 8.0),
        max_features=max(int(config.max_features), 8000),
    )

    attempt_records = [("strict", config)]
    if (
        relaxed_config.min_matches != config.min_matches
        or relaxed_config.min_inliers != config.min_inliers
        or relaxed_config.min_affine_inliers_floor != getattr(config, "min_affine_inliers_floor", 12)
        or abs(relaxed_config.ratio_test - config.ratio_test) > 1e-9
        or abs(relaxed_config.ransac_reproj_threshold - config.ransac_reproj_threshold) > 1e-9
        or relaxed_config.max_features != config.max_features
    ):
        attempt_records.append(("relaxed", relaxed_config))

    aggregate_failures: list[str] = []
    for attempt_name, attempt_config in attempt_records:
        successes, failures = _attempt(attempt_name, attempt_config)
        if successes:
            successes.sort(key=lambda item: item[0], reverse=True)
            _score, best_index, left_frame, right_frame, result = successes[0]
            return best_index, left_frame, right_frame, result
        if failures:
            joined = " | ".join(failures[:8])
            aggregate_failures.append(f"{attempt_name}[{joined}]")

    detail = " | ".join(aggregate_failures[:2]) if aggregate_failures else "unknown"
    raise ValueError(f"mesh-refresh calibration failed for all captured frames ({detail})")


def _build_rectilinear_remap(side_projection: dict[str, Any], *, output_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    output_width = max(1, int(output_size[0]))
    output_height = max(1, int(output_size[1]))
    src_focal = float(side_projection.get("focal_px") or 1.0)
    src_center = side_projection.get("center") if isinstance(side_projection.get("center"), (list, tuple)) else [0.0, 0.0]
    virtual_focal = float(side_projection.get("virtual_focal_px") or side_projection.get("focal_px") or 1.0)
    virtual_center = side_projection.get("virtual_center") if isinstance(side_projection.get("virtual_center"), (list, tuple)) else [output_width / 2.0, output_height / 2.0]
    rotation_raw = side_projection.get("virtual_to_source_rotation")
    rotation = np.eye(3, dtype=np.float64)
    if isinstance(rotation_raw, (list, tuple, np.ndarray)):
        try:
            rotation = np.asarray(rotation_raw, dtype=np.float64).reshape(3, 3)
        except Exception:
            rotation = np.eye(3, dtype=np.float64)
    grid_x, grid_y = np.meshgrid(np.arange(output_width, dtype=np.float64), np.arange(output_height, dtype=np.float64))
    virtual_dirs = np.stack(
        [
            (grid_x - float(virtual_center[0])) / max(1.0, float(virtual_focal)),
            (grid_y - float(virtual_center[1])) / max(1.0, float(virtual_focal)),
            np.ones((output_height, output_width), dtype=np.float64),
        ],
        axis=-1,
    )
    source_dirs = np.einsum("ij,hwj->hwi", rotation, virtual_dirs)
    z = source_dirs[..., 2]
    valid = np.isfinite(z) & (z > 1e-6)
    map_x = np.full((output_height, output_width), -1.0, dtype=np.float32)
    map_y = np.full((output_height, output_width), -1.0, dtype=np.float32)
    projected_x = (float(src_focal) * source_dirs[..., 0] / z) + float(src_center[0])
    projected_y = (float(src_focal) * source_dirs[..., 1] / z) + float(src_center[1])
    map_x[valid] = projected_x[valid].astype(np.float32)
    map_y[valid] = projected_y[valid].astype(np.float32)
    return map_x, map_y


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


def _compose_affine_inverse_map(
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
    remapped_x = cv2.remap(base_map_x, sample_x, sample_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=-1)
    remapped_y = cv2.remap(base_map_y, sample_x, sample_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=-1)
    return remapped_x, remapped_y


def _center_bias(overlap: np.ndarray) -> np.ndarray:
    height, width = overlap.shape[:2]
    if not np.any(overlap):
        return np.zeros((height, width), dtype=np.float32)
    columns = np.where(np.any(overlap, axis=0))[0]
    center_x = float(columns[0] + columns[-1]) * 0.5
    half_width = max(1.0, float(columns[-1] - columns[0] + 1) * 0.5)
    grid_x = np.arange(width, dtype=np.float32)[None, :]
    return np.abs(grid_x - center_x) / half_width


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


def _fit_mesh_field(
    sample_points: np.ndarray,
    displacements: np.ndarray,
    *,
    canvas_shape: tuple[int, int],
    overlap_mask: np.ndarray,
    grid_cols: int = DEFAULT_GRID_COLS,
    grid_rows: int = DEFAULT_GRID_ROWS,
) -> MeshField:
    height, width = canvas_shape
    if sample_points.shape[0] < 8:
        zero = np.zeros((height, width), dtype=np.float32)
        control_shape = (grid_rows + 1, grid_cols + 1)
        zero_control = np.zeros(control_shape, dtype=np.float32)
        return MeshField(grid_cols, grid_rows, zero_control, zero_control.copy(), zero, zero, zero, 0.0, 0.0, 0.0, True)
    node_cols = grid_cols + 1
    node_rows = grid_rows + 1
    node_count = node_cols * node_rows
    cell_w = float(max(1, width - 1)) / float(grid_cols)
    cell_h = float(max(1, height - 1)) / float(grid_rows)

    def node_index(ix: int, iy: int) -> int:
        return iy * node_cols + ix

    samples = sample_points.shape[0]
    design = np.zeros((samples, node_count), dtype=np.float64)
    for idx, (x, y) in enumerate(sample_points.tolist()):
        gx = np.clip(float(x) / max(1e-6, cell_w), 0.0, float(grid_cols) - 1e-6)
        gy = np.clip(float(y) / max(1e-6, cell_h), 0.0, float(grid_rows) - 1e-6)
        ix = int(math.floor(gx))
        iy = int(math.floor(gy))
        tx = gx - float(ix)
        ty = gy - float(iy)
        weights = (
            (node_index(ix, iy), (1.0 - tx) * (1.0 - ty)),
            (node_index(ix + 1, iy), tx * (1.0 - ty)),
            (node_index(ix, iy + 1), (1.0 - tx) * ty),
            (node_index(ix + 1, iy + 1), tx * ty),
        )
        for node_id, weight in weights:
            design[idx, node_id] += float(weight)

    regularization_rows: list[np.ndarray] = []
    regularization_targets: list[float] = []
    smoothness_weight = 0.18
    border_weight = 0.08
    for iy in range(node_rows):
        for ix in range(node_cols):
            current = node_index(ix, iy)
            if ix + 1 < node_cols:
                row = np.zeros(node_count, dtype=np.float64)
                row[current] = smoothness_weight
                row[node_index(ix + 1, iy)] = -smoothness_weight
                regularization_rows.append(row)
                regularization_targets.append(0.0)
            if iy + 1 < node_rows:
                row = np.zeros(node_count, dtype=np.float64)
                row[current] = smoothness_weight
                row[node_index(ix, iy + 1)] = -smoothness_weight
                regularization_rows.append(row)
                regularization_targets.append(0.0)
            if ix in {0, node_cols - 1} or iy in {0, node_rows - 1}:
                row = np.zeros(node_count, dtype=np.float64)
                row[current] = border_weight
                regularization_rows.append(row)
                regularization_targets.append(0.0)

    if regularization_rows:
        reg_matrix = np.stack(regularization_rows, axis=0)
        design_aug = np.vstack([design, reg_matrix])
        zeros_aug = np.asarray(regularization_targets, dtype=np.float64)
    else:
        design_aug = design
        zeros_aug = np.zeros((0,), dtype=np.float64)

    rhs_x = np.concatenate([displacements[:, 0].astype(np.float64), zeros_aug], axis=0)
    rhs_y = np.concatenate([displacements[:, 1].astype(np.float64), zeros_aug], axis=0)
    nodes_x, *_ = np.linalg.lstsq(design_aug, rhs_x, rcond=None)
    nodes_y, *_ = np.linalg.lstsq(design_aug, rhs_y, rcond=None)

    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    field_x = np.zeros((height, width), dtype=np.float32)
    field_y = np.zeros((height, width), dtype=np.float32)
    for iy in range(grid_rows):
        y0 = int(round((float(iy) / float(grid_rows)) * float(height - 1)))
        y1 = int(round((float(iy + 1) / float(grid_rows)) * float(height - 1)))
        y1 = max(y0 + 1, y1)
        rows = slice(y0, min(height, y1 + 1))
        local_y = (grid_y[rows, :] - float(y0)) / max(1.0, float(y1 - y0))
        local_y = np.clip(local_y, 0.0, 1.0)
        for ix in range(grid_cols):
            x0 = int(round((float(ix) / float(grid_cols)) * float(width - 1)))
            x1 = int(round((float(ix + 1) / float(grid_cols)) * float(width - 1)))
            x1 = max(x0 + 1, x1)
            cols = slice(x0, min(width, x1 + 1))
            local_x = (grid_x[rows, cols] - float(x0)) / max(1.0, float(x1 - x0))
            local_x = np.clip(local_x, 0.0, 1.0)
            n00 = node_index(ix, iy)
            n10 = node_index(ix + 1, iy)
            n01 = node_index(ix, iy + 1)
            n11 = node_index(ix + 1, iy + 1)
            w00 = (1.0 - local_x) * (1.0 - local_y[:, cols])
            w10 = local_x * (1.0 - local_y[:, cols])
            w01 = (1.0 - local_x) * local_y[:, cols]
            w11 = local_x * local_y[:, cols]
            field_x[rows, cols] = (
                float(nodes_x[n00]) * w00
                + float(nodes_x[n10]) * w10
                + float(nodes_x[n01]) * w01
                + float(nodes_x[n11]) * w11
            )
            field_y[rows, cols] = (
                float(nodes_y[n00]) * w00
                + float(nodes_y[n10]) * w10
                + float(nodes_y[n01]) * w01
                + float(nodes_y[n11]) * w11
            )

    overlap = overlap_mask > 0
    outside = (~overlap).astype(np.uint8)
    outside_distance = cv2.distanceTransform(outside, cv2.DIST_L2, 3)
    outside_influence = np.exp(-(outside_distance / 48.0)).astype(np.float32)
    overlap_columns = np.where(np.any(overlap, axis=0))[0]
    center_band = np.ones((height, width), dtype=np.float32)
    if overlap_columns.size >= 2:
        center_x = float(overlap_columns[0] + overlap_columns[-1]) * 0.5
        spread = max(24.0, float(overlap_columns[-1] - overlap_columns[0] + 1) * 0.45)
        center_band = np.exp(-((grid_x - center_x) ** 2) / (2.0 * spread * spread)).astype(np.float32)
    border_margin = max(12.0, min(width, height) * 0.08)
    border_distance = np.minimum.reduce(
        [
            grid_x,
            grid_y,
            np.maximum(0.0, float(width - 1) - grid_x),
            np.maximum(0.0, float(height - 1) - grid_y),
        ]
    ).astype(np.float32)
    border_influence = np.clip(border_distance / float(border_margin), 0.0, 1.0)
    influence = np.where(overlap, 1.0, outside_influence) * center_band * border_influence
    field_x *= influence
    field_y *= influence

    dx_dx = cv2.Sobel(field_x, cv2.CV_32F, 1, 0, ksize=3)
    dx_dy = cv2.Sobel(field_x, cv2.CV_32F, 0, 1, ksize=3)
    dy_dx = cv2.Sobel(field_y, cv2.CV_32F, 1, 0, ksize=3)
    dy_dy = cv2.Sobel(field_y, cv2.CV_32F, 0, 1, ksize=3)
    instability = np.sqrt(dx_dx * dx_dx + dx_dy * dx_dy + dy_dx * dy_dx + dy_dy * dy_dy).astype(np.float32)

    max_disp = float(np.max(np.sqrt(field_x * field_x + field_y * field_y))) if field_x.size else 0.0
    sample_rows = slice(None, None, max(1, height // 80))
    sample_cols = slice(None, None, max(1, width // 80))
    j00 = 1.0 - dx_dx[sample_rows, sample_cols]
    j01 = -dx_dy[sample_rows, sample_cols]
    j10 = -dy_dx[sample_rows, sample_cols]
    j11 = 1.0 - dy_dy[sample_rows, sample_cols]
    local_scale_drift = 0.0
    local_rotation_drift = 0.0
    for a, b, c, d in zip(j00.flat, j01.flat, j10.flat, j11.flat, strict=False):
        matrix = np.asarray([[float(a), float(b)], [float(c), float(d)]], dtype=np.float64)
        try:
            _, singular_values, vt = np.linalg.svd(matrix)
        except np.linalg.LinAlgError:
            continue
        local_scale_drift = max(local_scale_drift, float(np.max(np.abs(singular_values - 1.0))))
        rotation = vt.T
        local_rotation_drift = max(local_rotation_drift, abs(float(np.degrees(np.arctan2(rotation[1, 0], rotation[0, 0])))))

    return MeshField(
        grid_cols=grid_cols,
        grid_rows=grid_rows,
        control_displacement_x=nodes_x.reshape(node_rows, node_cols).astype(np.float32),
        control_displacement_y=nodes_y.reshape(node_rows, node_cols).astype(np.float32),
        displacement_x=field_x,
        displacement_y=field_y,
        instability=instability,
        max_displacement_px=max_disp,
        max_local_scale_drift=local_scale_drift,
        max_local_rotation_drift=local_rotation_drift,
        fallback_used=False,
    )


def _apply_mesh_to_canvas(
    frame: np.ndarray,
    mask: np.ndarray,
    mesh_field: MeshField,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    height, width = frame.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    remap_x = grid_x - mesh_field.displacement_x
    remap_y = grid_y - mesh_field.displacement_y
    warped_frame = cv2.remap(frame, remap_x, remap_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    warped_mask = cv2.remap(mask, remap_x, remap_y, interpolation=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return warped_frame, warped_mask, remap_x, remap_y


def _mesh_payload(mesh_field: MeshField | None) -> dict[str, Any]:
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


def _requested_residual_model(candidate_model: str) -> str:
    candidate_model = str(candidate_model or "").strip()
    if candidate_model.endswith("-mesh"):
        return "mesh"
    if candidate_model == "virtual-center-rectilinear-rigid":
        return "rigid"
    return "none"


def _effective_residual_model(candidate_model: str, *, fallback_used: bool) -> str:
    requested = _requested_residual_model(candidate_model)
    if requested == "mesh" and bool(fallback_used):
        return "rigid"
    return requested


def _residual_truth_fields(
    *,
    candidate_model: str,
    fallback_used: bool,
    status_detail: str = "",
) -> dict[str, Any]:
    requested = _requested_residual_model(candidate_model)
    effective = _effective_residual_model(candidate_model, fallback_used=fallback_used)
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


def _rollout_truth_fields(
    *,
    candidate_model: str,
    fallback_used: bool,
    status_detail: str = "",
    rollout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _residual_truth_fields(
        candidate_model=candidate_model,
        fallback_used=fallback_used,
        status_detail=status_detail,
    )
    rollout_payload = rollout or {}
    degraded_reason = ""
    if payload["degraded_to_rigid"]:
        degraded_reason = "requested mesh degraded to rigid during bakeoff"
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


def _build_artifact_metadata(
    metadata: dict[str, Any] | None,
    *,
    candidate_model: str,
    fallback_used: bool,
    status_detail: str = "",
    rollout: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    if rollout is None:
        payload.update(
            _residual_truth_fields(
                candidate_model=candidate_model,
                fallback_used=fallback_used,
                status_detail=status_detail,
            )
        )
    else:
        payload.update(
            _rollout_truth_fields(
                candidate_model=candidate_model,
                fallback_used=fallback_used,
                status_detail=status_detail,
                rollout=rollout,
            )
        )
    return payload


def _stamp_runtime_artifact_metadata(
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
    artifact["metadata"] = _build_artifact_metadata(
        artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {},
        candidate_model=candidate_model,
        fallback_used=fallback_used,
        status_detail=status_detail,
        rollout=rollout,
    )
    save_runtime_geometry_artifact(path, artifact)


def _compose_candidate_outputs(
    left_canvas: np.ndarray,
    right_canvas: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
    *,
    instability: np.ndarray | None = None,
    transition_px: int = 36,
) -> dict[str, Any]:
    overlap = (left_mask > 0) & (right_mask > 0)
    right_adjusted = right_canvas
    gain = 1.0
    bias = 0.0
    stitched_uncropped = np.zeros_like(left_canvas)
    stitched_uncropped[left_mask > 0] = left_canvas[left_mask > 0]
    stitched_uncropped[(right_mask > 0) & ~overlap] = right_adjusted[(right_mask > 0) & ~overlap]

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
        right_roi = right_adjusted[roi].astype(np.float32)
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
        right_gray = cv2.cvtColor(right_adjusted, cv2.COLOR_BGR2GRAY)
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
        "overlap_luma_diff": _compute_overlap_diff_mean(left_canvas, right_adjusted, overlap),
        "crop_ratio": crop_ratio,
        "crop_rect": (int(crop_x), int(crop_y), int(crop_w), int(crop_h)),
        "gain": float(gain),
        "bias": float(bias),
        "overlap_mask": overlap,
    }


def _right_edge_scale_drift(map_x: np.ndarray, map_y: np.ndarray) -> float:
    valid = np.isfinite(map_x) & np.isfinite(map_y) & (map_x >= 0.0) & (map_y >= 0.0)
    if not np.any(valid):
        return 0.0
    grad_x = cv2.Sobel(map_x, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(map_y, cv2.CV_32F, 1, 0, ksize=3)
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


def _residual_metrics(left_points: np.ndarray, right_points: np.ndarray) -> tuple[float, float]:
    if left_points.shape[0] == 0 or right_points.shape[0] == 0:
        return 0.0, 0.0
    residual = left_points - right_points
    errors = np.linalg.norm(residual, axis=1)
    vertical = np.abs(residual[:, 1])
    return float(np.mean(errors)), float(np.percentile(vertical, 90.0))


def _save_candidate_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _prepare_virtual_center_spec(
    *,
    candidate_model: str,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    left_inlier_points: np.ndarray,
    right_inlier_points: np.ndarray,
    output_resolution: tuple[int, int],
    virtual_solution: Any,
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
    left_map_x, left_map_y = _build_rectilinear_remap(left_projection, output_size=output_size)
    right_map_x, right_map_y = _build_rectilinear_remap(right_projection, output_size=output_size)
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

    left_virtual_points = _rectilinear_points_for_solution(
        left_inlier_points.tolist(),
        focal_px=float(virtual_solution.left_projection_focal_px),
        center=tuple(virtual_solution.left_projection_center),
        rotation=np.asarray(virtual_solution.left_to_virtual_rotation, dtype=np.float64),
        virtual_focal_px=float(virtual_solution.virtual_focal_px),
        virtual_center=tuple(virtual_solution.virtual_center),
    )
    right_virtual_points = _rectilinear_points_for_solution(
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
    base_map_x, base_map_y = _compose_affine_inverse_map(
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
    if candidate_model.endswith("-mesh"):
        try:
            mesh_field = _fit_mesh_field(
                left_virtual_points,
                left_virtual_points - right_aligned_points,
                canvas_shape=(output_size[1], output_size[0]),
                overlap_mask=((left_mask > 0) & (right_mask > 0)).astype(np.uint8),
            )
            _, _, mesh_remap_x, mesh_remap_y = _apply_mesh_to_canvas(
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
            fallback_used = bool(mesh_field.fallback_used)
            if fallback_used:
                status = "degraded-to-rigid"
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
        "right_edge_scale_drift": _right_edge_scale_drift(final_map_x, final_map_y),
        "status": status,
        "fallback_used": fallback_used,
        "mesh_max_displacement_px": 0.0 if mesh_field is None else float(mesh_field.max_displacement_px),
        "mesh_max_local_scale_drift": 0.0 if mesh_field is None else float(mesh_field.max_local_scale_drift),
        "mesh_max_local_rotation_drift": 0.0 if mesh_field is None else float(mesh_field.max_local_rotation_drift),
    }


def _render_virtual_center_from_spec(spec: dict[str, Any], left_frame: np.ndarray, right_frame: np.ndarray) -> dict[str, Any]:
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
    outputs = _compose_candidate_outputs(
        left_projected,
        final_right,
        left_mask,
        final_mask,
        instability=None if mesh_field is None else mesh_field.instability,
    )
    return outputs


def _resolve_active_runtime_paths() -> tuple[Path, Path]:
    site_config = load_runtime_site_config()
    paths = site_config.get("paths", {}) if isinstance(site_config.get("paths"), dict) else {}
    homography_file = Path(str(paths.get("homography_file") or DEFAULT_NATIVE_HOMOGRAPHY_PATH)).expanduser()
    geometry_file = runtime_geometry_artifact_path(homography_file)
    return homography_file, geometry_file


def _mesh_refresh_session_dir(root: Path | None = None) -> Path:
    return Path(root or DEFAULT_MESH_REFRESH_ROOT).expanduser() / _session_id()


def _resolve_mesh_refresh_dir(body: dict[str, Any] | None = None) -> Path | None:
    payload = body or {}
    value = payload.get("refresh_dir") or payload.get("bundle_dir")
    if not value:
        return None
    return Path(str(value)).expanduser()


def _validate_mesh_refresh_request(body: dict[str, Any] | None = None) -> None:
    payload = body or {}
    requested_model = str(payload.get("model") or "").strip()
    if requested_model and requested_model != INTERNAL_MESH_REFRESH_MODEL:
        raise ValueError(
            f"mesh-refresh only supports {INTERNAL_MESH_REFRESH_MODEL}; comparison candidate selection is no longer exposed here"
        )
    legacy_only_fields = [name for name in ("video_duration_sec", "video_fps") if name in payload]
    if legacy_only_fields:
        raise ValueError(
            f"mesh-refresh does not generate public comparison videos; unsupported fields: {', '.join(legacy_only_fields)}"
        )


def _load_mesh_refresh_manifest(session_dir: Path) -> dict[str, Any]:
    payload = json.loads((session_dir / "mesh_refresh.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("mesh refresh manifest must be a JSON object")
    return payload


def latest_mesh_refresh(root: Path | None = None) -> dict[str, Any] | None:
    refresh_root = Path(root or DEFAULT_MESH_REFRESH_ROOT).expanduser()
    if not refresh_root.exists():
        return None
    sessions = sorted(
        [path for path in refresh_root.iterdir() if path.is_dir() and (path / "mesh_refresh.json").exists()],
        key=lambda item: item.name,
        reverse=True,
    )
    if not sessions:
        return None
    return _load_mesh_refresh_manifest(sessions[0])


def _build_active_mesh_runtime_artifact(
    *,
    session_dir: Path,
    config: NativeCalibrationConfig,
    calibration_result: dict[str, Any],
    homography: np.ndarray,
    output_resolution: tuple[int, int],
    virtual_solution: Any,
    mesh_field: MeshField | None,
    fallback_used: bool = False,
    status_detail: str = "",
    crop_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    active_homography_path, active_geometry_path = _resolve_active_runtime_paths()
    active_homography_path.parent.mkdir(parents=True, exist_ok=True)
    active_geometry_path.parent.mkdir(parents=True, exist_ok=True)

    requested_residual_model = _requested_residual_model(INTERNAL_MESH_REFRESH_MODEL)
    artifact_metadata = _build_artifact_metadata(
        dict(calibration_result.get("metadata") or {}),
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        fallback_used=fallback_used,
        status_detail=status_detail,
    )
    _save_homography_file(
        active_homography_path,
        np.asarray(homography, dtype=np.float64),
        artifact_metadata,
        distortion_reference=str(calibration_result.get("distortion_reference") or "raw"),
    )

    artifact = build_runtime_geometry_artifact(
        source_homography_file=active_homography_path,
        geometry_file=active_geometry_path,
        homography=np.asarray(homography, dtype=np.float64),
        metadata=artifact_metadata,
        distortion_reference=str(calibration_result.get("distortion_reference") or "raw"),
        left_resolution=(int(calibration_result["left_frame"].shape[1]), int(calibration_result["left_frame"].shape[0])),
        right_resolution=(int(calibration_result["right_frame"].shape[1]), int(calibration_result["right_frame"].shape[0])),
        output_resolution=output_resolution,
        inliers_count=int(calibration_result.get("inliers_count") or 0),
        inlier_ratio=float(calibration_result.get("inlier_ratio") or 0.0),
        left_inlier_points=list(calibration_result.get("left_inlier_points") or []),
        right_inlier_points=list(calibration_result.get("right_inlier_points") or []),
        geometry_model="virtual-center-rectilinear",
        warp_model="virtual-center-remap",
        alignment_model="rigid",
        alignment_matrix=np.asarray(virtual_solution.rigid_matrix, dtype=np.float64),
        residual_model=requested_residual_model,
        mesh=_mesh_payload(mesh_field) if requested_residual_model == "mesh" else None,
        projection_model="rectilinear",
        projection_left_focal_px=float(virtual_solution.left_projection_focal_px),
        projection_left_center=tuple(virtual_solution.left_projection_center),
        projection_right_focal_px=float(virtual_solution.right_projection_focal_px),
        projection_right_center=tuple(virtual_solution.right_projection_center),
        virtual_camera={
            "model": "rectilinear",
            "focal_px": float(virtual_solution.virtual_focal_px),
            "center": [float(virtual_solution.virtual_center[0]), float(virtual_solution.virtual_center[1])],
            "output_resolution": [int(output_resolution[0]), int(output_resolution[1])],
            "midpoint_alpha": float(virtual_solution.midpoint_alpha),
            "left_to_virtual_rotation": np.asarray(virtual_solution.left_to_virtual_rotation, dtype=np.float64).reshape(3, 3).tolist(),
            "right_to_virtual_rotation": np.asarray(virtual_solution.right_to_virtual_rotation, dtype=np.float64).reshape(3, 3).tolist(),
        },
        seam_mode="fixed-seam",
        exposure_enabled=False,
        crop_rect=crop_rect,
    )
    save_runtime_geometry_artifact(active_geometry_path, artifact)

    snapshot_homography_path = session_dir / "runtime_homography.json"
    snapshot_geometry_path = session_dir / "runtime_geometry.json"
    _save_homography_file(
        snapshot_homography_path,
        np.asarray(homography, dtype=np.float64),
        artifact_metadata,
        distortion_reference=str(calibration_result.get("distortion_reference") or "raw"),
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
        fallback_used=fallback_used,
        status_detail=status_detail,
        rollout=rollout,
    )
    _stamp_runtime_artifact_metadata(
        snapshot_geometry_path,
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        fallback_used=fallback_used,
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
    if progress is not None:
        progress("connect_inputs", "Connecting to the camera streams.")
        progress("capture_frames", "Capturing synchronized frames.")
    clip = _capture_clip(config, clip_frames=max(3, int(clip_frames)))
    if progress is not None:
        progress("match_features", "Matching features across the captured frames.")
    representative_index, left_frame, right_frame, calibration_result = _select_best_clip_calibration(config, clip)
    homography = np.asarray(calibration_result["homography_matrix"], dtype=np.float64).reshape(3, 3)
    left_inlier_points = np.asarray(calibration_result.get("left_inlier_points") or [], dtype=np.float64).reshape(-1, 2)
    right_inlier_points = np.asarray(calibration_result.get("right_inlier_points") or [], dtype=np.float64).reshape(-1, 2)
    output_resolution = (
        int(calibration_result["output_resolution"][0]),
        int(calibration_result["output_resolution"][1]),
    )
    if progress is not None:
        progress("solve_geometry", "Solving rigid virtual-center geometry.")
    virtual_solution = _solve_virtual_center_rectilinear(
        left_points=list(calibration_result.get("left_inlier_points") or []),
        right_points=list(calibration_result.get("right_inlier_points") or []),
        left_shape=left_frame.shape,
        right_shape=right_frame.shape,
        output_resolution=output_resolution,
    )
    calibration_metadata = calibration_result.get("metadata")
    if isinstance(calibration_metadata, dict):
        calibration_metadata["virtual_center_mean_error_px"] = float(virtual_solution.mean_error_px)
        calibration_metadata["virtual_center_p95_error_px"] = float(virtual_solution.p95_error_px)
        calibration_metadata["virtual_center_scale"] = float(virtual_solution.rigid_scale)
        calibration_metadata["virtual_center_rotation_deg"] = float(virtual_solution.rigid_rotation_deg)
        calibration_metadata["virtual_center_translation_px"] = float(virtual_solution.rigid_translation_px)
        calibration_metadata["virtual_center_candidate_score"] = float(virtual_solution.candidate_score)
        calibration_metadata["virtual_center_crop_ratio"] = float(virtual_solution.crop_ratio)
        calibration_metadata["virtual_center_right_edge_scale_drift"] = float(virtual_solution.right_edge_scale_drift)
        calibration_metadata["virtual_center_roll_correction_deg"] = float(virtual_solution.virtual_roll_correction_deg)
        calibration_metadata["virtual_center_mask_tilt_deg"] = float(virtual_solution.mask_tilt_deg)
    spec = _prepare_virtual_center_spec(
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        left_frame=left_frame,
        right_frame=right_frame,
        left_inlier_points=left_inlier_points,
        right_inlier_points=right_inlier_points,
        output_resolution=output_resolution,
        virtual_solution=virtual_solution,
    )
    outputs = _render_virtual_center_from_spec(spec, left_frame, right_frame)
    if progress is not None:
        progress("build_artifact", "Writing the active runtime artifact.")

    refresh_dir = Path(session_dir).expanduser() if session_dir is not None else _mesh_refresh_session_dir()
    refresh_dir.mkdir(parents=True, exist_ok=True)

    artifact_info = _build_active_mesh_runtime_artifact(
        session_dir=refresh_dir,
        config=config,
        calibration_result=calibration_result,
        homography=homography,
        output_resolution=output_resolution,
        virtual_solution=virtual_solution,
        mesh_field=spec.get("mesh_field"),
        fallback_used=bool(spec.get("fallback_used")),
        status_detail=str(spec.get("status") or ""),
        crop_rect=tuple(int(value) for value in outputs.get("crop_rect") or [0, 0, output_resolution[0], output_resolution[1]]),
    )
    rollout = dict(artifact_info["rollout"])
    rollout_truth = _rollout_truth_fields(
        candidate_model=INTERNAL_MESH_REFRESH_MODEL,
        fallback_used=bool(spec.get("fallback_used")),
        status_detail=str(spec.get("status") or ""),
        rollout=rollout,
    )

    manifest = {
        "status": "ready",
        "session_id": refresh_dir.name,
        "refresh_dir": str(refresh_dir),
        "runtime_active_artifact_path": str(artifact_info["active_geometry_path"]),
        "mesh_refresh_model": INTERNAL_MESH_REFRESH_MODEL,
        "geometry_artifact_model": str(rollout.get("geometry_model") or ""),
        "geometry_residual_model": str(rollout.get("geometry_residual_model") or ""),
        **rollout_truth,
        "representative_frame_index": int(representative_index),
        "clip_frame_count": int(len(clip)),
        "mesh_refresh_calibration_mode": str(calibration_result.get("bakeoff_calibration_mode") or "strict"),
        "good_match_count": int(calibration_result.get("matches_count") or 0),
        "inlier_count": int(calibration_result.get("inliers_count") or 0),
        "inlier_ratio": float(calibration_result.get("inlier_ratio") or 0.0),
        "mesh_max_displacement_px": float(spec.get("mesh_max_displacement_px") or 0.0),
        "mesh_max_local_scale_drift": float(spec.get("mesh_max_local_scale_drift") or 0.0),
        "mesh_max_local_rotation_drift": float(spec.get("mesh_max_local_rotation_drift") or 0.0),
        "right_edge_scale_drift": float(spec.get("right_edge_scale_drift") or 0.0),
        "fallback_used": bool(spec.get("fallback_used")),
        "status_detail": str(spec.get("status") or "ready"),
        "crop_rect": [int(value) for value in outputs.get("crop_rect") or [0, 0, output_resolution[0], output_resolution[1]]],
        "active_homography_path": str(artifact_info["active_homography_path"]),
        "snapshot_homography_path": str(artifact_info["snapshot_homography_path"]),
        "snapshot_geometry_path": str(artifact_info["snapshot_geometry_path"]),
        "created_at_epoch_sec": int(time.time()),
    }
    (refresh_dir / "mesh_refresh.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if progress is not None:
        progress("artifact_ready", "Rigid runtime artifact is ready.")
    return manifest


def _mesh_refresh_config_from_body(body: dict[str, Any] | None = None) -> NativeCalibrationConfig:
    body = body or {}
    _validate_mesh_refresh_request(body)
    site_config = load_runtime_site_config()
    cameras = site_config.get("cameras", {}) if isinstance(site_config.get("cameras"), dict) else {}
    paths = site_config.get("paths", {}) if isinstance(site_config.get("paths"), dict) else {}
    runtime = site_config.get("runtime", {}) if isinstance(site_config.get("runtime"), dict) else {}
    return NativeCalibrationConfig(
        left_rtsp=str(body.get("left_rtsp") or cameras.get("left_rtsp") or "").strip(),
        right_rtsp=str(body.get("right_rtsp") or cameras.get("right_rtsp") or "").strip(),
        output_path=Path(str(paths.get("homography_file") or DEFAULT_NATIVE_HOMOGRAPHY_PATH)).expanduser(),
        inliers_output_path=Path(str(body.get("inliers_out") or paths.get("calibration_inliers_file") or "data/calibration_inliers.json")).expanduser(),
        debug_dir=Path(str(body.get("debug_dir") or paths.get("calibration_debug_dir") or "data/calibration_debug")).expanduser(),
        rtsp_transport=str(body.get("rtsp_transport") or runtime.get("rtsp_transport") or "tcp").strip(),
        rtsp_timeout_sec=max(1.0, float(body.get("rtsp_timeout_sec") or runtime.get("rtsp_timeout_sec") or 10.0)),
        warmup_frames=max(1, int(body.get("warmup_frames") or runtime.get("warmup_frames") or 45)),
        process_scale=max(0.1, float(body.get("process_scale") or runtime.get("process_scale") or 1.0)),
        calibration_mode="auto",
        assisted_reproj_threshold=max(1.0, float(body.get("assisted_reproj_threshold") or 12.0)),
        assisted_max_auto_matches=max(0, int(body.get("assisted_max_auto_matches") or 600)),
        match_backend="classic",
        review_required=False,
        min_matches=max(8, int(body.get("min_matches") or 40)),
        min_inliers=max(6, int(body.get("min_inliers") or 20)),
        ratio_test=float(body.get("ratio_test") or 0.75),
        ransac_reproj_threshold=float(body.get("ransac_thresh") or 5.0),
        max_features=max(500, int(body.get("max_features") or 4000)),
    )


def run_mesh_refresh_from_args(args: argparse.Namespace) -> int:
    config = NativeCalibrationConfig(
        left_rtsp=str(args.left_rtsp),
        right_rtsp=str(args.right_rtsp),
        output_path=Path(args.out),
        inliers_output_path=Path(args.inliers_out),
        debug_dir=Path(args.debug_dir),
        rtsp_transport=str(args.rtsp_transport),
        rtsp_timeout_sec=max(1.0, float(args.rtsp_timeout_sec)),
        warmup_frames=max(1, int(args.warmup_frames)),
        process_scale=max(0.1, float(args.process_scale)),
        calibration_mode="auto",
        assisted_reproj_threshold=max(1.0, float(args.assisted_reproj_threshold)),
        assisted_max_auto_matches=max(0, int(args.assisted_max_auto_matches)),
        match_backend="classic",
        review_required=False,
        min_matches=max(8, int(getattr(args, "min_matches", 40))),
        min_inliers=max(6, int(getattr(args, "min_inliers", 20))),
        ratio_test=float(getattr(args, "ratio_test", 0.75)),
        ransac_reproj_threshold=float(getattr(args, "ransac_thresh", 5.0)),
        max_features=max(500, int(getattr(args, "max_features", 4000))),
    )
    result = run_mesh_refresh(
        config,
        session_dir=Path(str(args.refresh_dir)).expanduser() if getattr(args, "refresh_dir", None) else None,
        clip_frames=max(3, int(getattr(args, "clip_frames", DEFAULT_CLIP_FRAMES))),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


class MeshRefreshService:
    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root or DEFAULT_MESH_REFRESH_ROOT)

    def state(self) -> dict[str, Any]:
        manifest = latest_mesh_refresh(self._root)
        if manifest is None:
            return {
                "status": "idle",
                "session_id": "",
                "refresh_dir": "",
                "runtime_active_artifact_path": "",
                "mesh_refresh_model": "",
                "geometry_artifact_model": "",
                "geometry_residual_model": "",
                "requested_residual_model": "",
                "effective_residual_model": "",
                "degraded_to_rigid": False,
                "fallback_used": False,
                "status_detail": "",
                "geometry_rollout_status": "",
                "runtime_launch_ready": False,
                "runtime_launch_ready_reason": "",
                "launch_compatible": False,
                "launch_compatibility_reason": "",
            }
        return manifest

    def run(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.run_with_progress(body)

    def run_with_progress(
        self,
        body: dict[str, Any] | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        config = _mesh_refresh_config_from_body(body)
        refresh_dir = _resolve_mesh_refresh_dir(body)
        clip_frames = max(3, int((body or {}).get("clip_frames") or DEFAULT_CLIP_FRAMES))
        return run_mesh_refresh(config, session_dir=refresh_dir, clip_frames=clip_frames, progress=progress)
