import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "native_runtime" / "build" / "windows-release" / "Release" / "stitch_runtime.exe"
FFMPEG = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
HOMOGRAPHY = ROOT / "output" / "native" / "runtime_homography.json"
LEFT_URL = "rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1&subtype=0"
RIGHT_URL = "rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1&subtype=0"
OUTPUT_TARGET = "udp://127.0.0.1:23000?pkt_size=1316"


def main() -> int:
    command = [
        str(RUNTIME),
        "--emit-hello",
        "--heartbeat-ms",
        "1000",
        "--left-url",
        LEFT_URL,
        "--right-url",
        RIGHT_URL,
        "--input-runtime",
        "ffmpeg-cuda",
        "--ffmpeg-bin",
        str(FFMPEG),
        "--homography-file",
        str(HOMOGRAPHY),
        "--transport",
        "tcp",
        "--timeout-sec",
        "10",
        "--reconnect-cooldown-sec",
        "1",
        "--sync-pair-mode",
        "latest",
        "--sync-match-max-delta-ms",
        "60",
        "--sync-manual-offset-ms",
        "0",
        "--stitch-output-scale",
        "0.25",
        "--output-runtime",
        "ffmpeg",
        "--output-target",
        OUTPUT_TARGET,
        "--output-width",
        "1920",
        "--output-height",
        "1080",
        "--output-codec",
        "h264_nvenc",
        "--output-bitrate",
        "6M",
        "--output-preset",
        "p1",
    ]
    env = dict(os.environ)
    path_entries = [
        str(ROOT / ".third_party" / "ffmpeg" / "current" / "bin"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64",
    ]
    env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env=env,
    )

    metrics_samples: list[dict] = []
    deadline = time.time() + 12.0
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "metrics":
                continue
            payload = event.get("payload") or {}
            metrics_samples.append(payload)
            if payload.get("output_active") and payload.get("stitch_fps", 0.0) > 0:
                if len(metrics_samples) >= 3:
                    break
        if proc.stdin and proc.poll() is None:
            try:
                proc.stdin.write('{"type":"shutdown"}\n')
                proc.stdin.flush()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        proc.kill()
        proc.wait(timeout=5)
        raise

    if not metrics_samples:
        sys.stdout.write("NO_METRICS\n")
        err = proc.stderr.read().strip()
        if err:
            sys.stdout.write(err + "\n")
        return 1

    active = [sample for sample in metrics_samples if sample.get("output_active")]
    sample = active[-1] if active else metrics_samples[-1]
    summary = {
        "status": sample.get("status"),
        "calibrated": sample.get("calibrated"),
        "output_active": sample.get("output_active"),
        "left_age_ms": sample.get("left_age_ms"),
        "right_age_ms": sample.get("right_age_ms"),
        "output_width": sample.get("output_width"),
        "output_height": sample.get("output_height"),
        "stitch_fps": sample.get("stitch_fps"),
        "worker_fps": sample.get("worker_fps"),
        "output_written_fps": sample.get("output_written_fps"),
        "output_frames_written": sample.get("output_frames_written"),
        "output_codec": sample.get("output_effective_codec"),
        "gpu_warp_count": sample.get("gpu_warp_count"),
        "gpu_blend_count": sample.get("gpu_blend_count"),
        "left_last_error": sample.get("left_last_error"),
        "right_last_error": sample.get("right_last_error"),
        "output_last_error": sample.get("output_last_error"),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
