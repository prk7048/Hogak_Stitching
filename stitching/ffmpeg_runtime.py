from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FfmpegRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FfmpegBinaries:
    ffmpeg: str
    ffprobe: str | None


@dataclass(frozen=True, slots=True)
class RtspDecodeSpec:
    url: str
    transport: str = "tcp"
    timeout_sec: float = 10.0
    use_hwaccel: bool = True
    hwaccel: str = "cuda"
    codec: str = "h264"
    output_pix_fmt: str = "bgr24"
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class NvencEncodeSpec:
    width: int
    height: int
    fps: float
    bitrate: str = "12M"
    codec: str = "h264_nvenc"
    preset: str = "p4"
    tune: str = "ll"
    rc: str = "cbr"
    pix_fmt: str = "bgr24"
    output_url: str = ""
    muxer: str = "rtsp"


@dataclass(frozen=True, slots=True)
class ProbeStreamInfo:
    codec_name: str
    codec_type: str
    width: int
    height: int
    pix_fmt: str
    avg_frame_rate: str
    r_frame_rate: str


def _candidate_binary_paths(binary_name: str) -> list[str]:
    env_name = "FFMPEG_BIN" if binary_name == "ffmpeg" else "FFPROBE_BIN"
    candidates: list[str] = []
    env_path = os.environ.get(env_name, "").strip()
    if env_path:
        candidates.append(env_path)

    found = shutil.which(binary_name)
    if found:
        candidates.append(found)

    common_roots = [
        Path(r"C:\ffmpeg\bin"),
        Path(r"C:\Program Files\ffmpeg\bin"),
        Path(r"C:\Program Files (x86)\ffmpeg\bin"),
        Path.home() / "ffmpeg" / "bin",
        Path.home() / "scoop" / "apps" / "ffmpeg" / "current" / "bin",
    ]
    for root in common_roots:
        candidates.append(str(root / f"{binary_name}.exe"))

    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        norm = os.path.normcase(os.path.normpath(item))
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(item)
    return unique


def find_binary(binary_name: str, *, required: bool = True) -> str | None:
    for candidate in _candidate_binary_paths(binary_name):
        if candidate and os.path.exists(candidate):
            return candidate
    if required:
        raise FfmpegRuntimeError(
            f"{binary_name} binary not found. Set {'FFMPEG_BIN' if binary_name == 'ffmpeg' else 'FFPROBE_BIN'} or install ffmpeg."
        )
    return None


def resolve_binaries() -> FfmpegBinaries:
    return FfmpegBinaries(
        ffmpeg=str(find_binary("ffmpeg", required=True)),
        ffprobe=find_binary("ffprobe", required=False),
    )


def build_ffprobe_stream_command(
    *,
    ffprobe_bin: str,
    url: str,
    transport: str = "tcp",
    timeout_sec: float = 10.0,
) -> list[str]:
    timeout_us = max(1, int(float(timeout_sec) * 1_000_000))
    return [
        ffprobe_bin,
        "-v",
        "error",
        "-rtsp_transport",
        transport,
        "-stimeout",
        str(timeout_us),
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,codec_type,width,height,avg_frame_rate,r_frame_rate,pix_fmt",
        "-of",
        "json",
        url,
    ]


def build_rtsp_decode_command(
    *,
    ffmpeg_bin: str,
    spec: RtspDecodeSpec,
) -> list[str]:
    timeout_us = max(1, int(float(spec.timeout_sec) * 1_000_000))
    cmd: list[str] = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-rtsp_transport",
        spec.transport,
        "-stimeout",
        str(timeout_us),
    ]
    if spec.use_hwaccel:
        cmd += [
            "-hwaccel",
            spec.hwaccel,
        ]
        if spec.codec:
            cmd += ["-c:v", f"{spec.codec}_cuvid"]
    cmd += [
        "-i",
        spec.url,
        "-an",
    ]
    if spec.width is not None and spec.height is not None:
        cmd += ["-vf", f"scale={int(spec.width)}:{int(spec.height)}"]
    cmd += [
        "-vsync",
        "0",
        "-pix_fmt",
        spec.output_pix_fmt,
        "-f",
        "rawvideo",
        "-",
    ]
    return cmd


def build_nvenc_stream_command(
    *,
    ffmpeg_bin: str,
    spec: NvencEncodeSpec,
) -> list[str]:
    if not spec.output_url:
        raise FfmpegRuntimeError("output_url is required for NVENC stream command")
    size = f"{int(spec.width)}x{int(spec.height)}"
    fps = max(1.0, float(spec.fps))
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "rawvideo",
        "-pix_fmt",
        spec.pix_fmt,
        "-s",
        size,
        "-r",
        f"{fps:.3f}",
        "-i",
        "-",
        "-an",
        "-c:v",
        spec.codec,
        "-preset",
        spec.preset,
        "-tune",
        spec.tune,
        "-rc",
        spec.rc,
        "-b:v",
        spec.bitrate,
        "-maxrate",
        spec.bitrate,
        "-bufsize",
        spec.bitrate,
        "-f",
        spec.muxer,
        spec.output_url,
    ]


def summarize_probe_json(raw_text: str) -> dict[str, Any]:
    payload = json.loads(raw_text)
    streams = payload.get("streams", [])
    if not streams:
        return {}
    stream = streams[0]
    return {
        "codec_name": stream.get("codec_name"),
        "codec_type": stream.get("codec_type"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "pix_fmt": stream.get("pix_fmt"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "r_frame_rate": stream.get("r_frame_rate"),
    }


def probe_stream_info(
    *,
    ffprobe_bin: str,
    url: str,
    transport: str = "tcp",
    timeout_sec: float = 10.0,
) -> ProbeStreamInfo:
    raw = subprocess.check_output(
        build_ffprobe_stream_command(
            ffprobe_bin=ffprobe_bin,
            url=url,
            transport=transport,
            timeout_sec=timeout_sec,
        ),
        stderr=subprocess.STDOUT,
        text=True,
        timeout=max(3.0, float(timeout_sec) + 1.0),
    )
    summary = summarize_probe_json(raw)
    if not summary:
        raise FfmpegRuntimeError(f"ffprobe returned no stream metadata for {url}")
    width = int(summary.get("width") or 0)
    height = int(summary.get("height") or 0)
    if width <= 0 or height <= 0:
        raise FfmpegRuntimeError(f"ffprobe did not return a valid frame size for {url}")
    return ProbeStreamInfo(
        codec_name=str(summary.get("codec_name") or ""),
        codec_type=str(summary.get("codec_type") or ""),
        width=width,
        height=height,
        pix_fmt=str(summary.get("pix_fmt") or ""),
        avg_frame_rate=str(summary.get("avg_frame_rate") or "0/1"),
        r_frame_rate=str(summary.get("r_frame_rate") or "0/1"),
    )


def parse_frame_rate(rate_text: str) -> float:
    text = str(rate_text or "").strip()
    if not text:
        return 0.0
    if "/" in text:
        num_text, den_text = text.split("/", 1)
        try:
            num = float(num_text)
            den = float(den_text)
        except ValueError:
            return 0.0
        if den == 0.0:
            return 0.0
        return num / den
    try:
        return float(text)
    except ValueError:
        return 0.0
