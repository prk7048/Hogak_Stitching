import argparse
import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stitching.final_stream_viewer import FinalStreamViewerSpec, launch_final_stream_viewer

FFMPEG = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
WIDTH = 640
HEIGHT = 360


def reserve_udp_port(preferred_port: int = 0) -> int:
    for candidate in (int(preferred_port), 0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", candidate))
            return int(sock.getsockname()[1])
        except OSError:
            if candidate == 0:
                raise
        finally:
            sock.close()
    raise RuntimeError("failed to reserve UDP port")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Hogak inspection viewer backends")
    parser.add_argument("--backend", choices=["auto", "ffplay", "vlc-low-latency", "opencv"], default="auto")
    args = parser.parse_args()
    port = reserve_udp_port()
    target = f"udp://127.0.0.1:{port}?pkt_size=1316"

    sender = subprocess.Popen(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-re",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={WIDTH}x{HEIGHT}:rate=30",
            "-t",
            "5",
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
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    viewer = None
    try:
        time.sleep(0.5)
        viewer = launch_final_stream_viewer(
            FinalStreamViewerSpec(
                target=target,
                ffmpeg_bin=str(FFMPEG),
                backend=str(args.backend),
                window_title=f"Hogak Viewer Test ({args.backend})",
                width=WIDTH,
                height=HEIGHT,
            )
        )
        time.sleep(1.5)
        alive = viewer.poll() is None
        actual_backend = getattr(viewer, "_hogak_viewer_backend", str(args.backend))
        print(f"viewer_alive={alive}")
        print(f"backend={actual_backend}")
        if not alive and viewer.stderr is not None:
            print(viewer.stderr.read().decode("utf-8", errors="ignore").strip())
        if sender.poll() is not None and sender.stderr is not None:
            print(sender.stderr.read().decode("utf-8", errors="ignore").strip())
        return 0 if alive else 1
    finally:
        if viewer is not None and viewer.poll() is None:
            viewer.kill()
            viewer.wait(timeout=5)
        if sender.poll() is None:
            sender.kill()
            sender.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
