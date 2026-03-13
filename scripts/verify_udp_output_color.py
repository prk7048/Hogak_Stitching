from __future__ import annotations

import socket
import subprocess
import time
import argparse
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
CAPTURE_PATH = ROOT / "tmp_udp_capture.png"
TS_PATH = ROOT / "tmp_udp_capture.ts"


def reserve_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codec", default="libx264")
    args = parser.parse_args()

    if not FFMPEG.exists():
        raise SystemExit(f"ffmpeg not found: {FFMPEG}")

    width, height = 320, 240
    fps = 10
    colors = [
        ("red", (0, 0, 255)),
        ("green", (0, 255, 0)),
        ("blue", (255, 0, 0)),
        ("white", (255, 255, 255)),
    ]
    frames = [np.full((height, width, 3), color, dtype=np.uint8) for _, color in colors]

    if TS_PATH.exists():
        TS_PATH.unlink()
    sender_cmd = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        args.codec,
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-vf",
        "format=pix_fmts=yuv420p:color_spaces=bt709:color_ranges=tv",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "mpegts",
        str(TS_PATH),
    ]
    if args.codec == "libx264":
        sender_cmd[18:18] = ["-preset", "ultrafast", "-tune", "zerolatency", "-bf", "0"]
    elif args.codec.endswith("_nvenc"):
        sender_cmd[18:18] = [
            "-preset",
            "p1",
            "-tune",
            "ll",
            "-rc",
            "cbr",
            "-zerolatency",
            "1",
            "-bf",
            "0",
            "-g",
            str(fps),
            "-keyint_min",
            str(fps),
        ]

    if CAPTURE_PATH.exists():
        CAPTURE_PATH.unlink()

    receiver_cmd = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(TS_PATH),
        "-frames:v",
        "1",
        str(CAPTURE_PATH),
    ]
    sender = subprocess.Popen(sender_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    assert sender.stdin is not None
    for frame in frames:
        sender.stdin.write(frame.tobytes())
        sender.stdin.flush()
        time.sleep(0.05)
    sender.stdin.close()

    sender.wait(timeout=20)
    receiver = subprocess.Popen(receiver_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    receiver.wait(timeout=20)

    sender_err = sender.stderr.read().decode("utf-8", "replace")
    receiver_err = receiver.stderr.read().decode("utf-8", "replace")

    print("sender_rc", sender.returncode)
    print("receiver_rc", receiver.returncode)
    print("codec", args.codec)
    print("sender_err", sender_err)
    print("receiver_err", receiver_err)
    print("capture_exists", CAPTURE_PATH.exists())

    if CAPTURE_PATH.exists():
        image = cv2.imread(str(CAPTURE_PATH), cv2.IMREAD_COLOR)
        print("capture_mean_bgr", image.mean(axis=(0, 1)).tolist())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
