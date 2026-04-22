from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from stitching.domain.geometry.artifact import load_runtime_geometry_artifact
from stitching.domain.geometry.policy import geometry_rollout_metadata


DEFAULT_PROJECT_STATUS_MESSAGE = (
    "Start Project reuses the active rigid geometry and regenerates it only when needed."
)


class RuntimeEventRecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = 0
    seq: int = 0
    type: str = ""
    timestamp_sec: float = 0.0
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeServiceStateModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    events: list[RuntimeEventRecordModel] = Field(default_factory=list)
    next_event_id: int = 1
    prepared_plan: Any | None = None
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    latest_hello: dict[str, Any] = Field(default_factory=dict)
    last_error: str = ""
    last_status: str = "idle"
    gpu_only_blockers: list[str] = Field(default_factory=list)
    project_start_phase: str = "idle"
    project_status_message: str = DEFAULT_PROJECT_STATUS_MESSAGE


class RuntimeStateRepository:
    def __init__(self) -> None:
        self._state = RuntimeServiceStateModel()

    @property
    def state(self) -> RuntimeServiceStateModel:
        return self._state

    def record_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        seq: int = 0,
        timestamp_sec: float | None = None,
    ) -> dict[str, Any]:
        record = RuntimeEventRecordModel(
            id=self._state.next_event_id,
            seq=int(seq),
            type=str(event_type),
            timestamp_sec=float(time.time() if timestamp_sec is None else timestamp_sec),
            payload=dict(payload or {}),
        )
        self._state.next_event_id += 1
        self._state.events.append(record)
        if len(self._state.events) > 200:
            self._state.events = self._state.events[-200:]
        return record.model_dump(mode="json")

    def recent_events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        return [record.model_dump(mode="json") for record in self._state.events[-max(1, int(limit)) :]]


class RuntimeEventIngestService:
    def __init__(self, repository: RuntimeStateRepository) -> None:
        self._repository = repository

    def ingest(self, event: Any, *, record_event: Callable[..., dict[str, Any]]) -> None:
        if event is None:
            return
        if event.type == "metrics":
            self._repository.state.latest_metrics = dict(event.payload or {})
        elif event.type == "hello":
            self._repository.state.latest_hello = dict(event.payload or {})
        record_event(event.type, event.payload, seq=event.seq)


class RuntimeStateProjector:
    def __init__(
        self,
        *,
        artifact_loader: Callable[[Path], dict[str, Any]] = load_runtime_geometry_artifact,
        rollout_metadata: Callable[[dict[str, Any]], dict[str, Any]] = geometry_rollout_metadata,
    ) -> None:
        self._artifact_loader = artifact_loader
        self._rollout_metadata = rollout_metadata

    def snapshot(self, state: RuntimeServiceStateModel, *, supervisor: Any) -> dict[str, Any]:
        process = supervisor.process if supervisor is not None else None
        running = process is not None and process.poll() is None
        snapshot = {
            "running": running,
            "prepared": state.prepared_plan is not None,
            "runtime_pid": None if process is None else process.pid,
            "runtime_returncode": None if process is None else process.returncode,
            "status": state.last_status,
            "last_error": state.last_error,
            "prepared_plan": None if state.prepared_plan is None else state.prepared_plan.summary,
            "latest_hello": dict(state.latest_hello),
            "latest_metrics": dict(state.latest_metrics),
            "event_count": len(state.events),
            "recent_events": self._repository_recent_events(state),
            "gpu_only_mode": True,
            "gpu_only_ready": len(state.gpu_only_blockers) == 0,
            "gpu_only_blockers": list(state.gpu_only_blockers),
            "project_start_phase": state.project_start_phase,
            "project_status_message": state.project_status_message,
        }
        snapshot.update(self.flatten_truth_metrics(state.latest_metrics))
        if state.prepared_plan is not None:
            summary = state.prepared_plan.summary
            for key in (
                "geometry_artifact_path",
                "geometry_artifact_model",
                "geometry_residual_model",
                "geometry_rollout_status",
                "geometry_operator_visible",
                "geometry_fallback_only",
                "output_runtime_mode",
                "production_output_runtime_mode",
                "launch_ready",
                "launch_ready_reason",
            ):
                if not snapshot.get(key):
                    snapshot[key] = summary.get(key, "")
            snapshot["gpu_only_mode"] = bool(summary.get("gpu_only_mode", True))
            snapshot["gpu_only_ready"] = len(state.gpu_only_blockers) == 0
            snapshot["gpu_only_blockers"] = list(state.gpu_only_blockers)
            snapshot.setdefault("validation_mode", "read-only")
            snapshot.setdefault("strict_fresh", summary.get("sync_pair_mode", "") == "service")
            try:
                geometry_artifact_path = getattr(state.prepared_plan, "geometry_artifact_path", None)
                artifact = (
                    self._artifact_loader(Path(str(geometry_artifact_path)))
                    if geometry_artifact_path is not None
                    else None
                )
            except Exception:
                artifact = None
            if isinstance(artifact, dict) and not running:
                for key, value in self.flatten_artifact_truth(artifact).items():
                    current = snapshot.get(key)
                    if current in (None, "", "-", 0, 0.0, False):
                        snapshot[key] = value
        return snapshot

    @staticmethod
    def _repository_recent_events(state: RuntimeServiceStateModel) -> list[dict[str, Any]]:
        return [record.model_dump(mode="json") for record in state.events[-30:]]

    @staticmethod
    def flatten_truth_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metrics, dict):
            metrics = {}

        def _string(name: str, default: str = "") -> str:
            value = metrics.get(name, default)
            return "" if value is None else str(value)

        def _float(name: str, default: float = 0.0) -> float:
            value = metrics.get(name, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(default)

        def _int(name: str, default: int = 0) -> int:
            value = metrics.get(name, default)
            try:
                return int(value)
            except (TypeError, ValueError):
                return int(default)

        def _bool(name: str, default: bool = False) -> bool:
            value = metrics.get(name, default)
            if isinstance(value, bool):
                return value
            return bool(value)

        return {
            "geometry_mode": _string("geometry_mode", "-"),
            "alignment_mode": _string("alignment_mode", "-"),
            "seam_mode": _string("seam_mode", "-"),
            "exposure_mode": _string("exposure_mode", "-"),
            "blend_mode": _string("blend_mode", "-"),
            "geometry_artifact_path": _string("geometry_artifact_path"),
            "geometry_artifact_model": _string("geometry_artifact_model", "-"),
            "geometry_rollout_status": _string("geometry_rollout_status"),
            "geometry_operator_visible": _bool("geometry_operator_visible"),
            "geometry_fallback_only": _bool("geometry_fallback_only"),
            "residual_alignment_error_px": _float("residual_alignment_error_px"),
            "seam_path_jitter_px": _float("seam_path_jitter_px"),
            "exposure_gain": _float("exposure_gain", 1.0),
            "exposure_bias": _float("exposure_bias"),
            "overlap_diff_mean": _float("overlap_diff_mean"),
            "stitched_mean_luma": _float("stitched_mean_luma"),
            "left_mean_luma": _float("left_mean_luma"),
            "right_mean_luma": _float("right_mean_luma"),
            "warped_mean_luma": _float("warped_mean_luma"),
            "only_left_pixels": _int("only_left_pixels"),
            "only_right_pixels": _int("only_right_pixels"),
            "overlap_pixels": _int("overlap_pixels"),
            "output_runtime_mode": _string("output_runtime_mode"),
            "production_output_runtime_mode": _string("production_output_runtime_mode"),
            "output_target": _string("output_target"),
            "production_output_target": _string("production_output_target"),
            "output_command_line": _string("output_command_line"),
            "production_output_command_line": _string("production_output_command_line"),
            "output_last_error": _string("output_last_error"),
            "production_output_last_error": _string("production_output_last_error"),
            "output_effective_codec": _string("output_effective_codec"),
            "production_output_effective_codec": _string("production_output_effective_codec"),
            "output_frames_written": _int("output_frames_written"),
            "output_frames_dropped": _int("output_frames_dropped"),
            "output_pending_frames": _int("output_pending_frames"),
            "output_queue_capacity": _int("output_queue_capacity"),
            "output_drop_policy": _string("output_drop_policy"),
            "production_output_frames_written": _int("production_output_frames_written"),
            "production_output_frames_dropped": _int("production_output_frames_dropped"),
            "production_output_pending_frames": _int("production_output_pending_frames"),
            "production_output_queue_capacity": _int("production_output_queue_capacity"),
            "production_output_drop_policy": _string("production_output_drop_policy"),
            "output_written_fps": _float("output_written_fps"),
            "production_output_written_fps": _float("production_output_written_fps"),
            "reused_count": _int("reused_count"),
            "wait_sync_pair_count": _int("wait_sync_pair_count"),
            "wait_paired_fresh_count": _int("wait_paired_fresh_count"),
            "pair_source_skew_ms_mean": _float("pair_source_skew_ms_mean"),
            "stitch_actual_fps": _float("stitch_actual_fps"),
            "stitch_fps": _float("stitch_fps"),
            "worker_fps": _float("worker_fps"),
            "gpu_enabled": _bool("gpu_enabled"),
            "gpu_reason": _string("gpu_reason"),
            "gpu_feature_enabled": _bool("gpu_feature_enabled"),
            "gpu_feature_reason": _string("gpu_feature_reason"),
            "gpu_warp_count": _int("gpu_warp_count"),
            "cpu_warp_count": _int("cpu_warp_count"),
            "gpu_blend_count": _int("gpu_blend_count"),
            "cpu_blend_count": _int("cpu_blend_count"),
            "launch_ready": _bool("launch_ready"),
            "launch_ready_reason": _string("launch_ready_reason"),
        }

    def flatten_artifact_truth(self, artifact: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(artifact, dict):
            return {}

        geometry = artifact.get("geometry", {}) if isinstance(artifact.get("geometry"), dict) else {}
        alignment = artifact.get("alignment", {}) if isinstance(artifact.get("alignment"), dict) else {}
        seam = artifact.get("seam", {}) if isinstance(artifact.get("seam"), dict) else {}
        exposure = artifact.get("exposure", {}) if isinstance(artifact.get("exposure"), dict) else {}

        def _string(section: dict[str, Any], name: str, default: str = "") -> str:
            value = section.get(name, default)
            return "" if value is None else str(value)

        geometry_mode = _string(geometry, "model", "-")
        seam_mode = _string(seam, "mode", "-")
        exposure_enabled = exposure.get("enabled")
        exposure_mode = "gain-bias" if bool(exposure_enabled) else "off"
        if not geometry_mode:
            geometry_mode = "-"
        rollout = self._rollout_metadata(artifact)

        return {
            "geometry_mode": str(rollout["geometry_model"] or geometry_mode),
            "geometry_requested_residual_model": str(rollout.get("geometry_requested_residual_model") or "-"),
            "geometry_residual_model": rollout["geometry_residual_model"],
            "alignment_mode": _string(alignment, "model", "-"),
            "seam_mode": seam_mode,
            "exposure_mode": exposure_mode,
            "blend_mode": seam_mode or _string(geometry, "warp_model", "-"),
            "geometry_artifact_model": rollout["geometry_model"],
            "geometry_rollout_status": rollout["geometry_rollout_status"],
            "geometry_operator_visible": bool(rollout["geometry_operator_visible"]),
            "geometry_fallback_only": bool(rollout["geometry_fallback_only"]),
            "fallback_used": bool(rollout["geometry_fallback_only"]),
            "launch_ready": bool(rollout["launch_ready"]),
            "launch_ready_reason": str(rollout["launch_ready_reason"]),
        }
