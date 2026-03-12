import argparse
import json
import time
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from stitching.runtime_client import RuntimeClient
from stitching.runtime_launcher import RuntimeLaunchSpec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left-rtsp", required=True)
    parser.add_argument("--right-rtsp", required=True)
    parser.add_argument("--input-runtime", choices=["ffmpeg-cpu", "ffmpeg-cuda"], default="ffmpeg-cuda")
    parser.add_argument("--homography-file", default="")
    parser.add_argument("--output-runtime", choices=["none", "ffmpeg"], default="ffmpeg")
    parser.add_argument("--output-target", default="udp://127.0.0.1:23000?pkt_size=1316")
    parser.add_argument("--duration-sec", type=float, default=8.0)
    parser.add_argument("--reset-calibration-after-sec", type=float, default=0.0)
    parser.add_argument("--reload-homography-file", default="")
    args = parser.parse_args()

    spec = RuntimeLaunchSpec(
        emit_hello=True,
        once=False,
        heartbeat_ms=1000,
        left_rtsp=args.left_rtsp,
        right_rtsp=args.right_rtsp,
        input_runtime=args.input_runtime,
        homography_file=args.homography_file,
        output_runtime=args.output_runtime,
        output_target=args.output_target,
    )

    client = RuntimeClient.launch(spec)
    reset_sent = False
    try:
        print(json.dumps(client.wait_for_hello(timeout_sec=5.0).raw, ensure_ascii=False))
        deadline = time.time() + max(1.0, float(args.duration_sec))
        while time.time() < deadline:
            if (
                not reset_sent
                and float(args.reset_calibration_after_sec) > 0.0
                and time.time() >= deadline - max(0.0, float(args.duration_sec) - float(args.reset_calibration_after_sec))
            ):
                client.reset_auto_calibration(homography_file=str(args.reload_homography_file or args.homography_file))
                reset_sent = True
            event = client.read_event(timeout_sec=1.5)
            if event is not None:
                print(json.dumps(event.raw, ensure_ascii=False))
    finally:
        try:
            client.shutdown()
        except Exception:
            pass
        try:
            client.process.wait(timeout=5)
        except Exception:
            client.process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
