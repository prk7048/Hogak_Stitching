from __future__ import annotations

import argparse
from pathlib import Path
from typing import Tuple

from stitching.perf_profiles import resolve_perf_profile


LEGACY_COMMANDS: tuple[str, ...] = (
    "video",
    "video-10s",
    "video-30s",
    "video-full",
    "live",
    "serve",
    "gui",
    "ffmpeg-env",
    "desktop",
)


def is_legacy_command(command: str | None) -> bool:
    return str(command or "").strip() in LEGACY_COMMANDS


def add_legacy_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    video_cmd = subparsers.add_parser("video", help="Legacy: stitch two videos offline")
    video_cmd.add_argument("--left", required=True, help="Path to left video")
    video_cmd.add_argument("--right", required=True, help="Path to right video")
    video_cmd.add_argument("--out", default="stitched.mp4", help="Output stitched video path")
    video_cmd.add_argument("--report", default="report.json", help="Output report json path")
    video_cmd.add_argument("--debug-dir", default="debug", help="Debug artifact directory")
    video_cmd.add_argument("--max-duration-sec", type=float, default=30.0, help="Maximum stitched duration")
    _add_video_common_args(video_cmd)

    preset_help = "Legacy: preset offline video stitching with auto input/output naming"
    for preset in ("video-10s", "video-30s", "video-full"):
        preset_cmd = subparsers.add_parser(preset, help=preset_help)
        preset_cmd.add_argument("--pair", default=None, help="Pair prefix, e.g. video10")
        preset_cmd.add_argument("--left", default=None, help="Optional left video path")
        preset_cmd.add_argument("--right", default=None, help="Optional right video path")
        preset_cmd.add_argument("--input-dir", default="input/videos")
        preset_cmd.add_argument("--output-dir", default="output/videos")
        preset_cmd.add_argument("--debug-root", default="output/debug")
        _add_video_common_args(preset_cmd)

    live_cmd = subparsers.add_parser("live", help="Legacy: stitch two RTSP streams directly in Python")
    live_cmd.add_argument("--left-rtsp", required=True, help="Left RTSP URL")
    live_cmd.add_argument("--right-rtsp", required=True, help="Right RTSP URL")
    live_cmd.add_argument("--out", default="output/videos/live_stitched.mp4")
    live_cmd.add_argument(
        "--output-runtime",
        choices=["opencv", "ffmpeg"],
        default="opencv",
        help="Output writer runtime. ffmpeg enables direct FFmpeg/NVENC writer.",
    )
    live_cmd.add_argument(
        "--output-target",
        default="",
        help="Optional override output target. May be a file path or stream URL when using ffmpeg output runtime.",
    )
    live_cmd.add_argument("--output-codec", default="h264_nvenc", help="FFmpeg output codec")
    live_cmd.add_argument("--output-bitrate", default="12M", help="FFmpeg output bitrate")
    live_cmd.add_argument("--output-preset", default="p4", help="FFmpeg output preset")
    live_cmd.add_argument("--output-muxer", default="", help="Optional FFmpeg output muxer override")
    live_cmd.add_argument("--report", default="output/videos/live_report.json")
    live_cmd.add_argument("--debug-dir", default="output/debug/live")
    live_cmd.add_argument("--max-duration-sec", type=float, default=0.0, help="0 means run until stopped")
    live_cmd.add_argument("--output-fps", type=float, default=20.0)
    live_cmd.add_argument("--calib-max-attempts", type=int, default=180)
    live_cmd.add_argument("--max-read-failures", type=int, default=45)
    live_cmd.add_argument("--reconnect-cooldown-sec", type=float, default=1.0)
    live_cmd.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    live_cmd.add_argument("--rtsp-timeout-sec", type=float, default=10.0)
    live_cmd.add_argument("--sync-buffer-sec", type=float, default=2.0)
    live_cmd.add_argument("--sync-match-max-delta-ms", type=float, default=80.0)
    live_cmd.add_argument("--sync-manual-offset-ms", type=float, default=0.0)
    live_cmd.add_argument("--sync-no-pair-timeout-sec", type=float, default=8.0)
    live_cmd.add_argument("--sync-pair-mode", choices=["latest", "oldest", "service"], default="latest")
    live_cmd.add_argument("--max-live-lag-sec", type=float, default=1.0)
    live_cmd.add_argument("--preview", action="store_true", help="Show live preview window")
    _add_video_common_args(live_cmd)

    serve_cmd = subparsers.add_parser("serve", help="Legacy: run local API + worker server")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8080)
    serve_cmd.add_argument("--storage-dir", default="storage")

    gui_cmd = subparsers.add_parser("gui", help="Legacy: run the Gradio GUI")
    gui_cmd.add_argument("--host", default="127.0.0.1")
    gui_cmd.add_argument("--port", type=int, default=7860)
    gui_cmd.add_argument("--share", action="store_true")

    ffmpeg_env_cmd = subparsers.add_parser("ffmpeg-env", help="Utility: inspect ffmpeg/ffprobe runtime availability")
    ffmpeg_env_cmd.add_argument("--rtsp-url", default="", help="Optional RTSP URL to print ffprobe command for")
    ffmpeg_env_cmd.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    ffmpeg_env_cmd.add_argument("--rtsp-timeout-sec", type=float, default=10.0)

    desktop_cmd = subparsers.add_parser("desktop", help="Legacy: run desktop RTSP live stitching preview")
    desktop_cmd.add_argument("--left-rtsp", default="", help="Left RTSP URL")
    desktop_cmd.add_argument("--right-rtsp", default="", help="Right RTSP URL")
    desktop_cmd.add_argument(
        "--input-runtime",
        choices=["opencv", "ffmpeg", "ffmpeg-cpu", "ffmpeg-cuda"],
        default="opencv",
        help="RTSP input runtime. ffmpeg-cuda uses direct ffmpeg subprocess with CUDA decode when possible.",
    )
    desktop_cmd.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    desktop_cmd.add_argument("--rtsp-timeout-sec", type=float, default=10.0)
    desktop_cmd.add_argument("--reconnect-cooldown-sec", type=float, default=1.0)
    desktop_cmd.add_argument("--sync-buffer-sec", type=float, default=0.6)
    desktop_cmd.add_argument("--sync-match-max-delta-ms", type=float, default=35.0)
    desktop_cmd.add_argument("--sync-manual-offset-ms", type=float, default=0.0)
    desktop_cmd.add_argument("--sync-pair-mode", choices=["none", "latest", "oldest", "service"], default="none")
    desktop_cmd.add_argument("--max-display-width", type=int, default=2880)
    desktop_cmd.add_argument("--process-scale", type=float, default=1.0, help="Preview processing scale")
    desktop_cmd.add_argument("--min-matches", type=int, default=20)
    desktop_cmd.add_argument("--min-inliers", type=int, default=8)
    desktop_cmd.add_argument("--ratio-test", type=float, default=0.82)
    desktop_cmd.add_argument("--ransac-thresh", type=float, default=6.0)
    desktop_cmd.add_argument("--stitch-every-n", type=int, default=1, help="Run stitching every N frames")
    desktop_cmd.add_argument("--max-features", type=int, default=2800, help="ORB feature count for stitching")
    desktop_cmd.add_argument("--stitch-output-scale", type=float, default=1.0, help="Scale factor for stitched panel")
    desktop_cmd.add_argument("--gpu-mode", choices=["off", "auto", "on"], default="on")
    desktop_cmd.add_argument("--gpu-device", type=int, default=0)
    desktop_cmd.add_argument("--cpu-threads", type=int, default=0, help="0 uses all logical CPU cores")
    desktop_cmd.add_argument("--manual-points", type=int, default=4)
    desktop_cmd.add_argument("--headless-benchmark", action="store_true")
    desktop_cmd.add_argument("--benchmark-log-interval-sec", type=float, default=1.0)
    desktop_cmd.add_argument("--benchmark-duration-sec", type=float, default=0.0)


def dispatch_legacy_command(args: argparse.Namespace) -> int:
    if args.command == "video":
        _run_video_from_args(
            args=args,
            left_path=Path(args.left),
            right_path=Path(args.right),
            output_path=Path(args.out),
            report_path=Path(args.report),
            debug_dir=Path(args.debug_dir),
            max_duration_sec=args.max_duration_sec,
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

        _run_video_from_args(
            args=args,
            left_path=left_path,
            right_path=right_path,
            output_path=output_path,
            report_path=report_path,
            debug_dir=debug_dir,
            max_duration_sec=max_duration_sec,
        )
        print(f"preset={preset}")
        print(f"left={left_path}")
        print(f"right={right_path}")
        print(f"out={output_path}")
        print(f"report={report_path}")
        print(f"debug={debug_dir}")
        return 0

    if args.command == "serve":
        from stitching.job_service import run_server

        run_server(host=args.host, port=args.port, storage_dir=Path(args.storage_dir))
        return 0

    if args.command == "gui":
        from stitching.gui_app import run_gui

        run_gui(host=args.host, port=args.port, share=bool(args.share))
        return 0

    if args.command == "ffmpeg-env":
        from stitching.ffmpeg_runtime import (
            FfmpegRuntimeError,
            NvencEncodeSpec,
            RtspDecodeSpec,
            build_ffprobe_stream_command,
            build_nvenc_stream_command,
            build_rtsp_decode_command,
            resolve_binaries,
        )

        try:
            bins = resolve_binaries()
        except FfmpegRuntimeError as exc:
            print(str(exc))
            return 2
        print(f"ffmpeg={bins.ffmpeg}")
        print(f"ffprobe={bins.ffprobe or 'NOT_FOUND'}")
        if args.rtsp_url:
            decode_cmd = build_rtsp_decode_command(
                ffmpeg_bin=bins.ffmpeg,
                spec=RtspDecodeSpec(
                    url=args.rtsp_url,
                    transport=args.rtsp_transport,
                    timeout_sec=float(args.rtsp_timeout_sec),
                ),
            )
            print("decode_cmd=" + " ".join(decode_cmd))
            if bins.ffprobe:
                probe_cmd = build_ffprobe_stream_command(
                    ffprobe_bin=bins.ffprobe,
                    url=args.rtsp_url,
                    transport=args.rtsp_transport,
                    timeout_sec=float(args.rtsp_timeout_sec),
                )
                print("probe_cmd=" + " ".join(probe_cmd))
            sample_nvenc_cmd = build_nvenc_stream_command(
                ffmpeg_bin=bins.ffmpeg,
                spec=NvencEncodeSpec(
                    width=1920,
                    height=1080,
                    fps=30.0,
                    output_url="rtsp://example.invalid/live/panorama",
                ),
            )
            print("sample_nvenc_cmd=" + " ".join(sample_nvenc_cmd))
        return 0

    if args.command == "live":
        _run_live_from_args(args)
        return 0

    if args.command == "desktop":
        from stitching.desktop_app import DesktopConfig, run_desktop

        cfg = DesktopConfig(
            left_rtsp=args.left_rtsp,
            right_rtsp=args.right_rtsp,
            input_runtime=args.input_runtime,
            rtsp_transport=args.rtsp_transport,
            rtsp_timeout_sec=max(0.1, float(args.rtsp_timeout_sec)),
            reconnect_cooldown_sec=max(0.2, float(args.reconnect_cooldown_sec)),
            sync_buffer_sec=max(0.2, float(args.sync_buffer_sec)),
            sync_match_max_delta_ms=max(1.0, float(args.sync_match_max_delta_ms)),
            sync_manual_offset_ms=float(args.sync_manual_offset_ms),
            sync_pair_mode=args.sync_pair_mode,
            max_display_width=max(640, int(args.max_display_width)),
            process_scale=max(0.1, float(args.process_scale)),
            min_matches=max(8, int(args.min_matches)),
            min_inliers=max(6, int(args.min_inliers)),
            ratio_test=float(args.ratio_test),
            ransac_thresh=float(args.ransac_thresh),
            stitch_every_n=max(1, int(args.stitch_every_n)),
            max_features=max(500, int(args.max_features)),
            stitch_output_scale=max(0.1, float(args.stitch_output_scale)),
            gpu_mode=args.gpu_mode,
            gpu_device=max(0, int(args.gpu_device)),
            cpu_threads=max(0, int(args.cpu_threads)),
            manual_points=max(4, int(args.manual_points)),
            headless_benchmark=bool(args.headless_benchmark),
            benchmark_log_interval_sec=max(0.1, float(args.benchmark_log_interval_sec)),
            benchmark_duration_sec=max(0.0, float(args.benchmark_duration_sec)),
        )
        return int(run_desktop(cfg))

    raise ValueError(f"unsupported legacy command: {args.command}")


def _add_video_common_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--min-matches", type=int, default=40)
    cmd.add_argument("--min-inliers", type=int, default=20)
    cmd.add_argument("--ratio-test", type=float, default=0.75)
    cmd.add_argument("--ransac-thresh", type=float, default=5.0)
    cmd.add_argument("--calib-start-sec", type=float, default=0.0)
    cmd.add_argument("--calib-end-sec", type=float, default=10.0)
    cmd.add_argument("--calib-step-sec", type=float, default=1.0)
    cmd.add_argument("--perf-mode", choices=["quality", "balanced", "fast"], default="quality")
    cmd.add_argument("--process-scale", type=float, default=None, help="Optional manual scale")
    cmd.add_argument("--homography-mode", choices=["off", "auto", "reuse", "refresh"], default="off")
    cmd.add_argument("--homography-file", default=None, help="Path to homography json")
    cmd.add_argument("--adaptive-seam", choices=["on", "off"], default="off")
    cmd.add_argument("--seam-update-interval", type=int, default=12)
    cmd.add_argument("--seam-temporal-penalty", type=float, default=1.5)
    cmd.add_argument("--seam-motion-weight", type=float, default=1.5)


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
    adaptive_seam: str,
    seam_update_interval: int,
    seam_temporal_penalty: float,
    seam_motion_weight: float,
) -> None:
    from stitching.video_stitching import VideoConfig, stitch_videos

    scale, max_features = resolve_perf_profile(perf_mode=perf_mode, process_scale=process_scale)
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
        adaptive_seam=(adaptive_seam != "off"),
        seam_update_interval=max(1, int(seam_update_interval)),
        seam_temporal_penalty=max(0.0, float(seam_temporal_penalty)),
        seam_motion_weight=max(0.0, float(seam_motion_weight)),
    )
    stitch_videos(
        left_path=left_path,
        right_path=right_path,
        output_path=output_path,
        report_path=report_path,
        debug_dir=debug_dir,
        config=config,
    )


def _run_video_from_args(
    args: argparse.Namespace,
    left_path: Path,
    right_path: Path,
    output_path: Path,
    report_path: Path,
    debug_dir: Path,
    max_duration_sec: float,
) -> None:
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
        adaptive_seam=args.adaptive_seam,
        seam_update_interval=args.seam_update_interval,
        seam_temporal_penalty=args.seam_temporal_penalty,
        seam_motion_weight=args.seam_motion_weight,
    )


def _run_live_from_args(args: argparse.Namespace) -> None:
    from stitching.live_stitching import LiveConfig, stitch_live_rtsp

    scale, max_features = resolve_perf_profile(perf_mode=args.perf_mode, process_scale=args.process_scale)
    config = LiveConfig(
        min_matches=args.min_matches,
        min_inliers=args.min_inliers,
        ratio_test=args.ratio_test,
        ransac_reproj_threshold=args.ransac_thresh,
        max_features=max_features,
        process_scale=scale,
        max_duration_sec=args.max_duration_sec,
        output_fps=args.output_fps,
        output_runtime=args.output_runtime,
        output_target_override=str(args.output_target or ""),
        output_codec=str(args.output_codec),
        output_bitrate=str(args.output_bitrate),
        output_preset=str(args.output_preset),
        output_muxer=str(args.output_muxer),
        calib_max_attempts=args.calib_max_attempts,
        max_read_failures=args.max_read_failures,
        reconnect_cooldown_sec=args.reconnect_cooldown_sec,
        rtsp_transport=args.rtsp_transport,
        rtsp_timeout_sec=max(0.1, float(args.rtsp_timeout_sec)),
        sync_buffer_sec=max(0.5, float(args.sync_buffer_sec)),
        sync_match_max_delta_ms=max(1.0, float(args.sync_match_max_delta_ms)),
        sync_manual_offset_ms=float(args.sync_manual_offset_ms),
        sync_no_pair_timeout_sec=max(1.0, float(args.sync_no_pair_timeout_sec)),
        sync_pair_mode=args.sync_pair_mode,
        max_live_lag_sec=max(0.0, float(args.max_live_lag_sec)),
        adaptive_seam=(args.adaptive_seam != "off"),
        seam_update_interval=max(1, int(args.seam_update_interval)),
        seam_temporal_penalty=max(0.0, float(args.seam_temporal_penalty)),
        seam_motion_weight=max(0.0, float(args.seam_motion_weight)),
        preview=bool(args.preview),
    )
    stitch_live_rtsp(
        left_rtsp=args.left_rtsp,
        right_rtsp=args.right_rtsp,
        output_path=Path(args.out),
        report_path=Path(args.report),
        debug_dir=Path(args.debug_dir),
        config=config,
    )
