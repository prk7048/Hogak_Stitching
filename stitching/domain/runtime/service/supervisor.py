from __future__ import annotations

from dataclasses import dataclass
import subprocess
from typing import Any

from stitching.domain.runtime.client import RuntimeClient
from stitching.domain.runtime.service.launcher import RuntimeLaunchSpec


@dataclass(slots=True)
class RuntimeSupervisor:
    """Lightweight runtime lifecycle wrapper.

    The supervisor keeps the launch/client lifecycle in one place so higher-level
    callers do not need to duplicate shutdown and cleanup behavior.
    """

    _client: RuntimeClient

    @classmethod
    def launch(cls, spec: RuntimeLaunchSpec | None = None) -> "RuntimeSupervisor":
        return cls(RuntimeClient.launch(spec))

    @property
    def client(self) -> RuntimeClient:
        return self._client

    @property
    def process(self) -> subprocess.Popen[str]:
        return self._client.process

    def __enter__(self) -> "RuntimeSupervisor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def wait_for_hello(self, timeout_sec: float = 3.0):
        return self._client.wait_for_hello(timeout_sec=timeout_sec)

    def read_event(self, timeout_sec: float = 1.0):
        return self._client.read_event(timeout_sec=timeout_sec)

    def request_metrics(self) -> None:
        self._client.request_metrics()

    def send_command(self, command_type: str, payload: dict[str, Any] | None = None) -> None:
        self._client.send_command(command_type, payload)

    def get_stderr_tail(self) -> str:
        return self._client.get_stderr_tail()

    def shutdown(self) -> None:
        self._client.shutdown()

    def close(self, *, wait_timeout_sec: float = 5.0) -> None:
        process = self._client.process
        if process.poll() is None:
            self._client.shutdown()
            try:
                process.wait(timeout=max(0.5, float(wait_timeout_sec)))
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    process.wait(timeout=2.0)
                except Exception:
                    pass
        elif process.returncode is None:
            try:
                process.wait(timeout=max(0.5, float(wait_timeout_sec)))
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
                try:
                    process.wait(timeout=2.0)
                except Exception:
                    pass
