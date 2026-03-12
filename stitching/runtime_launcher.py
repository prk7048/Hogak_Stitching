from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess


@dataclass(slots=True)
class RuntimeLaunchSpec:
    emit_hello: bool = True
    once: bool = False
    heartbeat_ms: int = 1000
    left_rtsp: str = ""
    right_rtsp: str = ""
    input_runtime: str = "ffmpeg-cuda"
    ffmpeg_bin: str = ""
    homography_file: str = ""
    frame_width: int = 1920
    frame_height: int = 1080
    transport: str = "tcp"
    video_codec: str = "h264"
    timeout_sec: float = 10.0
    reconnect_cooldown_sec: float = 1.0
    output_runtime: str = "none"
    output_target: str = ""
    output_codec: str = "h264_nvenc"
    output_bitrate: str = "12M"
    output_preset: str = "p4"
    output_muxer: str = ""
    output_width: int = 0
    output_height: int = 0
    sync_pair_mode: str = "none"
    sync_match_max_delta_ms: float = 35.0
    sync_manual_offset_ms: float = 0.0
    stitch_output_scale: float = 1.0
    stitch_every_n: int = 1
    gpu_mode: str = "on"
    gpu_device: int = 0
    headless_benchmark: bool = False
    extra_args: tuple[str, ...] = ()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_ffmpeg_binary(explicit_path: str = "") -> Path:
    candidates: list[Path] = []
    if explicit_path.strip():
        candidates.append(Path(explicit_path).expanduser())

    env_path = os.environ.get("FFMPEG_BIN", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    candidates.extend(
        [
            _repo_root() / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe",
            _repo_root() / ".third_party" / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("ffmpeg binary not found. Set FFMPEG_BIN or install ffmpeg.")


def resolve_runtime_binary() -> Path:
    override = os.environ.get("HOGAK_NATIVE_RUNTIME_BIN", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        if path.exists():
            return path

    candidates = (
        _repo_root() / "native_runtime" / "build" / "windows-release" / "Release" / "stitch_runtime.exe",
        _repo_root() / "native_runtime" / "build" / "windows-release" / "src" / "app" / "Release" / "stitch_runtime.exe",
        _repo_root() / "native_runtime" / "build" / "windows-debug" / "Debug" / "stitch_runtime.exe",
        _repo_root() / "native_runtime" / "build" / "windows-debug" / "src" / "app" / "Debug" / "stitch_runtime.exe",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "native runtime binary not found. Build native_runtime first or set HOGAK_NATIVE_RUNTIME_BIN."
    )


def build_runtime_command(spec: RuntimeLaunchSpec | None = None) -> list[str]:
    spec = spec or RuntimeLaunchSpec()
    command = [str(resolve_runtime_binary())]
    if spec.emit_hello:
        command.append("--emit-hello")
    if spec.once:
        command.append("--once")
    command.extend(["--heartbeat-ms", str(max(1, int(spec.heartbeat_ms)))])
    if spec.left_rtsp:
        command.extend(["--left-url", spec.left_rtsp])
    if spec.right_rtsp:
        command.extend(["--right-url", spec.right_rtsp])
    if spec.input_runtime:
        command.extend(["--input-runtime", spec.input_runtime])
    ffmpeg_bin = spec.ffmpeg_bin.strip()
    if ffmpeg_bin:
        command.extend(["--ffmpeg-bin", ffmpeg_bin])
    else:
        command.extend(["--ffmpeg-bin", str(resolve_ffmpeg_binary())])
    if spec.homography_file:
        command.extend(["--homography-file", spec.homography_file])
    command.extend(["--width", str(max(1, int(spec.frame_width)))])
    command.extend(["--height", str(max(1, int(spec.frame_height)))])
    if spec.transport:
        command.extend(["--transport", spec.transport])
    if spec.video_codec:
        command.extend(["--video-codec", spec.video_codec])
    command.extend(["--timeout-sec", f"{float(spec.timeout_sec):.3f}"])
    command.extend(["--reconnect-cooldown-sec", f"{float(spec.reconnect_cooldown_sec):.3f}"])
    if spec.output_runtime:
        command.extend(["--output-runtime", spec.output_runtime])
    if spec.output_target:
        command.extend(["--output-target", spec.output_target])
    if spec.output_codec:
        command.extend(["--output-codec", spec.output_codec])
    if spec.output_bitrate:
        command.extend(["--output-bitrate", spec.output_bitrate])
    if spec.output_preset:
        command.extend(["--output-preset", spec.output_preset])
    if spec.output_muxer:
        command.extend(["--output-muxer", spec.output_muxer])
    if spec.output_width > 0:
        command.extend(["--output-width", str(int(spec.output_width))])
    if spec.output_height > 0:
        command.extend(["--output-height", str(int(spec.output_height))])
    if spec.sync_pair_mode:
        command.extend(["--sync-pair-mode", spec.sync_pair_mode])
    command.extend(["--sync-match-max-delta-ms", f"{float(spec.sync_match_max_delta_ms):.3f}"])
    command.extend(["--sync-manual-offset-ms", f"{float(spec.sync_manual_offset_ms):.3f}"])
    command.extend(["--stitch-output-scale", f"{float(spec.stitch_output_scale):.3f}"])
    command.extend(["--stitch-every-n", str(max(1, int(spec.stitch_every_n)))])
    if spec.gpu_mode:
        command.extend(["--gpu-mode", spec.gpu_mode])
    command.extend(["--gpu-device", str(max(0, int(spec.gpu_device)))])
    if spec.headless_benchmark:
        command.append("--headless-benchmark")
    command.extend(spec.extra_args)
    return command


def launch_native_runtime(
    spec: RuntimeLaunchSpec | None = None,
    *,
    creationflags: int = 0,
) -> subprocess.Popen[str]:
    command = build_runtime_command(spec)
    repo_root = _repo_root()
    env = os.environ.copy()
    path_entries = [
        str(repo_root / ".third_party" / "ffmpeg" / "current" / "bin"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64",
    ]
    env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])
    return subprocess.Popen(
        command,
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        creationflags=creationflags,
        env=env,
    )
