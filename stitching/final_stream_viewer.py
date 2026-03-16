from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

_VIEWER_STARTUP_ATTEMPTS = 4
_VIEWER_STARTUP_RETRY_SEC = 0.75
_VIEWER_UDP_FIFO_SIZE = 262144
_VIEWER_MAX_WIDTH = 1920
_VIEWER_MAX_HEIGHT = 1080
_VIEWER_MAX_FPS = 30.0


def _wait_for_ready_signal(process: subprocess.Popen[bytes], timeout_sec: float = 8.0) -> bool:
    if process.stdout is None:
        return False

    ready_queue: "queue.Queue[str | None]" = queue.Queue(maxsize=1)

    def reader() -> None:
        try:
            line = process.stdout.readline()
            ready_queue.put(line.decode("utf-8", errors="ignore").strip() if line else None)
        except Exception:
            ready_queue.put(None)

    thread = threading.Thread(target=reader, name="hogak-viewer-ready", daemon=True)
    thread.start()
    deadline = time.time() + max(0.5, float(timeout_sec))
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            value = ready_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        return value == "READY"
    return False


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
            return
        except Exception:
            pass
    try:
        process.terminate()
        process.wait(timeout=1.5)
    except Exception:
        try:
            process.kill()
        except Exception:
            return


@dataclass(slots=True)
class FinalStreamViewerSpec:
    target: str
    creationflags: int = 0
    ffmpeg_bin: str = ""
    ffplay_bin: str = ""
    backend: str = "auto"
    window_title: str = "Hogak Final Stream"
    width: int = 0
    height: int = 0
    fps: float = 0.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_ffplay_binary(explicit_path: str = "") -> Path:
    candidates: list[Path] = []
    if explicit_path.strip():
        explicit_candidate = Path(explicit_path).expanduser()
        if explicit_candidate.name.lower() == "ffmpeg.exe":
            candidates.append(explicit_candidate.with_name("ffplay.exe"))
        candidates.append(explicit_candidate)

    env_path = os.environ.get("FFPLAY_BIN", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    found = shutil.which("ffplay")
    if found:
        candidates.append(Path(found))

    candidates.extend(
        [
            _repo_root() / ".third_party" / "ffmpeg" / "current" / "bin" / "ffplay.exe",
            _repo_root() / ".third_party" / "ffmpeg" / "bin" / "ffplay.exe",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("ffplay binary not found. Set FFPLAY_BIN or install ffplay.")


def resolve_ffmpeg_binary(explicit_path: str = "") -> Path:
    candidates: list[Path] = []
    if explicit_path.strip():
        explicit_candidate = Path(explicit_path).expanduser()
        if explicit_candidate.name.lower() == "ffplay.exe":
            candidates.append(explicit_candidate.with_name("ffmpeg.exe"))
        candidates.append(explicit_candidate)

    env_path = os.environ.get("FFMPEG_BIN", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))

    candidates.extend(
        [
            _repo_root() / ".third_party" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe",
            _repo_root() / ".third_party" / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("ffmpeg binary not found. Set FFMPEG_BIN or install ffmpeg.")


def _build_stream_receive_target(target: str) -> str:
    value = target.strip()
    if value.startswith("udp://"):
        endpoint = value.split("?", 1)[0][len("udp://") :]
        if endpoint.startswith("@"):
            endpoint = endpoint[1:]
        return f"udp://{endpoint}?fifo_size={_VIEWER_UDP_FIFO_SIZE}&overrun_nonfatal=1"
    if value.startswith("tcp://"):
        endpoint = value.split("?", 1)[0][len("tcp://") :]
        return f"tcp://{endpoint}"
    return value


def _compute_preview_size(width: int, height: int) -> tuple[int, int]:
    width = max(1, int(width))
    height = max(1, int(height))
    scale = min(_VIEWER_MAX_WIDTH / float(width), _VIEWER_MAX_HEIGHT / float(height), 1.0)
    preview_width = max(2, int(round(width * scale)))
    preview_height = max(2, int(round(height * scale)))
    if (preview_width % 2) != 0:
        preview_width -= 1
    if (preview_height % 2) != 0:
        preview_height -= 1
    return max(2, preview_width), max(2, preview_height)


def _build_ffplay_command(spec: FinalStreamViewerSpec) -> list[str]:
    if not spec.target.strip():
        raise ValueError("final stream viewer target is required")
    target = spec.target.strip()
    command = [
        str(resolve_ffplay_binary(spec.ffplay_bin or spec.ffmpeg_bin)),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
    ]
    if target.startswith("udp://"):
        command.extend(
            [
                "-f",
                "mpegts",
                "-analyzeduration",
                "1000000",
                "-probesize",
                "500000",
            ]
        )
        target = target.split("?", 1)[0]
    command.extend(["-i", target])
    return command


def _build_opencv_viewer_command(spec: FinalStreamViewerSpec) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "stitching.final_stream_viewer",
        "--target",
        spec.target,
        "--title",
        spec.window_title,
    ]
    if spec.width > 0:
        command.extend(["--width", str(int(spec.width))])
    if spec.height > 0:
        command.extend(["--height", str(int(spec.height))])
    if spec.fps > 0.0:
        command.extend(["--fps", f"{float(spec.fps):.3f}"])
    if spec.ffmpeg_bin.strip():
        command.extend(["--ffmpeg-bin", spec.ffmpeg_bin])
    return command


def _read_process_stderr(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    try:
        stderr_bytes = process.stderr.read() or b""
    except Exception:
        return ""
    return stderr_bytes.decode("utf-8", errors="ignore").strip()


def _launch_viewer_process(
    spec: FinalStreamViewerSpec,
    *,
    startupinfo: subprocess.STARTUPINFO | None,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        _build_opencv_viewer_command(spec),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=spec.creationflags,
        startupinfo=startupinfo,
    )


def _launch_ffplay_process(
    spec: FinalStreamViewerSpec,
    *,
    startupinfo: subprocess.STARTUPINFO | None,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        _build_ffplay_command(spec),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=spec.creationflags,
        startupinfo=startupinfo,
    )


def _viewer_backend_order(backend: str) -> tuple[str, ...]:
    selected = str(backend or "auto").strip().lower()
    if selected == "auto":
        return ("ffplay", "opencv")
    if selected in {"ffplay", "opencv"}:
        return (selected,)
    raise ValueError(f"unsupported viewer backend: {backend}")


def launch_final_stream_viewer(spec: FinalStreamViewerSpec) -> subprocess.Popen[bytes]:
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 1

    errors: list[str] = []
    for backend_name in _viewer_backend_order(spec.backend):
        for attempt in range(1, _VIEWER_STARTUP_ATTEMPTS + 1):
            process = (
                _launch_ffplay_process(spec, startupinfo=startupinfo)
                if backend_name == "ffplay"
                else _launch_viewer_process(spec, startupinfo=startupinfo)
            )
            time.sleep(0.6)
            if backend_name == "ffplay":
                if process.poll() is None:
                    setattr(process, "_hogak_viewer_backend", backend_name)
                    return process
            elif process.poll() is None and _wait_for_ready_signal(process, timeout_sec=8.0):
                setattr(process, "_hogak_viewer_backend", "opencv")
                return process

            _terminate_process(process)
            if process.poll() is None:
                try:
                    process.wait(timeout=2.0)
                except Exception:
                    pass
            stderr_text = _read_process_stderr(process)
            message = f"attempt={attempt} {backend_name} exited with code {process.returncode}"
            if stderr_text:
                message += f": {stderr_text}"
            errors.append(message)
            if attempt < _VIEWER_STARTUP_ATTEMPTS:
                time.sleep(_VIEWER_STARTUP_RETRY_SEC)
    raise RuntimeError("; ".join(errors))


def _opencv_viewer_main(target: str, title: str) -> int:
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as exc:
        print(f"opencv viewer missing dependency: {exc}", file=sys.stderr)
        return 2

    width = int(getattr(_opencv_viewer_main, "_width", 0) or 0)
    height = int(getattr(_opencv_viewer_main, "_height", 0) or 0)
    fps = float(getattr(_opencv_viewer_main, "_fps", 0.0) or 0.0)
    ffmpeg_bin = str(getattr(_opencv_viewer_main, "_ffmpeg_bin", "") or "")
    if width <= 0 or height <= 0:
        print("opencv viewer requires output width and height", file=sys.stderr)
        return 2

    receive_target = _build_stream_receive_target(target)
    preview_width, preview_height = _compute_preview_size(width, height)
    preview_fps = min(_VIEWER_MAX_FPS, fps) if fps > 0.0 else _VIEWER_MAX_FPS
    frame_bytes = width * height * 3
    video_filters = [f"scale={preview_width}:{preview_height}:flags=fast_bilinear"]
    if preview_fps > 0.0:
        video_filters.insert(0, f"fps={preview_fps:.3f}")
    command = [
        str(resolve_ffmpeg_binary(ffmpeg_bin)),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer+discardcorrupt",
        "-flags",
        "low_delay",
        "-analyzeduration",
        "1000000",
        "-probesize",
        "500000",
        "-i",
        receive_target,
        "-an",
        "-vf",
        ",".join(video_filters),
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "-",
    ]
    frame_bytes = preview_width * preview_height * 3

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    latest_frame: dict[str, object] = {"frame": None, "error": "", "eof": False}
    lock = threading.Lock()
    stop_event = threading.Event()

    def read_exact(pipe: object, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = pipe.read(remaining)  # type: ignore[attr-defined]
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def reader_loop() -> None:
        try:
            stdout = process.stdout
            if stdout is None:
                with lock:
                    latest_frame["error"] = "ffmpeg stdout pipe unavailable"
                return
            while not stop_event.is_set():
                raw = read_exact(stdout, frame_bytes)
                if len(raw) != frame_bytes:
                    break
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((preview_height, preview_width, 3))
                with lock:
                    latest_frame["frame"] = frame.copy()
        except Exception as exc:
            with lock:
                latest_frame["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with lock:
                latest_frame["eof"] = True

    reader = threading.Thread(target=reader_loop, name="hogak-opencv-viewer", daemon=True)
    reader.start()
    deadline = time.time() + 8.0

    try:
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title, preview_width, preview_height)
        ready_sent = False
        while True:
            frame = None
            eof = False
            error_text = ""
            with lock:
                if latest_frame["frame"] is not None:
                    frame = latest_frame["frame"]
                eof = bool(latest_frame["eof"])
                error_text = str(latest_frame["error"] or "")

            if frame is not None:
                if not ready_sent:
                    print("READY", flush=True)
                    ready_sent = True
                cv2.imshow(title, frame)
                deadline = time.time() + 8.0
            elif eof and time.time() >= deadline:
                if error_text:
                    print(error_text, file=sys.stderr)
                elif process.poll() is not None and process.stderr is not None:
                    stderr_text = process.stderr.read().decode("utf-8", errors="ignore").strip()
                    if stderr_text:
                        print(stderr_text, file=sys.stderr)
                else:
                    print(f"opencv viewer failed to receive frames: {receive_target}", file=sys.stderr)
                return 2

            key = cv2.waitKey(10) & 0xFF
            if key in (27, ord("q")):
                return 0
    finally:
        stop_event.set()
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="OpenCV inspection viewer helper")
    parser.add_argument("--target", required=True)
    parser.add_argument("--title", default="Hogak Final Stream")
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--fps", type=float, default=0.0)
    parser.add_argument("--ffmpeg-bin", default="")
    args = parser.parse_args()

    setattr(_opencv_viewer_main, "_width", int(args.width))
    setattr(_opencv_viewer_main, "_height", int(args.height))
    setattr(_opencv_viewer_main, "_fps", float(args.fps))
    setattr(_opencv_viewer_main, "_ffmpeg_bin", str(args.ffmpeg_bin))
    return _opencv_viewer_main(args.target, args.title)


if __name__ == "__main__":
    raise SystemExit(_main())
