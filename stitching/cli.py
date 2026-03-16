from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


CURRENT_MAIN_PATH_NOTE = (
    "Current Python entrypoints: native-calibrate -> native-runtime."
)


def _bootstrap_runtime_config(argv: list[str]) -> tuple[argparse.ArgumentParser, list[str]]:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument(
        "--runtime-config",
        help="Optional site config JSON path. Overrides config/runtime.json.",
    )
    bootstrap.add_argument(
        "--runtime-profile",
        help="Optional profile name under config/profiles/<name>.json. Merged on top of the base runtime config.",
    )
    known_args, remaining = bootstrap.parse_known_args(argv)
    if known_args.runtime_config:
        os.environ["HOGAK_RUNTIME_CONFIG"] = str(known_args.runtime_config)
    if known_args.runtime_profile:
        os.environ["HOGAK_RUNTIME_PROFILE"] = str(known_args.runtime_profile)
    return bootstrap, remaining


def _add_native_calibration_args(
    cmd: argparse.ArgumentParser,
    *,
    include_stream_args: bool = True,
) -> None:
    from stitching.project_defaults import (
        DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR,
        DEFAULT_NATIVE_HOMOGRAPHY_PATH,
        default_left_rtsp,
        default_right_rtsp,
    )

    if include_stream_args:
        cmd.add_argument(
            "--left-rtsp",
            default=default_left_rtsp(),
            help="Left RTSP URL (default: config/runtime.json or HOGAK_LEFT_RTSP)",
        )
        cmd.add_argument(
            "--right-rtsp",
            default=default_right_rtsp(),
            help="Right RTSP URL (default: config/runtime.json or HOGAK_RIGHT_RTSP)",
        )
        cmd.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
        cmd.add_argument("--rtsp-timeout-sec", type=float, default=10.0)
    cmd.add_argument(
        "--out",
        default=DEFAULT_NATIVE_HOMOGRAPHY_PATH,
        help="Output homography JSON path (default: config/runtime.json)",
    )
    cmd.add_argument(
        "--debug-dir",
        default=DEFAULT_NATIVE_CALIBRATION_DEBUG_DIR,
        help="Calibration debug image directory (default: config/runtime.json)",
    )
    cmd.add_argument(
        "--warmup-frames",
        type=int,
        default=45,
        help="Frames to read before selecting representative images",
    )
    cmd.add_argument("--process-scale", type=float, default=1.0, help="Calibration frame scale")
    cmd.add_argument(
        "--calibration-mode",
        choices=["assisted", "manual", "auto"],
        default="assisted",
        help="assisted(default): click any number of matching points, then auto-boost around them",
    )
    cmd.add_argument(
        "--assisted-reproj-threshold",
        type=float,
        default=12.0,
        help="Max reprojection error in pixels for seed-guided auto match reinforcement",
    )
    cmd.add_argument(
        "--assisted-max-auto-matches",
        type=int,
        default=600,
        help="Max number of auto-reinforced matches kept in assisted mode",
    )
    cmd.add_argument(
        "--match-backend",
        choices=["auto", "classic", "deep"],
        default="auto",
        help="Match backend for auto/assisted reinforcement. auto falls back to classic if deep is unavailable.",
    )
    cmd.add_argument(
        "--deep-backend",
        choices=["auto", "lightglue", "loftr"],
        default="auto",
        help="Preferred deep matcher backend when match-backend uses deep. auto tries LightGlue first, then LoFTR.",
    )
    cmd.add_argument(
        "--launch-runtime",
        action="store_true",
        help="Launch native runtime immediately after calibration succeeds",
    )
    cmd.add_argument(
        "--runtime-script",
        default="",
        help="Deprecated compatibility option. Runtime now launches directly via `python -m stitching.cli native-runtime`.",
    )
    cmd.add_argument("--min-matches", type=int, default=40)
    cmd.add_argument("--min-inliers", type=int, default=20)
    cmd.add_argument("--ratio-test", type=float, default=0.75)
    cmd.add_argument("--ransac-thresh", type=float, default=5.0)
    cmd.add_argument("--max-features", type=int, default=4000)

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    bootstrap, remaining = _bootstrap_runtime_config(argv)
    parser = argparse.ArgumentParser(
        description="Dual smartphone video stitching project CLI",
        epilog=CURRENT_MAIN_PATH_NOTE,
        parents=[bootstrap],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    native_calib_cmd = subparsers.add_parser(
        "native-calibrate",
        help="Current main path: capture RTSP frame pair and save runtime homography",
    )
    _add_native_calibration_args(native_calib_cmd)

    native_cmd = subparsers.add_parser(
        "native-runtime",
        help="Current main path: launch native runtime monitor and optional viewers",
    )
    from stitching.native_runtime_cli import add_native_runtime_args

    add_native_runtime_args(native_cmd)
    return parser.parse_args(remaining)


def main() -> int:
    args = parse_args()

    if args.command == "native-calibrate":
        from stitching.native_calibration import run_native_calibration

        return int(run_native_calibration(args))

    if args.command == "native-runtime":
        from stitching.native_runtime_cli import run_native_runtime_monitor

        return int(run_native_runtime_monitor(args))

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
