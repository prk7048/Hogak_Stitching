from __future__ import annotations

import threading
import time
from typing import Any, Callable


def wait_for_output_ready(
    service: Any,
    *,
    timeout_sec: float = 10.0,
    metrics_output_failure_reason: Callable[[dict[str, Any] | None], str],
    metrics_indicate_output_ready: Callable[[dict[str, Any] | None], bool],
    metric_int: Callable[[Any], int],
) -> dict[str, Any]:
    if service._supervisor is None:
        raise RuntimeError("runtime supervisor is not available")
    deadline = time.time() + max(1.0, float(timeout_sec))
    last_metrics_request_sec = 0.0
    while time.time() < deadline:
        if service._supervisor.process.poll() is not None:
            stderr_tail = service._supervisor.get_stderr_tail().strip()
            detail = "runtime exited before live output was ready"
            if stderr_tail:
                last_line = stderr_tail.splitlines()[-1].strip()
                if last_line:
                    detail = f"{detail}: {last_line}"
            raise RuntimeError(detail)

        now = time.time()
        if now - last_metrics_request_sec >= 1.0:
            service._supervisor.request_metrics()
            last_metrics_request_sec = now

        event = service._supervisor.read_event(timeout_sec=min(0.25, max(0.05, deadline - now)))
        if event is not None:
            service._ingest_runtime_event_locked(event)

        failure_reason = metrics_output_failure_reason(service._latest_metrics)
        if failure_reason:
            raise RuntimeError(f"runtime output failed before the first frame: {failure_reason}")
        if metrics_indicate_output_ready(service._latest_metrics):
            return dict(service._latest_metrics)

    frames_written = metric_int(service._latest_metrics.get("production_output_frames_written"))
    runtime_mode = str(service._latest_metrics.get("production_output_runtime_mode") or "").strip() or "unknown"
    failure_reason = metrics_output_failure_reason(service._latest_metrics)
    metrics_status = str(service._latest_metrics.get("status") or "").strip() or "unknown"
    geometry_mode = str(service._latest_metrics.get("geometry_mode") or "").strip()
    geometry_artifact_path = str(service._latest_metrics.get("geometry_artifact_path") or "").strip()
    left_last_error = str(service._latest_metrics.get("left_last_error") or "").strip()
    right_last_error = str(service._latest_metrics.get("right_last_error") or "").strip()
    raise RuntimeError(
        f"runtime did not confirm live output within {timeout_sec:.1f}s "
        f"(frames_written={frames_written}, output_path={runtime_mode}"
        f", status={metrics_status}"
        f"{', geometry=' + geometry_mode if geometry_mode else ''}"
        f"{', artifact=' + geometry_artifact_path if geometry_artifact_path else ''}"
        f"{', left_error=' + left_last_error if left_last_error else ''}"
        f"{', right_error=' + right_last_error if right_last_error else ''}"
        f"{', reason=' + failure_reason if failure_reason else ''})"
    )


def pump_runtime_events(service: Any) -> None:
    while not service._event_pump_stop.is_set():
        supervisor = service._supervisor
        if supervisor is None:
            break
        event = supervisor.read_event(timeout_sec=0.25)
        if event is None:
            if supervisor.process.poll() is not None:
                break
            continue
        with service._lock:
            service._ingest_runtime_event_locked(event)
    with service._lock:
        service._event_pump_thread = None


def start_event_pump(
    service: Any,
    *,
    pump_runtime_events_func: Callable[[Any], None] = pump_runtime_events,
) -> None:
    if service._event_pump_thread is not None:
        return
    service._event_pump_stop.clear()

    def _run() -> None:
        pump_runtime_events_func(service)

    service._event_pump_thread = threading.Thread(target=_run, name="hogak-runtime-events", daemon=True)
    service._event_pump_thread.start()
