from __future__ import annotations

import argparse
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PYTHON = ROOT / ".venv312" / "Scripts" / "python.exe"
FFPROBE = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffprobe.exe"
MIRRORED_TRANSMIT_PATH = ROOT / "output" / "native" / "verify_mirrored_transmit.ts"
DEFAULT_PROBE_PORT = 24000
DEFAULT_TRANSMIT_PORT = 24001


def reserve_udp_port(preferred_port: int) -> int:
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


def _start_runtime_reader(process: subprocess.Popen[str]) -> queue.Queue[str | None]:
    lines: "queue.Queue[str | None]" = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        try:
            for raw in iter(process.stdout.readline, ""):
                text = raw.rstrip()
                if text:
                    lines.put(text)
        finally:
            lines.put(None)

    threading.Thread(target=reader, name="verify-runtime-reader", daemon=True).start()
    return lines


def _wait_for_ready(
    process: subprocess.Popen[str],
    lines: "queue.Queue[str | None]",
    *,
    ready_tokens: tuple[str, ...],
    timeout_sec: float,
) -> tuple[str | None, bool]:
    deadline = time.time() + max(1.0, float(timeout_sec))
    ready_line: str | None = None
    viewer_ready = False
    while time.time() < deadline:
        if process.poll() is not None and lines.empty():
            break
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            continue
        if line is None:
            break
        print("runtime>", line)
        if ready_line is None and all(token in line for token in ready_tokens):
            ready_line = line
        if "viewer launched backend=" in line:
            viewer_ready = True
        if ready_line is not None and viewer_ready:
            return ready_line, True
    return ready_line, viewer_ready


def _parse_probe_size(line: str) -> tuple[int, int]:
    match = re.search(r"\bprobe=(\d+)x(\d+)\b", line)
    if match is None:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify standalone probe or mirrored transmit loopback")
    parser.add_argument("--mode", choices=["mirrored-transmit", "standalone-probe"], default="mirrored-transmit")
    parser.add_argument("--viewer-backend", choices=["auto", "ffplay", "vlc-low-latency", "opencv"], default="auto")
    args = parser.parse_args()

    probe_port = reserve_udp_port(DEFAULT_PROBE_PORT)
    probe_target = f"udp://127.0.0.1:{probe_port}?pkt_size=1316"
    transmit_port = reserve_udp_port(DEFAULT_TRANSMIT_PORT)
    transmit_target = f"udp://127.0.0.1:{transmit_port}?pkt_size=1316"

    runtime_cmd = [
        str(PYTHON),
        "-m",
        "stitching.cli",
        "native-runtime",
        "--no-output-ui",
        "--monitor-mode",
        "compact",
        "--duration-sec",
        "18",
        "--status-interval-sec",
        "1",
        "--viewer",
        "--viewer-backend",
        str(args.viewer_backend),
        "--viewer-title",
        f"Hogak Verify {args.mode}",
        "--probe-output-runtime",
        "ffmpeg",
        "--probe-output-target",
        probe_target,
        "--probe-output-codec",
        "libx264",
        "--probe-output-muxer",
        "mpegts",
    ]
    if args.mode == "mirrored-transmit":
        runtime_cmd.extend(
            [
                "--probe-source",
                "transmit",
                "--transmit-output-runtime",
                "ffmpeg",
                "--transmit-output-target",
                transmit_target,
                "--transmit-output-codec",
                "libx264",
                "--transmit-output-muxer",
                "mpegts",
            ]
        )
    else:
        runtime_cmd.extend(["--probe-source", "standalone", "--transmit-output-runtime", "none"])

    if MIRRORED_TRANSMIT_PATH.exists():
        MIRRORED_TRANSMIT_PATH.unlink()

    capture_process: subprocess.Popen[bytes] | None = None
    if args.mode == "mirrored-transmit":
        capture_process = subprocess.Popen(
            [
                str(FFPROBE.parent / "ffmpeg.exe"),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-fflags",
                "nobuffer+discardcorrupt",
                "-flags",
                "low_delay",
                "-i",
                f"udp://127.0.0.1:{transmit_port}?fifo_size=5000000&overrun_nonfatal=1",
                "-c",
                "copy",
                "-f",
                "mpegts",
                str(MIRRORED_TRANSMIT_PATH),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    runtime = subprocess.Popen(
        runtime_cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines = _start_runtime_reader(runtime)

    try:
        ready_tokens = (
            ("probe_source=transmit", "probe_active=True", "transmit_active=True")
            if args.mode == "mirrored-transmit"
            else ("probe_source=standalone", "probe_active=True")
        )
        ready_line, viewer_ready = _wait_for_ready(runtime, lines, ready_tokens=ready_tokens, timeout_sec=30.0)
        if ready_line is None:
            print("runtime did not reach expected active state within timeout")
            print("runtime_returncode", runtime.poll())
            return 1

        probe_width, probe_height = _parse_probe_size(ready_line)
        print("probe_size", (probe_width, probe_height))
        print("viewer_ready", viewer_ready)
        print("viewer_backend", args.viewer_backend)
        print("capture_started", capture_process is not None)
        print("mode", args.mode)
        runtime.wait(timeout=25)
        print("runtime_returncode", runtime.returncode)
        if capture_process is not None and capture_process.poll() is None:
            capture_process.terminate()
            try:
                capture_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                capture_process.kill()
                capture_process.wait(timeout=5)
        if capture_process is not None:
            capture_stderr = ""
            if capture_process.stderr is not None:
                capture_stderr = capture_process.stderr.read().decode("utf-8", errors="ignore").strip()
            print("capture_rc", capture_process.returncode)
            print("capture_stderr", capture_stderr)
        print("mirrored_transmit_exists", MIRRORED_TRANSMIT_PATH.exists())
        if MIRRORED_TRANSMIT_PATH.exists():
            print("mirrored_transmit_size", MIRRORED_TRANSMIT_PATH.stat().st_size)
            if FFPROBE.exists():
                probe = subprocess.run(
                    [
                        str(FFPROBE),
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=codec_name,width,height",
                        "-of",
                        "default=noprint_wrappers=1",
                        str(MIRRORED_TRANSMIT_PATH),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                print("ffprobe_rc", probe.returncode)
                print("ffprobe_stdout", probe.stdout.strip())
                print("ffprobe_stderr", probe.stderr.strip())
    finally:
        if runtime.poll() is None:
            runtime.terminate()
            try:
                runtime.wait(timeout=5)
            except subprocess.TimeoutExpired:
                runtime.kill()
                runtime.wait(timeout=5)
        if capture_process is not None and capture_process.poll() is None:
            capture_process.terminate()
            try:
                capture_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                capture_process.kill()
                capture_process.wait(timeout=5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
