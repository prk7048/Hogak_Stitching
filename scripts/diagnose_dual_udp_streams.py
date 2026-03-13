import argparse
import argparse
import json
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv312" / "Scripts" / "python.exe"
FFMPEG = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffprobe.exe"
OUT_DIR = ROOT / "output" / "debug"

RUNTIME_LOG = OUT_DIR / "diagnose_dual_runtime.log"
PROBE_TS = OUT_DIR / "diagnose_probe23000.ts"
PROBE_CAPTURE_LOG = OUT_DIR / "diagnose_probe23000_capture.log"
PROBE_DECODE_LOG = OUT_DIR / "diagnose_probe23000_decode.log"
PROBE_MONTAGE = OUT_DIR / "diagnose_probe23000_montage.png"

TRANSMIT_TS = OUT_DIR / "diagnose_transmit24000_dual.ts"
TRANSMIT_CAPTURE_LOG = OUT_DIR / "diagnose_transmit24000_dual_capture.log"
TRANSMIT_DECODE_LOG = OUT_DIR / "diagnose_transmit24000_dual_decode.log"
TRANSMIT_MONTAGE = OUT_DIR / "diagnose_transmit24000_dual_montage.png"


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _wmic_command(*args: str) -> list[str]:
    return ["wmic", *args]


def _read_wmic_stdout(*args: str) -> str:
    completed = subprocess.run(
        _wmic_command(*args),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    return (completed.stdout or "").strip()


def _query_cpu_total_percent() -> float:
    text = _read_wmic_stdout(
        "path",
        "Win32_PerfFormattedData_PerfOS_Processor",
        "where",
        "(Name='_Total')",
        "get",
        "PercentProcessorTime",
        "/value",
    )
    for line in text.splitlines():
        if line.startswith("PercentProcessorTime="):
            try:
                return float(line.split("=", 1)[1].strip())
            except ValueError:
                return 0.0
    return 0.0


def _query_process_cpu_samples() -> dict[str, float]:
    text = _read_wmic_stdout(
        "path",
        "Win32_PerfFormattedData_PerfProc_Process",
        "get",
        "Name,PercentProcessorTime",
    )
    results = {
        "stitch_runtime_cpu": 0.0,
        "ffmpeg_cpu_total": 0.0,
        "python_cpu_total": 0.0,
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Name") or line.startswith("_Total"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            value = float(parts[-1])
        except ValueError:
            continue
        name = " ".join(parts[:-1]).lower()
        if name == "stitch_runtime":
            results["stitch_runtime_cpu"] = value
        elif name.startswith("ffmpeg"):
            results["ffmpeg_cpu_total"] += value
        elif name.startswith("python"):
            results["python_cpu_total"] += value
    return results


def _query_gpu_samples() -> dict[str, float]:
    nvidia = subprocess.run(
        [
            r"C:\Windows\System32\nvidia-smi.exe",
            "--query-gpu=utilization.gpu,utilization.memory,utilization.encoder,utilization.decoder,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    line = (nvidia.stdout or "").strip().splitlines()
    if not line:
        return {
            "gpu_util": 0.0,
            "gpu_mem_util": 0.0,
            "gpu_encoder_util": 0.0,
            "gpu_decoder_util": 0.0,
            "gpu_mem_used_mb": 0.0,
            "gpu_mem_total_mb": 0.0,
            "gpu_temp_c": 0.0,
        }
    parts = [item.strip() for item in line[0].split(",")]
    values: list[float] = []
    for item in parts:
        try:
            values.append(float(item))
        except ValueError:
            values.append(0.0)
    while len(values) < 7:
        values.append(0.0)
    return {
        "gpu_util": values[0],
        "gpu_mem_util": values[1],
        "gpu_encoder_util": values[2],
        "gpu_decoder_util": values[3],
        "gpu_mem_used_mb": values[4],
        "gpu_mem_total_mb": values[5],
        "gpu_temp_c": values[6],
    }


def _collect_system_samples(duration_sec: float, interval_sec: float = 1.0) -> list[dict[str, float]]:
    samples: list[dict[str, float]] = []
    deadline = time.time() + max(1.0, float(duration_sec))
    while time.time() < deadline:
        sample = {"cpu_total": _query_cpu_total_percent()}
        sample.update(_query_process_cpu_samples())
        sample.update(_query_gpu_samples())
        samples.append(sample)
        time.sleep(max(0.2, float(interval_sec)))
    return samples


def _summarize_system_samples(samples: list[dict[str, float]]) -> dict[str, dict[str, float] | int]:
    if not samples:
        return {"samples": 0}

    def _summary(key: str) -> dict[str, float]:
        values = [float(sample.get(key) or 0.0) for sample in samples]
        return {
            "min": min(values),
            "avg": sum(values) / float(len(values)),
            "max": max(values),
        }

    return {
        "samples": len(samples),
        "cpu_total_percent": _summary("cpu_total"),
        "stitch_runtime_cpu_percent": _summary("stitch_runtime_cpu"),
        "ffmpeg_cpu_percent_total": _summary("ffmpeg_cpu_total"),
        "python_cpu_percent_total": _summary("python_cpu_total"),
        "gpu_util_percent": _summary("gpu_util"),
        "gpu_mem_util_percent": _summary("gpu_mem_util"),
        "gpu_encoder_util_percent": _summary("gpu_encoder_util"),
        "gpu_decoder_util_percent": _summary("gpu_decoder_util"),
        "gpu_mem_used_mb": _summary("gpu_mem_used_mb"),
        "gpu_temp_c": _summary("gpu_temp_c"),
    }


def _capture_stream(source_url: str, output_ts: Path, log_path: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "info",
            "-y",
            "-fflags",
            "nobuffer+discardcorrupt",
            "-flags",
            "low_delay",
            "-i",
            source_url,
            "-c",
            "copy",
            "-f",
            "mpegts",
            str(output_ts),
        ],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _decode_diagnostics(input_ts: Path, decode_log: Path, montage_path: Path) -> tuple[int, str]:
    decode = subprocess.run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(input_ts),
            "-f",
            "null",
            "-",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    decode_log.write_text(decode.stdout + decode.stderr, encoding="utf-8", errors="replace")

    montage = subprocess.run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_ts),
            "-vf",
            "fps=1,scale=640:-1,tile=2x2",
            "-frames:v",
            "1",
            str(montage_path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    montage_text = (montage.stdout + montage.stderr).strip()
    return decode.returncode, montage_text


def _ffprobe_text(path: Path) -> str:
    probe = subprocess.run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,avg_frame_rate",
            "-show_entries",
            "format=duration,size,bit_rate",
            "-of",
            "default=nw=1",
            str(path),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    return (probe.stdout + probe.stderr).strip()


def _count_patterns(text: str) -> dict[str, int]:
    needles = [
        "Packet corrupt",
        "timestamp discontinuity",
        "DTS discontinuity",
        "non-existing PPS",
        "error while decoding",
        "concealing",
        "no frame!",
    ]
    return {needle: text.count(needle) for needle in needles}


def _analyze_frame_repeats(path: Path) -> dict[str, float | int | str]:
    if not path.exists():
        return {"error": "missing_capture"}
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"error": "opencv_open_failed"}

    total_frames = 0
    repeated_transitions = 0
    max_repeat_run = 0
    current_repeat_run = 0
    diff_sum = 0.0
    downscale_width = 480
    previous_frame = None

    try:
        while total_frames < 360:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            height, width = frame.shape[:2]
            if width > downscale_width:
                scaled_height = max(1, int(round(height * (downscale_width / float(width)))))
                frame = cv2.resize(frame, (downscale_width, scaled_height), interpolation=cv2.INTER_AREA)
            if previous_frame is not None:
                mean_diff = float(np.mean(cv2.absdiff(frame, previous_frame)))
                diff_sum += mean_diff
                if mean_diff < 0.75:
                    repeated_transitions += 1
                    current_repeat_run += 1
                    max_repeat_run = max(max_repeat_run, current_repeat_run)
                else:
                    current_repeat_run = 0
            previous_frame = frame
            total_frames += 1
    finally:
        capture.release()

    transitions = max(0, total_frames - 1)
    return {
        "frames": total_frames,
        "transitions": transitions,
        "repeated_transitions": repeated_transitions,
        "repeated_ratio": (float(repeated_transitions) / float(transitions)) if transitions > 0 else 0.0,
        "max_repeat_run_frames": max_repeat_run + 1 if max_repeat_run > 0 else (1 if total_frames > 0 else 0),
        "avg_transition_diff": (diff_sum / float(transitions)) if transitions > 0 else 0.0,
    }


def _parse_runtime_metrics(log_path: Path) -> dict[str, object]:
    if not log_path.exists():
        return {"error": "missing_runtime_log"}

    metrics_payloads: list[dict[str, object]] = []
    active_payloads: list[dict[str, object]] = []
    status_counts: dict[str, int] = {}
    waiting_samples = 0
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or str(event.get("type") or "") != "metrics":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            metrics_payloads.append(payload)
            status = str(payload.get("status") or "-")
            status_counts[status] = status_counts.get(status, 0) + 1
            if status.startswith("waiting"):
                waiting_samples += 1
            if "stitching" in status:
                active_payloads.append(payload)

    if not metrics_payloads:
        return {"error": "no_metrics"}

    def _series(name: str) -> list[float]:
        values: list[float] = []
        for payload in metrics_payloads:
            value = payload.get(name)
            if isinstance(value, (int, float)):
                values.append(float(value))
        return values

    def _summary(values: list[float]) -> dict[str, float]:
        if not values:
            return {"min": 0.0, "avg": 0.0, "max": 0.0}
        return {
            "min": min(values),
            "avg": sum(values) / float(len(values)),
            "max": max(values),
        }

    first = metrics_payloads[0]
    last = metrics_payloads[-1]
    stitched_delta = int(last.get("stitched_count") or 0) - int(first.get("stitched_count") or 0)
    probe_written_delta = int(last.get("output_frames_written") or 0) - int(first.get("output_frames_written") or 0)
    transmit_written_delta = int(last.get("production_output_frames_written") or 0) - int(
        first.get("production_output_frames_written") or 0
    )

    return {
        "samples": len(metrics_payloads),
        "active_samples": len(active_payloads),
        "waiting_ratio": (float(waiting_samples) / float(len(metrics_payloads))) if metrics_payloads else 0.0,
        "status_counts": status_counts,
        "stitch_fps": _summary(_series("stitch_fps")),
        "worker_fps": _summary(_series("worker_fps")),
        "active_stitch_fps": _summary(
            [float(payload.get("stitch_fps") or 0.0) for payload in active_payloads if isinstance(payload.get("stitch_fps"), (int, float))]
        ),
        "active_worker_fps": _summary(
            [float(payload.get("worker_fps") or 0.0) for payload in active_payloads if isinstance(payload.get("worker_fps"), (int, float))]
        ),
        "probe_written_fps": _summary(_series("output_written_fps")),
        "transmit_written_fps": _summary(_series("production_output_written_fps")),
        "pair_skew_ms": _summary(_series("pair_skew_ms_mean")),
        "left_age_ms": _summary(_series("left_age_ms")),
        "right_age_ms": _summary(_series("right_age_ms")),
        "left_buffered_frames": _summary(_series("left_buffered_frames")),
        "right_buffered_frames": _summary(_series("right_buffered_frames")),
        "reused_count_delta": int(last.get("reused_count") or 0) - int(first.get("reused_count") or 0),
        "stitched_count_delta": stitched_delta,
        "probe_frames_written_delta": probe_written_delta,
        "transmit_frames_written_delta": transmit_written_delta,
        "probe_frames_dropped_delta": int(last.get("output_frames_dropped") or 0) - int(first.get("output_frames_dropped") or 0),
        "transmit_frames_dropped_delta": int(last.get("production_output_frames_dropped") or 0)
        - int(first.get("production_output_frames_dropped") or 0),
        "probe_to_stitched_ratio": (float(probe_written_delta) / float(stitched_delta)) if stitched_delta > 0 else 0.0,
        "transmit_to_stitched_ratio": (float(transmit_written_delta) / float(stitched_delta)) if stitched_delta > 0 else 0.0,
        "last_status": str(last.get("status") or "-"),
    }


def _print_summary(label: str, ts_path: Path, capture_log: Path, decode_log: Path, montage_path: Path) -> None:
    capture_text = capture_log.read_text(encoding="utf-8", errors="replace") if capture_log.exists() else ""
    decode_text = decode_log.read_text(encoding="utf-8", errors="replace") if decode_log.exists() else ""
    repeat_stats = _analyze_frame_repeats(ts_path)
    print(f"[{label}]")
    print("ts_exists", ts_path.exists())
    print("ts_size", ts_path.stat().st_size if ts_path.exists() else 0)
    if ts_path.exists():
        print(_ffprobe_text(ts_path))
    print("capture_counts", _count_patterns(capture_text))
    print("decode_counts", _count_patterns(decode_text))
    print("repeat_stats", repeat_stats)
    print("montage_exists", montage_path.exists())
    print("montage_path", montage_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture and compare probe 23000 vs transmit 24000")
    parser.add_argument("--warmup-sec", type=float, default=6.0)
    parser.add_argument("--capture-sec", type=float, default=10.0)
    parser.add_argument("--transport", default="udp", choices=["udp", "tcp"])
    parser.add_argument("--output-standard", default="realtime_gpu_1080p")
    args = parser.parse_args()

    if not PYTHON.exists():
        raise SystemExit(f"python not found: {PYTHON}")
    if not FFMPEG.exists():
        raise SystemExit(f"ffmpeg not found: {FFMPEG}")
    if not FFPROBE.exists():
        raise SystemExit(f"ffprobe not found: {FFPROBE}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup(
        [
            RUNTIME_LOG,
            PROBE_TS,
            PROBE_CAPTURE_LOG,
            PROBE_DECODE_LOG,
            PROBE_MONTAGE,
            TRANSMIT_TS,
            TRANSMIT_CAPTURE_LOG,
            TRANSMIT_DECODE_LOG,
            TRANSMIT_MONTAGE,
        ]
    )

    runtime_cmd = [
        str(PYTHON),
        "-m",
        "stitching.cli",
        "native-runtime",
        "--left-rtsp",
        "rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1&subtype=0",
        "--right-rtsp",
        "rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1&subtype=0",
        "--input-runtime",
        "ffmpeg-cuda",
        "--rtsp-transport",
        str(args.transport),
        "--input-buffer-frames",
        "4",
        "--rtsp-timeout-sec",
        "10",
        "--reconnect-cooldown-sec",
        "0.5",
        "--sync-manual-offset-ms",
        "0",
        "--pair-reuse-max-age-ms",
        "140",
        "--pair-reuse-max-consecutive",
        "4",
        "--probe-source",
        "standalone",
        "--probe-output-runtime",
        "ffmpeg",
        "--probe-output-target",
        "udp://127.0.0.1:23000?pkt_size=1316",
        "--output-standard",
        str(args.output_standard),
        "--transmit-output-runtime",
        "ffmpeg",
        "--transmit-output-target",
        "udp://127.0.0.1:24000?pkt_size=1316",
        "--transmit-output-codec",
        "h264_nvenc",
        "--transmit-output-bitrate",
        "16M",
        "--transmit-output-preset",
        "p4",
        "--transmit-output-debug-overlay",
        "--status-interval-sec",
        "1",
        "--duration-sec",
        "25",
        "--homography-file",
        str(ROOT / "output" / "native" / "runtime_homography.json"),
        "--no-output-ui",
        "--monitor-mode",
        "json",
        "--no-viewer",
    ]

    runtime = subprocess.Popen(
        runtime_cmd,
        cwd=str(ROOT),
        stdout=RUNTIME_LOG.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    probe_capture = None
    transmit_capture = None
    system_samples: list[dict[str, float]] = []
    try:
        time.sleep(max(1.0, float(args.warmup_sec)))
        probe_capture = _capture_stream(
            "udp://127.0.0.1:23000?fifo_size=5000000&overrun_nonfatal=1",
            PROBE_TS,
            PROBE_CAPTURE_LOG,
        )
        transmit_capture = _capture_stream(
            "udp://127.0.0.1:24000?fifo_size=5000000&overrun_nonfatal=1",
            TRANSMIT_TS,
            TRANSMIT_CAPTURE_LOG,
        )
        system_samples = _collect_system_samples(max(5.0, float(args.capture_sec)))
    finally:
        for proc in (probe_capture, transmit_capture):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        if runtime.poll() is None:
            runtime.terminate()
            try:
                runtime.wait(timeout=5)
            except subprocess.TimeoutExpired:
                runtime.kill()
                runtime.wait(timeout=5)

    probe_decode_rc, probe_montage_text = _decode_diagnostics(PROBE_TS, PROBE_DECODE_LOG, PROBE_MONTAGE)
    transmit_decode_rc, transmit_montage_text = _decode_diagnostics(TRANSMIT_TS, TRANSMIT_DECODE_LOG, TRANSMIT_MONTAGE)

    print("runtime_returncode", runtime.returncode)
    print("probe_decode_rc", probe_decode_rc)
    print("probe_montage_msg", probe_montage_text)
    print("transmit_decode_rc", transmit_decode_rc)
    print("transmit_montage_msg", transmit_montage_text)
    print("runtime_log", RUNTIME_LOG)
    print("[system_metrics]")
    print(_summarize_system_samples(system_samples))
    print("[runtime_metrics]")
    print(_parse_runtime_metrics(RUNTIME_LOG))
    _print_summary("probe23000", PROBE_TS, PROBE_CAPTURE_LOG, PROBE_DECODE_LOG, PROBE_MONTAGE)
    _print_summary("transmit24000", TRANSMIT_TS, TRANSMIT_CAPTURE_LOG, TRANSMIT_DECODE_LOG, TRANSMIT_MONTAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
