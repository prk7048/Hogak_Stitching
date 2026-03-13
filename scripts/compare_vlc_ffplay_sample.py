from __future__ import annotations

import subprocess
import time
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "output" / "debug" / "diagnose_transmit24000.ts"
FFMPEG = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
FFPLAY = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffplay.exe"
VLC = Path(r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")
OUT_DIR = ROOT / "output" / "debug"
SENDER_LOG = OUT_DIR / "compare_sender.log"
FFPLAY_LOG = OUT_DIR / "compare_ffplay.log"
VLC_LOG = OUT_DIR / "compare_vlc.log"
PORT = 25100


def _cleanup() -> None:
    for path in (SENDER_LOG, FFPLAY_LOG, VLC_LOG):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["sample", "testsrc"], default="sample")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    if args.source == "sample" and not SAMPLE.exists():
        print(f"sample missing: {SAMPLE}")
        return 1
    if not FFMPEG.exists() or not FFPLAY.exists():
        print("ffmpeg/ffplay missing")
        return 1
    if not VLC.exists():
        print(f"vlc missing: {VLC}")
        return 1

    target = f"udp://127.0.0.1:{PORT}?pkt_size=1316"
    if args.source == "sample":
        sender_cmd = [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "info",
            "-stream_loop",
            "-1",
            "-re",
            "-fflags",
            "+genpts",
            "-i",
            str(SAMPLE),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-f",
            "mpegts",
            target,
        ]
    else:
        sender_cmd = [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "info",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1920x1080:rate=30",
            "-t",
            "12",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-g",
            "30",
            "-keyint_min",
            "30",
            "-sc_threshold",
            "0",
            "-f",
            "mpegts",
            target,
        ]
    ffplay_cmd = [
        str(FFPLAY),
        "-hide_banner",
        "-loglevel",
        "info",
        "-stats",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-analyzeduration",
        "1000000",
        "-probesize",
        "500000",
        "-f",
        "mpegts",
        "-i",
        f"udp://127.0.0.1:{PORT}",
    ]
    vlc_cmd = [
        str(VLC),
        "--intf",
        "dummy",
        "--no-media-library",
        "--no-video-title-show",
        "--vout=dummy",
        "--aout=dummy",
        "--play-and-exit",
        "--run-time=10",
        "--verbose=3",
        "--network-caching=100",
        "--live-caching=100",
        "--clock-jitter=0",
        "--clock-synchro=0",
        "--drop-late-frames",
        "--skip-frames",
        "--demux=ts",
        f"udp://@:{PORT}",
    ]

    sender = subprocess.Popen(
        sender_cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=SENDER_LOG.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ffplay = None
    vlc = None
    try:
        time.sleep(1.5)
        ffplay = subprocess.Popen(
            ffplay_cmd,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=FFPLAY_LOG.open("w", encoding="utf-8"),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(1.0)
        vlc = subprocess.Popen(
            vlc_cmd,
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=VLC_LOG.open("w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        time.sleep(12.0)
    finally:
        for process in (ffplay, vlc, sender):
            if process is None or process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    print("[sender]")
    print(f"[source] {args.source}")
    print(SENDER_LOG.read_text(encoding="utf-8", errors="replace").encode("ascii", "replace").decode())
    print("[ffplay]")
    print(FFPLAY_LOG.read_text(encoding="utf-8", errors="replace").encode("ascii", "replace").decode())
    print("[vlc]")
    print(VLC_LOG.read_text(encoding="utf-8", errors="replace").encode("ascii", "replace").decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
