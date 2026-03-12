from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import time


@dataclass(slots=True)
class FinalStreamViewerSpec:
    target: str
    creationflags: int = 0
    ffplay_bin: str = ""
    window_title: str = "Hogak Final Stream"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_ffplay_binary(explicit_path: str = "") -> Path:
    candidates: list[Path] = []
    if explicit_path.strip():
        candidates.append(Path(explicit_path).expanduser())

    env_path = os.environ.get("FFPLAY_BIN", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    found = shutil.which("ffplay")
    if found:
        candidates.append(Path(found))

    candidates.extend(
        [
            _repo_root() / ".third_party" / "ffmpeg" / "current" / "bin" / "ffplay.exe",
            _repo_root() / ".third_party" / "ffmpeg" / "bin" / "ffplay.exe",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("ffplay binary not found. Set FFPLAY_BIN or install ffplay.")


def build_ffplay_command(spec: FinalStreamViewerSpec) -> list[str]:
    if not spec.target.strip():
        raise ValueError("final stream viewer target is required")
    target = spec.target.strip()
    command = [
        str(resolve_ffplay_binary(spec.ffplay_bin)),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-window_title",
        spec.window_title,
    ]
    if target.startswith("udp://"):
        command.extend(
            [
                "-f",
                "mpegts",
                "-analyzeduration",
                "1000000",
                "-probesize",
                "500000",
            ]
        )
        target = target.split("?", 1)[0]
    command.append(target)
    return command


def launch_final_stream_viewer(spec: FinalStreamViewerSpec) -> subprocess.Popen[bytes]:
    creationflags = spec.creationflags
    process: subprocess.Popen[bytes] = subprocess.Popen(
        build_ffplay_command(spec),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    time.sleep(0.6)
    if process.poll() is not None:
        stderr_text = ""
        if process.stderr is not None:
            try:
                stderr_bytes = process.stderr.read() or b""
                stderr_text = stderr_bytes.decode("utf-8", errors="ignore").strip()
            except Exception:
                stderr_text = ""
        raise RuntimeError(
            f"ffplay exited immediately with code {process.returncode}"
            + (f": {stderr_text}" if stderr_text else "")
        )
    return process
