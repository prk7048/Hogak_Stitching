from __future__ import annotations

import cv2
import numpy as np


def _blend_feather(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
) -> np.ndarray:
    """겹치는 영역을 거리 기반 가중치로 부드럽게 합성한다."""

    left_valid = left_mask > 0
    right_valid = right_mask > 0
    overlap = left_valid & right_valid
    only_left = left_valid & ~right_valid
    only_right = right_valid & ~left_valid

    result = np.zeros_like(canvas_left, dtype=np.float32)
    result[only_left] = canvas_left[only_left]
    result[only_right] = warped_right[only_right]

    if np.any(overlap):
        dist_left = cv2.distanceTransform(left_valid.astype(np.uint8), cv2.DIST_L2, 3)
        dist_right = cv2.distanceTransform(right_valid.astype(np.uint8), cv2.DIST_L2, 3)
        denom = dist_left + dist_right + 1e-6
        weight_left = dist_left / denom
        weight_right = dist_right / denom
        w_l = weight_left[overlap][:, None]
        w_r = weight_right[overlap][:, None]
        result[overlap] = canvas_left[overlap] * w_l + warped_right[overlap] * w_r

    return np.clip(result, 0, 255).astype(np.uint8)


def _compute_seam_cost_map(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    overlap: np.ndarray,
    prev_canvas_left: np.ndarray | None = None,
    prev_warped_right: np.ndarray | None = None,
    motion_weight: float = 0.0,
) -> np.ndarray:
    """
    seam-cut 비용맵.
    밝기 차이 + 경계(gradient) + 움직임 비용을 합쳐 seam이 정적인 영역을 지나가도록 유도한다.
    """

    gray_left = cv2.cvtColor(canvas_left, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray_right = cv2.cvtColor(warped_right, cv2.COLOR_BGR2GRAY).astype(np.float32)
    diff = cv2.absdiff(gray_left, gray_right)

    grad_x = cv2.Sobel(gray_left, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray_left, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = cv2.magnitude(grad_x, grad_y)

    cost = diff + 0.25 * grad_mag

    if (
        motion_weight > 0.0
        and prev_canvas_left is not None
        and prev_warped_right is not None
        and prev_canvas_left.shape == canvas_left.shape
        and prev_warped_right.shape == warped_right.shape
    ):
        prev_gray_left = cv2.cvtColor(prev_canvas_left, cv2.COLOR_BGR2GRAY).astype(np.float32)
        prev_gray_right = cv2.cvtColor(prev_warped_right, cv2.COLOR_BGR2GRAY).astype(np.float32)
        motion_left = cv2.absdiff(gray_left, prev_gray_left)
        motion_right = cv2.absdiff(gray_right, prev_gray_right)
        motion = np.maximum(motion_left, motion_right)
        cost = cost + float(motion_weight) * motion

    cost[~overlap] = 1e9
    return cost


def _find_seam_path(
    overlap: np.ndarray,
    cost_map: np.ndarray,
    smoothness_penalty: float,
    prev_seam_path: np.ndarray | None = None,
    temporal_penalty: float = 0.0,
) -> np.ndarray:
    """동적 계획법으로 상->하 seam 경로를 찾는다."""

    h, w = overlap.shape
    seam_path = np.full(h, -1, dtype=np.int32)
    ys, xs = np.where(overlap)
    if len(xs) == 0 or len(ys) == 0:
        seam_path[:] = w // 2
        return seam_path

    x_min, x_max = int(xs.min()), int(xs.max())
    seam_w = x_max - x_min + 1
    inf = 1e18
    dp = np.full((h, seam_w), inf, dtype=np.float64)
    prev_idx = np.full((h, seam_w), -1, dtype=np.int32)
    temporal_penalty = max(0.0, float(temporal_penalty))
    has_prev_seam = prev_seam_path is not None and prev_seam_path.shape[0] == h

    first_valid_row = int(ys.min())
    valid0 = overlap[first_valid_row, x_min : x_max + 1]
    row0_cost = cost_map[first_valid_row, x_min : x_max + 1]
    if has_prev_seam:
        row_x = x_min + np.arange(seam_w, dtype=np.float64)
        row0_cost = row0_cost + temporal_penalty * np.abs(row_x - float(prev_seam_path[first_valid_row]))
    dp[first_valid_row, valid0] = row0_cost[valid0]

    for y in range(first_valid_row + 1, h):
        valid = overlap[y, x_min : x_max + 1]
        if not np.any(valid):
            continue
        prev_cost = dp[y - 1]
        row_cost = cost_map[y, x_min : x_max + 1]
        for x in np.where(valid)[0]:
            temporal_cost = 0.0
            if has_prev_seam:
                temporal_cost = temporal_penalty * abs((x_min + x) - float(prev_seam_path[y]))
            candidates = []
            for step in (-1, 0, 1):
                px = x + step
                if px < 0 or px >= seam_w:
                    continue
                p_cost = prev_cost[px]
                if p_cost >= inf * 0.5:
                    continue
                penalty = smoothness_penalty * abs(step)
                candidates.append((p_cost + penalty, px))
            if not candidates:
                candidates.append((0.0, x))
            best_cost, best_prev = min(candidates, key=lambda t: t[0])
            dp[y, x] = row_cost[x] + temporal_cost + best_cost
            prev_idx[y, x] = best_prev

    last_row = int(ys.max())
    valid_last = overlap[last_row, x_min : x_max + 1]
    if not np.any(valid_last):
        seam_path[:] = (x_min + x_max) // 2
        return seam_path

    last_candidates = np.where(valid_last)[0]
    best_x_local = int(last_candidates[np.argmin(dp[last_row, last_candidates])])
    seam_path[last_row] = x_min + best_x_local

    for y in range(last_row, first_valid_row, -1):
        prev_local = prev_idx[y, seam_path[y] - x_min]
        seam_path[y - 1] = seam_path[y] if prev_local < 0 else x_min + int(prev_local)

    for y in range(first_valid_row):
        seam_path[y] = seam_path[first_valid_row]
    for y in range(first_valid_row, h):
        if seam_path[y] < 0:
            seam_path[y] = seam_path[y - 1]
    return seam_path


def _blend_seam_path(
    canvas_left: np.ndarray,
    warped_right: np.ndarray,
    left_mask: np.ndarray,
    right_mask: np.ndarray,
    seam_path: np.ndarray,
    transition_px: int,
) -> np.ndarray:
    """seam 경로를 기준으로 좌/우 영상을 선형 페이드로 합성한다."""

    left_valid = left_mask > 0
    right_valid = right_mask > 0
    overlap = left_valid & right_valid
    only_left = left_valid & ~right_valid
    only_right = right_valid & ~left_valid

    result = np.zeros_like(canvas_left, dtype=np.float32)
    result[only_left] = canvas_left[only_left]
    result[only_right] = warped_right[only_right]

    if np.any(overlap):
        transition = max(2, int(transition_px))
        h, w = overlap.shape
        x_coords = np.arange(w, dtype=np.float32)
        for y in range(h):
            row_overlap = overlap[y]
            if not np.any(row_overlap):
                continue
            seam_x = float(seam_path[y])
            right_w = np.clip((x_coords - (seam_x - transition / 2.0)) / transition, 0.0, 1.0)
            left_w = 1.0 - right_w
            blend_row = (
                canvas_left[y].astype(np.float32) * left_w[:, None]
                + warped_right[y].astype(np.float32) * right_w[:, None]
            )
            result[y, row_overlap] = blend_row[row_overlap]

    return np.clip(result, 0, 255).astype(np.uint8)
