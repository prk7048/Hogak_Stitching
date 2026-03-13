from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutputPreset:
    key: str
    label: str
    width: int
    height: int
    fps: float
    codec: str
    bitrate: str
    muxer: str
    output_scale: float
    sync_pair_mode: str
    allow_frame_reuse: bool
    sync_match_max_delta_ms: float


OUTPUT_PRESETS: dict[str, OutputPreset] = {
    "realtime_1080p": OutputPreset(
        key="realtime_1080p",
        label="Realtime 1080p",
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264_nvenc",
        bitrate="6M",
        muxer="mpegts",
        output_scale=0.25,
        sync_pair_mode="service",
        allow_frame_reuse=False,
        sync_match_max_delta_ms=35.0,
    ),
    "realtime_hq_1080p": OutputPreset(
        key="realtime_hq_1080p",
        label="Realtime HQ 1080p",
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264_nvenc",
        bitrate="12M",
        muxer="mpegts",
        output_scale=0.50,
        sync_pair_mode="service",
        allow_frame_reuse=True,
        sync_match_max_delta_ms=60.0,
    ),
    "realtime_gpu_1080p": OutputPreset(
        key="realtime_gpu_1080p",
        label="Realtime GPU 1080p",
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264_nvenc",
        bitrate="16M",
        muxer="mpegts",
        output_scale=0.75,
        sync_pair_mode="service",
        allow_frame_reuse=True,
        sync_match_max_delta_ms=75.0,
    ),
    "realtime_hq_1080p_strict": OutputPreset(
        key="realtime_hq_1080p_strict",
        label="Realtime HQ 1080p Strict",
        width=1920,
        height=1080,
        fps=30.0,
        codec="h264_nvenc",
        bitrate="12M",
        muxer="mpegts",
        output_scale=0.50,
        sync_pair_mode="service",
        allow_frame_reuse=False,
        sync_match_max_delta_ms=35.0,
    ),
    "ntsc_sd": OutputPreset(
        key="ntsc_sd",
        label="NTSC SD (720x480 29.97)",
        width=720,
        height=480,
        fps=29.97,
        codec="h264_nvenc",
        bitrate="3M",
        muxer="mpegts",
        output_scale=0.20,
        sync_pair_mode="latest",
        allow_frame_reuse=False,
        sync_match_max_delta_ms=35.0,
    ),
    "pal_sd": OutputPreset(
        key="pal_sd",
        label="PAL SD (720x576 25)",
        width=720,
        height=576,
        fps=25.0,
        codec="h264_nvenc",
        bitrate="3M",
        muxer="mpegts",
        output_scale=0.20,
        sync_pair_mode="latest",
        allow_frame_reuse=False,
        sync_match_max_delta_ms=40.0,
    ),
    "ntsc_hd": OutputPreset(
        key="ntsc_hd",
        label="NTSC HD (1920x1080 29.97)",
        width=1920,
        height=1080,
        fps=29.97,
        codec="h264_nvenc",
        bitrate="6M",
        muxer="mpegts",
        output_scale=0.25,
        sync_pair_mode="latest",
        allow_frame_reuse=False,
        sync_match_max_delta_ms=35.0,
    ),
    "pal_hd": OutputPreset(
        key="pal_hd",
        label="PAL HD (1920x1080 25)",
        width=1920,
        height=1080,
        fps=25.0,
        codec="h264_nvenc",
        bitrate="6M",
        muxer="mpegts",
        output_scale=0.25,
        sync_pair_mode="latest",
        allow_frame_reuse=False,
        sync_match_max_delta_ms=40.0,
    ),
}


def get_output_preset(name: str) -> OutputPreset:
    key = str(name).strip().lower()
    if key not in OUTPUT_PRESETS:
        raise KeyError(f"unknown output preset: {name}")
    return OUTPUT_PRESETS[key]
