from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple


def _add_video_common_args(cmd: argparse.ArgumentParser) -> None:
    """영상 스티칭 공통 옵션."""

    cmd.add_argument("--min-matches", type=int, default=80)
    cmd.add_argument("--min-inliers", type=int, default=30)
    cmd.add_argument("--ratio-test", type=float, default=0.75)
    cmd.add_argument("--ransac-thresh", type=float, default=5.0)
    cmd.add_argument("--calib-start-sec", type=float, default=0.0)
    cmd.add_argument("--calib-end-sec", type=float, default=10.0)
    cmd.add_argument("--calib-step-sec", type=float, default=1.0)

    # 성능 모드: 사용자가 속도/품질을 쉽게 선택할 수 있게 단순화
    cmd.add_argument(
        "--perf-mode",
        choices=["quality", "balanced", "fast"],
        default="quality",
        help="quality(기본), balanced, fast",
    )
    # 필요 시 perf-mode 위에 덮어쓰는 수동 스케일
    cmd.add_argument("--process-scale", type=float, default=None, help="Optional manual scale (e.g. 0.5)")

    # H 저장/재사용 모드
    cmd.add_argument(
        "--homography-mode",
        choices=["off", "auto", "reuse", "refresh"],
        default="off",
        help="off/auto/reuse/refresh",
    )
    cmd.add_argument("--homography-file", default=None, help="Path to homography json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual smartphone video stitching MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 수동 모드
    video_cmd = subparsers.add_parser("video", help="Stitch two videos (offline)")
    video_cmd.add_argument("--left", required=True, help="Path to left video")
    video_cmd.add_argument("--right", required=True, help="Path to right video")
    video_cmd.add_argument("--out", default="stitched.mp4", help="Output stitched video path")
    video_cmd.add_argument("--report", default="report.json", help="Output report json path")
    video_cmd.add_argument("--debug-dir", default="debug", help="Debug artifact directory")
    video_cmd.add_argument("--max-duration-sec", type=float, default=30.0, help="Maximum stitched duration")
    _add_video_common_args(video_cmd)

    # 프리셋 모드
    preset_help = "Preset video stitching with auto input/output naming"
    for preset in ("video-10s", "video-30s", "video-full"):
        preset_cmd = subparsers.add_parser(preset, help=preset_help)
        preset_cmd.add_argument("--pair", default=None, help="Pair prefix, e.g. video10")
        preset_cmd.add_argument("--left", default=None, help="Optional left video path")
        preset_cmd.add_argument("--right", default=None, help="Optional right video path")
        preset_cmd.add_argument("--input-dir", default="input/videos")
        preset_cmd.add_argument("--output-dir", default="output/videos")
        preset_cmd.add_argument("--debug-root", default="output/debug")
        _add_video_common_args(preset_cmd)

    serve_cmd = subparsers.add_parser("serve", help="Run local API + worker server")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8080)
    serve_cmd.add_argument("--storage-dir", default="storage")
    return parser.parse_args()


def _derive_pair_base(left_path: Path) -> str:
    stem = left_path.stem
    if "_left" in stem:
        return stem.replace("_left", "", 1)
    return stem


def _resolve_pair_from_prefix(input_dir: Path, pair: str) -> Tuple[Path, Path]:
    candidates = sorted(input_dir.glob(f"{pair}_left*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for left in candidates:
        right = left.with_name(left.name.replace("_left", "_right", 1))
        if right.exists():
            return left, right
    raise FileNotFoundError(f"cannot find matched left/right for pair '{pair}' in {input_dir}")


def _resolve_latest_pair(input_dir: Path) -> Tuple[Path, Path]:
    left_candidates = sorted(input_dir.glob("*_left*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for left in left_candidates:
        right = left.with_name(left.name.replace("_left", "_right", 1))
        if right.exists():
            return left, right
    raise FileNotFoundError(f"cannot find any matched *_left/*_right pair in {input_dir}")


def _resolve_preset_inputs(args: argparse.Namespace) -> Tuple[Path, Path, str]:
    input_dir = Path(args.input_dir)
    if args.left and args.right:
        left = Path(args.left)
        right = Path(args.right)
        if not left.exists() or not right.exists():
            raise FileNotFoundError("provided --left/--right path does not exist")
        return left, right, _derive_pair_base(left)
    if args.pair:
        left, right = _resolve_pair_from_prefix(input_dir=input_dir, pair=args.pair)
        return left, right, args.pair
    left, right = _resolve_latest_pair(input_dir=input_dir)
    return left, right, _derive_pair_base(left)


def _resolve_perf_profile(perf_mode: str, process_scale: float | None) -> tuple[float, int]:
    """
    성능 모드 -> (스케일, max_features)
    process_scale을 주면 스케일만 수동으로 덮어쓴다.
    """

    mode = (perf_mode or "quality").lower()
    profiles = {
        "quality": (1.0, 4000),
        "balanced": (0.75, 2800),
        "fast": (0.5, 1800),
    }
    scale, max_features = profiles.get(mode, profiles["quality"])
    if process_scale is not None:
        scale = float(process_scale)
    if scale <= 0:
        raise ValueError("process_scale must be > 0")
    return scale, int(max_features)


def _run_video(
    left_path: Path,
    right_path: Path,
    output_path: Path,
    report_path: Path,
    debug_dir: Path,
    min_matches: int,
    min_inliers: int,
    ratio_test: float,
    ransac_thresh: float,
    max_duration_sec: float,
    calib_start_sec: float,
    calib_end_sec: float,
    calib_step_sec: float,
    perf_mode: str,
    process_scale: float | None,
    homography_mode: str,
    homography_file: str | None,
) -> None:
    try:
        from stitching.video_stitching import VideoConfig, stitch_videos
    except ModuleNotFoundError as exc:
        if exc.name == "cv2":
            print("Missing dependency: opencv-python. Install with `python -m pip install -r requirements.txt`.")
            raise SystemExit(2)
        raise

    scale, max_features = _resolve_perf_profile(perf_mode=perf_mode, process_scale=process_scale)
    homography_path = Path(homography_file) if homography_file else None

    config = VideoConfig(
        min_matches=min_matches,
        min_inliers=min_inliers,
        ratio_test=ratio_test,
        ransac_reproj_threshold=ransac_thresh,
        max_duration_sec=max_duration_sec,
        calib_start_sec=calib_start_sec,
        calib_end_sec=calib_end_sec,
        calib_step_sec=calib_step_sec,
        max_features=max_features,
        perf_mode=perf_mode,
        process_scale=scale,
        homography_mode=homography_mode,
        homography_file=homography_path,
    )
    stitch_videos(
        left_path=left_path,
        right_path=right_path,
        output_path=output_path,
        report_path=report_path,
        debug_dir=debug_dir,
        config=config,
    )


def main() -> int:
    args = parse_args()

    if args.command == "video":
        _run_video(
            left_path=Path(args.left),
            right_path=Path(args.right),
            output_path=Path(args.out),
            report_path=Path(args.report),
            debug_dir=Path(args.debug_dir),
            min_matches=args.min_matches,
            min_inliers=args.min_inliers,
            ratio_test=args.ratio_test,
            ransac_thresh=args.ransac_thresh,
            max_duration_sec=args.max_duration_sec,
            calib_start_sec=args.calib_start_sec,
            calib_end_sec=args.calib_end_sec,
            calib_step_sec=args.calib_step_sec,
            perf_mode=args.perf_mode,
            process_scale=args.process_scale,
            homography_mode=args.homography_mode,
            homography_file=args.homography_file,
        )
        return 0

    if args.command in {"video-10s", "video-30s", "video-full"}:
        left_path, right_path, pair_base = _resolve_preset_inputs(args)
        output_dir = Path(args.output_dir)
        debug_root = Path(args.debug_root)

        if args.command == "video-10s":
            preset = "10s"
            max_duration_sec = 10.0
        elif args.command == "video-30s":
            preset = "30s"
            max_duration_sec = 30.0
        else:
            preset = "full"
            max_duration_sec = 0.0

        output_path = output_dir / f"{pair_base}_{preset}_stitched.mp4"
        report_path = output_dir / f"{pair_base}_{preset}_report.json"
        debug_dir = debug_root / f"{pair_base}_{preset}"

        _run_video(
            left_path=left_path,
            right_path=right_path,
            output_path=output_path,
            report_path=report_path,
            debug_dir=debug_dir,
            min_matches=args.min_matches,
            min_inliers=args.min_inliers,
            ratio_test=args.ratio_test,
            ransac_thresh=args.ransac_thresh,
            max_duration_sec=max_duration_sec,
            calib_start_sec=args.calib_start_sec,
            calib_end_sec=args.calib_end_sec,
            calib_step_sec=args.calib_step_sec,
            perf_mode=args.perf_mode,
            process_scale=args.process_scale,
            homography_mode=args.homography_mode,
            homography_file=args.homography_file,
        )
        print(f"preset={preset}")
        print(f"left={left_path}")
        print(f"right={right_path}")
        print(f"out={output_path}")
        print(f"report={report_path}")
        print(f"debug={debug_dir}")
        return 0

    if args.command == "serve":
        try:
            from stitching.job_service import run_server
        except ModuleNotFoundError as exc:
            if exc.name == "cv2":
                print("Missing dependency: opencv-python. Install with `python -m pip install -r requirements.txt`.")
                return 2
            raise
        run_server(host=args.host, port=args.port, storage_dir=Path(args.storage_dir))
        return 0

    return 1
