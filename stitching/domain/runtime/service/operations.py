from __future__ import annotations

from typing import Any

from stitching.domain.runtime.backend.status import _is_recoverable_missing_geometry_reason
from stitching.domain.runtime.errors import ProjectBlockedError, ProjectRequestError
from stitching.domain.runtime.site_config import RuntimeSiteConfigError
from stitching.domain.runtime.service.metrics import confirm_output_timeout_sec
from stitching.domain.runtime.service.supervisor import RuntimeSupervisor


class RuntimePlanOperations:
    def __init__(self, service: Any) -> None:
        self._service = service

    def prepare(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._service._lock:
            try:
                explicit_artifact_path = self._service._resolve_requested_artifact_path(request)
                if explicit_artifact_path is not None:
                    try:
                        auto_geometry = self._service._ensure_default_geometry_artifact(request)
                    except Exception as exc:
                        raise ProjectRequestError(str(exc)) from exc
                else:
                    try:
                        auto_geometry = self._service._ensure_default_geometry_artifact(request)
                    except Exception as exc:
                        if not _is_recoverable_missing_geometry_reason(exc):
                            raise
                        auto_geometry = {
                            "calibrated": False,
                            "artifact_path": "",
                            "geometry_model": "",
                            "launch_ready": False,
                            "message": str(exc).strip()
                            or "Start Project will regenerate stitch geometry before launch.",
                        }
                    else:
                        auto_geometry = {
                            "calibrated": False,
                            "artifact_path": str(auto_geometry.get("artifact_path") or ""),
                            "geometry_model": str(auto_geometry.get("geometry_model") or ""),
                            "launch_ready": bool(auto_geometry.get("launch_ready")),
                            "message": "existing launch-ready rigid geometry artifact reused",
                        }
                try:
                    plan = self._service._build_plan(request)
                except RuntimeSiteConfigError as exc:
                    raise ProjectRequestError(str(exc)) from exc
                except ValueError as exc:
                    message = str(exc).strip()
                    if "must be configured" in message:
                        raise ProjectRequestError(message) from exc
                    raise
                blockers = self._service._gpu_only_blockers_for_plan(plan)
                self._service._gpu_only_blockers = blockers
                if blockers:
                    raise ProjectBlockedError(" / ".join(blockers))
            except Exception as exc:
                self._service._last_error = f"prepare failed: {exc}"
                if isinstance(exc, (ProjectRequestError, ProjectBlockedError)):
                    self._service._last_status = "blocked"
                    self._service._project_start_phase = "blocked"
                    self._service._project_status_message = str(exc)
                else:
                    self._service._last_status = "error"
                    self._service._project_start_phase = "error"
                    self._service._project_status_message = str(exc).strip()
                self._service._record_event("error", {"code": "prepare_failed", "message": str(exc)})
                raise
            self._service._prepared_plan = plan
            self._service._last_status = "prepared"
            self._service._last_error = ""
            self._service._project_start_phase = "idle"
            self._service._project_status_message = "Project is ready to start."
            self._service._record_event(
                "status",
                {"status": "prepared", "geometry_artifact_path": str(plan.geometry_artifact_path)},
            )
            return {
                "ok": True,
                "prepared": plan.summary,
                "auto_calibrated": bool(auto_geometry.get("calibrated")),
                "message": str(auto_geometry.get("message") or "runtime prepared"),
                "state": self._service._snapshot_locked(),
            }


class RuntimeEventMetricsOperations:
    def __init__(self, service: Any) -> None:
        self._service = service

    def set_project_progress(self, phase: str, message: str) -> None:
        with self._service._lock:
            normalized_phase = str(phase or "idle").strip() or "idle"
            normalized_message = str(message or "").strip()
            changed = (
                normalized_phase != self._service._project_start_phase
                or normalized_message != self._service._project_status_message
            )
            self._service._project_start_phase = normalized_phase
            self._service._project_status_message = normalized_message
            if changed:
                self._service._record_event(
                    "status",
                    {
                        "status": normalized_phase,
                        "phase": normalized_phase,
                        "message": normalized_message,
                    },
                )

    def project_progress(self) -> tuple[str, str]:
        with self._service._lock:
            return self._service._project_start_phase, self._service._project_status_message

    def state(self) -> dict[str, Any]:
        with self._service._lock:
            return self._service._snapshot_locked()


class RuntimeLifecycleOperations:
    def __init__(self, service: Any) -> None:
        self._service = service

    def start(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._service._lock:
            if self._service._supervisor is not None and self._service._supervisor.process.poll() is None:
                self._service._last_status = "already_running"
                self._service._project_start_phase = "running"
                self._service._project_status_message = "Project is already running."
                return {"ok": True, "state": self._service._snapshot_locked()}
            auto_prepare_result: dict[str, Any] | None = None
            if self._service._prepared_plan is None:
                try:
                    auto_prepare_result = self._service.prepare(request)
                except ProjectBlockedError as exc:
                    detail = str(exc).strip() or "runtime prepare is blocked"
                    self._service._last_status = "blocked"
                    self._service._project_start_phase = "blocked"
                    self._service._project_status_message = detail
                    raise ProjectBlockedError(detail) from exc
            plan = self._service._prepared_plan
            if plan is None:
                raise ValueError("runtime plan is unavailable after automatic prepare")
            blockers = self._service._gpu_only_blockers_for_plan(plan)
            self._service._gpu_only_blockers = blockers
            if blockers:
                detail = " / ".join(blockers)
                self._service._last_error = f"start failed: {detail}"
                self._service._last_status = "blocked"
                self._service._project_start_phase = "blocked"
                self._service._project_status_message = detail
                self._service._record_event("error", {"code": "gpu_only_blocked", "message": self._service._last_error})
                raise ProjectBlockedError(detail)
            self._service._last_status = "starting"
            self._service._last_error = ""
            self._service._supervisor = RuntimeSupervisor.launch(plan.launch_spec)

            def _close_failed_supervisor() -> None:
                self._service._event_pump_stop.set()
                supervisor = self._service._supervisor
                self._service._supervisor = None
                try:
                    if supervisor is not None:
                        supervisor.close()
                except Exception:
                    pass

            try:
                hello = self._service._supervisor.wait_for_hello(timeout_sec=5.0)
                self._service._latest_hello = hello.payload
                self._service._record_event("hello", hello.payload, seq=hello.seq)
                self._service._supervisor.client.reload_config(plan.reload_payload)
                self._service._supervisor.request_metrics()
                self._service._record_event(
                    "status",
                    {"status": "reload_sent", "geometry_artifact_path": str(plan.geometry_artifact_path)},
                )
            except Exception as exc:
                detail = str(exc).strip() or "runtime launch handshake failed"
                _close_failed_supervisor()
                self._service._last_status = "error"
                self._service._last_error = detail
                self._service._project_start_phase = "error"
                self._service._project_status_message = detail
                self._service._record_event("error", {"code": "start_failed", "message": detail})
                raise

            self._service.set_project_progress("confirm_output", "Waiting for the first live output frame.")
            try:
                self._service._wait_for_output_ready_locked(timeout_sec=confirm_output_timeout_sec(plan))
            except Exception as exc:
                detail = str(exc).strip() or "runtime did not confirm live output"
                _close_failed_supervisor()
                self._service._last_status = "blocked"
                self._service._last_error = detail
                self._service._project_start_phase = "blocked"
                self._service._project_status_message = detail
                self._service._record_event("error", {"code": "start_blocked", "message": detail})
                raise ProjectBlockedError(detail) from exc
            self._service._start_event_pump()
            self._service._last_status = "running"
            self._service._last_error = ""
            self._service._project_start_phase = "running"
            transmit_receive_uri = self._service._receive_uri_from_target(plan.summary.get("transmit_target", ""))
            receive_uri = transmit_receive_uri or str(plan.summary.get("transmit_target", "")).strip()
            start_message = f"런타임을 시작했습니다. 외부 플레이어에서 {receive_uri} 를 여세요."
            self._service._project_status_message = start_message
            self._service._record_event(
                "status",
                {"status": "running", "runtime_pid": self._service._supervisor.process.pid, "message": start_message},
            )
            if auto_prepare_result is not None:
                prepare_message = str(auto_prepare_result.get("message") or "").strip()
                if prepare_message:
                    start_message = f"{prepare_message} / {start_message}"
            return {
                "ok": True,
                "auto_prepared": auto_prepare_result is not None,
                "auto_calibrated": bool(auto_prepare_result and auto_prepare_result.get("auto_calibrated")),
                "message": start_message,
                "state": self._service._snapshot_locked(),
            }

    def stop(self) -> dict[str, Any]:
        with self._service._lock:
            self._service._event_pump_stop.set()
            if self._service._supervisor is not None:
                try:
                    self._service._supervisor.shutdown()
                except Exception:
                    pass
                try:
                    self._service._supervisor.close()
                except Exception:
                    pass
                self._service._supervisor = None
            if self._service._event_pump_thread is not None:
                self._service._event_pump_thread.join(timeout=2.5)
                self._service._event_pump_thread = None
            self._service._last_status = "stopped"
            self._service._last_error = ""
            self._service._project_start_phase = "idle"
            self._service._project_status_message = "Project is stopped."
            self._service._record_event("stopped", {"status": "stopped"})
            return {"ok": True, "message": "Runtime stopped.", "state": self._service._snapshot_locked()}
