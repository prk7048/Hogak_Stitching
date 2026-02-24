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
    _estimate_affine_homography,
    _detect_and_match,
    _estimate_homography,
    _prepare_warp_plan,
    _blend_feather,
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
            lag_frames = _estimate_sync_offset_frames(left_signal, right_signal, max_lag_frames)
            report["metrics"]["estimated_sync_offset_ms"] = round((lag_frames / base_fps) * 1000, 2)

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

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output_path), fourcc, base_fps, (plan.width, plan.height))
        if not writer.isOpened():
            raise StitchingFailure(ErrorCode.ENCODE_FAIL, f"cannot open encoder: {output_path}")

        max_frames = int(config.max_duration_sec * base_fps)
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
