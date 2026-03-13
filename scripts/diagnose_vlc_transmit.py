from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv312" / "Scripts" / "python.exe"
VLC = Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")
OUT_DIR = ROOT / "output" / "debug"
RUNTIME_LOG = OUT_DIR / "diagnose_vlc_runtime.log"
VLC_LOG = OUT_DIR / "diagnose_vlc.log"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in (RUNTIME_LOG, VLC_LOG):
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    if not VLC.exists():
        print(f"vlc not found: {VLC}")
        return 1

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
        "udp",
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
        "transmit",
        "--probe-output-target",
        "udp://127.0.0.1:23000?pkt_size=1316",
        "--output-standard",
        "realtime_gpu_1080p",
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
        "compact",
    ]

    vlc_cmd = [
        str(VLC),
        "--intf",
        "dummy",
        "--play-and-exit",
        "--run-time=12",
        "--verbose=2",
        "udp://@:24000",
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
    vlc = None
    try:
        time.sleep(8)
        vlc = subprocess.Popen(
            vlc_cmd,
            cwd=str(ROOT),
            stdout=VLC_LOG.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(14)
    finally:
        if vlc is not None and vlc.poll() is None:
            vlc.terminate()
            try:
                vlc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vlc.kill()
                vlc.wait(timeout=5)
        if runtime.poll() is None:
            runtime.terminate()
            try:
                runtime.wait(timeout=5)
            except subprocess.TimeoutExpired:
                runtime.kill()
                runtime.wait(timeout=5)

    print("[runtime-log]")
    print(RUNTIME_LOG.read_text(encoding="utf-8", errors="replace"))
    print("[vlc-log]")
    print(VLC_LOG.read_text(encoding="utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
