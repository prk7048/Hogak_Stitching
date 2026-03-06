from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from stitching.ffmpeg_runtime import (
    FfmpegRuntimeError,
    RtspDecodeSpec,
    build_rtsp_decode_command,
    probe_stream_info,
    resolve_binaries,
)


@dataclass(slots=True)
class TimedFrame:
    ts: float
    frame: np.ndarray


class FfmpegRtspReader:
    def __init__(
        self,
        *,
        name: str,
        url: str,
        transport: str,
        timeout_sec: float,
        reconnect_cooldown_sec: float,
        sync_buffer_sec: float,
        runtime: str,
    ) -> None:
        self.name = name
        self.url = url
        self.transport = transport
        self.timeout_sec = float(timeout_sec)
        self.reconnect_cooldown_sec = float(reconnect_cooldown_sec)
        self.runtime = runtime

        base_fps = 30.0
        maxlen = max(10, int(round(base_fps * max(0.3, float(sync_buffer_sec)) * 2.0)))
        self._buffer: deque[TimedFrame] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame_packet: TimedFrame | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: deque[str] = deque(maxlen=8)
        self._last_error = ""
        self._frames_total = 0
        self._buffer_overflow_drops = 0
        self._stale_drops = 0
        self._width = 0
        self._height = 0
        self._codec_name = ""
        self._bins = resolve_binaries()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"ffmpeg-rtsp-{self.name}")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._stop_process()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame_packet is None else self._frame_packet.frame

    def has_frames(self) -> bool:
        with self._lock:
            return bool(self._buffer)

    def pop_oldest(self) -> TimedFrame | None:
        with self._lock:
            if not self._buffer:
                return None
            return self._buffer.popleft()

    def pop_latest(self) -> TimedFrame | None:
        with self._lock:
            if not self._buffer:
                return None
            latest = self._buffer[-1]
            dropped = max(0, len(self._buffer) - 1)
            self._stale_drops += dropped
            self._buffer.clear()
            return latest

    def keep_latest_only(self) -> None:
        with self._lock:
            if len(self._buffer) <= 1:
                return
            latest = self._buffer[-1]
            dropped = len(self._buffer) - 1
            self._buffer.clear()
            self._buffer.append(latest)
            self._stale_drops += dropped

    def pop_closest(self, *, target_ts: float, max_delta_sec: float) -> tuple[TimedFrame | None, float | None]:
        with self._lock:
            if not self._buffer:
                return None, None

            while len(self._buffer) >= 2 and self._buffer[1].ts <= (target_ts - max_delta_sec):
                self._buffer.popleft()
                self._stale_drops += 1

            best_idx = -1
            best_abs_delta = float("inf")
            for idx, packet in enumerate(self._buffer):
                abs_delta = abs(packet.ts - target_ts)
                if abs_delta < best_abs_delta:
                    best_abs_delta = abs_delta
                    best_idx = idx
                if packet.ts > target_ts and abs_delta > best_abs_delta:
                    break

            if best_idx < 0 or best_abs_delta > max_delta_sec:
                return None, None

            chosen = self._buffer[best_idx]
            for _ in range(best_idx + 1):
                self._buffer.popleft()
            return chosen, (chosen.ts - target_ts)

    def snapshot_stats(self) -> dict[str, int | str]:
        with self._lock:
            return {
                "frames_total": int(self._frames_total),
                "last_error": self._last_error,
                "buffer_size": int(len(self._buffer)),
                "buffer_overflow_drops": int(self._buffer_overflow_drops),
                "stale_drops": int(self._stale_drops),
                "runtime": self.runtime,
                "codec": self._codec_name,
            }

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def _set_frame(self, frame: np.ndarray, recv_ts: float) -> None:
        with self._lock:
            packet = TimedFrame(ts=float(recv_ts), frame=frame)
            if len(self._buffer) == self._buffer.maxlen:
                self._buffer_overflow_drops += 1
            self._buffer.append(packet)
            self._frame_packet = packet
            self._last_error = ""
            self._frames_total += 1

    def _probe_stream(self) -> None:
        if self._width > 0 and self._height > 0:
            return
        if not self._bins.ffprobe:
            raise FfmpegRuntimeError("ffprobe binary not found. FFmpeg direct runtime requires ffprobe.")
        info = probe_stream_info(
            ffprobe_bin=self._bins.ffprobe,
            url=self.url,
            transport=self.transport,
            timeout_sec=self.timeout_sec,
        )
        self._codec_name = info.codec_name
        self._width = int(info.width)
        self._height = int(info.height)

    def _decode_codec_name(self) -> str:
        codec = self._codec_name.lower().strip()
        if codec.startswith("h264"):
            return "h264"
        if codec in {"hevc", "h265"} or codec.startswith("hevc"):
            return "hevc"
        return ""

    def _use_hwaccel(self) -> bool:
        return self.runtime == "ffmpeg-cuda"

    def _build_command(self) -> list[str]:
        codec = self._decode_codec_name()
        use_hwaccel = self._use_hwaccel() and bool(codec)
        return build_rtsp_decode_command(
            ffmpeg_bin=self._bins.ffmpeg,
            spec=RtspDecodeSpec(
                url=self.url,
                transport=self.transport,
                timeout_sec=self.timeout_sec,
                use_hwaccel=use_hwaccel,
                hwaccel="cuda",
                codec=codec or "h264",
                output_pix_fmt="bgr24",
                width=self._width,
                height=self._height,
            ),
        )

    def _stderr_pump(self, pipe: object) -> None:
        if pipe is None:
            return
        try:
            while not self._stop_event.is_set():
                raw = pipe.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr_lines.append(line)
        except Exception:
            return

    def _open_process(self) -> None:
        self._probe_stream()
        self._stderr_lines.clear()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = subprocess.Popen(
            self._build_command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**8,
            creationflags=creationflags,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_pump,
            args=(self._process.stderr,),
            daemon=True,
            name=f"ffmpeg-rtsp-{self.name}-stderr",
        )
        self._stderr_thread.start()

    def _stop_process(self) -> None:
        proc = self._process
        self._process = None
        if proc is None:
            return
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stderr is not None:
                proc.stderr.close()
        except Exception:
            pass
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

    def _read_exact(self, size: int) -> bytes | None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return None
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0 and not self._stop_event.is_set():
            chunk = proc.stdout.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining > 0:
            return None
        return b"".join(chunks)

    def _run(self) -> None:
        cooldown = max(0.2, float(self.reconnect_cooldown_sec))
        while not self._stop_event.is_set():
            try:
                self._open_process()
            except Exception as exc:
                self._set_error(str(exc))
                time.sleep(cooldown)
                continue

            frame_bytes = max(1, self._width * self._height * 3)
            while not self._stop_event.is_set():
                raw = self._read_exact(frame_bytes)
                if raw is None:
                    detail = self._stderr_lines[-1] if self._stderr_lines else "ffmpeg stream ended"
                    self._set_error(detail)
                    break
                try:
                    # Keep the array backed by the bytes buffer to avoid an extra
                    # 1080p frame copy on every decode step.
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(self._height, self._width, 3)
                except Exception as exc:
                    self._set_error(f"rawvideo reshape failed: {exc}")
                    break
                self._set_frame(frame, time.perf_counter())

            self._stop_process()
            if not self._stop_event.is_set():
                time.sleep(cooldown)
