from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from validation_support import OUT_DIR, ROOT, add_common_runtime_input_args, counter_delta, mean, safe_float, safe_int

from stitching.project_defaults import DEFAULT_NATIVE_HOMOGRAPHY_PATH
from stitching.output_presets import get_output_preset
from stitching.runtime_client import RuntimeClient
from stitching.runtime_launcher import RuntimeLaunchSpec, build_runtime_command


def _parse_metrics(log_path: Path, *, mode: str) -> dict[str, Any]:
    metrics: list[dict[str, Any]] = []
    hello_payload: dict[str, Any] = {}
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event_type == "hello":
            hello_payload = payload
        elif event_type == "metrics":
            metrics.append(payload)
    if not metrics:
        return {
            "mode": mode,
            "error": "no_metrics",
            "log_path": str(log_path),
            "hello_payload": hello_payload,
        }
    first = metrics[0]
    last = metrics[-1]
    stitch_metric_key = "stitch_actual_fps" if any(safe_float(item.get("stitch_actual_fps")) > 0.0 for item in metrics) else "stitch_fps"
    stitch_fps_values = [safe_float(item.get(stitch_metric_key)) for item in metrics if safe_float(item.get(stitch_metric_key)) > 0.0]
    pair_skew_values = [safe_float(item.get("pair_skew_ms_mean")) for item in metrics if safe_float(item.get("pair_skew_ms_mean")) > 0.0]
    left_age_values = [safe_float(item.get("left_age_ms")) for item in metrics if safe_float(item.get("left_age_ms")) > 0.0]
    right_age_values = [safe_float(item.get("right_age_ms")) for item in metrics if safe_float(item.get("right_age_ms")) > 0.0]
    active_samples = [
        item
        for item in metrics
        if str(item.get("status") or "").strip().lower() == "active"
    ]
    return {
        "mode": mode,
        "log_path": str(log_path),
        "hello_payload": hello_payload,
        "metrics_samples": len(metrics),
        "active_samples": len(active_samples),
        "first_status": str(first.get("status") or ""),
        "last_status": str(last.get("status") or ""),
        "sync_pair_mode": str(last.get("sync_pair_mode") or first.get("sync_pair_mode") or mode),
        "stitch_metric_key": stitch_metric_key,
        "stitch_fps_last": safe_float(last.get(stitch_metric_key)),
        "stitch_fps_avg_nonzero": mean(stitch_fps_values),
        "stitch_fps_max": max(stitch_fps_values) if stitch_fps_values else 0.0,
        "pair_skew_ms_last": safe_float(last.get("pair_skew_ms_mean")),
        "pair_skew_ms_avg_nonzero": mean(pair_skew_values),
        "input_age_ms_last": {
            "left": safe_float(last.get("left_age_ms")),
            "right": safe_float(last.get("right_age_ms")),
        },
        "input_age_ms_avg_nonzero": {
            "left": mean(left_age_values),
            "right": mean(right_age_values),
        },
        "wait_deltas": {
            "both_streams": counter_delta(first, last, "wait_both_streams_count"),
            "sync_pair": counter_delta(first, last, "wait_sync_pair_count"),
            "next_frame": counter_delta(first, last, "wait_next_frame_count"),
            "paired_fresh": counter_delta(first, last, "wait_paired_fresh_count"),
            "paired_fresh_left": counter_delta(first, last, "wait_paired_fresh_left_count"),
            "paired_fresh_right": counter_delta(first, last, "wait_paired_fresh_right_count"),
            "paired_fresh_both": counter_delta(first, last, "wait_paired_fresh_both_count"),
            "realtime_fallback_pair": counter_delta(first, last, "realtime_fallback_pair_count"),
        },
        "failure_deltas": {
            "left_launch": counter_delta(first, last, "left_launch_failures"),
            "right_launch": counter_delta(first, last, "right_launch_failures"),
            "left_read": counter_delta(first, last, "left_read_failures"),
            "right_read": counter_delta(first, last, "right_read_failures"),
            "left_restart": counter_delta(first, last, "left_reader_restarts"),
            "right_restart": counter_delta(first, last, "right_reader_restarts"),
        },
        "reader_metrics_last": {
            "avg_frame_interval_ms": {
                "left": safe_float(last.get("left_avg_frame_interval_ms")),
                "right": safe_float(last.get("right_avg_frame_interval_ms")),
            },
            "max_frame_interval_ms": {
                "left": safe_float(last.get("left_max_frame_interval_ms")),
                "right": safe_float(last.get("right_max_frame_interval_ms")),
            },
            "late_frame_intervals": {
                "left": safe_int(last.get("left_late_frame_intervals")),
                "right": safe_int(last.get("right_late_frame_intervals")),
            },
            "buffer_span_ms": {
                "left": safe_float(last.get("left_buffer_span_ms")),
                "right": safe_float(last.get("right_buffer_span_ms")),
            },
            "buffer_seq_span": {
                "left": safe_int(last.get("left_buffer_seq_span")),
                "right": safe_int(last.get("right_buffer_seq_span")),
            },
            "avg_read_ms": {
                "left": safe_float(last.get("left_avg_read_ms")),
                "right": safe_float(last.get("right_avg_read_ms")),
            },
            "max_read_ms": {
                "left": safe_float(last.get("left_max_read_ms")),
                "right": safe_float(last.get("right_max_read_ms")),
            },
            "selected_lag_ms": {
                "left": safe_float(last.get("selected_left_lag_ms")),
                "right": safe_float(last.get("selected_right_lag_ms")),
            },
            "selected_lag_frames": {
                "left": safe_int(last.get("selected_left_lag_frames")),
                "right": safe_int(last.get("selected_right_lag_frames")),
            },
            "fresh_wait_age_ms_avg": {
                "left": safe_float(last.get("wait_paired_fresh_left_age_ms_avg")),
                "right": safe_float(last.get("wait_paired_fresh_right_age_ms_avg")),
            },
        },
        "final_errors": {
            "left": str(last.get("left_last_error") or ""),
            "right": str(last.get("right_last_error") or ""),
            "probe": str(last.get("probe_last_error") or ""),
            "transmit": str(last.get("transmit_last_error") or ""),
        },
    }


def _build_runtime_spec(args: argparse.Namespace, *, mode: str, run_tag: str) -> tuple[RuntimeLaunchSpec, Path]:
    mode_output_path = OUT_DIR / f"compare_pair_{mode}_{run_tag}.ts"
    try:
        mode_output_target = str(mode_output_path.relative_to(ROOT))
    except ValueError:
        mode_output_target = str(mode_output_path)
    preset = get_output_preset(str(args.output_standard))
    allow_frame_reuse = bool(args.allow_frame_reuse) or bool(preset.allow_frame_reuse)
    sync_match_max_delta_ms = float(args.sync_match_max_delta_ms) if float(args.sync_match_max_delta_ms) > 0.0 else float(preset.sync_match_max_delta_ms)
    production_output_runtime = "none" if bool(args.disable_production_output) else str(args.production_output_runtime)
    production_output_target = "" if bool(args.disable_production_output) else mode_output_target
    production_output_codec = "" if bool(args.disable_production_output) else str(preset.codec or "h264_nvenc")
    production_output_bitrate = "" if bool(args.disable_production_output) else str(preset.bitrate or "16M")
    production_output_preset = "" if bool(args.disable_production_output) else "p4"
    production_output_muxer = "" if bool(args.disable_production_output) else str(preset.muxer or "")
    production_output_width = 0 if bool(args.disable_production_output) else max(0, int(preset.width))
    production_output_height = 0 if bool(args.disable_production_output) else max(0, int(preset.height))
    production_output_fps = 0.0 if bool(args.disable_production_output) else max(0.0, float(preset.fps))
    spec = RuntimeLaunchSpec(
        emit_hello=True,
        once=False,
        heartbeat_ms=1000,
        left_rtsp=str(args.left_rtsp),
        right_rtsp=str(args.right_rtsp),
        input_runtime=str(args.input_runtime),
        input_pipe_format=str(args.input_pipe_format),
        homography_file=str(args.homography_file),
        transport=str(args.transport),
        input_buffer_frames=max(1, int(args.input_buffer_frames)),
        disable_freeze_detection=bool(args.disable_freeze_detection),
        timeout_sec=max(0.1, float(args.rtsp_timeout_sec)),
        reconnect_cooldown_sec=max(0.1, float(args.reconnect_cooldown_sec)),
        output_runtime="none",
        output_target="",
        output_codec="",
        output_bitrate="",
        output_preset="",
        output_muxer="",
        production_output_runtime=production_output_runtime,
        production_output_profile="production-compatible",
        production_output_target=production_output_target,
        production_output_codec=production_output_codec,
        production_output_bitrate=production_output_bitrate,
        production_output_preset=production_output_preset,
        production_output_muxer=production_output_muxer,
        production_output_width=production_output_width,
        production_output_height=production_output_height,
        production_output_fps=production_output_fps,
        sync_pair_mode=str(mode),
        allow_frame_reuse=allow_frame_reuse,
        pair_reuse_max_age_ms=max(1.0, float(args.pair_reuse_max_age_ms)),
        pair_reuse_max_consecutive=max(1, int(args.pair_reuse_max_consecutive)),
        sync_match_max_delta_ms=max(1.0, sync_match_max_delta_ms),
        sync_manual_offset_ms=0.0,
        stitch_output_scale=max(0.1, float(preset.output_scale)),
        stitch_every_n=1,
        gpu_mode="on",
        gpu_device=0,
    )
    return spec, mode_output_path


def _run_mode(args: argparse.Namespace, *, mode: str, run_tag: str) -> tuple[dict[str, Any], int, Path]:
    log_path = OUT_DIR / f"compare_pair_{mode}_{run_tag}_runtime.log"
    spec, mode_output_path = _build_runtime_spec(args, mode=mode, run_tag=run_tag)
    command = build_runtime_command(spec)
    command_line = subprocess.list2cmdline(command)
    client = RuntimeClient.launch(spec)
    event_lines: list[str] = []
    returncode = 0
    try:
        hello = client.wait_for_hello(timeout_sec=15.0)
        event_lines.append(json.dumps(hello.raw, ensure_ascii=False))
        deadline = datetime.now().timestamp() + max(1.0, float(args.duration_sec))
        while datetime.now().timestamp() < deadline:
            event = client.read_event(timeout_sec=1.5)
            if event is None:
                if client.process.poll() is not None:
                    break
                continue
            event_lines.append(json.dumps(event.raw, ensure_ascii=False))
    except Exception as exc:
        event_lines.append(f"[compare-pair-modes exception] {exc}")
    finally:
        try:
            client.shutdown()
        except Exception:
            pass
        try:
            client.process.wait(timeout=5)
        except Exception:
            client.process.kill()
            try:
                client.process.wait(timeout=5)
            except Exception:
                pass
        returncode = int(client.process.returncode or 0)
        stderr_tail = client.get_stderr_tail().strip()
        if stderr_tail:
            event_lines.append("[runtime-stderr]")
            event_lines.append(stderr_tail)
    log_path.write_text("\n".join(event_lines) + ("\n" if event_lines else ""), encoding="utf-8", errors="replace")
    summary = _parse_metrics(log_path, mode=mode)
    summary["returncode"] = returncode
    summary["command"] = command
    summary["command_line"] = command_line
    summary["transmit_capture_path"] = str(mode_output_path)
    return summary, returncode, log_path


def _build_comparison(latest: dict[str, Any], service: dict[str, Any]) -> dict[str, Any]:
    if str(latest.get("error") or "") or str(service.get("error") or ""):
        return {
            "error": "comparison_incomplete",
            "latest_error": str(latest.get("error") or ""),
            "service_error": str(service.get("error") or ""),
        }
    latest_waits = latest.get("wait_deltas") or {}
    service_waits = service.get("wait_deltas") or {}
    latest_failures = latest.get("failure_deltas") or {}
    service_failures = service.get("failure_deltas") or {}
    return {
        "stitch_fps_last_delta_service_minus_latest": safe_float(service.get("stitch_fps_last")) - safe_float(latest.get("stitch_fps_last")),
        "stitch_fps_avg_delta_service_minus_latest": safe_float(service.get("stitch_fps_avg_nonzero")) - safe_float(latest.get("stitch_fps_avg_nonzero")),
        "pair_skew_ms_last_delta_service_minus_latest": safe_float(service.get("pair_skew_ms_last")) - safe_float(latest.get("pair_skew_ms_last")),
        "wait_delta_service_minus_latest": {
            key: safe_int(service_waits.get(key)) - safe_int(latest_waits.get(key))
            for key in sorted(set(latest_waits) | set(service_waits))
        },
        "failure_delta_service_minus_latest": {
            key: safe_int(service_failures.get(key)) - safe_int(latest_failures.get(key))
            for key in sorted(set(latest_failures) | set(service_failures))
        },
    }


def _print_summary(summary: dict[str, Any]) -> None:
    mode = str(summary.get("mode") or "?")
    print(f"[{mode}]")
    print("returncode", int(summary.get("returncode") or 0))
    print("log_path", summary.get("log_path"))
    if str(summary.get("error") or ""):
        print("error", summary.get("error"))
        return
    print("status", f"{summary.get('first_status')} -> {summary.get('last_status')}")
    print("metrics_samples", int(summary.get("metrics_samples") or 0))
    print("active_samples", int(summary.get("active_samples") or 0))
    print("stitch_metric_key", summary.get("stitch_metric_key"))
    print(
        "stitch_fps",
        {
            "last": round(safe_float(summary.get("stitch_fps_last")), 3),
            "avg_nonzero": round(safe_float(summary.get("stitch_fps_avg_nonzero")), 3),
            "max": round(safe_float(summary.get("stitch_fps_max")), 3),
        },
    )
    print(
        "pair_skew_ms",
        {
            "last": round(safe_float(summary.get("pair_skew_ms_last")), 3),
            "avg_nonzero": round(safe_float(summary.get("pair_skew_ms_avg_nonzero")), 3),
        },
    )
    print("wait_deltas", summary.get("wait_deltas"))
    print("failure_deltas", summary.get("failure_deltas"))
    print("final_errors", summary.get("final_errors"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare native runtime pair scheduler modes using direct JSON monitor output")
    add_common_runtime_input_args(parser)
    parser.add_argument("--homography-file", default=DEFAULT_NATIVE_HOMOGRAPHY_PATH)
    parser.add_argument("--duration-sec", type=float, default=6.0)
    parser.add_argument("--disable-freeze-detection", action="store_true")
    parser.add_argument("--sync-match-max-delta-ms", type=float, default=0.0)
    parser.add_argument("--allow-frame-reuse", action="store_true")
    parser.add_argument("--disable-production-output", action="store_true")
    parser.add_argument("--production-output-runtime", choices=["ffmpeg", "gpu-direct"], default="ffmpeg")
    parser.add_argument("--summary-json", default=str(OUT_DIR / "compare_pair_modes_summary.json"))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_tag = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    latest_summary, latest_rc, latest_log = _run_mode(args, mode="latest", run_tag=run_tag)
    service_summary, service_rc, service_log = _run_mode(args, mode="service", run_tag=run_tag)

    latest_json = OUT_DIR / "pair_mode_latest_summary.json"
    latest_json.write_text(json.dumps(latest_summary, ensure_ascii=True, indent=2), encoding="utf-8")
    service_json = OUT_DIR / "pair_mode_service_summary.json"
    service_json.write_text(json.dumps(service_summary, ensure_ascii=True, indent=2), encoding="utf-8")

    comparison = {
        "latest": latest_summary,
        "service": service_summary,
        "comparison": _build_comparison(latest_summary, service_summary),
        "artifacts": {
            "latest_log": str(latest_log),
            "service_log": str(service_log),
            "latest_summary_json": str(latest_json),
            "service_summary_json": str(service_json),
            "latest_transmit_capture": str(latest_summary.get("transmit_capture_path") or ""),
            "service_transmit_capture": str(service_summary.get("transmit_capture_path") or ""),
        },
    }
    summary_path = Path(str(args.summary_json)).expanduser()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(comparison, ensure_ascii=True, indent=2), encoding="utf-8")

    _print_summary(latest_summary)
    _print_summary(service_summary)
    print("[comparison]")
    print(comparison["comparison"])
    print("summary_json", summary_path)

    if latest_rc != 0 or service_rc != 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
