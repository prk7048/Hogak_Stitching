from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Callable


def backup_homography_file(path: Path) -> Path | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    backup_path = file_path.with_name(f"{file_path.stem}.bak_{timestamp}{file_path.suffix}")
    shutil.copy2(file_path, backup_path)
    return backup_path


def run_native_calibration(
    args: Any,
    *,
    cv2_module: Any,
    require_configured_rtsp_urls_func: Callable[..., None],
    native_calibration_config_cls: type,
    default_calibration_inliers_file: str,
    calibrate_native_homography_func: Callable[..., dict[str, Any]],
    stitching_failure_cls: type,
) -> int:
    if cv2_module is None:
        print("Missing dependency: opencv-python. Install requirements in your venv first.")
        return 2
    require_configured_rtsp_urls_func(
        str(args.left_rtsp),
        str(args.right_rtsp),
        context="native calibration",
    )
    config = native_calibration_config_cls(
        left_rtsp=str(args.left_rtsp),
        right_rtsp=str(args.right_rtsp),
        output_path=Path(args.out),
        inliers_output_path=Path(default_calibration_inliers_file),
        debug_dir=Path(args.debug_dir),
        rtsp_transport=str(args.rtsp_transport),
        rtsp_timeout_sec=max(1.0, float(args.rtsp_timeout_sec)),
        warmup_frames=max(1, int(args.warmup_frames)),
        process_scale=max(0.1, float(args.process_scale)),
        calibration_mode=str(args.calibration_mode),
        assisted_reproj_threshold=max(1.0, float(args.assisted_reproj_threshold)),
        assisted_max_auto_matches=max(0, int(args.assisted_max_auto_matches)),
        match_backend=str(args.match_backend),
        review_required=not bool(getattr(args, "skip_review", False)),
        min_matches=max(8, int(args.min_matches)),
        min_inliers=max(6, int(args.min_inliers)),
        ratio_test=float(args.ratio_test),
        ransac_reproj_threshold=float(args.ransac_thresh),
        max_features=max(500, int(args.max_features)),
    )
    try:
        result = calibrate_native_homography_func(config)
    except stitching_failure_cls as exc:
        print(f"native calibration failed: {exc.code.value}: {exc.detail}")
        return 2

    output_width, output_height = result["output_resolution"]
    print(
        f"homography_saved={result['homography_file']} "
        f"geometry_saved={result['geometry_file']} "
        f"inliers_saved={result['inliers_file']} "
        f"output={output_width}x{output_height} "
        f"matches={result['matches_count']} "
        f"inliers={result['inliers_count']} "
        f"manual_points={result['manual_points_count']} "
        f"mode={result['calibration_mode']} "
        f"seed_model={result['seed_guidance_model']} "
        f"model={result['transform_model']} "
        f"backend={result['match_backend']} "
        f"geometry_schema={result['geometry_schema_version']} "
        f"geometry_model={result.get('geometry_model', 'virtual-center-rectilinear')} "
        f"score={result['candidate_score']:.3f} "
        f"repr_err={result['mean_reprojection_error']:.2f}"
    )
    return 0
