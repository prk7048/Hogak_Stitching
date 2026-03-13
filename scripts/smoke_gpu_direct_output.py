from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stitching.project_defaults import DEFAULT_NATIVE_HOMOGRAPHY_PATH, default_left_rtsp, default_right_rtsp

RUNTIME_BIN = ROOT / "native_runtime" / "build" / "windows-release" / "Release" / "stitch_runtime.exe"
FFMPEG_BIN = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"
FFPROBE_BIN = ROOT / ".third_party" / "ffmpeg" / "current" / "bin" / "ffprobe.exe"


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    path_entries = [
        str(ROOT / ".third_party" / "ffmpeg" / "current" / "bin"),
        str(ROOT / ".third_party" / "ffmpeg-dev" / "current" / "bin"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.1\bin\x64",
    ]
    env["PATH"] = os.pathsep.join(path_entries + [env.get("PATH", "")])
    return env


def _start_reader(stream: object, label: str) -> tuple["queue.Queue[str | None]", threading.Thread]:
    lines: "queue.Queue[str | None]" = queue.Queue()

    def reader() -> None:
        assert hasattr(stream, "readline")
        try:
            while True:
                raw = stream.readline()
                if not raw:
                    break
                text = raw.rstrip()
                if text:
                    lines.put(f"{label}> {text}")
        finally:
            lines.put(None)

    thread = threading.Thread(target=reader, name=f"gpu-direct-smoke-{label}", daemon=True)
    thread.start()
    return lines, thread


def _drain_queue(lines: "queue.Queue[str | None]") -> list[str]:
    collected: list[str] = []
    finished = False
    while not finished:
        try:
            item = lines.get_nowait()
        except queue.Empty:
            break
        if item is None:
            finished = True
            continue
        collected.append(item)
    return collected


def _reserve_udp_port(preferred_port: int) -> int:
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


def _reserve_tcp_port(preferred_port: int) -> int:
    for candidate in (int(preferred_port), 0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", candidate))
            return int(sock.getsockname()[1])
        except OSError:
            if candidate == 0:
                raise
        finally:
            sock.close()
    raise RuntimeError("failed to reserve TCP port")


def _start_capture_process(
    input_target: str,
    output_path: Path,
) -> subprocess.Popen[str]:
    capture_cmd = [
        str(FFPROBE_BIN.parent / "ffmpeg.exe"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-fflags",
        "nobuffer+discardcorrupt",
        "-flags",
        "low_delay",
        "-analyzeduration",
        "0",
        "-probesize",
        "32",
        "-i",
        input_target,
        "-c",
        "copy",
        "-f",
        "mpegts",
        str(output_path),
    ]
    return subprocess.Popen(
        capture_cmd,
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _request_runtime_shutdown(process: subprocess.Popen[str]) -> bool:
    if process.poll() is not None:
        return True
    if process.stdin is None:
        return False
    try:
        process.stdin.write('{"seq":1,"type":"shutdown","payload":{}}\n')
        process.stdin.flush()
        process.stdin.close()
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test native gpu-direct output path")
    parser.add_argument("--mode", choices=["file", "udp", "tee-preview"], default="file")
    parser.add_argument("--duration-sec", type=float, default=12.0)
    parser.add_argument(
        "--target",
        default=str(ROOT / "output" / "debug" / "gpu_direct_smoke.ts"),
        help="Output file written by gpu-direct writer in file mode",
    )
    parser.add_argument("--codec", default="h264_nvenc")
    parser.add_argument("--muxer", default="mpegts")
    parser.add_argument("--bitrate", default="12M")
    parser.add_argument("--udp-port", type=int, default=25000)
    parser.add_argument("--preview-port", type=int, default=25001)
    parser.add_argument(
        "--capture-path",
        default=str(ROOT / "output" / "debug" / "gpu_direct_smoke_udp_capture.ts"),
        help="Captured file when mode=udp",
    )
    parser.add_argument(
        "--preview-capture-path",
        default=str(ROOT / "output" / "debug" / "gpu_direct_smoke_tcp_preview_capture.ts"),
        help="Captured file from local TCP preview leg when mode=tee-preview",
    )
    args = parser.parse_args()

    tcp_preview_port = 0
    preview_output_path: Path | None = None
    if args.mode == "udp":
        udp_port = _reserve_udp_port(args.udp_port)
        runtime_target = f"udp://127.0.0.1:{udp_port}?pkt_size=1316"
        output_path = Path(args.capture_path).resolve()
    elif args.mode == "tee-preview":
        udp_port = _reserve_udp_port(args.udp_port)
        preview_candidate = args.preview_port if int(args.preview_port) > 0 else udp_port + 1
        tcp_preview_port = _reserve_tcp_port(preview_candidate)
        runtime_target = (
            f"[f=mpegts:onfail=ignore]udp://127.0.0.1:{udp_port}?pkt_size=1316"
            f"|[f=mpegts:onfail=ignore]tcp://0.0.0.0:{tcp_preview_port}?listen=1"
        )
        output_path = Path(args.capture_path).resolve()
        preview_output_path = Path(args.preview_capture_path).resolve()
    else:
        udp_port = 0
        runtime_target = str(Path(args.target).resolve())
        output_path = Path(args.target).resolve()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    if preview_output_path is not None:
        preview_output_path.parent.mkdir(parents=True, exist_ok=True)
        if preview_output_path.exists():
            preview_output_path.unlink()

    cmd = [
        str(RUNTIME_BIN),
        "--emit-hello",
        "--heartbeat-ms",
        "1000",
        "--left-url",
        default_left_rtsp(),
        "--right-url",
        default_right_rtsp(),
        "--input-runtime",
        "ffmpeg-cuda",
        "--ffmpeg-bin",
        str(FFMPEG_BIN),
        "--homography-file",
        str(ROOT / DEFAULT_NATIVE_HOMOGRAPHY_PATH),
        "--width",
        "1920",
        "--height",
        "1080",
        "--transport",
        "tcp",
        "--input-buffer-frames",
        "8",
        "--video-codec",
        "h264",
        "--output-runtime",
        "none",
        "--production-output-runtime",
        "gpu-direct",
        "--production-output-profile",
        "production-compatible",
        "--production-output-target",
        runtime_target,
        "--production-output-codec",
        str(args.codec),
        "--production-output-bitrate",
        str(args.bitrate),
        "--production-output-preset",
        "p4",
        "--production-output-muxer",
        str(args.muxer),
    ]

    capture_process: subprocess.Popen[str] | None = None
    preview_capture_process: subprocess.Popen[str] | None = None
    if args.mode in {"udp", "tee-preview"}:
        capture_process = _start_capture_process(
            f"udp://127.0.0.1:{udp_port}?fifo_size=5000000&overrun_nonfatal=1",
            output_path,
        )
        time.sleep(1.0)

    print("command:", subprocess.list2cmdline(cmd))
    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_runtime_env(),
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_q, stdout_thread = _start_reader(process.stdout, "stdout")
    stderr_q, stderr_thread = _start_reader(process.stderr, "stderr")

    start = time.time()
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    preview_started = False
    try:
        while time.time() - start < max(1.0, float(args.duration_sec)):
            stdout_lines.extend(_drain_queue(stdout_q))
            stderr_lines.extend(_drain_queue(stderr_q))
            if (
                args.mode == "tee-preview"
                and not preview_started
                and preview_output_path is not None
                and any('"production_output_active":true' in line for line in stdout_lines[-12:])
            ):
                time.sleep(0.5)
                preview_capture_process = _start_capture_process(
                    f"tcp://127.0.0.1:{tcp_preview_port}",
                    preview_output_path,
                )
                preview_started = True
            if process.poll() is not None:
                break
            time.sleep(0.25)
    finally:
        if process.poll() is None:
            _request_runtime_shutdown(process)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        time.sleep(0.75)
        if capture_process is not None and capture_process.poll() is None:
            capture_process.terminate()
            try:
                capture_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                capture_process.kill()
                capture_process.wait(timeout=10)
        if preview_capture_process is not None and preview_capture_process.poll() is None:
            preview_capture_process.terminate()
            try:
                preview_capture_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                preview_capture_process.kill()
                preview_capture_process.wait(timeout=10)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        stdout_lines.extend(_drain_queue(stdout_q))
        stderr_lines.extend(_drain_queue(stderr_q))

    print("returncode:", process.returncode)
    print("mode:", args.mode)
    print("runtime_target:", runtime_target)
    print("stdout_tail:")
    for line in stdout_lines[-30:]:
        print(line)
    print("stderr_tail:")
    for line in stderr_lines[-30:]:
        print(line)
    if capture_process is not None:
        capture_stderr = ""
        if capture_process.stderr is not None:
            capture_stderr = capture_process.stderr.read().strip()
        print("capture_rc:", capture_process.returncode)
        print("capture_stderr:")
        print(capture_stderr)
    if preview_capture_process is not None:
        preview_capture_stderr = ""
        if preview_capture_process.stderr is not None:
            preview_capture_stderr = preview_capture_process.stderr.read().strip()
        print("preview_capture_rc:", preview_capture_process.returncode)
        print("preview_capture_stderr:")
        print(preview_capture_stderr)

    if not output_path.exists():
        print("output_exists: False")
        return 1

    print("output_exists: True")
    print("output_size:", output_path.stat().st_size)

    if output_path.stat().st_size <= 0:
        return 1

    if FFPROBE_BIN.exists():
        probe = subprocess.run(
            [
                str(FFPROBE_BIN),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "default=noprint_wrappers=1",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        print("ffprobe_rc:", probe.returncode)
        print("ffprobe_stdout:")
        print(probe.stdout.strip())
        print("ffprobe_stderr:")
        print(probe.stderr.strip())
        if probe.returncode != 0:
            return 1
        if preview_output_path is not None:
            if not preview_output_path.exists():
                print("preview_output_exists: False")
                return 1
            print("preview_output_exists: True")
            print("preview_output_size:", preview_output_path.stat().st_size)
            if preview_output_path.stat().st_size <= 0:
                return 1
            preview_probe = subprocess.run(
                [
                    str(FFPROBE_BIN),
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height",
                    "-of",
                    "default=noprint_wrappers=1",
                    str(preview_output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            print("preview_ffprobe_rc:", preview_probe.returncode)
            print("preview_ffprobe_stdout:")
            print(preview_probe.stdout.strip())
            print("preview_ffprobe_stderr:")
            print(preview_probe.stderr.strip())
            if preview_probe.returncode != 0:
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
