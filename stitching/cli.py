from __future__ import annotations

import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual smartphone stitching MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    image_cmd = subparsers.add_parser("image", help="Stitch two images")
    image_cmd.add_argument("--left", required=True, help="Path to left image")
    image_cmd.add_argument("--right", required=True, help="Path to right image")
    image_cmd.add_argument("--out", default="stitched_image.png", help="Output stitched image path")
    image_cmd.add_argument("--report", default="report.json", help="Output report json path")
    image_cmd.add_argument("--debug-dir", default="debug", help="Debug artifact directory")
    image_cmd.add_argument("--min-matches", type=int, default=80)
    image_cmd.add_argument("--min-inliers", type=int, default=30)
    image_cmd.add_argument("--ratio-test", type=float, default=0.75)
    image_cmd.add_argument("--ransac-thresh", type=float, default=5.0)

    video_cmd = subparsers.add_parser("video", help="Stitch two videos (offline)")
    video_cmd.add_argument("--left", required=True, help="Path to left video")
    video_cmd.add_argument("--right", required=True, help="Path to right video")
    video_cmd.add_argument("--out", default="stitched.mp4", help="Output stitched video path")
    video_cmd.add_argument("--report", default="report.json", help="Output report json path")
    video_cmd.add_argument("--debug-dir", default="debug", help="Debug artifact directory")
    video_cmd.add_argument("--min-matches", type=int, default=80)
    video_cmd.add_argument("--min-inliers", type=int, default=30)
    video_cmd.add_argument("--ratio-test", type=float, default=0.75)
    video_cmd.add_argument("--ransac-thresh", type=float, default=5.0)
    video_cmd.add_argument(
        "--max-duration-sec",
        type=float,
        default=30.0,
        help="Maximum stitched duration (seconds)",
    )
    video_cmd.add_argument(
        "--sync-sample-sec",
        type=float,
        default=5.0,
        help="Duration to sample for synchronization estimation",
    )

    serve_cmd = subparsers.add_parser("serve", help="Run local API + worker server")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8080)
    serve_cmd.add_argument("--storage-dir", default="storage")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.command == "image":
        try:
            from stitching.image_stitching import StitchConfig, stitch_images
        except ModuleNotFoundError as exc:
            if exc.name == "cv2":
                print("Missing dependency: opencv-python. Install with `python -m pip install -r requirements.txt`.")
                return 2
            raise

        config = StitchConfig(
            min_matches=args.min_matches,
            min_inliers=args.min_inliers,
            ratio_test=args.ratio_test,
            ransac_reproj_threshold=args.ransac_thresh,
        )
        stitch_images(
            left_path=Path(args.left),
            right_path=Path(args.right),
            output_path=Path(args.out),
            report_path=Path(args.report),
            debug_dir=Path(args.debug_dir),
            config=config,
        )
        return 0

    if args.command == "video":
        try:
            from stitching.video_stitching import VideoConfig, stitch_videos
        except ModuleNotFoundError as exc:
            if exc.name == "cv2":
                print("Missing dependency: opencv-python. Install with `python -m pip install -r requirements.txt`.")
                return 2
            raise

        config = VideoConfig(
            min_matches=args.min_matches,
            min_inliers=args.min_inliers,
            ratio_test=args.ratio_test,
            ransac_reproj_threshold=args.ransac_thresh,
            max_duration_sec=args.max_duration_sec,
            sync_sample_sec=args.sync_sample_sec,
        )
        stitch_videos(
            left_path=Path(args.left),
            right_path=Path(args.right),
            output_path=Path(args.out),
            report_path=Path(args.report),
            debug_dir=Path(args.debug_dir),
            config=config,
        )
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
