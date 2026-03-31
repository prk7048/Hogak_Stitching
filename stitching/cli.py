from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from stitching.runtime_site_config import RuntimeSiteConfigError


CURRENT_MAIN_PATH_NOTE = (
    "Current Python entrypoints: prepare-runtime/native-calibrate -> run-runtime/native-runtime -> validate-runtime/native-validate, plus operator-server for the FastAPI operator surface."
)

COMMAND_ALIASES = {
    "prepare-runtime": "native-calibrate",
    "run-runtime": "native-runtime",
    "validate-runtime": "native-validate",
}


def _normalize_command_name(command: str) -> str:
    return COMMAND_ALIASES.get(str(command), str(command))


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
        DEFAULT_NATIVE_DISTORTION_AUTO_SAVE,
        DEFAULT_NATIVE_DISTORTION_CAMERA_MODEL,
        DEFAULT_NATIVE_DISTORTION_HORIZONTAL_FOV_DEG,
        DEFAULT_NATIVE_DISTORTION_LENS_MODEL_HINT,
        DEFAULT_NATIVE_DISTORTION_MODE,
        DEFAULT_NATIVE_DISTORTION_VERTICAL_FOV_DEG,
        DEFAULT_NATIVE_HOMOGRAPHY_PATH,
        DEFAULT_NATIVE_LEFT_DISTORTION_FILE,
        DEFAULT_NATIVE_RIGHT_DISTORTION_FILE,
        DEFAULT_NATIVE_USE_SAVED_DISTORTION,
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
        "--distortion-mode",
        choices=["off", "runtime-lines"],
        default=DEFAULT_NATIVE_DISTORTION_MODE,
        help="Compatibility flag. Distortion is currently disabled and calibration uses raw images.",
    )
    cmd.add_argument(
        "--use-saved-distortion",
        dest="use_saved_distortion",
        action="store_true",
        default=DEFAULT_NATIVE_USE_SAVED_DISTORTION,
        help="Compatibility flag. Ignored because distortion is currently disabled.",
    )
    cmd.add_argument(
        "--no-use-saved-distortion",
        dest="use_saved_distortion",
        action="store_false",
        help="Compatibility flag. Ignored because distortion is currently disabled.",
    )
    cmd.add_argument(
        "--distortion-auto-save",
        dest="distortion_auto_save",
        action="store_true",
        default=DEFAULT_NATIVE_DISTORTION_AUTO_SAVE,
        help="Compatibility flag. Ignored because distortion is currently disabled.",
    )
    cmd.add_argument(
        "--no-distortion-auto-save",
        dest="distortion_auto_save",
        action="store_false",
        help="Compatibility flag. Ignored because distortion is currently disabled.",
    )
    cmd.add_argument("--left-distortion-file", default=DEFAULT_NATIVE_LEFT_DISTORTION_FILE)
    cmd.add_argument("--right-distortion-file", default=DEFAULT_NATIVE_RIGHT_DISTORTION_FILE)
    cmd.add_argument(
        "--distortion-lens-model-hint",
        choices=["auto", "pinhole", "fisheye"],
        default=DEFAULT_NATIVE_DISTORTION_LENS_MODEL_HINT,
        help="Optional prior for distortion fitting. auto evaluates pinhole and fisheye candidates.",
    )
    cmd.add_argument(
        "--distortion-horizontal-fov-deg",
        type=float,
        default=DEFAULT_NATIVE_DISTORTION_HORIZONTAL_FOV_DEG,
        help="Optional horizontal FOV prior in degrees for guided distortion fit.",
    )
    cmd.add_argument(
        "--distortion-vertical-fov-deg",
        type=float,
        default=DEFAULT_NATIVE_DISTORTION_VERTICAL_FOV_DEG,
        help="Optional vertical FOV prior in degrees for guided distortion fit.",
    )
    cmd.add_argument(
        "--distortion-camera-model",
        default=DEFAULT_NATIVE_DISTORTION_CAMERA_MODEL,
        help="Optional camera model label recorded with the saved distortion artifact.",
    )
    cmd.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip the final calibration review window. Used by runtime-managed automatic recalibration.",
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
        choices=["classic"],
        default="classic",
        help="Calibration match backend. The current path uses the classic matcher only.",
    )
    cmd.add_argument(
        "--launch-runtime",
        action="store_true",
        help="Launch native runtime immediately after calibration succeeds",
    )
    cmd.add_argument("--min-matches", type=int, default=40)
    cmd.add_argument("--min-inliers", type=int, default=20)
    # ratio_test : 값이 작을수록 정확도가 높아지고, 매칭되는 점이 적어짐
    cmd.add_argument("--ratio-test", type=float, default=0.75)
    # Ransac 알고리즘이 가짜 매칭(outlier)를 골라낼 떄 사용하는 허용 오차거리 (오차가 이 안으로 들어와야 진짜 매칭으로 인정)
    cmd.add_argument("--ransac-thresh", type=float, default=5.0)
    # 이미지 한장에서 찾을 특징점의 최대 갯수
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
        aliases=["prepare-runtime"],
        help="Current main path: capture RTSP frame pair and save runtime homography",
    )
    _add_native_calibration_args(native_calib_cmd)

    native_cmd = subparsers.add_parser(
        "native-runtime",
        aliases=["run-runtime"],
        help="Current main path: launch native runtime monitor and optional viewers",
    )
    from stitching.native_runtime_cli import add_native_runtime_args

    add_native_runtime_args(native_cmd)

    native_validate_cmd = subparsers.add_parser(
        "native-validate",
        aliases=["validate-runtime"],
        help="Run strict fresh 30 validation and write a JSON report",
    )
    from stitching.native_runtime_validation import add_native_validation_args

    add_native_validation_args(native_validate_cmd)

    operator_server_cmd = subparsers.add_parser(
        "operator-server",
        help="Run the unified FastAPI + React operator surface",
    )
    operator_server_cmd.add_argument("--host", default="127.0.0.1", help="FastAPI bind host (default: 127.0.0.1)")
    operator_server_cmd.add_argument("--port", type=int, default=8088, help="FastAPI bind port (default: 8088)")
    args = parser.parse_args(remaining)
    args.command = _normalize_command_name(args.command)
    return args


def main() -> int:
    try:
        args = parse_args()

        if args.command == "native-calibrate":
            from stitching.native_calibration import run_native_calibration

            return int(run_native_calibration(args))

        if args.command == "native-runtime":
            from stitching.native_runtime_cli import run_native_runtime_monitor

            return int(run_native_runtime_monitor(args))

        if args.command == "native-validate":
            from stitching.native_runtime_validation import run_native_validation

            return int(run_native_validation(args))

        if args.command == "operator-server":
            from stitching.runtime_backend import main as run_runtime_backend

            os.environ["HOGAK_BACKEND_HOST"] = str(args.host)
            os.environ["HOGAK_BACKEND_PORT"] = str(int(args.port))
            return int(run_runtime_backend())
    except RuntimeSiteConfigError as exc:
        print(f"runtime config error: {exc}", file=sys.stderr)
        return 2

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
