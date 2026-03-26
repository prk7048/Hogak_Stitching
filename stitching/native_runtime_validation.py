from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from stitching.output_presets import OUTPUT_PRESETS, get_output_preset
from stitching.project_defaults import (
    DEFAULT_NATIVE_HOMOGRAPHY_PATH,
    DEFAULT_NATIVE_INPUT_BUFFER_FRAMES,
    DEFAULT_NATIVE_INPUT_RUNTIME,
    DEFAULT_NATIVE_RTSP_TRANSPORT,
    DEFAULT_NATIVE_SYNC_AUTO_OFFSET_CONFIDENCE_MIN,
    DEFAULT_NATIVE_SYNC_AUTO_OFFSET_MAX_SEARCH_MS,
    DEFAULT_NATIVE_SYNC_MANUAL_OFFSET_MS,
    DEFAULT_NATIVE_SYNC_MATCH_MAX_DELTA_MS,
    DEFAULT_NATIVE_SYNC_RECALIBRATION_INTERVAL_SEC,
    DEFAULT_NATIVE_SYNC_RECALIBRATION_TRIGGER_SKEW_MS,
    DEFAULT_NATIVE_SYNC_RECALIBRATION_TRIGGER_WAIT_RATIO,
    DEFAULT_NATIVE_SYNC_AUTO_OFFSET_WINDOW_SEC,
    DEFAULT_NATIVE_SYNC_TIME_SOURCE,
    DEFAULT_NATIVE_TRANSMIT_PRESET,
    DEFAULT_NATIVE_TRANSMIT_RUNTIME,
    DEFAULT_NATIVE_TRANSMIT_TARGET,
    DEFAULT_NATIVE_TRANSMIT_BITRATE,
    default_left_rtsp,
    default_output_standard,
    default_right_rtsp,
)
from stitching.runtime_client import RuntimeClient
from stitching.runtime_launcher import RuntimeLaunchSpec, resolve_ffmpeg_binary
from stitching.runtime_site_config import repo_root, require_configured_rtsp_urls


def add_native_validation_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--left-rtsp", default=default_left_rtsp(), help="Left RTSP URL")
    cmd.add_argument("--right-rtsp", default=default_right_rtsp(), help="Right RTSP URL")
    cmd.add_argument("--input-runtime", choices=["ffmpeg-cpu", "ffmpeg-cuda"], default=DEFAULT_NATIVE_INPUT_RUNTIME)
    cmd.add_argument("--ffmpeg-bin", default="", help="Optional explicit ffmpeg.exe path")
    cmd.add_argument("--rtsp-transport", choices=["tcp", "udp"], default=DEFAULT_NATIVE_RTSP_TRANSPORT)
    cmd.add_argument("--input-buffer-frames", type=int, default=DEFAULT_NATIVE_INPUT_BUFFER_FRAMES)
    cmd.add_argument("--homography-file", default=DEFAULT_NATIVE_HOMOGRAPHY_PATH)
    cmd.add_argument("--output-standard", choices=sorted(OUTPUT_PRESETS), default=default_output_standard())
    cmd.add_argument("--duration-sec", type=float, default=600.0, help="Validation runtime duration in seconds")
    cmd.add_argument("--source-probe-sec", type=float, default=5.0, help="ffprobe capture duration per RTSP source")
    cmd.add_argument(
        "--allow-frame-reuse",
        action="store_true",
        help="Override strict-fresh baseline and allow stale one-side reuse during validation",
    )
    cmd.add_argument(
        "--sync-time-source",
        choices=["pts-offset-auto", "pts-offset-manual", "pts-offset-hybrid", "arrival", "wallclock"],
        default=DEFAULT_NATIVE_SYNC_TIME_SOURCE,
    )
    cmd.add_argument("--sync-match-max-delta-ms", type=float, default=None)
    cmd.add_argument("--sync-manual-offset-ms", type=float, default=DEFAULT_NATIVE_SYNC_MANUAL_OFFSET_MS)
    cmd.add_argument("--sync-auto-offset-window-sec", type=float, default=DEFAULT_NATIVE_SYNC_AUTO_OFFSET_WINDOW_SEC)
    cmd.add_argument("--sync-auto-offset-max-search-ms", type=float, default=DEFAULT_NATIVE_SYNC_AUTO_OFFSET_MAX_SEARCH_MS)
    cmd.add_argument("--sync-recalibration-interval-sec", type=float, default=DEFAULT_NATIVE_SYNC_RECALIBRATION_INTERVAL_SEC)
    cmd.add_argument("--sync-recalibration-trigger-skew-ms", type=float, default=DEFAULT_NATIVE_SYNC_RECALIBRATION_TRIGGER_SKEW_MS)
    cmd.add_argument("--sync-recalibration-trigger-wait-ratio", type=float, default=DEFAULT_NATIVE_SYNC_RECALIBRATION_TRIGGER_WAIT_RATIO)
    cmd.add_argument("--sync-auto-offset-confidence-min", type=float, default=DEFAULT_NATIVE_SYNC_AUTO_OFFSET_CONFIDENCE_MIN)
    cmd.add_argument("--gpu-mode", choices=["off", "auto", "on"], default="on")
    cmd.add_argument("--gpu-device", type=int, default=0)
    cmd.add_argument("--report-out", default="", help="Optional explicit JSON report path")
    cmd.add_argument("--label", default="strict-fresh-30", help="Validation label written into the report")


def _sanitize_rtsp_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except Exception:
        return text
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _resolve_ffprobe_binary(explicit_ffmpeg: str = "") -> Path:
    ffmpeg = resolve_ffmpeg_binary(explicit_ffmpeg)
    candidates = [
        ffmpeg.with_name("ffprobe.exe" if ffmpeg.suffix.lower() == ".exe" else "ffprobe"),
    ]
    found = shutil.which("ffprobe")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("ffprobe binary not found. Install ffprobe or set FFMPEG_BIN.")


def _float_values(items: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for item in items:
        value = item.get(key)
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _collect_packet_side_data_counts(packets: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for packet in packets:
        side_data_list = packet.get("side_data_list")
        if not isinstance(side_data_list, list):
            continue
        for side_data in side_data_list:
            if not isinstance(side_data, dict):
                continue
            side_type = str(side_data.get("side_data_type") or "").strip()
            if side_type:
                counts[side_type] += 1
    return counts


def _split_packets_and_frames(decoded: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    packets: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []

    raw_packets = decoded.get("packets")
    if isinstance(raw_packets, list):
        packets.extend(item for item in raw_packets if isinstance(item, dict))

    raw_frames = decoded.get("frames")
    if isinstance(raw_frames, list):
        frames.extend(item for item in raw_frames if isinstance(item, dict))

    raw_combined = decoded.get("packets_and_frames")
    if isinstance(raw_combined, list):
        for item in raw_combined:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "packet":
                packets.append(item)
            elif item_type == "frame":
                frames.append(item)
    return packets, frames


def _probe_single_source(
    ffprobe_bin: Path,
    rtsp_url: str,
    duration_sec: float,
    *,
    rtsp_transport: str,
) -> dict[str, Any]:
    command = [
        str(ffprobe_bin),
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        str(rtsp_transport),
        "-select_streams",
        "v:0",
        "-show_streams",
        "-show_packets",
        "-show_frames",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,time_base:packet=pts_time,dts_time,flags,side_data_list:frame=best_effort_timestamp_time,pkt_dts_time,key_frame,pict_type",
        "-of",
        "json",
        "-read_intervals",
        f"%+{max(1.0, float(duration_sec)):.3f}",
        rtsp_url,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    payload: dict[str, Any] = {
        "url": _sanitize_rtsp_url(rtsp_url),
        "returncode": int(completed.returncode),
        "stderr": completed.stderr.strip(),
        "stream": {},
        "packet_count": 0,
        "frame_count": 0,
        "best_effort_timestamp_count": 0,
        "best_effort_first_sec": 0.0,
        "best_effort_last_sec": 0.0,
        "pkt_dts_first_sec": 0.0,
        "pkt_dts_last_sec": 0.0,
        "prft_count": 0,
        "rtcp_sender_report_count": 0,
        "wallclock_hint_available": False,
    }
    if completed.returncode != 0:
        return payload
    try:
        decoded = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload["stderr"] = (payload["stderr"] + "\ninvalid ffprobe json output").strip()
        payload["returncode"] = -1
        return payload

    streams = decoded.get("streams") if isinstance(decoded, dict) else None
    stream = streams[0] if isinstance(streams, list) and streams else {}
    if isinstance(stream, dict):
        payload["stream"] = {
            "codec_name": str(stream.get("codec_name") or ""),
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "r_frame_rate": str(stream.get("r_frame_rate") or ""),
            "avg_frame_rate": str(stream.get("avg_frame_rate") or ""),
            "time_base": str(stream.get("time_base") or ""),
        }

    packets, frames = _split_packets_and_frames(decoded if isinstance(decoded, dict) else {})
    payload["packet_count"] = len(packets)
    payload["frame_count"] = len(frames)

    best_effort = _float_values(frames, "best_effort_timestamp_time")
    pkt_dts = _float_values(frames, "pkt_dts_time")
    payload["best_effort_timestamp_count"] = len(best_effort)
    if best_effort:
        payload["best_effort_first_sec"] = best_effort[0]
        payload["best_effort_last_sec"] = best_effort[-1]
    if pkt_dts:
        payload["pkt_dts_first_sec"] = pkt_dts[0]
        payload["pkt_dts_last_sec"] = pkt_dts[-1]

    side_data_counts = _collect_packet_side_data_counts(packets)
    prft_count = 0
    rtcp_sender_report_count = 0
    for side_type, count in side_data_counts.items():
        normalized = side_type.lower()
        if "producer reference time" in normalized or normalized == "prft":
            prft_count += count
        if "rtcp sender report" in normalized:
            rtcp_sender_report_count += count
    payload["prft_count"] = prft_count
    payload["rtcp_sender_report_count"] = rtcp_sender_report_count
    payload["wallclock_hint_available"] = prft_count > 0 or rtcp_sender_report_count > 0
    return payload


def _probe_source_timing(args: argparse.Namespace) -> dict[str, Any]:
    ffprobe_bin = _resolve_ffprobe_binary(str(args.ffmpeg_bin or ""))
    left = _probe_single_source(
        ffprobe_bin,
        str(args.left_rtsp),
        float(args.source_probe_sec),
        rtsp_transport=str(args.rtsp_transport),
    )
    right = _probe_single_source(
        ffprobe_bin,
        str(args.right_rtsp),
        float(args.source_probe_sec),
        rtsp_transport=str(args.rtsp_transport),
    )
    left_ok = int(left.get("returncode") or 0) == 0
    right_ok = int(right.get("returncode") or 0) == 0
    wallclock_possible = bool(left.get("wallclock_hint_available")) and bool(right.get("wallclock_hint_available"))
    if not left_ok or not right_ok:
        reason = "ffprobe_failed"
    elif wallclock_possible:
        reason = "both_streams_expose_wallclock_hints"
    else:
        reason = "cross_camera_wallclock_hints_missing_or_incomplete"
    return {
        "probe_duration_sec": float(args.source_probe_sec),
        "rtsp_transport": str(args.rtsp_transport),
        "ffprobe_bin": str(ffprobe_bin),
        "left": left,
        "right": right,
        "cross_camera_wallclock_comparable": wallclock_possible,
        "result": "wallclock 가능" if wallclock_possible else "비교 불가 -> fallback-arrival 유지",
        "reason": reason,
    }


def _make_validation_report_path(label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in label.strip()) or "strict-fresh-30"
    return repo_root() / "output" / "debug" / f"native_validate_{safe_label}_{timestamp}.json"


def _compact_runtime_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "status",
        "sync_pair_mode",
        "left_fps",
        "right_fps",
        "stitch_actual_fps",
        "production_output_written_fps",
        "pair_skew_ms_mean",
        "pair_source_skew_ms_mean",
        "sync_effective_offset_ms",
        "sync_offset_source",
        "sync_offset_confidence",
        "sync_recalibration_count",
        "left_age_ms",
        "right_age_ms",
        "left_source_age_ms",
        "right_source_age_ms",
        "left_read_failures",
        "right_read_failures",
        "left_reader_restarts",
        "right_reader_restarts",
        "wait_next_frame_count",
        "wait_sync_pair_count",
        "wait_paired_fresh_count",
        "source_time_valid_left",
        "source_time_valid_right",
        "source_time_mode",
        "left_buffered_frames",
        "right_buffered_frames",
        "gpu_errors",
    )
    return {key: payload.get(key) for key in keys}


def _mean(values: list[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


def _classify_validation(
    *,
    returncode: int,
    source_probe_wallclock_comparable: bool,
    status_counts: Counter[str],
    source_mode_counts: Counter[str],
    final_metrics: dict[str, Any],
    stitch_values: list[float],
    transmit_values: list[float],
    pair_skew_values: list[float],
    pair_source_skew_values: list[float],
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    sample_count = max(1, sum(status_counts.values()))
    wait_sync_ratio = status_counts.get("waiting sync pair", 0) / float(sample_count)
    final_left_fps = float(final_metrics.get("left_fps") or 0.0)
    final_right_fps = float(final_metrics.get("right_fps") or 0.0)
    final_stitch_actual_fps = float(final_metrics.get("stitch_actual_fps") or 0.0)
    final_transmit_fps = float(final_metrics.get("production_output_written_fps") or 0.0)
    final_read_failures = int(final_metrics.get("left_read_failures") or 0) + int(final_metrics.get("right_read_failures") or 0)
    final_restarts = int(final_metrics.get("left_reader_restarts") or 0) + int(final_metrics.get("right_reader_restarts") or 0)
    dominant_source_mode = source_mode_counts.most_common(1)[0][0] if source_mode_counts else "fallback-arrival"

    if returncode != 0:
        reasons.append(f"runtime_exit_code={returncode}")
        return "fail", "code-limited", reasons

    if final_read_failures > 0 or final_restarts > 0:
        reasons.append(f"reader_failures={final_read_failures}")
        reasons.append(f"reader_restarts={final_restarts}")
        decision = "investigate"
    elif wait_sync_ratio >= 0.35:
        reasons.append(f"wait_sync_ratio={wait_sync_ratio:.2f}")
        decision = "investigate"
    elif final_stitch_actual_fps > 0.0 and final_stitch_actual_fps < 24.0:
        reasons.append(f"stitch_actual_fps={final_stitch_actual_fps:.2f}")
        decision = "investigate"
    elif final_transmit_fps > 0.0 and final_transmit_fps < 24.0:
        reasons.append(f"transmit_fps={final_transmit_fps:.2f}")
        decision = "investigate"
    else:
        decision = "pass"

    if dominant_source_mode == "fallback-arrival":
        reasons.append(f"source_mode={dominant_source_mode}")
        bottleneck_guess = "code-limited"
    elif (
        wait_sync_ratio >= 0.35
        or (
            final_stitch_actual_fps <= 1.0
            and final_left_fps >= 20.0
            and final_right_fps >= 20.0
        )
    ):
        bottleneck_guess = "source-limited"
    else:
        bottleneck_guess = "code-limited"

    if pair_skew_values:
        reasons.append(f"pair_skew_ms_mean={_mean(pair_skew_values):.2f}")
    if pair_source_skew_values and dominant_source_mode != "fallback-arrival":
        reasons.append(f"pair_source_skew_ms_mean={_mean(pair_source_skew_values):.2f}")
    if stitch_values:
        reasons.append(f"stitch_actual_fps_mean={_mean(stitch_values):.2f}")
    if transmit_values:
        reasons.append(f"transmit_fps_mean={_mean(transmit_values):.2f}")
    return decision, bottleneck_guess, reasons


def run_native_validation(args: argparse.Namespace) -> int:
    require_configured_rtsp_urls(str(args.left_rtsp), str(args.right_rtsp), context="native-validate")

    preset = get_output_preset(str(args.output_standard))
    source_probe = _probe_source_timing(args)
    spec = RuntimeLaunchSpec(
        emit_hello=True,
        once=False,
        heartbeat_ms=1000,
        left_rtsp=str(args.left_rtsp),
        right_rtsp=str(args.right_rtsp),
        input_runtime=str(args.input_runtime),
        ffmpeg_bin=str(args.ffmpeg_bin or ""),
        homography_file=str(args.homography_file or ""),
        transport=str(args.rtsp_transport),
        input_buffer_frames=max(1, int(args.input_buffer_frames)),
        output_runtime="none",
        output_target="",
        output_codec="",
        output_bitrate="",
        output_preset="",
        output_muxer="",
        output_width=0,
        output_height=0,
        output_fps=0.0,
        production_output_runtime=DEFAULT_NATIVE_TRANSMIT_RUNTIME,
        production_output_target=DEFAULT_NATIVE_TRANSMIT_TARGET,
        production_output_codec=str(preset.codec),
        production_output_bitrate=DEFAULT_NATIVE_TRANSMIT_BITRATE or str(preset.bitrate),
        production_output_preset=DEFAULT_NATIVE_TRANSMIT_PRESET or str(preset.label),
        production_output_muxer=str(preset.muxer),
        production_output_width=int(preset.width),
        production_output_height=int(preset.height),
        production_output_fps=float(preset.fps),
        sync_pair_mode="service",
        allow_frame_reuse=bool(args.allow_frame_reuse),
        pair_reuse_max_age_ms=140.0,
        pair_reuse_max_consecutive=4,
        sync_time_source=str(args.sync_time_source),
        sync_match_max_delta_ms=float(args.sync_match_max_delta_ms or preset.sync_match_max_delta_ms),
        sync_manual_offset_ms=float(args.sync_manual_offset_ms),
        sync_auto_offset_window_sec=float(args.sync_auto_offset_window_sec),
        sync_auto_offset_max_search_ms=float(args.sync_auto_offset_max_search_ms),
        sync_recalibration_interval_sec=float(args.sync_recalibration_interval_sec),
        sync_recalibration_trigger_skew_ms=float(args.sync_recalibration_trigger_skew_ms),
        sync_recalibration_trigger_wait_ratio=float(args.sync_recalibration_trigger_wait_ratio),
        sync_auto_offset_confidence_min=float(args.sync_auto_offset_confidence_min),
        stitch_output_scale=float(preset.output_scale),
        stitch_every_n=1,
        gpu_mode=str(args.gpu_mode),
        gpu_device=max(0, int(args.gpu_device)),
    )

    status_counts: Counter[str] = Counter()
    source_mode_counts: Counter[str] = Counter()
    stitch_values: list[float] = []
    transmit_values: list[float] = []
    pair_skew_values: list[float] = []
    pair_source_skew_values: list[float] = []
    final_metrics: dict[str, Any] = {}
    hello_payload: dict[str, Any] = {}
    started_at = datetime.now().isoformat(timespec="seconds")
    started_monotonic = time.time()

    client = RuntimeClient.launch(spec)
    returncode = 0
    stderr_tail = ""
    try:
        hello = client.wait_for_hello(timeout_sec=5.0)
        hello_payload = dict(hello.payload)
        deadline = time.time() + max(1.0, float(args.duration_sec))
        while time.time() < deadline:
            event = client.read_event(timeout_sec=1.5)
            if event is None:
                if client.process.poll() is not None:
                    break
                continue
            if event.type != "metrics":
                continue
            payload = dict(event.payload)
            final_metrics = payload
            status = str(payload.get("status") or "-")
            source_mode = str(payload.get("source_time_mode") or "fallback-arrival")
            status_counts[status] += 1
            source_mode_counts[source_mode] += 1

            try:
                pair_skew_values.append(float(payload.get("pair_skew_ms_mean") or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                pair_source_skew_values.append(float(payload.get("pair_source_skew_ms_mean") or 0.0))
            except (TypeError, ValueError):
                pass
            try:
                stitch_value = float(payload.get("stitch_actual_fps") or 0.0)
                if stitch_value > 0.0:
                    stitch_values.append(stitch_value)
            except (TypeError, ValueError):
                pass
            try:
                transmit_value = float(payload.get("production_output_written_fps") or 0.0)
                if transmit_value > 0.0:
                    transmit_values.append(transmit_value)
            except (TypeError, ValueError):
                pass
        try:
            client.shutdown()
        except Exception:
            pass
        try:
            client.process.wait(timeout=5)
        except Exception:
            client.process.kill()
        returncode = int(client.process.returncode or 0)
        stderr_tail = client.get_stderr_tail().strip()
    finally:
        if client.process.poll() is None:
            try:
                client.process.kill()
            except Exception:
                pass

    decision, bottleneck_guess, reasons = _classify_validation(
        returncode=returncode,
        source_probe_wallclock_comparable=bool(source_probe.get("cross_camera_wallclock_comparable")),
        status_counts=status_counts,
        source_mode_counts=source_mode_counts,
        final_metrics=final_metrics,
        stitch_values=stitch_values,
        transmit_values=transmit_values,
        pair_skew_values=pair_skew_values,
        pair_source_skew_values=pair_source_skew_values,
    )

    finished_at = datetime.now().isoformat(timespec="seconds")
    report = {
        "label": str(args.label),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_requested_sec": float(args.duration_sec),
        "duration_actual_sec": round(max(0.0, time.time() - started_monotonic), 3),
        "baseline": {
            "left_rtsp": _sanitize_rtsp_url(str(args.left_rtsp)),
            "right_rtsp": _sanitize_rtsp_url(str(args.right_rtsp)),
            "input_runtime": str(args.input_runtime),
            "rtsp_transport": str(args.rtsp_transport),
            "input_buffer_frames": max(1, int(args.input_buffer_frames)),
            "homography_file": str(args.homography_file or ""),
            "output_standard": str(args.output_standard),
            "strict_fresh": not bool(args.allow_frame_reuse),
            "allow_frame_reuse": bool(args.allow_frame_reuse),
            "sync_pair_mode": "service",
            "sync_time_source": str(args.sync_time_source),
            "sync_match_max_delta_ms": float(args.sync_match_max_delta_ms or preset.sync_match_max_delta_ms),
            "sync_manual_offset_ms": float(args.sync_manual_offset_ms),
            "sync_auto_offset_window_sec": float(args.sync_auto_offset_window_sec),
            "sync_auto_offset_max_search_ms": float(args.sync_auto_offset_max_search_ms),
            "sync_recalibration_interval_sec": float(args.sync_recalibration_interval_sec),
            "sync_recalibration_trigger_skew_ms": float(args.sync_recalibration_trigger_skew_ms),
            "sync_recalibration_trigger_wait_ratio": float(args.sync_recalibration_trigger_wait_ratio),
            "sync_auto_offset_confidence_min": float(args.sync_auto_offset_confidence_min),
            "transmit_runtime": DEFAULT_NATIVE_TRANSMIT_RUNTIME,
            "transmit_target": DEFAULT_NATIVE_TRANSMIT_TARGET,
            "transmit_codec": str(preset.codec),
            "transmit_bitrate": DEFAULT_NATIVE_TRANSMIT_BITRATE or str(preset.bitrate),
            "transmit_preset": DEFAULT_NATIVE_TRANSMIT_PRESET,
            "transmit_muxer": str(preset.muxer),
            "transmit_width": int(preset.width),
            "transmit_height": int(preset.height),
            "transmit_fps": float(preset.fps),
            "stitch_output_scale": float(preset.output_scale),
            "probe_disabled": True,
        },
        "source_probe": source_probe,
        "runtime_validation": {
            "returncode": returncode,
            "hello": hello_payload,
            "status_counts": dict(status_counts),
            "source_mode_counts": dict(source_mode_counts),
            "sample_count": int(sum(status_counts.values())),
            "stitch_actual_fps_mean": round(_mean(stitch_values), 3),
            "stitch_actual_fps_max": round(max(stitch_values), 3) if stitch_values else 0.0,
            "transmit_fps_mean": round(_mean(transmit_values), 3),
            "transmit_fps_max": round(max(transmit_values), 3) if transmit_values else 0.0,
            "pair_skew_ms_mean": round(_mean(pair_skew_values), 3),
            "pair_source_skew_ms_mean": round(_mean(pair_source_skew_values), 3),
            "final_metrics": _compact_runtime_metrics(final_metrics),
            "stderr_tail": stderr_tail,
            "decision": decision,
            "bottleneck_guess": bottleneck_guess,
            "reasoning": reasons,
        },
    }

    report_path = Path(str(args.report_out).strip()).expanduser() if str(args.report_out).strip() else _make_validation_report_path(str(args.label))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"native-validate decision={decision} bottleneck={bottleneck_guess} report={report_path}")
    for reason in reasons:
        print(f"  - {reason}")
    return 0 if decision != "fail" else 1
