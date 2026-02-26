from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from stitching.errors import ErrorCode
from stitching.image_stitching import (
    StitchConfig,
    StitchingFailure,
    _apply_gain_bias,
    _compensate_exposure,
    _compute_overlap_diff_mean,
    _compute_seam_cost_map,
    _find_seam_path,
    _estimate_affine_homography,
    _detect_and_match,
    _estimate_homography,
    _prepare_warp_plan,
    _blend_feather,
    _blend_seam_path,
)
from stitching.reporting import (
    StageTimer,
    base_report,
    finalize_total_time,
    mark_failed,
    mark_succeeded,
    write_report,
)


@dataclass(slots=True)
class VideoConfig(StitchConfig):
    max_duration_sec: float = 30.0
    sync_sample_sec: float = 5.0
    sync_max_lag_ms: int = 2000
    sync_refine_window_frames: int = 4
    sync_refine_probe_frame: int = 12
    video_ghost_diff_threshold: float = 8.0


def _open_capture(path: Path) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise StitchingFailure(ErrorCode.PROBE_FAIL, f"cannot open video: {path}")
    return cap


def _probe_video(path: Path) -> dict:
    cap = _open_capture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
        raise StitchingFailure(ErrorCode.PROBE_FAIL, f"invalid video metadata: {path}")
    return {
        "fps": float(fps),
        "frame_count": frame_count,
        "resolution": [width, height],
        "duration_sec": frame_count / fps,
    }


def _luma_signal(path: Path, sample_frames: int) -> np.ndarray:
    cap = _open_capture(path)
    signal = []
    for _ in range(sample_frames):
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        signal.append(float(gray.mean()))
    cap.release()
    if len(signal) < 16:
        raise StitchingFailure(ErrorCode.SYNC_FAIL, f"not enough frames for sync: {path}")
    return np.array(signal, dtype=np.float32)


def _estimate_sync_offset_frames(
    left_signal: np.ndarray, right_signal: np.ndarray, max_lag_frames: int
) -> int:
    left_centered = left_signal - left_signal.mean()
    right_centered = right_signal - right_signal.mean()
    left_std = float(left_centered.std())
    right_std = float(right_centered.std())
    if left_std < 1e-6 or right_std < 1e-6:
        raise StitchingFailure(ErrorCode.SYNC_FAIL, "signals are too flat for sync")

    left_norm = left_centered / left_std
    right_norm = right_centered / right_std

    best_score = -1e18
    best_lag = 0
    for lag in range(-max_lag_frames, max_lag_frames + 1):
        if lag >= 0:
            length = min(len(left_norm) - lag, len(right_norm))
            if length <= 0:
                continue
            lhs = left_norm[lag : lag + length]
            rhs = right_norm[:length]
        else:
            shift = -lag
            length = min(len(left_norm), len(right_norm) - shift)
            if length <= 0:
                continue
            lhs = left_norm[:length]
            rhs = right_norm[shift : shift + length]
        score = float(np.dot(lhs, rhs)) / max(length, 1)
        if score > best_score:
            best_score = score
            best_lag = lag

    return best_lag


def _skip_frames(cap: cv2.VideoCapture, count: int) -> None:
    for _ in range(max(0, count)):
        ok, _ = cap.read()
        if not ok:
            break


def _read_frame_at(path: Path, index: int) -> np.ndarray | None:
    cap = _open_capture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, float(max(0, index)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return frame


def _quick_alignment_score(left_frame: np.ndarray, right_frame: np.ndarray) -> int:
    if left_frame is None or right_frame is None:
        return 0
    if left_frame.shape[:2] != right_frame.shape[:2]:
        right_frame = cv2.resize(
            right_frame,
            (left_frame.shape[1], left_frame.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )
    gray_left = cv2.cvtColor(left_frame, cv2.COLOR_BGR2GRAY)
    gray_right = cv2.cvtColor(right_frame, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=2000)
    kp_l, des_l = orb.detectAndCompute(gray_left, None)
    kp_r, des_r = orb.detectAndCompute(gray_right, None)
    if des_l is None or des_r is None:
        return 0
    knn = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False).knnMatch(des_l, des_r, k=2)
    good: list[cv2.DMatch] = []
    for pair in knn:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)
    if len(good) < 12:
        return 0
    src = np.float32([kp_r[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_l[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    _, inlier_mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if inlier_mask is None:
        return 0
    return int(inlier_mask.ravel().sum())


def _refine_sync_offset_frames(
    left_path: Path,
    right_path: Path,
    coarse_lag_frames: int,
    window_frames: int,
    probe_frame: int,
) -> tuple[int, int]:
    best_lag = coarse_lag_frames
    best_score = -1
    base_idx = max(0, probe_frame)
    for delta in range(-window_frames, window_frames + 1):
        candidate = coarse_lag_frames + delta
        left_idx = base_idx + max(0, candidate)
        right_idx = base_idx + max(0, -candidate)
        left_frame = _read_frame_at(left_path, left_idx)
        right_frame = _read_frame_at(right_path, right_idx)
        score = _quick_alignment_score(left_frame, right_frame) if left_frame is not None and right_frame is not None else 0
        if score > best_score:
            best_score = score
            best_lag = candidate
    return best_lag, best_score


def stitch_videos(
    left_path: Path,
    right_path: Path,
    output_path: Path,
    report_path: Path,
    debug_dir: Path,
    config: VideoConfig | None = None,
    job_id: str | None = None,
    status_hook: Callable[[str], None] | None = None,
) -> dict:
    config = config or VideoConfig()
    started_at = time.perf_counter()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    report = base_report(
        pipeline="video",
        inputs={"left": str(left_path), "right": str(right_path)},
        job_id=job_id,
    )
    stage_times: dict[str, float] = {}
    report["metrics"]["processing_time_sec"] = stage_times
    debug_dir.mkdir(parents=True, exist_ok=True)

    left_cap: cv2.VideoCapture | None = None
    right_cap: cv2.VideoCapture | None = None
    writer: cv2.VideoWriter | None = None

    try:
        if status_hook is not None:
            status_hook("probing")
        with StageTimer(stage_times, "probe"):
            left_meta = _probe_video(left_path)
            right_meta = _probe_video(right_path)

        base_fps = float(min(left_meta["fps"], right_meta["fps"]))
        sample_frames = max(16, int(config.sync_sample_sec * base_fps))
        max_lag_frames = max(1, int(config.sync_max_lag_ms * base_fps / 1000))

        if status_hook is not None:
            status_hook("syncing")
        with StageTimer(stage_times, "sync"):
            left_signal = _luma_signal(left_path, sample_frames)
            right_signal = _luma_signal(right_path, sample_frames)
            coarse_lag_frames = _estimate_sync_offset_frames(left_signal, right_signal, max_lag_frames)
            lag_frames, refine_score = _refine_sync_offset_frames(
                left_path=left_path,
                right_path=right_path,
                coarse_lag_frames=coarse_lag_frames,
                window_frames=config.sync_refine_window_frames,
                probe_frame=config.sync_refine_probe_frame,
            )
            report["metrics"]["estimated_sync_offset_ms"] = round((lag_frames / base_fps) * 1000, 2)
            report["metrics"]["coarse_sync_offset_ms"] = round((coarse_lag_frames / base_fps) * 1000, 2)
            report["metrics"]["sync_refine_score"] = int(refine_score)
            if lag_frames != coarse_lag_frames:
                report["warnings"].append("sync_offset_refined")

        left_cap = _open_capture(left_path)
        right_cap = _open_capture(right_path)
        if lag_frames > 0:
            _skip_frames(left_cap, lag_frames)
        elif lag_frames < 0:
            _skip_frames(right_cap, -lag_frames)

        ok_l, frame_left = left_cap.read()
        ok_r, frame_right = right_cap.read()
        if not ok_l or not ok_r:
            raise StitchingFailure(ErrorCode.SYNC_FAIL, "cannot read aligned frames")

        seam_path: np.ndarray | None = None
        exposure_gain = 1.0
        exposure_bias = 0.0

        if status_hook is not None:
            status_hook("stitching")
        with StageTimer(stage_times, "homography"):
            target_size = (frame_left.shape[1], frame_left.shape[0])
            if (frame_right.shape[1], frame_right.shape[0]) != target_size:
                frame_right = cv2.resize(frame_right, target_size, interpolation=cv2.INTER_LINEAR)

            keypoints_left, keypoints_right, matches = _detect_and_match(frame_left, frame_right, config)
            report["metrics"]["matches_count"] = len(matches)
            used_fallback = False
            try:
                homography, inlier_mask = _estimate_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    config,
                )
                plan = _prepare_warp_plan(frame_left.shape[:2], frame_right.shape[:2], homography, config)
            except StitchingFailure as exc:
                if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
                    raise
                homography, inlier_mask = _estimate_affine_homography(
                    keypoints_left,
                    keypoints_right,
                    matches,
                    config,
                )
                plan = _prepare_warp_plan(frame_left.shape[:2], frame_right.shape[:2], homography, config)
                used_fallback = True
            report["metrics"]["inliers_count"] = int(inlier_mask.ravel().sum())
            if used_fallback:
                report["warnings"].append("homography_unstable_fallback_affine")

            debug_matches = cv2.drawMatches(
                frame_left,
                keypoints_left,
                frame_right,
                keypoints_right,
                matches[:200],
                None,
                matchesMask=[int(v) for v in inlier_mask.ravel().tolist()[:200]],
                flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
            )
            cv2.imwrite(str(debug_dir / "video_inliers.jpg"), debug_matches)

            warped_right_probe = cv2.warpPerspective(
                frame_right,
                plan.homography_adjusted,
                (plan.width, plan.height),
            )
            right_mask_probe = cv2.warpPerspective(
                np.ones(frame_right.shape[:2], dtype=np.uint8) * 255,
                plan.homography_adjusted,
                (plan.width, plan.height),
            )
            canvas_left_probe = np.zeros((plan.height, plan.width, 3), dtype=np.uint8)
            left_mask_probe = np.zeros((plan.height, plan.width), dtype=np.uint8)
            lh_probe, lw_probe = frame_left.shape[:2]
            canvas_left_probe[plan.ty : plan.ty + lh_probe, plan.tx : plan.tx + lw_probe] = frame_left
            left_mask_probe[plan.ty : plan.ty + lh_probe, plan.tx : plan.tx + lw_probe] = 255
            overlap_probe = (left_mask_probe > 0) & (right_mask_probe > 0)
            if config.exposure_compensation:
                warped_right_probe, exposure_gain, exposure_bias = _compensate_exposure(
                    canvas_left=canvas_left_probe,
                    warped_right=warped_right_probe,
                    overlap=overlap_probe,
                    right_mask=right_mask_probe,
                    config=config,
                )
            overlap_diff_mean = _compute_overlap_diff_mean(
                canvas_left_probe,
                warped_right_probe,
                overlap_probe,
            )
            use_seam_cut = used_fallback or overlap_diff_mean >= config.video_ghost_diff_threshold
            if use_seam_cut:
                cost_map = _compute_seam_cost_map(
                    canvas_left=canvas_left_probe,
                    warped_right=warped_right_probe,
                    overlap=overlap_probe,
                )
                seam_path = _find_seam_path(
                    overlap=overlap_probe,
                    cost_map=cost_map,
                    smoothness_penalty=config.seam_smoothness_penalty,
                )
                report["metrics"]["blend_mode"] = "seam_cut"
                report["metrics"]["seam_x"] = int(np.median(seam_path))
                if "homography_unstable_fallback_affine" not in report["warnings"]:
                    report["warnings"].append("high_overlap_difference_seam_cut")
            else:
                report["metrics"]["blend_mode"] = "feather"
                report["metrics"]["seam_x"] = None
            report["metrics"]["overlap_diff_mean"] = round(float(overlap_diff_mean), 3)
            report["metrics"]["exposure_gain"] = round(float(exposure_gain), 4)
            report["metrics"]["exposure_bias"] = round(float(exposure_bias), 4)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output_path), fourcc, base_fps, (plan.width, plan.height))
        if not writer.isOpened():
            raise StitchingFailure(ErrorCode.ENCODE_FAIL, f"cannot open encoder: {output_path}")

        available_left = max(0, int(left_meta["frame_count"]) - max(0, lag_frames))
        available_right = max(0, int(right_meta["frame_count"]) - max(0, -lag_frames))
        max_available_frames = min(available_left, available_right)
        if config.max_duration_sec <= 0:
            max_frames = max_available_frames
        else:
            max_frames = min(max_available_frames, int(config.max_duration_sec * base_fps))
        processed = 0

        with StageTimer(stage_times, "frame_loop"):
            pending_left = frame_left
            pending_right = frame_right
            while processed < max_frames:
                if pending_left is None or pending_right is None:
                    break
                if (pending_right.shape[1], pending_right.shape[0]) != (
                    pending_left.shape[1],
                    pending_left.shape[0],
                ):
                    pending_right = cv2.resize(
                        pending_right,
                        (pending_left.shape[1], pending_left.shape[0]),
                        interpolation=cv2.INTER_LINEAR,
                    )

                warped_right = cv2.warpPerspective(
                    pending_right,
                    plan.homography_adjusted,
                    (plan.width, plan.height),
                )
                right_mask = cv2.warpPerspective(
                    np.ones(pending_right.shape[:2], dtype=np.uint8) * 255,
                    plan.homography_adjusted,
                    (plan.width, plan.height),
                )
                canvas_left = np.zeros((plan.height, plan.width, 3), dtype=np.uint8)
                left_mask = np.zeros((plan.height, plan.width), dtype=np.uint8)
                lh, lw = pending_left.shape[:2]
                canvas_left[plan.ty : plan.ty + lh, plan.tx : plan.tx + lw] = pending_left
                left_mask[plan.ty : plan.ty + lh, plan.tx : plan.tx + lw] = 255

                if config.exposure_compensation:
                    warped_right = _apply_gain_bias(warped_right, gain=exposure_gain, bias=exposure_bias, mask=right_mask)

                if report["metrics"].get("blend_mode") == "seam_cut" and seam_path is not None:
                    stitched = _blend_seam_path(
                        canvas_left,
                        warped_right,
                        left_mask,
                        right_mask,
                        seam_path=seam_path,
                        transition_px=config.seam_transition_px,
                    )
                else:
                    stitched = _blend_feather(canvas_left, warped_right, left_mask, right_mask)
                writer.write(stitched)
                processed += 1

                ok_l, next_left = left_cap.read()
                ok_r, next_right = right_cap.read()
                if not ok_l or not ok_r:
                    break
                pending_left = next_left
                pending_right = next_right

        report["metrics"]["processed_frames"] = processed
        report["metrics"]["output_resolution"] = [int(plan.width), int(plan.height)]
        if processed <= 0:
            raise StitchingFailure(ErrorCode.ENCODE_FAIL, "no output frames produced")

        mark_succeeded(report)

    except StitchingFailure as exc:
        mark_failed(report, exc.code, exc.detail)
    except Exception as exc:  # pragma: no cover - defensive path
        mark_failed(report, ErrorCode.INTERNAL_ERROR, f"unexpected error: {exc}")
    finally:
        if left_cap is not None:
            left_cap.release()
        if right_cap is not None:
            right_cap.release()
        if writer is not None:
            writer.release()
        finalize_total_time(report, started_at)
        write_report(report_path, report)

    return report
