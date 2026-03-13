from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv312" / "Scripts" / "python.exe"
FFMPEG = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffprobe.exe"
OUT_DIR = ROOT / "output" / "debug"
RUNTIME_LOG = OUT_DIR / "diagnose_transmit24000_runtime.log"
CAPTURE_LOG = OUT_DIR / "diagnose_transmit24000_capture.log"
CAPTURE_TS = OUT_DIR / "diagnose_transmit24000.ts"


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _print_file(label: str, path: Path, tail_lines: int = 80) -> None:
    print(f"[{label}] {path}")
    if not path.exists():
        print("(missing)")
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-tail_lines:]:
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose transmit freeze on UDP 24000")
    parser.add_argument("--warmup-sec", type=float, default=6.0)
    parser.add_argument("--capture-sec", type=float, default=35.0)
    parser.add_argument("--status-interval-sec", type=float, default=1.0)
    parser.add_argument("--transport", default="udp", choices=["udp", "tcp"])
    parser.add_argument("--probe-source", default="transmit", choices=["transmit", "standalone", "disabled"])
    parser.add_argument("--transmit-codec", default="h264_nvenc")
    parser.add_argument("--transmit-bitrate", default="16M")
    parser.add_argument("--transmit-preset", default="p4")
    parser.add_argument("--transmit-debug-overlay", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup([RUNTIME_LOG, CAPTURE_LOG, CAPTURE_TS])

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
        "--pair-reuse-max-age-ms",
        "140",
        "--pair-reuse-max-consecutive",
        "4",
        "--probe-source",
        str(args.probe_source),
        "--output-standard",
        "realtime_gpu_1080p",
        "--transmit-output-runtime",
        "ffmpeg",
        "--transmit-output-target",
        "udp://127.0.0.1:24000?pkt_size=1316",
        "--transmit-output-codec",
        str(args.transmit_codec),
        "--transmit-output-bitrate",
        str(args.transmit_bitrate),
        "--transmit-output-preset",
        str(args.transmit_preset),
        "--status-interval-sec",
        str(args.status_interval_sec),
        "--homography-file",
        str(ROOT / "output" / "native" / "runtime_homography.json"),
        "--no-output-ui",
        "--monitor-mode",
        "compact",
    ]
    if args.probe_source != "disabled":
        runtime_cmd.extend(
            [
                "--probe-output-runtime",
                "ffmpeg",
                "--probe-output-target",
                "udp://127.0.0.1:23000?pkt_size=1316",
            ]
        )
    if args.transmit_debug_overlay:
        runtime_cmd.append("--transmit-output-debug-overlay")

    capture_cmd = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "info",
        "-fflags",
        "nobuffer+discardcorrupt",
        "-flags",
        "low_delay",
        "-i",
        "udp://127.0.0.1:24000?fifo_size=5000000&overrun_nonfatal=1",
        "-c",
        "copy",
        "-f",
        "mpegts",
        str(CAPTURE_TS),
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
    capture: subprocess.Popen[str] | None = None
    try:
        time.sleep(max(1.0, float(args.warmup_sec)))
        capture = subprocess.Popen(
            capture_cmd,
            cwd=str(ROOT),
            stdout=CAPTURE_LOG.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(max(5.0, float(args.capture_sec)))
    finally:
        if capture is not None and capture.poll() is None:
            capture.terminate()
            try:
                capture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait(timeout=5)
        if runtime.poll() is None:
            runtime.terminate()
            try:
                runtime.wait(timeout=5)
            except subprocess.TimeoutExpired:
                runtime.kill()
                runtime.wait(timeout=5)

    print("runtime_returncode", runtime.returncode)
    print("capture_returncode", None if capture is None else capture.returncode)
    print("capture_exists", CAPTURE_TS.exists())
    if CAPTURE_TS.exists():
        print("capture_size", CAPTURE_TS.stat().st_size)
        probe = subprocess.run(
            [
                str(FFPROBE),
                "-v",
                "error",
                "-show_entries",
                "format=duration,size,bit_rate",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate,r_frame_rate",
                "-of",
                "default=noprint_wrappers=1",
                str(CAPTURE_TS),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=20,
        )
        print("[ffprobe]")
        print(probe.stdout.strip())
        if probe.stderr.strip():
            print(probe.stderr.strip())

    _print_file("runtime-log", RUNTIME_LOG)
    _print_file("capture-log", CAPTURE_LOG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
