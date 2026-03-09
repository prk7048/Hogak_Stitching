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
    extra_args: tuple[str, ...] = ()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
    if spec.ffmpeg_bin:
        command.extend(["--ffmpeg-bin", spec.ffmpeg_bin])
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
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64",
    ]
    env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])
    return subprocess.Popen(
        command,
        cwd=str(repo_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,
        creationflags=creationflags,
        env=env,
    )
