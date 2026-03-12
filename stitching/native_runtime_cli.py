from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time
from typing import Any

from stitching.final_stream_viewer import FinalStreamViewerSpec, launch_final_stream_viewer
from stitching.runtime_client import RuntimeClient
from stitching.runtime_launcher import RuntimeLaunchSpec


DEFAULT_OUTPUT_TARGET = "udp://127.0.0.1:23000?pkt_size=1316"
DEFAULT_VIEWER_TARGET = "udp://127.0.0.1:23000"


class SystemStatsSampler:
    def __init__(self, interval_sec: float = 1.0) -> None:
        self._interval_sec = max(0.2, float(interval_sec))
        self._psutil = self._load_psutil()
        self._powershell = self._resolve_powershell()
        self._nvidia_smi = self._resolve_nvidia_smi()
        self._cpu_percent = -1.0
        self._cpu_per_core: list[float] = []
        self._gpu_percent = -1.0
        self._gpu_mem_used = -1.0
        self._gpu_mem_total = -1.0
        self._gpu_temp_c = -1.0
        self._last_error = ""
        self._last_sample_sec = 0.0

    def _load_psutil(self) -> Any | None:
        try:
            import psutil  # type: ignore
        except Exception:
            return None
        return psutil

    def _resolve_powershell(self) -> str | None:
        candidates = [
            shutil.which("powershell"),
            shutil.which("pwsh"),
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _resolve_nvidia_smi(self) -> str | None:
        candidates = [
            shutil.which("nvidia-smi"),
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def start(self) -> None:
        self._refresh(force=True)

    def stop(self) -> None:
        return

    def snapshot(self) -> dict[str, Any]:
        self._refresh(force=False)
        return {
            "cpu_percent": float(self._cpu_percent),
            "cpu_per_core": list(self._cpu_per_core),
            "gpu_percent": float(self._gpu_percent),
            "gpu_mem_used": float(self._gpu_mem_used),
            "gpu_mem_total": float(self._gpu_mem_total),
            "gpu_temp_c": float(self._gpu_temp_c),
            "last_error": str(self._last_error),
        }

    def _sample_cpu(self) -> tuple[float, list[float]]:
        if self._psutil is not None:
            try:
                per_core = [float(v) for v in self._psutil.cpu_percent(interval=None, percpu=True)]
                if per_core:
                    return float(sum(per_core) / len(per_core)), per_core
            except Exception:
                pass
        total, per_core = self._sample_cpu_typeperf()
        if per_core or total >= 0.0:
            return total, per_core
        total, per_core = self._sample_cpu_powershell()
        if per_core or total >= 0.0:
            return total, per_core
        return self._sample_cpu_wmic()

    def _sample_cpu_typeperf(self) -> tuple[float, list[float]]:
        try:
            cpu_count = max(1, int(os.cpu_count() or 1))
            counters = [r"\Processor(_Total)\% Processor Time"]
            counters.extend(rf"\Processor({index})\% Processor Time" for index in range(cpu_count))
            out = subprocess.check_output(
                ["typeperf", *counters, "-sc", "1"],
                timeout=3.0,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            lines = [line.strip() for line in out.splitlines() if line.strip()]
            if len(lines) < 2:
                return -1.0, []
            values_line = lines[-1]
            if values_line.startswith("\"") and values_line.endswith("\""):
                values = values_line[1:-1].split("\",\"")
            else:
                values = values_line.split(",")
            if len(values) < 2:
                return -1.0, []
            parsed: list[float] = []
            for value in values[1:]:
                cleaned = value.strip().strip('"').replace(",", ".")
                try:
                    parsed.append(float(cleaned))
                except ValueError:
                    parsed.append(-1.0)
            if not parsed:
                return -1.0, []
            total = parsed[0]
            per_core = [value for value in parsed[1:] if value >= 0.0]
            if total < 0.0 and per_core:
                total = float(sum(per_core) / len(per_core))
            return total, per_core
        except Exception:
            return -1.0, []

    def _sample_cpu_powershell(self) -> tuple[float, list[float]]:
        if self._powershell is None:
            return -1.0, []
        try:
            script = (
                "$samples = (Get-Counter '\\Processor(*)\\% Processor Time').CounterSamples | "
                "Select-Object InstanceName,CookedValue; "
                "$samples | ConvertTo-Json -Compress"
            )
            out = subprocess.check_output(
                [
                    self._powershell,
                    "-NoProfile",
                    "-Command",
                    script,
                ],
                timeout=2.5,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            payload = json.loads(out)
            items = payload if isinstance(payload, list) else [payload]
            total = -1.0
            per_core_pairs: list[tuple[int, float]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("InstanceName", "")).strip()
                value = item.get("CookedValue")
                try:
                    pct = float(value)
                except (TypeError, ValueError):
                    continue
                if name.lower() == "_total":
                    total = pct
                elif re.fullmatch(r"\d+", name):
                    per_core_pairs.append((int(name), pct))
            per_core_pairs.sort(key=lambda item: item[0])
            per_core = [pct for _, pct in per_core_pairs]
            if total < 0.0 and per_core:
                total = float(sum(per_core) / len(per_core))
            return total, per_core
        except Exception:
            return -1.0, []

    def _sample_cpu_wmic(self) -> tuple[float, list[float]]:
        try:
            out = subprocess.check_output(
                ["wmic", "cpu", "get", "loadpercentage"],
                timeout=2.0,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for raw in out.splitlines():
                line = raw.strip()
                if not line or not any(ch.isdigit() for ch in line):
                    continue
                try:
                    value = float(line)
                except ValueError:
                    continue
                return value, []
        except Exception:
            pass
        return -1.0, []

    def _sample_gpu(self) -> tuple[float, float, float, float]:
        if self._nvidia_smi is None:
            return -1.0, -1.0, -1.0, -1.0
        try:
            out = subprocess.check_output(
                [
                    self._nvidia_smi,
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                timeout=1.5,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            line = out.strip().splitlines()[0]
            vals = [x.strip() for x in line.split(",")]
            if len(vals) < 4:
                return -1.0, -1.0, -1.0, -1.0
            return float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
        except Exception:
            return -1.0, -1.0, -1.0, -1.0

    def _refresh(self, *, force: bool) -> None:
        now = time.time()
        if not force and (now - self._last_sample_sec) < self._interval_sec:
            return
        if self._psutil is not None:
            try:
                self._psutil.cpu_percent(interval=None, percpu=True)
            except Exception:
                pass
        try:
            cpu, per_core = self._sample_cpu()
            gpu, gmem_used, gmem_total, gtemp = self._sample_gpu()
            self._cpu_percent = cpu
            self._cpu_per_core = per_core
            self._gpu_percent = gpu
            self._gpu_mem_used = gmem_used
            self._gpu_mem_total = gmem_total
            self._gpu_temp_c = gtemp
            self._last_error = ""
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
        self._last_sample_sec = now


def add_native_runtime_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--left-rtsp", required=True, help="Left RTSP URL")
    cmd.add_argument("--right-rtsp", required=True, help="Right RTSP URL")
    cmd.add_argument("--input-runtime", choices=["ffmpeg-cpu", "ffmpeg-cuda"], default="ffmpeg-cuda")
    cmd.add_argument("--ffmpeg-bin", default="", help="Optional explicit ffmpeg.exe path")
    cmd.add_argument("--rtsp-transport", choices=["tcp", "udp"], default="tcp")
    cmd.add_argument("--rtsp-timeout-sec", type=float, default=10.0)
    cmd.add_argument("--reconnect-cooldown-sec", type=float, default=1.0)
    cmd.add_argument("--heartbeat-ms", type=int, default=1000)
    cmd.add_argument("--homography-file", default="", help="Optional fixed 3x3 homography JSON path")
    cmd.add_argument("--output-runtime", choices=["none", "ffmpeg"], default="ffmpeg")
    cmd.add_argument("--output-target", default=DEFAULT_OUTPUT_TARGET, help="Encoded output target")
    cmd.add_argument("--output-codec", default="h264_nvenc")
    cmd.add_argument("--output-bitrate", default="12M")
    cmd.add_argument("--output-preset", default="p4")
    cmd.add_argument("--output-muxer", default="")
    cmd.add_argument("--sync-pair-mode", choices=["none", "latest", "oldest"], default="none")
    cmd.add_argument("--sync-match-max-delta-ms", type=float, default=35.0)
    cmd.add_argument("--sync-manual-offset-ms", type=float, default=0.0)
    cmd.add_argument("--stitch-output-scale", type=float, default=1.0)
    cmd.add_argument("--stitch-every-n", type=int, default=1)
    cmd.add_argument("--gpu-mode", choices=["off", "auto", "on"], default="on")
    cmd.add_argument("--gpu-device", type=int, default=0)
    cmd.add_argument("--headless-benchmark", action="store_true")
    cmd.add_argument("--duration-sec", type=float, default=0.0, help="0 runs until Ctrl+C")
    cmd.add_argument("--status-interval-sec", type=float, default=5.0, help="Status line interval while state is unchanged")
    cmd.add_argument("--monitor-mode", choices=["dashboard", "compact", "json"], default="dashboard")
    cmd.add_argument("--recent-events", type=int, default=8, help="How many recent non-metric events to keep in dashboard mode")
    cmd.add_argument("--verbose-events", action="store_true", help="Print every runtime event as raw JSON")
    cmd.add_argument("--viewer", action="store_true", help="Launch ffplay and watch the final output stream")
    cmd.add_argument("--viewer-target", default="", help="Override viewer target (defaults to UDP local stream)")
    cmd.add_argument("--viewer-title", default="Hogak Final Stream")


def _compact_metrics(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    status = str(payload.get("status") or "-")
    parts.append(f"status={status}")
    parts.append(f"calibrated={bool(payload.get('calibrated'))}")
    parts.append(f"output_active={bool(payload.get('output_active'))}")

    output_width = int(payload.get("output_width") or 0)
    output_height = int(payload.get("output_height") or 0)
    if output_width > 0 and output_height > 0:
        parts.append(f"output={output_width}x{output_height}")

    stitch_fps = payload.get("stitch_fps")
    if isinstance(stitch_fps, (int, float)):
        parts.append(f"stitch_fps={float(stitch_fps):.2f}")
    output_written_fps = payload.get("output_written_fps")
    if isinstance(output_written_fps, (int, float)):
        parts.append(f"output_fps={float(output_written_fps):.2f}")

    pair_skew_ms_mean = payload.get("pair_skew_ms_mean")
    if isinstance(pair_skew_ms_mean, (int, float)):
        parts.append(f"pair_skew_ms={float(pair_skew_ms_mean):.2f}")

    parts.append(f"written={int(payload.get('output_frames_written') or 0)}")

    output_effective_codec = str(payload.get("output_effective_codec") or "").strip()
    if output_effective_codec:
        parts.append(f"codec={output_effective_codec}")

    gpu_errors = int(payload.get("gpu_errors") or 0)
    if gpu_errors > 0:
        parts.append(f"gpu_errors={gpu_errors}")

    for key in ("output_last_error", "left_last_error", "right_last_error"):
        value = str(payload.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def _status_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        payload.get("status"),
        payload.get("calibrated"),
        payload.get("output_active"),
        payload.get("output_width"),
        payload.get("output_height"),
        payload.get("output_effective_codec"),
        payload.get("output_last_error"),
        payload.get("left_last_error"),
        payload.get("right_last_error"),
        payload.get("gpu_errors"),
    )


def _format_flag(value: bool) -> str:
    return "yes" if value else "no"


def _trim_text(text: str, width: int) -> str:
    if width <= 0:
        return text
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _render_dashboard(
    payload: dict[str, Any],
    *,
    hello_payload: dict[str, Any],
    viewer_enabled: bool,
    system_stats: dict[str, Any],
    recent_events: deque[str],
    last_update_sec: float,
) -> str:
    columns = max(80, shutil.get_terminal_size(fallback=(120, 30)).columns)
    short = max(20, columns - 18)
    status = str(payload.get("status") or "-")
    output_width = int(payload.get("output_width") or 0)
    output_height = int(payload.get("output_height") or 0)
    output_size = f"{output_width}x{output_height}" if output_width > 0 and output_height > 0 else "-"
    runtime_name = str(hello_payload.get("runtime") or "native-runtime")
    protocol = str(hello_payload.get("protocol") or "jsonl-v1")
    codec = str(payload.get("output_effective_codec") or "-")
    output_target = _trim_text(str(payload.get("output_target") or "-"), short)
    output_error = _trim_text(str(payload.get("output_last_error") or "-"), short)
    left_error = _trim_text(str(payload.get("left_last_error") or "-"), short)
    right_error = _trim_text(str(payload.get("right_last_error") or "-"), short)
    cpu_percent = float(system_stats.get("cpu_percent") or -1.0)
    cpu_per_core = [float(v) for v in system_stats.get("cpu_per_core", [])]
    gpu_percent = float(system_stats.get("gpu_percent") or -1.0)
    gpu_mem_used = float(system_stats.get("gpu_mem_used") or -1.0)
    gpu_mem_total = float(system_stats.get("gpu_mem_total") or -1.0)
    gpu_temp_c = float(system_stats.get("gpu_temp_c") or -1.0)

    lines = [
        f"Hogak Native Runtime Monitor  runtime={runtime_name}  protocol={protocol}",
        "=" * min(columns, 120),
        (
            f"status={status}  calibrated={_format_flag(bool(payload.get('calibrated')))}  "
            f"viewer={_format_flag(viewer_enabled)}  updated_at={time.strftime('%H:%M:%S', time.localtime(last_update_sec))}"
        ),
        (
            f"input  left_fps={float(payload.get('left_fps') or 0.0):6.2f}  "
            f"right_fps={float(payload.get('right_fps') or 0.0):6.2f}  "
            f"pair_skew_ms={float(payload.get('pair_skew_ms_mean') or 0.0):7.2f}  "
            f"buffered_output={int(payload.get('output_frames_written') or 0)}"
        ),
        (
            f"stitch stitch_fps={float(payload.get('stitch_fps') or 0.0):6.2f}  "
            f"worker_fps={float(payload.get('worker_fps') or 0.0):6.2f}  "
            f"output_fps={float(payload.get('output_written_fps') or 0.0):6.2f}  "
            f"gpu_warp={int(payload.get('gpu_warp_count') or 0)}  "
            f"gpu_blend={int(payload.get('gpu_blend_count') or 0)}  "
            f"cpu_blend={int(payload.get('cpu_blend_count') or 0)}"
        ),
        (
            f"output active={_format_flag(bool(payload.get('output_active')))}  "
            f"size={output_size}  codec={codec}  "
            f"dropped={int(payload.get('output_frames_dropped') or 0)}"
        ),
        (
            f"luma   stitched={float(payload.get('stitched_mean_luma') or 0.0):6.2f}  "
            f"left={float(payload.get('left_mean_luma') or 0.0):6.2f}  "
            f"right={float(payload.get('right_mean_luma') or 0.0):6.2f}  "
            f"warped={float(payload.get('warped_mean_luma') or 0.0):6.2f}"
        ),
        (
            f"errors gpu_errors={int(payload.get('gpu_errors') or 0)}  "
            f"left_stale={int(payload.get('left_stale_drops') or 0)}  "
            f"right_stale={int(payload.get('right_stale_drops') or 0)}"
        ),
        (
            f"system cpu_total={cpu_percent:6.2f}%  "
            f"gpu_total={gpu_percent:6.2f}%  "
            f"gpu_mem={gpu_mem_used:6.0f}/{gpu_mem_total:6.0f} MB  "
            f"gpu_temp={gpu_temp_c:5.1f} C"
        ),
        f"target {output_target}",
        f"outerr {output_error}",
        f"left   {left_error}",
        f"right  {right_error}",
        "-" * min(columns, 120),
        "cpu cores:",
    ]
    if cpu_per_core:
        chunk = max(1, min(8, columns // 18))
        for start in range(0, len(cpu_per_core), chunk):
            segment = cpu_per_core[start : start + chunk]
            lines.append("  " + "  ".join(f"{start + idx}:{value:4.1f}%" for idx, value in enumerate(segment)))
    else:
        lines.append("  (unavailable)")
    lines.extend(
        [
            "-" * min(columns, 120),
        "recent events:",
        ]
    )
    if recent_events:
        lines.extend(recent_events)
    else:
        lines.append("(no recent events)")
    return "\n".join(lines)


def _print_dashboard(
    payload: dict[str, Any],
    *,
    hello_payload: dict[str, Any],
    viewer_enabled: bool,
    system_stats: dict[str, Any],
    recent_events: deque[str],
    last_update_sec: float,
) -> None:
    if os.name == "nt":
        os.system("cls")
    else:
        print("\033[2J\033[H", end="")
    print(
        _render_dashboard(
            payload,
            hello_payload=hello_payload,
            viewer_enabled=viewer_enabled,
            system_stats=system_stats,
            recent_events=recent_events,
            last_update_sec=last_update_sec,
        )
    )


def _format_event_line(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "status":
        message = str(payload.get("message") or "").strip() or "-"
        return f"status: {message}"
    if event_type in {"warning", "error"}:
        message = str(payload.get("message") or "").strip() or "-"
        return f"{event_type}: {message}"
    return json.dumps({"type": event_type, "payload": payload}, ensure_ascii=False)


def run_native_runtime_monitor(args: argparse.Namespace) -> int:
    spec = RuntimeLaunchSpec(
        emit_hello=True,
        once=False,
        heartbeat_ms=max(100, int(args.heartbeat_ms)),
        left_rtsp=args.left_rtsp,
        right_rtsp=args.right_rtsp,
        input_runtime=args.input_runtime,
        ffmpeg_bin=str(args.ffmpeg_bin or ""),
        homography_file=str(args.homography_file or ""),
        transport=args.rtsp_transport,
        video_codec="h264",
        timeout_sec=max(0.1, float(args.rtsp_timeout_sec)),
        reconnect_cooldown_sec=max(0.1, float(args.reconnect_cooldown_sec)),
        output_runtime=args.output_runtime,
        output_target=str(args.output_target or ""),
        output_codec=str(args.output_codec),
        output_bitrate=str(args.output_bitrate),
        output_preset=str(args.output_preset),
        output_muxer=str(args.output_muxer),
        sync_pair_mode=str(args.sync_pair_mode),
        sync_match_max_delta_ms=max(1.0, float(args.sync_match_max_delta_ms)),
        sync_manual_offset_ms=float(args.sync_manual_offset_ms),
        stitch_output_scale=max(0.1, float(args.stitch_output_scale)),
        stitch_every_n=max(1, int(args.stitch_every_n)),
        gpu_mode=str(args.gpu_mode),
        gpu_device=max(0, int(args.gpu_device)),
        headless_benchmark=bool(args.headless_benchmark),
    )

    client = RuntimeClient.launch(spec)
    viewer_proc: subprocess.Popen[bytes] | None = None
    viewer_launch_attempted = False
    viewer_retry_after_sec = 0.0
    stats_sampler = SystemStatsSampler(interval_sec=1.0)
    stats_sampler.start()
    runtime_stderr = ""
    last_status_signature: tuple[Any, ...] | None = None
    last_status_emit_sec = 0.0
    last_metrics_payload: dict[str, Any] = {}
    last_dashboard_render_sec = 0.0
    recent_events: deque[str] = deque(maxlen=max(1, int(args.recent_events)))
    hello_payload: dict[str, Any] = {}
    try:
        hello = client.wait_for_hello(timeout_sec=5.0)
        hello_payload = dict(hello.payload)
        if args.verbose_events:
            print(json.dumps(hello.raw, ensure_ascii=False))

        if args.viewer and args.output_runtime != "none":
            recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] viewer pending: waiting for output stream")

        deadline = 0.0 if float(args.duration_sec) <= 0.0 else (time.time() + float(args.duration_sec))
        while True:
            if deadline and time.time() >= deadline:
                break
            event = client.read_event(timeout_sec=1.5)
            if event is None:
                if client.process.poll() is not None:
                    break
                continue
            if event.type == "metrics":
                last_metrics_payload = dict(event.payload)
                if (
                    args.viewer
                    and args.output_runtime != "none"
                    and viewer_proc is None
                    and time.time() >= viewer_retry_after_sec
                    and (
                        (
                            bool(event.payload.get("output_active"))
                            and int(event.payload.get("output_frames_written") or 0) >= 15
                        )
                        or int(event.payload.get("output_frames_written") or 0) >= 30
                    )
                ):
                    viewer_launch_attempted = True
                    viewer_target = str(args.viewer_target or args.output_target or DEFAULT_VIEWER_TARGET)
                    try:
                        viewer_proc = launch_final_stream_viewer(
                            FinalStreamViewerSpec(
                                target=viewer_target,
                                window_title=str(args.viewer_title),
                            )
                        )
                        viewer_message = f"[{time.strftime('%H:%M:%S')}] viewer launched pid={viewer_proc.pid}"
                        recent_events.appendleft(viewer_message)
                        if args.monitor_mode == "compact" and not args.verbose_events:
                            print(viewer_message)
                    except Exception as exc:
                        viewer_retry_after_sec = time.time() + 2.0
                        viewer_message = f"[{time.strftime('%H:%M:%S')}] viewer error: {exc}"
                        recent_events.appendleft(viewer_message)
                        if args.monitor_mode == "compact" and not args.verbose_events:
                            print(viewer_message)
                if args.verbose_events:
                    print(json.dumps(event.raw, ensure_ascii=False))
                elif args.monitor_mode == "json":
                    print(json.dumps(event.raw, ensure_ascii=False))
                elif args.monitor_mode == "dashboard":
                    _print_dashboard(
                        last_metrics_payload,
                        hello_payload=hello_payload,
                        viewer_enabled=viewer_proc is not None,
                        system_stats=stats_sampler.snapshot(),
                        recent_events=recent_events,
                        last_update_sec=time.time(),
                    )
                    last_dashboard_render_sec = time.time()
                else:
                    now_sec = time.time()
                    status_signature = _status_signature(event.payload)
                    status_interval_sec = max(0.5, float(args.status_interval_sec))
                    if (
                        status_signature != last_status_signature
                        or now_sec - last_status_emit_sec >= status_interval_sec
                    ):
                        print(_compact_metrics(event.payload))
                        last_status_signature = status_signature
                        last_status_emit_sec = now_sec
            else:
                event_line = _format_event_line(event.type, event.payload)
                recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] {event_line}")
                if args.verbose_events or args.monitor_mode == "json":
                    print(json.dumps(event.raw, ensure_ascii=False))
                elif args.monitor_mode == "compact":
                    print(event_line)
                elif last_metrics_payload and time.time() - last_dashboard_render_sec >= 0.2:
                    _print_dashboard(
                        last_metrics_payload,
                        hello_payload=hello_payload,
                        viewer_enabled=viewer_proc is not None,
                        system_stats=stats_sampler.snapshot(),
                        recent_events=recent_events,
                        last_update_sec=time.time(),
                    )
                    last_dashboard_render_sec = time.time()
            if client.process.poll() is not None:
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            client.shutdown()
        except Exception:
            pass
        try:
            client.process.wait(timeout=5)
        except Exception:
            client.process.kill()
        runtime_stderr = client.get_stderr_tail().strip()
        stats_sampler.stop()
        if viewer_proc is not None and viewer_proc.poll() is None:
            viewer_proc.send_signal(signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.SIGINT)
            try:
                viewer_proc.wait(timeout=3)
            except Exception:
                viewer_proc.kill()

    returncode = int(client.process.returncode or 0)
    if args.monitor_mode == "dashboard" and last_metrics_payload:
        recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] process exited returncode={returncode}")
        _print_dashboard(
            last_metrics_payload,
            hello_payload=hello_payload,
            viewer_enabled=False,
            system_stats=stats_sampler.snapshot(),
            recent_events=recent_events,
            last_update_sec=time.time(),
        )
    if returncode != 0:
        print(f"native_runtime_exit_code={returncode}")
    if runtime_stderr:
        print("[native-runtime stderr]")
        print(runtime_stderr)
    return returncode
