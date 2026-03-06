from __future__ import annotations

import subprocess
import threading
from collections import deque

import numpy as np

from stitching.ffmpeg_runtime import (
    FfmpegRuntimeError,
    RawVideoOutputSpec,
    build_rawvideo_output_command,
    resolve_binaries,
)


class FfmpegRawVideoWriter:
    def __init__(self, *, spec: RawVideoOutputSpec) -> None:
        self.spec = spec
        self._bins = resolve_binaries()
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: deque[str] = deque(maxlen=8)
        self._last_error = ""

    @property
    def last_error(self) -> str:
        return self._last_error

    def open(self) -> None:
        if self._process is not None:
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            build_rawvideo_output_command(ffmpeg_bin=self._bins.ffmpeg, spec=self.spec),
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            bufsize=10**8,
            creationflags=creationflags,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_pump,
            args=(self._process.stderr,),
            daemon=True,
            name="ffmpeg-rawvideo-writer-stderr",
        )
        self._stderr_thread.start()

    def is_opened(self) -> bool:
        return self._process is not None and self._process.stdin is not None

    def write(self, frame: np.ndarray) -> None:
        if self._process is None or self._process.stdin is None:
            raise FfmpegRuntimeError("FFmpeg writer is not open")
        if frame.dtype != np.uint8:
            raise FfmpegRuntimeError(f"FFmpeg writer expects uint8 frame, got {frame.dtype}")
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise FfmpegRuntimeError(f"FFmpeg writer expects HxWx3 BGR frame, got shape {frame.shape}")
        height, width = frame.shape[:2]
        if width != int(self.spec.width) or height != int(self.spec.height):
            raise FfmpegRuntimeError(
                f"FFmpeg writer frame size mismatch: got {width}x{height}, expected {self.spec.width}x{self.spec.height}"
            )
        try:
            self._process.stdin.write(frame.tobytes())
        except Exception as exc:
            detail = self._stderr_lines[-1] if self._stderr_lines else str(exc)
            self._last_error = detail
            raise FfmpegRuntimeError(f"FFmpeg writer write failed: {detail}") from exc

    def release(self) -> None:
        proc = self._process
        self._process = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5.0)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=0.5)
            self._stderr_thread = None

    def _stderr_pump(self, pipe: object) -> None:
        if pipe is None:
            return
        try:
            while True:
                raw = pipe.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr_lines.append(line)
                    self._last_error = line
        except Exception:
            return
