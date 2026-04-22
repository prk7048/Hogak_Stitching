from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any

from stitching.domain.runtime.service.launcher import RuntimeLaunchSpec
from stitching.domain.runtime.service.monitor import (
    pump_runtime_events as _pump_runtime_events_impl,
    start_event_pump as _start_event_pump_impl,
    wait_for_output_ready as _wait_for_output_ready_impl,
)
from stitching.domain.runtime.plan.builder import (
    build_runtime_plan as _build_runtime_plan_impl,
    ensure_default_geometry_artifact as _ensure_default_geometry_artifact_impl,
    gpu_only_blockers_for_plan as _gpu_only_blockers_for_plan_impl,
    resolve_requested_artifact_path as _resolve_requested_artifact_path_impl,
)
from stitching.domain.runtime.service.metrics import (
    _metric_int,
    _metrics_indicate_output_ready,
    metrics_output_failure_reason,
)
from stitching.domain.runtime.service.operations import (
    RuntimeEventMetricsOperations,
    RuntimeLifecycleOperations,
    RuntimePlanOperations,
)
from stitching.domain.runtime.service.state import (
    RuntimeEventIngestService,
    RuntimeStateProjector,
    RuntimeStateRepository,
)
from stitching.domain.runtime.service.supervisor import RuntimeSupervisor


@dataclass(slots=True)
class RuntimePlan:
    geometry_artifact_path: Path
    launch_spec: RuntimeLaunchSpec
    reload_payload: dict[str, Any]
    summary: dict[str, Any]


class RuntimeService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._supervisor: RuntimeSupervisor | None = None
        self._event_pump_thread: threading.Thread | None = None
        self._event_pump_stop = threading.Event()
        self._state_repo = RuntimeStateRepository()
        self._state_projector = RuntimeStateProjector()
        self._event_ingest = RuntimeEventIngestService(self._state_repo)
        self._plan_operations = RuntimePlanOperations(self)
        self._event_metrics = RuntimeEventMetricsOperations(self)
        self._lifecycle = RuntimeLifecycleOperations(self)

    def _record_event(self, event_type: str, payload: dict[str, Any] | None = None, *, seq: int = 0) -> dict[str, Any]:
        return self._state_repo.record_event(event_type, payload, seq=seq, timestamp_sec=time.time())

    @property
    def _prepared_plan(self) -> RuntimePlan | None:
        value = self._state_repo.state.prepared_plan
        return value if value is None or isinstance(value, RuntimePlan) else None

    @_prepared_plan.setter
    def _prepared_plan(self, value: RuntimePlan | None) -> None:
        self._state_repo.state.prepared_plan = value

    @property
    def _latest_metrics(self) -> dict[str, Any]:
        return self._state_repo.state.latest_metrics

    @_latest_metrics.setter
    def _latest_metrics(self, value: dict[str, Any]) -> None:
        self._state_repo.state.latest_metrics = dict(value or {})

    @property
    def _latest_hello(self) -> dict[str, Any]:
        return self._state_repo.state.latest_hello

    @_latest_hello.setter
    def _latest_hello(self, value: dict[str, Any]) -> None:
        self._state_repo.state.latest_hello = dict(value or {})

    @property
    def _last_error(self) -> str:
        return self._state_repo.state.last_error

    @_last_error.setter
    def _last_error(self, value: Any) -> None:
        self._state_repo.state.last_error = str(value or "")

    @property
    def _last_status(self) -> str:
        return self._state_repo.state.last_status

    @_last_status.setter
    def _last_status(self, value: Any) -> None:
        self._state_repo.state.last_status = str(value or "")

    @property
    def _gpu_only_blockers(self) -> list[str]:
        return self._state_repo.state.gpu_only_blockers

    @_gpu_only_blockers.setter
    def _gpu_only_blockers(self, value: list[str]) -> None:
        self._state_repo.state.gpu_only_blockers = [str(item).strip() for item in list(value or []) if str(item).strip()]

    @property
    def _project_start_phase(self) -> str:
        return self._state_repo.state.project_start_phase

    @_project_start_phase.setter
    def _project_start_phase(self, value: Any) -> None:
        normalized = str(value or "idle").strip()
        self._state_repo.state.project_start_phase = normalized or "idle"

    @property
    def _project_status_message(self) -> str:
        return self._state_repo.state.project_status_message

    @_project_status_message.setter
    def _project_status_message(self, value: Any) -> None:
        self._state_repo.state.project_status_message = str(value or "").strip()

    @staticmethod
    def _receive_uri_from_target(target: str) -> str:
        text = str(target or "").strip()
        if not text.startswith("udp://"):
            return text
        endpoint = text.split("?", 1)[0][len("udp://") :]
        host_port = endpoint[1:] if endpoint.startswith("@") else endpoint
        separator = host_port.rfind(":")
        if separator < 0:
            return text
        port = host_port[separator + 1 :].strip()
        if not port:
            return text
        return f"udp://@:{port}"

    def set_project_progress(self, phase: str, message: str) -> None:
        self._event_metrics.set_project_progress(phase, message)

    def project_progress(self) -> tuple[str, str]:
        return self._event_metrics.project_progress()

    def _ensure_default_geometry_artifact(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        return _ensure_default_geometry_artifact_impl(request)

    def _build_plan(self, request: dict[str, Any] | None = None) -> RuntimePlan:
        return _build_runtime_plan_impl(
            request,
            plan_factory=RuntimePlan,
        )

    @staticmethod
    def _resolve_requested_artifact_path(request: dict[str, Any] | None = None) -> Path | None:
        return _resolve_requested_artifact_path_impl(request)

    def _snapshot_locked(self) -> dict[str, Any]:
        return self._state_projector.snapshot(self._state_repo.state, supervisor=self._supervisor)

    @staticmethod
    def _gpu_only_blockers_for_plan(plan: RuntimePlan) -> list[str]:
        return _gpu_only_blockers_for_plan_impl(plan)

    def prepare(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._plan_operations.prepare(request)

    def _ingest_runtime_event_locked(self, event: Any) -> None:
        self._event_ingest.ingest(event, record_event=self._record_event)

    def _wait_for_output_ready_locked(self, *, timeout_sec: float = 10.0) -> dict[str, Any]:
        return _wait_for_output_ready_impl(
            self,
            timeout_sec=timeout_sec,
            metrics_output_failure_reason=metrics_output_failure_reason,
            metrics_indicate_output_ready=_metrics_indicate_output_ready,
            metric_int=_metric_int,
        )

    def _start_event_pump(self) -> None:
        _start_event_pump_impl(self, pump_runtime_events_func=_pump_runtime_events_impl)

    def _pump_runtime_events(self) -> None:
        _pump_runtime_events_impl(self)

    def start(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._lifecycle.start(request)

    def stop(self) -> dict[str, Any]:
        return self._lifecycle.stop()

    def state(self) -> dict[str, Any]:
        return self._event_metrics.state()

