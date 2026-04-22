from __future__ import annotations

from pathlib import Path
from typing import Any

from stitching.domain.runtime.contract import normalize_schema_v2_reload_payload
from stitching.domain.runtime.service.launcher import RuntimeLaunchSpec


def _side_input(request_inputs: dict[str, Any], side: str) -> dict[str, Any]:
    value = request_inputs.get(side)
    return value if isinstance(value, dict) else {}


def _bool_value(payload: dict[str, Any], key: str, default: bool) -> bool:
    if key in payload:
        return bool(payload.get(key))
    return default


def build_launch_spec_and_reload_payload(
    plan_request: dict[str, Any],
    *,
    left_rtsp: str,
    right_rtsp: str,
    geometry_artifact: Path,
) -> tuple[RuntimeLaunchSpec, dict[str, Any]]:
    request_inputs = plan_request.get("request_inputs") if isinstance(plan_request.get("request_inputs"), dict) else {}
    request_timing = plan_request.get("request_timing") if isinstance(plan_request.get("request_timing"), dict) else {}
    request_probe = plan_request.get("request_probe") if isinstance(plan_request.get("request_probe"), dict) else {}
    request_transmit = plan_request.get("request_transmit") if isinstance(plan_request.get("request_transmit"), dict) else {}
    request_runtime = plan_request.get("request_runtime") if isinstance(plan_request.get("request_runtime"), dict) else {}
    runtime = plan_request.get("runtime") if isinstance(plan_request.get("runtime"), dict) else {}

    left_input = _side_input(request_inputs, "left")
    right_input = _side_input(request_inputs, "right")

    base_spec = RuntimeLaunchSpec()
    probe = runtime.get("probe", {}) if isinstance(runtime.get("probe"), dict) else {}
    transmit = runtime.get("transmit", {}) if isinstance(runtime.get("transmit"), dict) else {}

    probe_runtime = str(
        request_probe.get("runtime")
        or probe.get("runtime")
        or base_spec.output_runtime
        or "ffmpeg"
    ).strip()
    probe_target = str(request_probe.get("target") or probe.get("target") or "").strip()
    transmit_runtime = str(
        request_transmit.get("runtime")
        or transmit.get("runtime")
        or base_spec.production_output_runtime
        or "gpu-direct"
    ).strip()
    transmit_target = str(request_transmit.get("target") or transmit.get("target") or "").strip()
    cadence_fps = float(
        request_probe.get("fps")
        or request_transmit.get("fps")
        or runtime.get("output_cadence_fps")
        or 30.0
    )
    heartbeat_sec = float(
        request_runtime.get("benchmark_log_interval_sec")
        or runtime.get("benchmark_log_interval_sec")
        or runtime.get("status_interval_sec")
        or 5.0
    )

    launch_spec = RuntimeLaunchSpec(
        emit_hello=True,
        once=False,
        heartbeat_ms=max(250, int(round(heartbeat_sec * 1000.0))),
        left_rtsp=left_rtsp,
        right_rtsp=right_rtsp,
        input_runtime=str(request_runtime.get("input_runtime") or runtime.get("input_runtime") or base_spec.input_runtime),
        input_pipe_format=str(
            request_runtime.get("input_pipe_format")
            or runtime.get("input_pipe_format")
            or base_spec.input_pipe_format
        ),
        ffmpeg_bin=str(request_runtime.get("ffmpeg_bin") or runtime.get("ffmpeg_bin") or ""),
        transport=str(
            request_runtime.get("rtsp_transport")
            or runtime.get("rtsp_transport")
            or left_input.get("transport")
            or base_spec.transport
        ),
        input_buffer_frames=int(
            request_runtime.get("input_buffer_frames")
            or runtime.get("input_buffer_frames")
            or left_input.get("buffer_frames")
            or base_spec.input_buffer_frames
        ),
        timeout_sec=float(
            request_runtime.get("rtsp_timeout_sec")
            or runtime.get("rtsp_timeout_sec")
            or left_input.get("timeout_sec")
            or base_spec.timeout_sec
        ),
        reconnect_cooldown_sec=float(
            request_runtime.get("reconnect_cooldown_sec")
            or runtime.get("reconnect_cooldown_sec")
            or left_input.get("reconnect_cooldown_sec")
            or base_spec.reconnect_cooldown_sec
        ),
        output_runtime=probe_runtime,
        output_profile="inspection",
        output_target=probe_target,
        output_codec=str(request_probe.get("codec") or probe.get("codec") or base_spec.output_codec),
        output_bitrate=str(request_probe.get("bitrate") or probe.get("bitrate") or base_spec.output_bitrate),
        output_preset=str(request_probe.get("preset") or probe.get("preset") or base_spec.output_preset),
        output_muxer=str(request_probe.get("muxer") or probe.get("muxer") or ""),
        output_width=int(request_probe.get("width") or probe.get("width") or 0),
        output_height=int(request_probe.get("height") or probe.get("height") or 0),
        output_fps=float(request_probe.get("fps") or probe.get("fps") or cadence_fps),
        output_debug_overlay=_bool_value(request_probe, "debug_overlay", bool(probe.get("debug_overlay") or False)),
        production_output_runtime=transmit_runtime,
        production_output_profile="production-compatible",
        production_output_target=transmit_target,
        production_output_codec=str(
            request_transmit.get("codec") or transmit.get("codec") or base_spec.production_output_codec
        ),
        production_output_bitrate=str(
            request_transmit.get("bitrate") or transmit.get("bitrate") or base_spec.production_output_bitrate
        ),
        production_output_preset=str(
            request_transmit.get("preset") or transmit.get("preset") or base_spec.production_output_preset
        ),
        production_output_muxer=str(request_transmit.get("muxer") or transmit.get("muxer") or ""),
        production_output_width=int(
            request_transmit.get("width") or transmit.get("width") or base_spec.production_output_width
        ),
        production_output_height=int(
            request_transmit.get("height") or transmit.get("height") or base_spec.production_output_height
        ),
        production_output_fps=float(request_transmit.get("fps") or transmit.get("fps") or cadence_fps),
        production_output_debug_overlay=_bool_value(
            request_transmit,
            "debug_overlay",
            bool(
                transmit.get("debug_overlay")
                if "debug_overlay" in transmit
                else base_spec.production_output_debug_overlay
            ),
        ),
        sync_pair_mode=str(request_timing.get("pair_mode") or runtime.get("sync_pair_mode") or "service"),
        allow_frame_reuse=bool(
            request_timing.get("allow_frame_reuse")
            if "allow_frame_reuse" in request_timing
            else runtime.get("allow_frame_reuse")
            if "allow_frame_reuse" in runtime
            else False
        ),
        pair_reuse_max_age_ms=float(
            request_timing.get("reuse_max_age_ms") or runtime.get("pair_reuse_max_age_ms") or 90.0
        ),
        pair_reuse_max_consecutive=int(
            request_timing.get("reuse_max_consecutive") or runtime.get("pair_reuse_max_consecutive") or 2
        ),
        sync_match_max_delta_ms=float(
            request_timing.get("match_max_delta_ms") or runtime.get("sync_match_max_delta_ms") or 35.0
        ),
        sync_time_source=str(request_timing.get("time_source") or runtime.get("sync_time_source") or "pts-offset-auto"),
        sync_manual_offset_ms=float(
            request_timing.get("manual_offset_ms") or runtime.get("sync_manual_offset_ms") or 0.0
        ),
        sync_auto_offset_window_sec=float(
            request_timing.get("auto_offset_window_sec") or runtime.get("sync_auto_offset_window_sec") or 4.0
        ),
        sync_auto_offset_max_search_ms=float(
            request_timing.get("auto_offset_max_search_ms") or runtime.get("sync_auto_offset_max_search_ms") or 500.0
        ),
        sync_recalibration_interval_sec=float(
            request_timing.get("recalibration_interval_sec") or runtime.get("sync_recalibration_interval_sec") or 60.0
        ),
        sync_recalibration_trigger_skew_ms=float(
            request_timing.get("recalibration_trigger_skew_ms") or runtime.get("sync_recalibration_trigger_skew_ms") or 45.0
        ),
        sync_recalibration_trigger_wait_ratio=float(
            request_timing.get("recalibration_trigger_wait_ratio")
            or runtime.get("sync_recalibration_trigger_wait_ratio")
            or 0.5
        ),
        sync_auto_offset_confidence_min=float(
            request_timing.get("auto_offset_confidence_min") or runtime.get("sync_auto_offset_confidence_min") or 0.85
        ),
        stitch_output_scale=float(
            request_runtime.get("stitch_output_scale") or runtime.get("stitch_output_scale") or 1.0
        ),
        stitch_every_n=int(request_runtime.get("stitch_every_n") or runtime.get("stitch_every_n") or 1),
        gpu_mode=str(request_runtime.get("gpu_mode") or runtime.get("gpu_mode") or "on"),
        gpu_device=int(request_runtime.get("gpu_device") or runtime.get("gpu_device") or 0),
        headless_benchmark=bool(
            request_runtime.get("headless_benchmark")
            if "headless_benchmark" in request_runtime
            else runtime.get("headless_benchmark")
            if "headless_benchmark" in runtime
            else False
        ),
    )

    reload_payload = normalize_schema_v2_reload_payload(
        {
            "inputs": {
                "left": {
                    "url": left_rtsp,
                    "transport": str(
                        request_runtime.get("rtsp_transport")
                        or runtime.get("rtsp_transport")
                        or left_input.get("transport")
                        or base_spec.transport
                    ),
                    "timeout_sec": float(
                        request_runtime.get("rtsp_timeout_sec")
                        or runtime.get("rtsp_timeout_sec")
                        or left_input.get("timeout_sec")
                        or base_spec.timeout_sec
                    ),
                    "reconnect_cooldown_sec": float(
                        request_runtime.get("reconnect_cooldown_sec")
                        or runtime.get("reconnect_cooldown_sec")
                        or left_input.get("reconnect_cooldown_sec")
                        or base_spec.reconnect_cooldown_sec
                    ),
                    "buffer_frames": int(
                        request_runtime.get("input_buffer_frames")
                        or runtime.get("input_buffer_frames")
                        or left_input.get("buffer_frames")
                        or base_spec.input_buffer_frames
                    ),
                },
                "right": {
                    "url": right_rtsp,
                    "transport": str(
                        request_runtime.get("rtsp_transport")
                        or runtime.get("rtsp_transport")
                        or right_input.get("transport")
                        or base_spec.transport
                    ),
                    "timeout_sec": float(
                        request_runtime.get("rtsp_timeout_sec")
                        or runtime.get("rtsp_timeout_sec")
                        or right_input.get("timeout_sec")
                        or base_spec.timeout_sec
                    ),
                    "reconnect_cooldown_sec": float(
                        request_runtime.get("reconnect_cooldown_sec")
                        or runtime.get("reconnect_cooldown_sec")
                        or right_input.get("reconnect_cooldown_sec")
                        or base_spec.reconnect_cooldown_sec
                    ),
                    "buffer_frames": int(
                        request_runtime.get("input_buffer_frames")
                        or runtime.get("input_buffer_frames")
                        or right_input.get("buffer_frames")
                        or base_spec.input_buffer_frames
                    ),
                },
            },
            "geometry": {
                "artifact_path": str(geometry_artifact),
            },
            "timing": {
                "pair_mode": str(request_timing.get("pair_mode") or runtime.get("sync_pair_mode") or "service"),
                "allow_frame_reuse": bool(
                    request_timing.get("allow_frame_reuse")
                    if "allow_frame_reuse" in request_timing
                    else runtime.get("allow_frame_reuse")
                    if "allow_frame_reuse" in runtime
                    else False
                ),
                "reuse_max_age_ms": float(
                    request_timing.get("reuse_max_age_ms") or runtime.get("pair_reuse_max_age_ms") or 90.0
                ),
                "reuse_max_consecutive": int(
                    request_timing.get("reuse_max_consecutive") or runtime.get("pair_reuse_max_consecutive") or 2
                ),
                "match_max_delta_ms": float(
                    request_timing.get("match_max_delta_ms") or runtime.get("sync_match_max_delta_ms") or 35.0
                ),
                "time_source": str(request_timing.get("time_source") or runtime.get("sync_time_source") or "pts-offset-auto"),
                "manual_offset_ms": float(
                    request_timing.get("manual_offset_ms") or runtime.get("sync_manual_offset_ms") or 0.0
                ),
                "auto_offset_window_sec": float(
                    request_timing.get("auto_offset_window_sec") or runtime.get("sync_auto_offset_window_sec") or 4.0
                ),
                "auto_offset_max_search_ms": float(
                    request_timing.get("auto_offset_max_search_ms") or runtime.get("sync_auto_offset_max_search_ms") or 500.0
                ),
                "recalibration_interval_sec": float(
                    request_timing.get("recalibration_interval_sec")
                    or runtime.get("sync_recalibration_interval_sec")
                    or 60.0
                ),
                "recalibration_trigger_skew_ms": float(
                    request_timing.get("recalibration_trigger_skew_ms")
                    or runtime.get("sync_recalibration_trigger_skew_ms")
                    or 45.0
                ),
                "recalibration_trigger_wait_ratio": float(
                    request_timing.get("recalibration_trigger_wait_ratio")
                    or runtime.get("sync_recalibration_trigger_wait_ratio")
                    or 0.5
                ),
                "auto_offset_confidence_min": float(
                    request_timing.get("auto_offset_confidence_min")
                    or runtime.get("sync_auto_offset_confidence_min")
                    or 0.85
                ),
            },
            "outputs": {
                "probe": {
                    "runtime": probe_runtime,
                    "target": probe_target,
                    "codec": str(request_probe.get("codec") or probe.get("codec") or base_spec.output_codec),
                    "bitrate": str(request_probe.get("bitrate") or probe.get("bitrate") or base_spec.output_bitrate),
                    "preset": str(request_probe.get("preset") or probe.get("preset") or base_spec.output_preset),
                    "muxer": str(request_probe.get("muxer") or probe.get("muxer") or ""),
                    "width": int(request_probe.get("width") or probe.get("width") or 0),
                    "height": int(request_probe.get("height") or probe.get("height") or 0),
                    "fps": float(request_probe.get("fps") or probe.get("fps") or cadence_fps),
                    "debug_overlay": _bool_value(request_probe, "debug_overlay", bool(probe.get("debug_overlay") or False)),
                },
                "transmit": {
                    "runtime": transmit_runtime,
                    "target": transmit_target,
                    "codec": str(
                        request_transmit.get("codec") or transmit.get("codec") or base_spec.production_output_codec
                    ),
                    "bitrate": str(
                        request_transmit.get("bitrate")
                        or transmit.get("bitrate")
                        or base_spec.production_output_bitrate
                    ),
                    "preset": str(
                        request_transmit.get("preset")
                        or transmit.get("preset")
                        or base_spec.production_output_preset
                    ),
                    "muxer": str(request_transmit.get("muxer") or transmit.get("muxer") or ""),
                    "width": int(
                        request_transmit.get("width") or transmit.get("width") or base_spec.production_output_width
                    ),
                    "height": int(
                        request_transmit.get("height") or transmit.get("height") or base_spec.production_output_height
                    ),
                    "fps": float(request_transmit.get("fps") or transmit.get("fps") or cadence_fps),
                    "debug_overlay": _bool_value(
                        request_transmit,
                        "debug_overlay",
                        bool(
                            transmit.get("debug_overlay")
                            if "debug_overlay" in transmit
                            else base_spec.production_output_debug_overlay
                        ),
                    ),
                },
            },
            "runtime": {
                "input_runtime": str(request_runtime.get("input_runtime") or runtime.get("input_runtime") or base_spec.input_runtime),
                "ffmpeg_bin": str(request_runtime.get("ffmpeg_bin") or runtime.get("ffmpeg_bin") or ""),
                "gpu_mode": str(request_runtime.get("gpu_mode") or runtime.get("gpu_mode") or "on"),
                "gpu_device": int(request_runtime.get("gpu_device") or runtime.get("gpu_device") or 0),
                "stitch_output_scale": float(
                    request_runtime.get("stitch_output_scale") or runtime.get("stitch_output_scale") or 1.0
                ),
                "stitch_every_n": int(request_runtime.get("stitch_every_n") or runtime.get("stitch_every_n") or 1),
                "benchmark_log_interval_sec": float(
                    request_runtime.get("benchmark_log_interval_sec")
                    or runtime.get("benchmark_log_interval_sec")
                    or runtime.get("status_interval_sec")
                    or 5.0
                ),
                "headless_benchmark": bool(
                    request_runtime.get("headless_benchmark")
                    if "headless_benchmark" in request_runtime
                    else runtime.get("headless_benchmark")
                    if "headless_benchmark" in runtime
                    else False
                ),
            },
        }
    )
    return launch_spec, reload_payload
