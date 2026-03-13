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
import sys
import time
from typing import Any

from stitching.final_stream_viewer import FinalStreamViewerSpec, launch_final_stream_viewer
from stitching.output_presets import OUTPUT_PRESETS, get_output_preset
from stitching.project_defaults import (
    DEFAULT_NATIVE_INPUT_BUFFER_FRAMES,
    DEFAULT_NATIVE_INPUT_PIPE_FORMAT,
    DEFAULT_NATIVE_INPUT_RUNTIME,
    DEFAULT_NATIVE_HOMOGRAPHY_PATH,
    DEFAULT_NATIVE_PAIR_REUSE_MAX_AGE_MS,
    DEFAULT_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE,
    DEFAULT_NATIVE_PROBE_SOURCE,
    DEFAULT_NATIVE_PROBE_TARGET,
    DEFAULT_NATIVE_RECONNECT_COOLDOWN_SEC,
    DEFAULT_NATIVE_RTSP_TIMEOUT_SEC,
    DEFAULT_NATIVE_RTSP_TRANSPORT,
    DEFAULT_NATIVE_STATUS_INTERVAL_SEC,
    DEFAULT_NATIVE_SYNC_MATCH_MAX_DELTA_MS,
    DEFAULT_NATIVE_TRANSMIT_TARGET,
    DEFAULT_NATIVE_VLC_PREVIEW_TARGET,
    default_native_viewer_backend,
    default_left_rtsp,
    default_output_standard,
    default_right_rtsp,
)
from stitching.runtime_client import RuntimeClient
from stitching.runtime_launcher import RuntimeLaunchSpec
from stitching.runtime_launcher import query_gpu_direct_status


DEFAULT_PROBE_TARGET = DEFAULT_NATIVE_PROBE_TARGET
DEFAULT_TRANSMIT_TARGET = DEFAULT_NATIVE_TRANSMIT_TARGET
DEFAULT_VLC_PREVIEW_TARGET = DEFAULT_NATIVE_VLC_PREVIEW_TARGET
DEFAULT_VIEWER_TARGET = "udp://127.0.0.1:23000"
DEFAULT_PROBE_SOURCE = DEFAULT_NATIVE_PROBE_SOURCE
_OUTPUT_ROLE_FIELDS = ("runtime", "target", "codec", "bitrate", "preset", "muxer", "width", "height", "fps")


def _prompt_runtime_start(default_value: str, *, default_vlc_low_latency: bool = False) -> tuple[str, bool, bool]:
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return default_value, False, default_vlc_low_latency

    selection = {
        "value": default_value,
        "run_calibration_first": True,
        "use_vlc_low_latency": bool(default_vlc_low_latency),
    }
    root = tk.Tk()
    root.title("Hogak Native Runtime")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frame, text="Select output standard").grid(row=0, column=0, sticky="w")
    values = [f"{preset.key} - {preset.label}" for preset in OUTPUT_PRESETS.values()]
    key_by_label = {f"{preset.key} - {preset.label}": preset.key for preset in OUTPUT_PRESETS.values()}
    current_label = next((label for label, key in key_by_label.items() if key == default_value), values[0])
    combo = ttk.Combobox(frame, values=values, state="readonly", width=34)
    combo.set(current_label)
    combo.grid(row=1, column=0, pady=(8, 12), sticky="ew")

    run_calibration_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        frame,
        text="Run calibration first",
        variable=run_calibration_var,
    ).grid(row=2, column=0, sticky="w", pady=(0, 12))

    vlc_low_latency_var = tk.BooleanVar(value=bool(default_vlc_low_latency))
    ttk.Checkbutton(
        frame,
        text="Open VLC low-latency transmit",
        variable=vlc_low_latency_var,
    ).grid(row=3, column=0, sticky="w", pady=(0, 12))

    def on_run() -> None:
        selection["value"] = key_by_label.get(combo.get(), default_value)
        selection["run_calibration_first"] = bool(run_calibration_var.get())
        selection["use_vlc_low_latency"] = bool(vlc_low_latency_var.get())
        root.destroy()

    def on_cancel() -> None:
        selection["value"] = default_value
        selection["run_calibration_first"] = False
        selection["use_vlc_low_latency"] = bool(default_vlc_low_latency)
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=4, column=0, sticky="e")
    ttk.Button(buttons, text="Run", command=on_run).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(buttons, text="Cancel", command=on_cancel).grid(row=0, column=1)

    root.bind("<Return>", lambda _event: on_run())
    root.bind("<Escape>", lambda _event: on_cancel())
    root.mainloop()
    return (
        str(selection["value"] or default_value),
        bool(selection["run_calibration_first"]),
        bool(selection["use_vlc_low_latency"]),
    )


def _argv_has_option(option_name: str) -> bool:
    prefix = f"{option_name}="
    return any(arg == option_name or arg.startswith(prefix) for arg in sys.argv[1:])


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


def _resolve_output_role(
    args: argparse.Namespace,
    *,
    alias_prefix: str,
    legacy_prefix: str,
) -> tuple[dict[str, Any], bool]:
    config: dict[str, Any] = {}
    explicit = False
    for field in _OUTPUT_ROLE_FIELDS:
        alias_name = f"{alias_prefix}_{field}"
        alias_value = getattr(args, alias_name)
        if alias_value is not None:
            config[field] = alias_value
            explicit = True
            continue
        config[field] = getattr(args, f"{legacy_prefix}_{field}")
    alias_debug_overlay = getattr(args, f"{alias_prefix}_debug_overlay")
    if alias_debug_overlay is not None:
        config["debug_overlay"] = bool(alias_debug_overlay)
        explicit = True
    else:
        legacy_debug_overlay = getattr(args, f"{legacy_prefix}_debug_overlay")
        config["debug_overlay"] = bool(legacy_debug_overlay) if legacy_debug_overlay is not None else False
    return config, explicit


def _is_enabled_output(config: dict[str, Any]) -> bool:
    return str(config.get("runtime") or "none").strip() != "none" and bool(str(config.get("target") or "").strip())


def _has_output_target(config: dict[str, Any]) -> bool:
    return bool(str(config.get("target") or "").strip())


def _apply_output_preset(
    config: dict[str, Any],
    preset: Any,
    *,
    preserve_existing: bool,
) -> dict[str, Any]:
    updated = dict(config)
    preset_values = {
        "width": int(preset.width),
        "height": int(preset.height),
        "fps": float(preset.fps),
        "codec": str(preset.codec),
        "bitrate": str(preset.bitrate),
        "muxer": str(preset.muxer),
    }
    for field, preset_value in preset_values.items():
        if not preserve_existing:
            updated[field] = preset_value
            continue
        current_value = updated.get(field)
        if field in {"width", "height"}:
            if int(current_value or 0) <= 0:
                updated[field] = preset_value
        elif field == "fps":
            if float(current_value or 0.0) <= 0.0:
                updated[field] = preset_value
        elif not str(current_value or "").strip():
            updated[field] = preset_value
    return updated


def _inherit_probe_profile_from_transmit(
    probe_config: dict[str, Any],
    *,
    probe_explicit: bool,
    transmit_config: dict[str, Any],
) -> dict[str, Any]:
    if probe_explicit or not _is_enabled_output(transmit_config):
        return probe_config

    inherited = dict(probe_config)
    for field in ("codec", "bitrate", "preset", "muxer"):
        value = str(transmit_config.get(field) or "").strip()
        if value:
            inherited[field] = value
    for field in ("width", "height"):
        value = int(transmit_config.get(field) or 0)
        if value > 0:
            inherited[field] = value
    fps_value = float(transmit_config.get("fps") or 0.0)
    if fps_value > 0.0:
        inherited["fps"] = fps_value
    return inherited


def _infer_output_muxer(target: str) -> str:
    text = str(target or "").strip().lower()
    if text.startswith("rtsp://"):
        return "rtsp"
    if text.startswith("rtmp://"):
        return "flv"
    if text.startswith("srt://") or text.startswith("udp://"):
        return "mpegts"
    if text.endswith(".ts"):
        return "mpegts"
    if text.endswith(".flv"):
        return "flv"
    return ""


def _resolve_probe_source(
    args: argparse.Namespace,
    *,
    probe_config: dict[str, Any],
    transmit_config: dict[str, Any],
) -> str:
    requested = str(getattr(args, "probe_source", DEFAULT_PROBE_SOURCE) or DEFAULT_PROBE_SOURCE).strip().lower()
    probe_has_target = _has_output_target(probe_config)
    standalone_probe_enabled = _is_enabled_output(probe_config)
    transmit_enabled = _is_enabled_output(transmit_config)

    if requested == "disabled":
        return "disabled"
    if requested == "standalone":
        if not standalone_probe_enabled:
            return "disabled"
        return "standalone"
    if requested == "transmit":
        if not transmit_enabled:
            raise ValueError("--probe-source transmit requires transmit output to be enabled")
        if not probe_has_target:
            raise ValueError("--probe-source transmit requires a probe target")
        return "transmit"
    if standalone_probe_enabled:
        return "standalone"
    return "disabled"


def _gpu_direct_requested(*output_configs: dict[str, Any]) -> bool:
    for config in output_configs:
        if str(config.get("runtime") or "none").strip() == "gpu-direct" and _has_output_target(config):
            return True
    return False


def _format_gpu_direct_preflight(status: dict[str, Any]) -> str:
    provider = str(status.get("provider") or "unknown")
    dependency_ready = bool(status.get("dependency_ready"))
    message = str(status.get("status") or "").strip() or "unknown"
    ffmpeg_dev_root = str(status.get("ffmpeg_dev_root") or "").strip()
    parts = [
        "gpu-direct preflight:",
        f"provider={provider}",
        f"dependency_ready={dependency_ready}",
        f"status={message}",
    ]
    if ffmpeg_dev_root:
        parts.append(f"ffmpeg_dev_root={ffmpeg_dev_root}")
    return " ".join(parts)


def _build_tee_leg(target: str, muxer: str) -> str:
    options = []
    if muxer:
        options.append(f"f={muxer}")
    if muxer == "mpegts":
        options.extend(
            [
                "mpegts_flags=resend_headers",
            ]
        )
    options.append("onfail=ignore")
    return f"[{':'.join(options)}]{target}"


def _build_transmit_output_with_mirrors(
    transmit_config: dict[str, Any],
    *,
    mirror_targets: list[str],
) -> dict[str, Any]:
    mirrored = dict(transmit_config)
    transmit_target = str(mirrored.get("target") or "").strip()
    if not transmit_target:
        raise ValueError("transmit output target is required for mirrored output mode")

    transmit_muxer = str(mirrored.get("muxer") or _infer_output_muxer(transmit_target)).strip()
    normalized_mirrors = [str(target or "").strip() for target in mirror_targets]
    normalized_mirrors = [target for target in normalized_mirrors if target and target != transmit_target]
    if not normalized_mirrors:
        mirrored["muxer"] = transmit_muxer
        mirrored["target"] = transmit_target
        return mirrored

    legs = [_build_tee_leg(transmit_target, transmit_muxer)]
    seen_targets = {transmit_target}
    for mirror_target in normalized_mirrors:
        if mirror_target in seen_targets:
            continue
        mirror_muxer = _infer_output_muxer(mirror_target) or transmit_muxer
        legs.append(_build_tee_leg(mirror_target, mirror_muxer))
        seen_targets.add(mirror_target)

    mirrored["muxer"] = "tee"
    mirrored["target"] = "|".join(legs)
    return mirrored


def _build_mirrored_transmit_output(
    transmit_config: dict[str, Any],
    *,
    probe_target: str,
) -> dict[str, Any]:
    return _build_transmit_output_with_mirrors(
        transmit_config,
        mirror_targets=[probe_target],
    )


def _extract_stream_host_port(target: str, *, scheme: str) -> tuple[str, int] | None:
    text = str(target or "").strip()
    prefix = f"{scheme}://"
    if not text.lower().startswith(prefix):
        return None
    endpoint = text.split("?", 1)[0][len(prefix) :]
    if scheme == "udp" and endpoint.startswith("@"):
        endpoint = endpoint[1:]
    if ":" in endpoint:
        host, port_text = endpoint.rsplit(":", 1)
    else:
        host, port_text = "127.0.0.1", endpoint
    try:
        port = int(port_text)
    except ValueError:
        return None
    if port <= 0 or port > 65535:
        return None
    return (host or "127.0.0.1"), port


def _normalize_tcp_client_target(target: str) -> str:
    parsed = _extract_stream_host_port(target, scheme="tcp")
    if parsed is None:
        return str(target or "").strip()
    _host, port = parsed
    return f"tcp://127.0.0.1:{port}"


def _derive_vlc_preview_targets(transmit_target: str) -> tuple[str, str]:
    udp_parsed = _extract_stream_host_port(transmit_target, scheme="udp")
    if udp_parsed is not None:
        _host, port = udp_parsed
        preview_port = port + 1 if port < 65535 else port
        return (
            f"tcp://0.0.0.0:{preview_port}?listen=1",
            f"tcp://127.0.0.1:{preview_port}",
        )

    tcp_parsed = _extract_stream_host_port(transmit_target, scheme="tcp")
    if tcp_parsed is not None:
        return ("", _normalize_tcp_client_target(transmit_target))

    return ("", "")


def _decorate_pipeline_metrics(
    payload: dict[str, Any],
    *,
    probe_source: str,
    probe_target: str,
    transmit_target: str,
) -> dict[str, Any]:
    projected = dict(payload)
    projected["probe_source"] = probe_source

    if probe_source == "transmit":
        projected["probe_active"] = bool(payload.get("production_output_active"))
        projected["probe_width"] = int(payload.get("production_output_width") or 0)
        projected["probe_height"] = int(payload.get("production_output_height") or 0)
        projected["probe_written_fps"] = float(payload.get("production_output_written_fps") or 0.0)
        projected["probe_frames_written"] = int(payload.get("production_output_frames_written") or 0)
        projected["probe_frames_dropped"] = int(payload.get("production_output_frames_dropped") or 0)
        projected["probe_effective_codec"] = str(payload.get("production_output_effective_codec") or "")
        projected["probe_last_error"] = str(payload.get("production_output_last_error") or "")
    elif probe_source == "standalone":
        projected["probe_active"] = bool(payload.get("output_active"))
        projected["probe_width"] = int(payload.get("output_width") or 0)
        projected["probe_height"] = int(payload.get("output_height") or 0)
        projected["probe_written_fps"] = float(payload.get("output_written_fps") or 0.0)
        projected["probe_frames_written"] = int(payload.get("output_frames_written") or 0)
        projected["probe_frames_dropped"] = int(payload.get("output_frames_dropped") or 0)
        projected["probe_effective_codec"] = str(payload.get("output_effective_codec") or "")
        projected["probe_last_error"] = str(payload.get("output_last_error") or "")
    else:
        projected["probe_active"] = False
        projected["probe_width"] = 0
        projected["probe_height"] = 0
        projected["probe_written_fps"] = 0.0
        projected["probe_frames_written"] = 0
        projected["probe_frames_dropped"] = 0
        projected["probe_effective_codec"] = ""
        projected["probe_last_error"] = ""

    projected["probe_target_user"] = str(probe_target or "")
    projected["transmit_active"] = bool(payload.get("production_output_active"))
    projected["transmit_width"] = int(payload.get("production_output_width") or 0)
    projected["transmit_height"] = int(payload.get("production_output_height") or 0)
    projected["transmit_written_fps"] = float(payload.get("production_output_written_fps") or 0.0)
    projected["transmit_frames_written"] = int(payload.get("production_output_frames_written") or 0)
    projected["transmit_frames_dropped"] = int(payload.get("production_output_frames_dropped") or 0)
    projected["transmit_effective_codec"] = str(payload.get("production_output_effective_codec") or "")
    projected["transmit_last_error"] = str(payload.get("production_output_last_error") or "")
    projected["transmit_target_user"] = str(transmit_target or payload.get("production_output_target") or "")
    return projected


def add_native_runtime_args(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--left-rtsp", default=default_left_rtsp(), help="Left RTSP URL")
    cmd.add_argument("--right-rtsp", default=default_right_rtsp(), help="Right RTSP URL")
    cmd.add_argument("--input-runtime", choices=["ffmpeg-cpu", "ffmpeg-cuda"], default=DEFAULT_NATIVE_INPUT_RUNTIME)
    cmd.add_argument("--input-pipe-format", choices=["bgr24", "nv12"], default=DEFAULT_NATIVE_INPUT_PIPE_FORMAT)
    cmd.add_argument("--ffmpeg-bin", default="", help="Optional explicit ffmpeg.exe path")
    cmd.add_argument("--rtsp-transport", choices=["tcp", "udp"], default=DEFAULT_NATIVE_RTSP_TRANSPORT)
    cmd.add_argument(
        "--input-buffer-frames",
        type=int,
        default=DEFAULT_NATIVE_INPUT_BUFFER_FRAMES,
        help="Max buffered frames per RTSP reader",
    )
    cmd.add_argument("--disable-freeze-detection", action="store_true", help="Disable input freeze/motion probing for performance-focused runs")
    cmd.add_argument("--rtsp-timeout-sec", type=float, default=DEFAULT_NATIVE_RTSP_TIMEOUT_SEC)
    cmd.add_argument("--reconnect-cooldown-sec", type=float, default=DEFAULT_NATIVE_RECONNECT_COOLDOWN_SEC)
    cmd.add_argument("--heartbeat-ms", type=int, default=1000)
    cmd.add_argument("--homography-file", default=DEFAULT_NATIVE_HOMOGRAPHY_PATH, help="Optional fixed 3x3 homography JSON path")
    cmd.add_argument("--output-runtime", choices=["none", "ffmpeg", "gpu-direct"], default="none")
    cmd.add_argument("--output-profile", choices=["inspection", "production-compatible"], default="inspection")
    cmd.add_argument("--output-target", default=DEFAULT_PROBE_TARGET, help="Legacy alias for local encoded probe target")
    cmd.add_argument("--output-codec", default="h264_nvenc")
    cmd.add_argument("--output-bitrate", default="12M")
    cmd.add_argument("--output-preset", default="p4")
    cmd.add_argument("--output-muxer", default="")
    cmd.add_argument("--output-width", type=int, default=0)
    cmd.add_argument("--output-height", type=int, default=0)
    cmd.add_argument("--output-fps", type=float, default=0.0)
    cmd.add_argument("--output-debug-overlay", action="store_true", default=None)
    cmd.add_argument("--production-output-runtime", choices=["none", "ffmpeg", "gpu-direct"], default="ffmpeg")
    cmd.add_argument(
        "--production-output-profile",
        choices=["inspection", "production-compatible"],
        default="production-compatible",
    )
    cmd.add_argument(
        "--production-output-target",
        default=DEFAULT_TRANSMIT_TARGET,
        help="Legacy alias for final transmitted encoded output target",
    )
    cmd.add_argument("--production-output-codec", default="h264_nvenc")
    cmd.add_argument("--production-output-bitrate", default="12M")
    cmd.add_argument("--production-output-preset", default="p4")
    cmd.add_argument("--production-output-muxer", default="")
    cmd.add_argument("--production-output-width", type=int, default=0)
    cmd.add_argument("--production-output-height", type=int, default=0)
    cmd.add_argument("--production-output-fps", type=float, default=0.0)
    cmd.add_argument("--production-output-debug-overlay", action="store_true", default=None)
    cmd.add_argument(
        "--probe-output-runtime",
        choices=["none", "ffmpeg", "gpu-direct"],
        default=None,
        help="Runtime for local post-encode probe output. Viewer reads this stream.",
    )
    cmd.add_argument("--probe-output-target", default=None, help="Local post-encode probe target (default: local UDP loopback)")
    cmd.add_argument("--probe-output-codec", default=None, help="Probe codec override")
    cmd.add_argument("--probe-output-bitrate", default=None, help="Probe bitrate override")
    cmd.add_argument("--probe-output-preset", default=None, help="Probe encoder preset override")
    cmd.add_argument("--probe-output-muxer", default=None, help="Probe muxer override")
    cmd.add_argument("--probe-output-width", type=int, default=None, help="Probe width override")
    cmd.add_argument("--probe-output-height", type=int, default=None, help="Probe height override")
    cmd.add_argument("--probe-output-fps", type=float, default=None, help="Probe fps override")
    cmd.add_argument("--probe-output-debug-overlay", action="store_true", default=None, help="Burn debug overlay into probe output")
    cmd.add_argument(
        "--probe-source",
        choices=["auto", "transmit", "standalone", "disabled"],
        default=DEFAULT_PROBE_SOURCE,
        help="auto prefers a standalone local probe encode when enabled; use transmit only for explicit mirrored-transmit debugging",
    )
    cmd.add_argument(
        "--transmit-output-runtime",
        choices=["none", "ffmpeg", "gpu-direct"],
        default=None,
        help="Runtime for final transmitted encoded output",
    )
    cmd.add_argument("--transmit-output-target", default=None, help="Final transmitted output target")
    cmd.add_argument("--transmit-output-codec", default=None, help="Transmit codec override")
    cmd.add_argument("--transmit-output-bitrate", default=None, help="Transmit bitrate override")
    cmd.add_argument("--transmit-output-preset", default=None, help="Transmit encoder preset override")
    cmd.add_argument("--transmit-output-muxer", default=None, help="Transmit muxer override")
    cmd.add_argument("--transmit-output-width", type=int, default=None, help="Transmit width override")
    cmd.add_argument("--transmit-output-height", type=int, default=None, help="Transmit height override")
    cmd.add_argument("--transmit-output-fps", type=float, default=None, help="Transmit fps override")
    cmd.add_argument(
        "--transmit-output-debug-overlay",
        action="store_true",
        default=None,
        help="Burn debug overlay into transmit output",
    )
    cmd.add_argument(
        "--output-standard",
        choices=sorted(OUTPUT_PRESETS.keys()),
        default="",
        help="Named output preset. Python applies width/height/fps/codec/bitrate/muxer before launching runtime.",
    )
    cmd.add_argument("--no-output-ui", action="store_true", help="Skip preset selection UI and use default output standard")
    cmd.add_argument("--sync-pair-mode", choices=["none", "latest", "oldest", "service"], default="none")
    cmd.add_argument("--allow-frame-reuse", action="store_true", help="Allow stale one-side pair reuse for smoother output")
    cmd.add_argument("--pair-reuse-max-age-ms", type=float, default=DEFAULT_NATIVE_PAIR_REUSE_MAX_AGE_MS)
    cmd.add_argument("--pair-reuse-max-consecutive", type=int, default=DEFAULT_NATIVE_PAIR_REUSE_MAX_CONSECUTIVE)
    cmd.add_argument("--sync-match-max-delta-ms", type=float, default=DEFAULT_NATIVE_SYNC_MATCH_MAX_DELTA_MS)
    cmd.add_argument("--sync-manual-offset-ms", type=float, default=0.0)
    cmd.add_argument("--stitch-output-scale", type=float, default=1.0)
    cmd.add_argument("--stitch-every-n", type=int, default=1)
    cmd.add_argument("--gpu-mode", choices=["off", "auto", "on"], default="on")
    cmd.add_argument("--gpu-device", type=int, default=0)
    cmd.add_argument("--headless-benchmark", action="store_true")
    cmd.add_argument("--duration-sec", type=float, default=0.0, help="0 runs until Ctrl+C")
    cmd.add_argument(
        "--status-interval-sec",
        type=float,
        default=DEFAULT_NATIVE_STATUS_INTERVAL_SEC,
        help="Status line interval while state is unchanged",
    )
    cmd.add_argument("--monitor-mode", choices=["dashboard", "compact", "json"], default="dashboard")
    cmd.add_argument("--recent-events", type=int, default=8, help="How many recent non-metric events to keep in dashboard mode")
    cmd.add_argument("--verbose-events", action="store_true", help="Print every runtime event as raw JSON")
    cmd.add_argument("--viewer", dest="viewer", action="store_true", help="Launch the final output viewer")
    cmd.add_argument("--no-viewer", dest="viewer", action="store_false", help="Disable final stream viewer")
    cmd.set_defaults(viewer=True)
    cmd.add_argument(
        "--viewer-backend",
        choices=["auto", "ffplay", "vlc-low-latency", "opencv"],
        default=default_native_viewer_backend(),
        help="Viewer backend selection (auto prefers ffplay and falls back to OpenCV)",
    )
    cmd.add_argument(
        "--open-vlc-low-latency",
        action="store_true",
        default=str(os.environ.get("HOGAK_OPEN_VLC_LOW_LATENCY", "0")).strip().lower() in {"1", "true", "yes", "on"},
        help="Open an additional VLC low-latency window on a transmit preview path while keeping probe viewer behavior unchanged",
    )
    cmd.add_argument(
        "--vlc-target",
        default="",
        help="Override VLC low-latency receive target (defaults to auto TCP preview for UDP transmit)",
    )
    cmd.add_argument("--viewer-target", default="", help="Override viewer target (defaults to local probe stream)")
    cmd.add_argument("--viewer-title", default="Hogak Final Stream")


def _compact_metrics(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    status = str(payload.get("status") or "-")
    parts.append(f"status={status}")
    pair_mode = str(payload.get("sync_pair_mode") or "").strip()
    if pair_mode:
        parts.append(f"pair_mode={pair_mode}")
    parts.append(f"calibrated={bool(payload.get('calibrated'))}")
    parts.append(f"probe_source={str(payload.get('probe_source') or 'standalone')}")
    parts.append(f"probe_active={bool(payload.get('probe_active'))}")
    parts.append(f"transmit_active={bool(payload.get('transmit_active'))}")

    probe_width = int(payload.get("probe_width") or 0)
    probe_height = int(payload.get("probe_height") or 0)
    if probe_width > 0 and probe_height > 0:
        parts.append(f"probe={probe_width}x{probe_height}")
    transmit_width = int(payload.get("transmit_width") or 0)
    transmit_height = int(payload.get("transmit_height") or 0)
    if transmit_width > 0 and transmit_height > 0:
        parts.append(f"transmit={transmit_width}x{transmit_height}")

    stitch_fps = payload.get("stitch_fps")
    if isinstance(stitch_fps, (int, float)):
        parts.append(f"stitch_fps={float(stitch_fps):.2f}")
    stitch_actual_fps = payload.get("stitch_actual_fps")
    if isinstance(stitch_actual_fps, (int, float)):
        parts.append(f"stitch_actual_fps={float(stitch_actual_fps):.2f}")
    probe_written_fps = payload.get("probe_written_fps")
    if isinstance(probe_written_fps, (int, float)):
        parts.append(f"probe_fps={float(probe_written_fps):.2f}")
    transmit_written_fps = payload.get("transmit_written_fps")
    if isinstance(transmit_written_fps, (int, float)):
        parts.append(f"transmit_fps={float(transmit_written_fps):.2f}")

    pair_skew_ms_mean = payload.get("pair_skew_ms_mean")
    if isinstance(pair_skew_ms_mean, (int, float)):
        parts.append(f"pair_skew_ms={float(pair_skew_ms_mean):.2f}")
    left_age_ms = payload.get("left_age_ms")
    right_age_ms = payload.get("right_age_ms")
    if isinstance(left_age_ms, (int, float)) and isinstance(right_age_ms, (int, float)):
        parts.append(f"input_age_ms=({float(left_age_ms):.0f},{float(right_age_ms):.0f})")
    left_gap_ms = payload.get("left_avg_frame_interval_ms")
    right_gap_ms = payload.get("right_avg_frame_interval_ms")
    if isinstance(left_gap_ms, (int, float)) and isinstance(right_gap_ms, (int, float)):
        parts.append(f"input_gap_ms=({float(left_gap_ms):.1f},{float(right_gap_ms):.1f})")
    left_gap_max_ms = payload.get("left_max_frame_interval_ms")
    right_gap_max_ms = payload.get("right_max_frame_interval_ms")
    if isinstance(left_gap_max_ms, (int, float)) and isinstance(right_gap_max_ms, (int, float)):
        if float(left_gap_max_ms) > 0.0 or float(right_gap_max_ms) > 0.0:
            parts.append(f"gap_max_ms=({float(left_gap_max_ms):.1f},{float(right_gap_max_ms):.1f})")
    left_buffer_span_ms = payload.get("left_buffer_span_ms")
    right_buffer_span_ms = payload.get("right_buffer_span_ms")
    if isinstance(left_buffer_span_ms, (int, float)) and isinstance(right_buffer_span_ms, (int, float)):
        parts.append(f"queue_span_ms=({float(left_buffer_span_ms):.1f},{float(right_buffer_span_ms):.1f})")
    left_read_ms = payload.get("left_avg_read_ms")
    right_read_ms = payload.get("right_avg_read_ms")
    if isinstance(left_read_ms, (int, float)) and isinstance(right_read_ms, (int, float)):
        parts.append(f"read_ms=({float(left_read_ms):.2f},{float(right_read_ms):.2f})")
    left_buffered = int(payload.get("left_buffered_frames") or 0)
    right_buffered = int(payload.get("right_buffered_frames") or 0)
    if left_buffered > 0 or right_buffered > 0:
        parts.append(f"input_buffer=({left_buffered},{right_buffered})")
    wait_both = int(payload.get("wait_both_streams_count") or 0)
    wait_sync = int(payload.get("wait_sync_pair_count") or 0)
    wait_next = int(payload.get("wait_next_frame_count") or 0)
    wait_fresh = int(payload.get("wait_paired_fresh_count") or 0)
    wait_total = wait_both + wait_sync + wait_next + wait_fresh
    if wait_total > 0:
        parts.append(f"waits=({wait_both},{wait_sync},{wait_next},{wait_fresh})")
    wait_fresh_left = int(payload.get("wait_paired_fresh_left_count") or 0)
    wait_fresh_right = int(payload.get("wait_paired_fresh_right_count") or 0)
    wait_fresh_both = int(payload.get("wait_paired_fresh_both_count") or 0)
    if wait_fresh_left > 0 or wait_fresh_right > 0 or wait_fresh_both > 0:
        parts.append(f"fresh_waits=({wait_fresh_left},{wait_fresh_right},{wait_fresh_both})")
    left_late_intervals = int(payload.get("left_late_frame_intervals") or 0)
    right_late_intervals = int(payload.get("right_late_frame_intervals") or 0)
    if left_late_intervals > 0 or right_late_intervals > 0:
        parts.append(f"late_gap=({left_late_intervals},{right_late_intervals})")
    left_fresh_wait_age_ms = payload.get("wait_paired_fresh_left_age_ms_avg")
    right_fresh_wait_age_ms = payload.get("wait_paired_fresh_right_age_ms_avg")
    if isinstance(left_fresh_wait_age_ms, (int, float)) and isinstance(right_fresh_wait_age_ms, (int, float)):
        if float(left_fresh_wait_age_ms) > 0.0 or float(right_fresh_wait_age_ms) > 0.0:
            parts.append(f"fresh_wait_age_ms=({float(left_fresh_wait_age_ms):.1f},{float(right_fresh_wait_age_ms):.1f})")
    selected_left_lag_frames = int(payload.get("selected_left_lag_frames") or 0)
    selected_right_lag_frames = int(payload.get("selected_right_lag_frames") or 0)
    selected_left_lag_ms = payload.get("selected_left_lag_ms")
    selected_right_lag_ms = payload.get("selected_right_lag_ms")
    if (
        selected_left_lag_frames > 0
        or selected_right_lag_frames > 0
        or (isinstance(selected_left_lag_ms, (int, float)) and float(selected_left_lag_ms) > 0.0)
        or (isinstance(selected_right_lag_ms, (int, float)) and float(selected_right_lag_ms) > 0.0)
    ):
        parts.append(
            "sel_lag="
            f"({selected_left_lag_frames}/{float(selected_left_lag_ms or 0.0):.1f}ms,"
            f"{selected_right_lag_frames}/{float(selected_right_lag_ms or 0.0):.1f}ms)"
        )
    left_launch_failures = int(payload.get("left_launch_failures") or 0)
    right_launch_failures = int(payload.get("right_launch_failures") or 0)
    if left_launch_failures > 0 or right_launch_failures > 0:
        parts.append(f"launch_fail=({left_launch_failures},{right_launch_failures})")
    left_read_failures = int(payload.get("left_read_failures") or 0)
    right_read_failures = int(payload.get("right_read_failures") or 0)
    if left_read_failures > 0 or right_read_failures > 0:
        parts.append(f"read_fail=({left_read_failures},{right_read_failures})")
    left_reader_restarts = int(payload.get("left_reader_restarts") or 0)
    right_reader_restarts = int(payload.get("right_reader_restarts") or 0)
    if left_reader_restarts > 0 or right_reader_restarts > 0:
        parts.append(f"reader_restart=({left_reader_restarts},{right_reader_restarts})")
    left_motion = payload.get("left_motion_mean")
    right_motion = payload.get("right_motion_mean")
    if isinstance(left_motion, (int, float)) and isinstance(right_motion, (int, float)):
        parts.append(f"input_motion=({float(left_motion):.2f},{float(right_motion):.2f})")
    if bool(payload.get("left_content_frozen")) or bool(payload.get("right_content_frozen")):
        parts.append(
            f"frozen=({bool(payload.get('left_content_frozen'))},{bool(payload.get('right_content_frozen'))})"
        )

    parts.append(f"probe_written={int(payload.get('probe_frames_written') or 0)}")
    parts.append(f"transmit_written={int(payload.get('transmit_frames_written') or 0)}")

    probe_effective_codec = str(payload.get("probe_effective_codec") or "").strip()
    if probe_effective_codec:
        parts.append(f"probe_codec={probe_effective_codec}")
    transmit_effective_codec = str(payload.get("transmit_effective_codec") or "").strip()
    if transmit_effective_codec:
        parts.append(f"transmit_codec={transmit_effective_codec}")

    gpu_errors = int(payload.get("gpu_errors") or 0)
    if gpu_errors > 0:
        parts.append(f"gpu_errors={gpu_errors}")

    probe_last_error = str(payload.get("probe_last_error") or "").strip()
    if probe_last_error:
        parts.append(f"probe_error={probe_last_error}")
    transmit_last_error = str(payload.get("transmit_last_error") or "").strip()
    if transmit_last_error:
        parts.append(f"transmit_error={transmit_last_error}")
    for key in ("left_last_error", "right_last_error"):
        value = str(payload.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    return " ".join(parts)


def _status_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        payload.get("status"),
        payload.get("sync_pair_mode"),
        payload.get("calibrated"),
        payload.get("probe_source"),
        payload.get("probe_active"),
        payload.get("transmit_active"),
        payload.get("probe_width"),
        payload.get("probe_height"),
        payload.get("transmit_width"),
        payload.get("transmit_height"),
        payload.get("probe_effective_codec"),
        payload.get("transmit_effective_codec"),
        payload.get("probe_last_error"),
        payload.get("transmit_last_error"),
        payload.get("left_last_error"),
        payload.get("right_last_error"),
        payload.get("left_launch_failures"),
        payload.get("right_launch_failures"),
        payload.get("left_read_failures"),
        payload.get("right_read_failures"),
        payload.get("left_reader_restarts"),
        payload.get("right_reader_restarts"),
        payload.get("left_content_frozen"),
        payload.get("right_content_frozen"),
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
    probe_width = int(payload.get("probe_width") or 0)
    probe_height = int(payload.get("probe_height") or 0)
    probe_size = f"{probe_width}x{probe_height}" if probe_width > 0 and probe_height > 0 else "-"
    transmit_width = int(payload.get("transmit_width") or 0)
    transmit_height = int(payload.get("transmit_height") or 0)
    transmit_size = (
        f"{transmit_width}x{transmit_height}"
        if transmit_width > 0 and transmit_height > 0
        else "-"
    )
    runtime_name = str(hello_payload.get("runtime") or "native-runtime")
    protocol = str(hello_payload.get("protocol") or "jsonl-v1")
    probe_codec = str(payload.get("probe_effective_codec") or "-")
    transmit_codec = str(payload.get("transmit_effective_codec") or "-")
    output_target = _trim_text(str(payload.get("probe_target_user") or "-"), short)
    production_output_target = _trim_text(str(payload.get("transmit_target_user") or "-"), short)
    output_error = _trim_text(str(payload.get("probe_last_error") or "-"), short)
    production_output_error = _trim_text(str(payload.get("transmit_last_error") or "-"), short)
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
            f"pair_mode={payload.get('sync_pair_mode') or '-'}  "
            f"probe_source={payload.get('probe_source') or '-'}  "
            f"viewer={_format_flag(viewer_enabled)}  updated_at={time.strftime('%H:%M:%S', time.localtime(last_update_sec))}"
        ),
        (
            f"input  left_fps={float(payload.get('left_fps') or 0.0):6.2f}  "
            f"right_fps={float(payload.get('right_fps') or 0.0):6.2f}  "
            f"pair_skew_ms={float(payload.get('pair_skew_ms_mean') or 0.0):7.2f}  "
            f"left_age_ms={float(payload.get('left_age_ms') or 0.0):7.0f}  "
            f"right_age_ms={float(payload.get('right_age_ms') or 0.0):7.0f}  "
            f"buffer=({int(payload.get('left_buffered_frames') or 0)},{int(payload.get('right_buffered_frames') or 0)})"
        ),
        (
            f"motion left={float(payload.get('left_motion_mean') or 0.0):6.2f}  "
            f"right={float(payload.get('right_motion_mean') or 0.0):6.2f}  "
            f"stitched={float(payload.get('stitched_motion_mean') or 0.0):6.2f}  "
            f"frozen=({_format_flag(bool(payload.get('left_content_frozen')))},"
            f"{_format_flag(bool(payload.get('right_content_frozen')))})"
        ),
        (
            f"stitch internal_fps={float(payload.get('stitch_fps') or 0.0):6.2f}  "
            f"actual_fps={float(payload.get('stitch_actual_fps') or 0.0):6.2f}  "
            f"worker_fps={float(payload.get('worker_fps') or 0.0):6.2f}  "
            f"probe_fps={float(payload.get('probe_written_fps') or 0.0):6.2f}  "
            f"transmit_fps={float(payload.get('transmit_written_fps') or 0.0):6.2f}  "
            f"gpu_warp={int(payload.get('gpu_warp_count') or 0)}  "
            f"gpu_blend={int(payload.get('gpu_blend_count') or 0)}  "
            f"cpu_blend={int(payload.get('cpu_blend_count') or 0)}"
        ),
        (
            f"probe  active={_format_flag(bool(payload.get('probe_active')))}  "
            f"size={probe_size}  codec={probe_codec}  "
            f"dropped={int(payload.get('probe_frames_dropped') or 0)}  "
            f"written={int(payload.get('probe_frames_written') or 0)}"
        ),
        (
            f"transm active={_format_flag(bool(payload.get('transmit_active')))}  "
            f"size={transmit_size}  codec={transmit_codec}  "
            f"dropped={int(payload.get('transmit_frames_dropped') or 0)}  "
            f"written={int(payload.get('transmit_frames_written') or 0)}"
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
            f"right_stale={int(payload.get('right_stale_drops') or 0)}  "
            f"launch_fail=({int(payload.get('left_launch_failures') or 0)},"
            f"{int(payload.get('right_launch_failures') or 0)})  "
            f"read_fail=({int(payload.get('left_read_failures') or 0)},"
            f"{int(payload.get('right_read_failures') or 0)})  "
            f"restart=({int(payload.get('left_reader_restarts') or 0)},"
            f"{int(payload.get('right_reader_restarts') or 0)})  "
            f"waits=({int(payload.get('wait_both_streams_count') or 0)},"
            f"{int(payload.get('wait_sync_pair_count') or 0)},"
            f"{int(payload.get('wait_next_frame_count') or 0)},"
            f"{int(payload.get('wait_paired_fresh_count') or 0)})  "
            f"freeze_restarts=({int(payload.get('left_freeze_restarts') or 0)},"
            f"{int(payload.get('right_freeze_restarts') or 0)})"
        ),
        (
            f"system cpu_total={cpu_percent:6.2f}%  "
            f"gpu_total={gpu_percent:6.2f}%  "
            f"gpu_mem={gpu_mem_used:6.0f}/{gpu_mem_total:6.0f} MB  "
            f"gpu_temp={gpu_temp_c:5.1f} C"
        ),
        f"probe_target   {output_target}",
        f"probe_err      {output_error}",
        f"transmit_target {production_output_target}",
        f"transmit_err    {production_output_error}",
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
    run_calibration_first = False
    default_vlc_low_latency = bool(args.open_vlc_low_latency)
    if not str(args.output_standard or "").strip() and not bool(args.no_output_ui):
        args.output_standard, run_calibration_first, use_vlc_low_latency = _prompt_runtime_start(
            default_output_standard(),
            default_vlc_low_latency=default_vlc_low_latency,
        )
        args.open_vlc_low_latency = bool(use_vlc_low_latency)
    if not str(args.output_standard or "").strip():
        args.output_standard = default_output_standard()

    if run_calibration_first:
        repo_root = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        env["OUTPUT_STANDARD"] = str(args.output_standard)
        env["HOGAK_VIEWER_BACKEND"] = str(args.viewer_backend or "auto")
        env["HOGAK_OPEN_VLC_LOW_LATENCY"] = "1" if bool(args.open_vlc_low_latency) else "0"
        command = [
            sys.executable,
            "-m",
            "stitching.cli",
            "native-calibrate",
            "--launch-runtime",
            "--runtime-script",
            "scripts/run_native_runtime.cmd",
        ]
        completed = subprocess.run(command, cwd=str(repo_root), env=env, check=False)
        return int(completed.returncode)

    probe_output, probe_explicit = _resolve_output_role(
        args,
        alias_prefix="probe_output",
        legacy_prefix="output",
    )
    transmit_output, transmit_explicit = _resolve_output_role(
        args,
        alias_prefix="transmit_output",
        legacy_prefix="production_output",
    )
    sync_pair_mode_explicit = _argv_has_option("--sync-pair-mode")
    allow_frame_reuse_explicit = _argv_has_option("--allow-frame-reuse")
    sync_match_max_delta_explicit = _argv_has_option("--sync-match-max-delta-ms")
    if str(args.output_standard or "").strip():
        preset = get_output_preset(str(args.output_standard))
        transmit_output = _apply_output_preset(
            transmit_output,
            preset,
            preserve_existing=transmit_explicit,
        )
        args.stitch_output_scale = float(preset.output_scale)
        if not sync_pair_mode_explicit:
            args.sync_pair_mode = preset.sync_pair_mode
        if not allow_frame_reuse_explicit:
            args.allow_frame_reuse = bool(preset.allow_frame_reuse)
        if not sync_match_max_delta_explicit:
            args.sync_match_max_delta_ms = float(preset.sync_match_max_delta_ms)
    probe_output = _inherit_probe_profile_from_transmit(
        probe_output,
        probe_explicit=probe_explicit,
        transmit_config=transmit_output,
    )
    probe_source = _resolve_probe_source(
        args,
        probe_config=probe_output,
        transmit_config=transmit_output,
    )
    probe_target_for_viewer = str(probe_output.get("target") or DEFAULT_PROBE_TARGET)
    transmit_target_for_display = str(transmit_output.get("target") or "")
    launch_probe_output = dict(probe_output)
    transmit_mirror_targets: list[str] = []
    if probe_source == "transmit":
        transmit_mirror_targets.append(probe_target_for_viewer)
        launch_probe_output["runtime"] = "none"
        launch_probe_output["target"] = ""
    vlc_enabled = bool(args.open_vlc_low_latency)
    auto_vlc_output_target = ""
    auto_vlc_target = ""
    explicit_vlc_target = str(args.vlc_target or "").strip()
    vlc_preview_requested = vlc_enabled or bool(explicit_vlc_target)
    if vlc_preview_requested and not explicit_vlc_target:
        auto_vlc_output_target, auto_vlc_target = _derive_vlc_preview_targets(transmit_target_for_display)
        if auto_vlc_output_target:
            transmit_mirror_targets.append(auto_vlc_output_target)
    elif explicit_vlc_target:
        explicit_vlc_output_target = _normalize_tcp_client_target(explicit_vlc_target)
        explicit_vlc_listener_target = explicit_vlc_output_target
        parsed_tcp = _extract_stream_host_port(explicit_vlc_output_target, scheme="tcp")
        if parsed_tcp is not None:
            _host, port = parsed_tcp
            explicit_vlc_listener_target = f"tcp://0.0.0.0:{port}?listen=1"
        transmit_mirror_targets.append(explicit_vlc_listener_target)
    launch_transmit_output = _build_transmit_output_with_mirrors(
        transmit_output,
        mirror_targets=transmit_mirror_targets,
    )

    if _gpu_direct_requested(launch_probe_output, launch_transmit_output):
        gpu_direct_status = query_gpu_direct_status()
        print(_format_gpu_direct_preflight(gpu_direct_status))
        if not bool(gpu_direct_status.get("dependency_ready")):
            print(
                "gpu-direct dependencies are not ready. "
                "Install FFmpeg dev headers/libs first or fall back to ffmpeg runtime."
            )
            return 2

    spec = RuntimeLaunchSpec(
        emit_hello=True,
        once=False,
        heartbeat_ms=max(100, int(args.heartbeat_ms)),
        left_rtsp=args.left_rtsp,
        right_rtsp=args.right_rtsp,
        input_runtime=args.input_runtime,
        input_pipe_format=args.input_pipe_format,
        ffmpeg_bin=str(args.ffmpeg_bin or ""),
        homography_file=str(args.homography_file or ""),
        transport=args.rtsp_transport,
        input_buffer_frames=max(1, int(args.input_buffer_frames)),
        disable_freeze_detection=bool(args.disable_freeze_detection),
        video_codec="h264",
        timeout_sec=max(0.1, float(args.rtsp_timeout_sec)),
        reconnect_cooldown_sec=max(0.1, float(args.reconnect_cooldown_sec)),
        output_runtime=str(launch_probe_output["runtime"] or "none"),
        output_profile=str(args.output_profile or "inspection"),
        output_target=str(launch_probe_output["target"] or ""),
        output_codec=str(launch_probe_output["codec"] or ""),
        output_bitrate=str(launch_probe_output["bitrate"] or ""),
        output_preset=str(launch_probe_output["preset"] or ""),
        output_muxer=str(launch_probe_output["muxer"] or ""),
        output_width=max(0, int(launch_probe_output["width"] or 0)),
        output_height=max(0, int(launch_probe_output["height"] or 0)),
        output_fps=max(0.0, float(launch_probe_output["fps"] or 0.0)),
        output_debug_overlay=bool(launch_probe_output.get("debug_overlay")),
        production_output_runtime=str(launch_transmit_output["runtime"] or "none"),
        production_output_profile=str(args.production_output_profile or "production-compatible"),
        production_output_target=str(launch_transmit_output["target"] or ""),
        production_output_codec=str(launch_transmit_output["codec"] or ""),
        production_output_bitrate=str(launch_transmit_output["bitrate"] or ""),
        production_output_preset=str(launch_transmit_output["preset"] or ""),
        production_output_muxer=str(launch_transmit_output["muxer"] or ""),
        production_output_width=max(0, int(launch_transmit_output["width"] or 0)),
        production_output_height=max(0, int(launch_transmit_output["height"] or 0)),
        production_output_fps=max(0.0, float(launch_transmit_output["fps"] or 0.0)),
        production_output_debug_overlay=bool(launch_transmit_output.get("debug_overlay")),
        sync_pair_mode=str(args.sync_pair_mode),
        allow_frame_reuse=bool(args.allow_frame_reuse),
        pair_reuse_max_age_ms=max(1.0, float(args.pair_reuse_max_age_ms)),
        pair_reuse_max_consecutive=max(1, int(args.pair_reuse_max_consecutive)),
        sync_match_max_delta_ms=max(1.0, float(args.sync_match_max_delta_ms)),
        sync_manual_offset_ms=float(args.sync_manual_offset_ms),
        stitch_output_scale=max(0.1, float(args.stitch_output_scale)),
        stitch_every_n=max(1, int(args.stitch_every_n)),
        gpu_mode=str(args.gpu_mode),
        gpu_device=max(0, int(args.gpu_device)),
        headless_benchmark=bool(args.headless_benchmark),
    )

    client = RuntimeClient.launch(spec)
    viewer_backend = str(args.viewer_backend or "auto")
    viewer_proc: subprocess.Popen[bytes] | None = None
    viewer_launch_failures = 0
    next_viewer_launch_sec = 0.0
    vlc_proc: subprocess.Popen[bytes] | None = None
    vlc_launch_failures = 0
    next_vlc_launch_sec = 0.0
    stats_sampler = SystemStatsSampler(interval_sec=1.0)
    stats_sampler.start()
    runtime_stderr = ""
    last_status_signature: tuple[Any, ...] | None = None
    last_status_emit_sec = 0.0
    last_metrics_payload: dict[str, Any] = {}
    last_dashboard_render_sec = 0.0
    recent_events: deque[str] = deque(maxlen=max(1, int(args.recent_events)))
    hello_payload: dict[str, Any] = {}
    probe_enabled = probe_source != "disabled" and bool(str(probe_target_for_viewer).strip())
    viewer_target = str(args.viewer_target or probe_target_for_viewer or DEFAULT_VIEWER_TARGET)
    vlc_target = str(_normalize_tcp_client_target(explicit_vlc_target) if explicit_vlc_target else (auto_vlc_target or transmit_target_for_display or DEFAULT_VLC_PREVIEW_TARGET))
    try:
        hello = client.wait_for_hello(timeout_sec=5.0)
        hello_payload = dict(hello.payload)
        if args.verbose_events:
            print(json.dumps(hello.raw, ensure_ascii=False))

        recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] probe source: {probe_source}")
        if args.viewer and probe_enabled:
            recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] viewer pending: waiting for local probe output")
        if vlc_enabled and bool(vlc_target.strip()):
            recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] vlc pending: waiting for transmit preview {vlc_target}")

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
                last_metrics_payload = _decorate_pipeline_metrics(
                    event.payload,
                    probe_source=probe_source,
                    probe_target=probe_target_for_viewer,
                    transmit_target=transmit_target_for_display,
                )
                if viewer_proc is not None and viewer_proc.poll() is not None:
                    viewer_proc = None
                    args.viewer = False
                    recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] viewer closed")
                elif (
                    args.viewer
                    and probe_enabled
                    and viewer_proc is None
                    and time.time() >= next_viewer_launch_sec
                    and (
                        (
                            viewer_target.strip().startswith("tcp://")
                            and bool(last_metrics_payload.get("probe_active"))
                        )
                        or
                        (
                            bool(last_metrics_payload.get("probe_active"))
                            and int(last_metrics_payload.get("probe_frames_written") or 0) >= 8
                        )
                        or int(last_metrics_payload.get("probe_frames_written") or 0) >= 16
                    )
                ):
                    try:
                        viewer_proc = launch_final_stream_viewer(
                            FinalStreamViewerSpec(
                                target=viewer_target,
                                ffmpeg_bin=str(args.ffmpeg_bin or ""),
                                backend=str(args.viewer_backend or "auto"),
                                window_title=str(args.viewer_title),
                                width=int(last_metrics_payload.get("probe_width") or 0),
                                height=int(last_metrics_payload.get("probe_height") or 0),
                                fps=float(last_metrics_payload.get("probe_written_fps") or 0.0),
                            )
                        )
                        actual_backend = str(getattr(viewer_proc, "_hogak_viewer_backend", viewer_backend))
                        viewer_launch_failures = 0
                        viewer_message = (
                            f"[{time.strftime('%H:%M:%S')}] viewer launched "
                            f"backend={actual_backend} pid={viewer_proc.pid}"
                        )
                        recent_events.appendleft(viewer_message)
                        if args.monitor_mode == "compact" and not args.verbose_events:
                            print(viewer_message)
                    except Exception as exc:
                        viewer_launch_failures += 1
                        next_viewer_launch_sec = time.time() + min(5.0, 1.0 + (viewer_launch_failures * 0.75))
                        viewer_message = f"[{time.strftime('%H:%M:%S')}] viewer error: {exc}"
                        recent_events.appendleft(viewer_message)
                        if args.monitor_mode == "compact" and not args.verbose_events:
                            print(viewer_message)
                if vlc_proc is not None and vlc_proc.poll() is not None:
                    vlc_proc = None
                    vlc_enabled = False
                    recent_events.appendleft(f"[{time.strftime('%H:%M:%S')}] vlc closed")
                elif (
                    vlc_enabled
                    and bool(vlc_target.strip())
                    and vlc_proc is None
                    and time.time() >= next_vlc_launch_sec
                    and bool(last_metrics_payload.get("transmit_active"))
                    and int(last_metrics_payload.get("transmit_frames_written") or 0) >= 8
                ):
                    try:
                        vlc_proc = launch_final_stream_viewer(
                            FinalStreamViewerSpec(
                                target=vlc_target,
                                ffmpeg_bin=str(args.ffmpeg_bin or ""),
                                backend="vlc-low-latency",
                                window_title="Hogak Transmit VLC",
                                width=int(last_metrics_payload.get("transmit_width") or 0),
                                height=int(last_metrics_payload.get("transmit_height") or 0),
                                fps=float(last_metrics_payload.get("transmit_written_fps") or 0.0),
                            )
                        )
                        actual_backend = str(getattr(vlc_proc, "_hogak_viewer_backend", "vlc-low-latency"))
                        vlc_launch_failures = 0
                        vlc_message = (
                            f"[{time.strftime('%H:%M:%S')}] vlc launched "
                            f"backend={actual_backend} pid={vlc_proc.pid}"
                        )
                        recent_events.appendleft(vlc_message)
                        if args.monitor_mode == "compact" and not args.verbose_events:
                            print(vlc_message)
                    except Exception as exc:
                        vlc_launch_failures += 1
                        next_vlc_launch_sec = time.time() + min(5.0, 1.0 + (vlc_launch_failures * 0.75))
                        vlc_message = f"[{time.strftime('%H:%M:%S')}] vlc error: {exc}"
                        recent_events.appendleft(vlc_message)
                        if args.monitor_mode == "compact" and not args.verbose_events:
                            print(vlc_message)
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
                    status_signature = _status_signature(last_metrics_payload)
                    status_interval_sec = max(0.5, float(args.status_interval_sec))
                    if (
                        status_signature != last_status_signature
                        or now_sec - last_status_emit_sec >= status_interval_sec
                    ):
                        print(_compact_metrics(last_metrics_payload))
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
        if vlc_proc is not None and vlc_proc.poll() is None:
            vlc_proc.send_signal(signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.SIGINT)
            try:
                vlc_proc.wait(timeout=3)
            except Exception:
                vlc_proc.kill()

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
