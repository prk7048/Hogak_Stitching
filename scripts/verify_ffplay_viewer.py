import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stitching.final_stream_viewer import FinalStreamViewerSpec, launch_final_stream_viewer

FFMPEG = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
TARGET = "udp://127.0.0.1:24000"


def main() -> int:
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
            "testsrc=size=640x360:rate=30",
            "-t",
            "5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-f",
            "mpegts",
            TARGET,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    viewer = None
    try:
        time.sleep(0.5)
        viewer = launch_final_stream_viewer(FinalStreamViewerSpec(target=TARGET, window_title="Hogak Viewer Test"))
        time.sleep(1.0)
        alive = viewer.poll() is None
        print(f"viewer_alive={alive}")
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
