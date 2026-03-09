from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import time
from typing import Any

from stitching.runtime_contract import EngineCommand
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

    @classmethod
    def launch(cls, spec: RuntimeLaunchSpec | None = None) -> "RuntimeClient":
        return cls(launch_native_runtime(spec))

    @property
    def process(self) -> subprocess.Popen[str]:
        return self._process

    def send_command(self, command_type: str, payload: dict[str, Any] | None = None) -> None:
        if self._process.stdin is None:
            raise RuntimeError("runtime stdin is unavailable")
        command = EngineCommand(seq=self._seq, type=command_type, payload=payload or {})
        self._seq += 1
        self._process.stdin.write(command.to_json() + "\n")
        self._process.stdin.flush()

    def read_event(self, timeout_sec: float = 1.0) -> RuntimeEventMessage | None:
        if self._process.stdout is None:
            raise RuntimeError("runtime stdout is unavailable")
        deadline = time.perf_counter() + max(0.01, float(timeout_sec))
        while time.perf_counter() < deadline:
            line = self._process.stdout.readline()
            if not line:
                time.sleep(0.01)
                continue
            text = line.strip()
            if not text:
                continue
            return RuntimeEventMessage(raw=json.loads(text))
        return None

    def wait_for_hello(self, timeout_sec: float = 3.0) -> RuntimeEventMessage:
        deadline = time.perf_counter() + max(0.1, float(timeout_sec))
        while time.perf_counter() < deadline:
            event = self.read_event(timeout_sec=0.25)
            if event is None:
                continue
            if event.type == "hello":
                return event
        raise TimeoutError("native runtime did not emit hello event in time")

    def request_metrics(self) -> None:
        self.send_command("request_snapshot", {"kind": "metrics"})

    def shutdown(self) -> None:
        try:
            self.send_command("shutdown", {})
        except Exception:
            pass
