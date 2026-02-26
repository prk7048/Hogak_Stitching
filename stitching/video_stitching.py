from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from stitching.errors import ErrorCode
from stitching.reporting import (
    StageTimer,
    base_report,
    finalize_total_time,
    mark_failed,
    mark_succeeded,
    write_report,
)
from stitching.stitch_core import (
    StitchConfig,
    StitchingFailure,
    _apply_gain_bias,
    _blend_feather,
    _blend_seam_path,
    _compensate_exposure,
    _compute_overlap_diff_mean,
    _compute_seam_cost_map,
    _detect_and_match,
    _estimate_affine_homography,
    _estimate_homography,
    _find_seam_path,
    _prepare_warp_plan,
)


@dataclass(slots=True)
class VideoConfig(StitchConfig):
    """영상 스티칭 전용 설정."""

    # 0 또는 음수면 가능한 전체 길이를 모두 출력한다.
    max_duration_sec: float = 30.0

    # 동기화는 외부에서 이미 끝났다고 가정하므로,
    # 캘리브레이션(어느 시점으로 H를 구할지)만 탐색한다.
    calib_start_sec: float = 0.0
    calib_end_sec: float = 10.0
    calib_step_sec: float = 1.0

    # 겹침 영역 차이가 크면 feather 대신 seam-cut을 쓴다.
    video_ghost_diff_threshold: float = 8.0


def _open_capture(path: Path) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise StitchingFailure(ErrorCode.PROBE_FAIL, f"cannot open video: {path}")
    return cap


def _probe_video(path: Path) -> dict:
    """영상 메타데이터(FPS/프레임 수/해상도)를 읽는다."""

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


def _read_frame_at(path: Path, index: int) -> np.ndarray | None:
    """특정 프레임 인덱스를 랜덤 액세스로 읽는다."""

    cap = _open_capture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, float(max(0, index)))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return frame


def _compute_reprojection_error(
    keypoints_left: list[cv2.KeyPoint],
    keypoints_right: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
    inlier_mask: np.ndarray,
    homography: np.ndarray,
) -> float:
    """후보 H의 품질을 비교하기 위한 평균 재투영 오차."""

    if not matches:
        return float("inf")
    src_points = np.float32([keypoints_right[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst_points = np.float32([keypoints_left[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(src_points, homography)
    errors = np.linalg.norm(projected.reshape(-1, 2) - dst_points.reshape(-1, 2), axis=1)
    inliers = inlier_mask.ravel().astype(bool)
    if inliers.size != errors.size or not np.any(inliers):
        return float("inf")
    return float(errors[inliers].mean())


def _build_calibration_offsets(base_fps: float, max_available_frames: int, config: VideoConfig) -> list[int]:
    """캘리브레이션 후보 시점(프레임 인덱스)을 만든다."""

    if max_available_frames <= 1 or base_fps <= 0:
        return [0]

    max_offset = max_available_frames - 1
    max_sec = max_offset / base_fps
    start_sec = min(max(config.calib_start_sec, 0.0), max_sec)
    end_requested = config.calib_end_sec if config.calib_end_sec > 0 else max_sec
    end_sec = min(max(end_requested, start_sec), max_sec)
    step_sec = max(0.25, float(config.calib_step_sec))

    offsets: set[int] = set()
    t = start_sec
    while t <= end_sec + 1e-9:
        offsets.add(int(round(t * base_fps)))
        t += step_sec
    if not offsets:
        offsets.add(int(round(start_sec * base_fps)))
    return sorted(max(0, min(max_offset, idx)) for idx in offsets)


def _evaluate_homography_candidate(
    frame_left: np.ndarray,
    frame_right: np.ndarray,
    config: VideoConfig,
) -> dict:
    """
    한 시점(frame pair)에서 H를 추정하고 점수를 계산한다.
    이 함수 결과를 여러 시점에서 비교해 가장 안정적인 후보를 고른다.
    """

    if frame_right.shape[:2] != frame_left.shape[:2]:
        frame_right = cv2.resize(
            frame_right,
            (frame_left.shape[1], frame_left.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )

    keypoints_left, keypoints_right, matches = _detect_and_match(frame_left, frame_right, config)

    used_fallback = False
    try:
        homography, inlier_mask = _estimate_homography(
            keypoints_left,
            keypoints_right,
            matches,
            config,
        )
    except StitchingFailure as exc:
        if exc.code != ErrorCode.HOMOGRAPHY_FAIL:
            raise
        homography, inlier_mask = _estimate_affine_homography(
            keypoints_left,
            keypoints_right,
            matches,
            config,
        )
        used_fallback = True

    plan = _prepare_warp_plan(frame_left.shape[:2], frame_right.shape[:2], homography, config)
    inliers_count = int(inlier_mask.ravel().sum())
    reproj_error = _compute_reprojection_error(
        keypoints_left=keypoints_left,
        keypoints_right=keypoints_right,
        matches=matches,
        inlier_mask=inlier_mask,
        homography=homography,
    )
    return {
        "frame_left": frame_left,
        "frame_right": frame_right,
        "keypoints_left": keypoints_left,
        "keypoints_right": keypoints_right,
        "matches": matches,
        "inlier_mask": inlier_mask,
        "plan": plan,
        "used_fallback": used_fallback,
        "matches_count": len(matches),
        "inliers_count": inliers_count,
        "reproj_error": reproj_error,
    }


def _is_better_candidate(new_candidate: dict, best_candidate: dict | None) -> bool:
    """inlier > reproj_error > matches > fallback 여부 순으로 후보를 비교."""

    if best_candidate is None:
        return True
    if int(new_candidate["inliers_count"]) != int(best_candidate["inliers_count"]):
        return int(new_candidate["inliers_count"]) > int(best_candidate["inliers_count"])
    if float(new_candidate["reproj_error"]) != float(best_candidate["reproj_error"]):
        return float(new_candidate["reproj_error"]) < float(best_candidate["reproj_error"])
    if int(new_candidate["matches_count"]) != int(best_candidate["matches_count"]):
        return int(new_candidate["matches_count"]) > int(best_candidate["matches_count"])
    if bool(new_candidate["used_fallback"]) != bool(best_candidate["used_fallback"]):
        return not bool(new_candidate["used_fallback"])
    return False


def _select_calibration_candidate(
    left_path: Path,
    right_path: Path,
    base_fps: float,
    max_available_frames: int,
    config: VideoConfig,
) -> tuple[dict, list[dict]]:
    """지정한 구간에서 여러 후보 프레임을 평가해 best H를 선택한다."""

    offsets = _build_calibration_offsets(base_fps, max_available_frames, config)
    summaries: list[dict] = []
    best_candidate: dict | None = None
    last_failure: StitchingFailure | None = None

    for offset in offsets:
        left_frame = _read_frame_at(left_path, offset)
        right_frame = _read_frame_at(right_path, offset)
        time_sec = round(offset / base_fps, 3) if base_fps > 0 else 0.0
        if left_frame is None or right_frame is None:
            summaries.append(
                {
                    "time_sec": time_sec,
                    "status": "no_frame",
                    "matches_count": 0,
                    "inliers_count": 0,
                    "reproj_error": None,
                    "used_fallback": False,
                    "detail": "frame read failed",
                }
            )
            continue

        try:
            candidate = _evaluate_homography_candidate(left_frame, right_frame, config)
        except StitchingFailure as exc:
            last_failure = exc
            summaries.append(
                {
                    "time_sec": time_sec,
                    "status": "failed",
                    "matches_count": 0,
                    "inliers_count": 0,
                    "reproj_error": None,
                    "used_fallback": False,
                    "detail": f"{exc.code.value}:{exc.detail}",
                }
            )
            continue

        candidate["time_sec"] = time_sec
        summaries.append(
            {
                "time_sec": time_sec,
                "status": "ok",
                "matches_count": int(candidate["matches_count"]),
                "inliers_count": int(candidate["inliers_count"]),
                "reproj_error": round(float(candidate["reproj_error"]), 3),
                "used_fallback": bool(candidate["used_fallback"]),
                "detail": "",
            }
        )
        if _is_better_candidate(candidate, best_candidate):
            best_candidate = candidate

    if best_candidate is None:
        if last_failure is not None:
            raise StitchingFailure(last_failure.code, f"calibration window failed: {last_failure.detail}")
        raise StitchingFailure(ErrorCode.HOMOGRAPHY_FAIL, "calibration window failed: no valid frame candidates")

    return best_candidate, summaries


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
    """
    두 영상이 이미 시간 동기화되어 있다고 가정하고 스티칭한다.
    즉, 본 파이프라인에서는 동기화 추정/보정을 수행하지 않는다.
    """

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

        # FPS가 다르면 낮은 FPS를 기준으로 저장한다.
        base_fps = float(min(left_meta["fps"], right_meta["fps"]))
        max_available_frames = min(int(left_meta["frame_count"]), int(right_meta["frame_count"]))
        if max_available_frames <= 0:
            raise StitchingFailure(ErrorCode.PROBE_FAIL, "no overlapping timeline")

        if status_hook is not None:
            status_hook("stitching")

        seam_path: np.ndarray | None = None
        exposure_gain = 1.0
        exposure_bias = 0.0

        with StageTimer(stage_times, "homography"):
            # 한 프레임 고정이 아니라 구간 후보를 평가해 가장 안정적인 H를 선택한다.
            best_candidate, calib_candidates = _select_calibration_candidate(
                left_path=left_path,
                right_path=right_path,
                base_fps=base_fps,
                max_available_frames=max_available_frames,
                config=config,
            )
            report["metrics"]["calib_candidates"] = calib_candidates
            report["metrics"]["calib_candidates_total"] = len(calib_candidates)
            report["metrics"]["calib_candidates_valid"] = int(
                sum(1 for item in calib_candidates if item.get("status") == "ok")
            )
            report["metrics"]["calib_used_time_sec"] = round(float(best_candidate["time_sec"]), 3)
            report["metrics"]["calib_best_inliers"] = int(best_candidate["inliers_count"])
            report["metrics"]["calib_best_reproj_error"] = round(float(best_candidate["reproj_error"]), 3)

            frame_left = best_candidate["frame_left"]
            frame_right = best_candidate["frame_right"]
            keypoints_left = best_candidate["keypoints_left"]
            keypoints_right = best_candidate["keypoints_right"]
            matches = best_candidate["matches"]
            inlier_mask = best_candidate["inlier_mask"]
            plan = best_candidate["plan"]
            used_fallback = bool(best_candidate["used_fallback"])

            report["metrics"]["matches_count"] = int(best_candidate["matches_count"])
            report["metrics"]["inliers_count"] = int(best_candidate["inliers_count"])
            if used_fallback:
                report["warnings"].append("homography_unstable_fallback_affine")

            # 디버그: 캘리브레이션 시점에서 inlier 매칭을 저장한다.
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

            # 블렌딩 모드(seam_cut / feather)를 미리 결정하기 위해 probe 프레임을 평가한다.
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

        # 실제 프레임 루프 시작
        left_cap = _open_capture(left_path)
        right_cap = _open_capture(right_path)
        ok_l, first_left = left_cap.read()
        ok_r, first_right = right_cap.read()
        if not ok_l or not ok_r:
            raise StitchingFailure(ErrorCode.PROBE_FAIL, "cannot read first aligned frames")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output_path), fourcc, base_fps, (plan.width, plan.height))
        if not writer.isOpened():
            raise StitchingFailure(ErrorCode.ENCODE_FAIL, f"cannot open encoder: {output_path}")

        if config.max_duration_sec <= 0:
            max_frames = max_available_frames
        else:
            max_frames = min(max_available_frames, int(config.max_duration_sec * base_fps))
        processed = 0

        with StageTimer(stage_times, "frame_loop"):
            pending_left = first_left
            pending_right = first_right
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
                    warped_right = _apply_gain_bias(
                        warped_right,
                        gain=exposure_gain,
                        bias=exposure_bias,
                        mask=right_mask,
                    )

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
    except Exception as exc:  # pragma: no cover - 방어 코드
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
