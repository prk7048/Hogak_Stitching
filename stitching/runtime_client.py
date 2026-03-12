from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
from queue import Empty, Queue
import subprocess
import threading
from typing import Any

from stitching.runtime_contract import EngineCommand, SUPPORTED_RELOAD_CONFIG_FIELDS
from stitching.runtime_launcher import RuntimeLaunchSpec, launch_native_runtime


@dataclass(slots=True)
class RuntimeEventMessage:
    raw: dict[str, Any]

    @property
    def type(self) -> str:
        return str(self.raw.get("type", ""))

    @property
    def payload(self) -> dict[str, Any]:
        payload = self.raw.get("payload", {})
        return payload if isinstance(payload, dict) else {}


class RuntimeClient:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._process = process
        self._seq = 1
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._pending_events: deque[RuntimeEventMessage] = deque()
        self._event_queue: Queue[RuntimeEventMessage | None] = Queue()
        self._stderr_thread: threading.Thread | None = None
        self._stdout_thread: threading.Thread | None = None
        if self._process.stdout is not None:
            self._stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
            self._stdout_thread.start()
        if self._process.stderr is not None:
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()

    @classmethod
    def launch(cls, spec: RuntimeLaunchSpec | None = None) -> "RuntimeClient":
        return cls(launch_native_runtime(spec))

    @property
    def process(self) -> subprocess.Popen[str]:
        return self._process

    def _drain_stderr(self) -> None:
        if self._process.stderr is None:
            self._stderr_tail.append("stderr unavailable")
            return
        while True:
            line = self._process.stderr.readline()
            if not line:
                break
            text = line.rstrip()
            if text:
                self._stderr_tail.append(text)

    def _drain_stdout(self) -> None:
        if self._process.stdout is None:
            self._event_queue.put(None)
            return
        while True:
            line = self._process.stdout.readline()
            if not line:
                break
            text = line.strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            self._event_queue.put(RuntimeEventMessage(raw=raw))
        self._event_queue.put(None)

    def get_stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    def send_command(self, command_type: str, payload: dict[str, Any] | None = None) -> None:
        if self._process.stdin is None:
            raise RuntimeError("runtime stdin is unavailable")
        command = EngineCommand(seq=self._seq, type=command_type, payload=payload or {})
        self._seq += 1
        self._process.stdin.write(command.to_json() + "\n")
        self._process.stdin.flush()

    def read_event(self, timeout_sec: float = 1.0) -> RuntimeEventMessage | None:
        if self._pending_events:
            return self._pending_events.popleft()
        if self._process.stdout is None:
            raise RuntimeError("runtime stdout is unavailable")
        timeout = max(0.01, float(timeout_sec))
        try:
            event = self._event_queue.get(timeout=timeout)
        except Empty:
            return None
        if event is None:
            return None
        return event

    def wait_for_hello(self, timeout_sec: float = 3.0) -> RuntimeEventMessage:
        remaining_sec = max(0.1, float(timeout_sec))
        poll_sec = min(0.25, remaining_sec)
        while remaining_sec > 0.0:
            event = self.read_event(timeout_sec=poll_sec)
            remaining_sec -= poll_sec
            if event is None:
                if self._process.poll() is not None:
                    break
                poll_sec = min(0.25, remaining_sec)
                continue
            if event.type == "hello":
                return event
            self._pending_events.append(event)
            poll_sec = min(0.25, remaining_sec)
        raise TimeoutError("native runtime did not emit hello event in time")

    def request_metrics(self) -> None:
        self.send_command("request_snapshot", {"kind": "metrics"})

    def reload_config(self, payload: dict[str, Any]) -> None:
        unsupported = sorted(set(payload) - set(SUPPORTED_RELOAD_CONFIG_FIELDS))
        if unsupported:
            raise ValueError(f"unsupported reload_config fields: {', '.join(unsupported)}")
        self.send_command("reload_config", payload)

    def reset_auto_calibration(self, *, homography_file: str = "") -> None:
        payload: dict[str, Any] = {}
        if homography_file.strip():
            payload["homography_file"] = homography_file.strip()
        self.send_command("reset_auto_calibration", payload)

    def reload_homography(self, homography_file: str) -> None:
        if not homography_file.strip():
            raise ValueError("homography_file is required")
        self.send_command("reload_homography", {"homography_file": homography_file.strip()})

    def shutdown(self) -> None:
        try:
            self.send_command("shutdown", {})
        except Exception:
            pass
