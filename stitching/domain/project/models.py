from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stitching.domain.geometry.models import GeometryTruthModel
from stitching.domain.runtime.models import OutputPathTruthModel, RuntimeTruthModel, ZeroCopyTruthModel


class ProjectLogEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = 0
    timestamp_sec: float = 0.0
    phase: str = ""
    level: str = "info"
    message: str = ""


class ProjectDebugStepModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    label: str = ""
    state: str = "pending"
    message: str = ""
    timestamp_sec: float = 0.0


class ProjectDebugModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    current_stage: str = ""
    steps: list[ProjectDebugStepModel] = Field(default_factory=list)


class ProjectStartInputSideModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    transport: str
    timeout_sec: float
    reconnect_cooldown_sec: float
    buffer_frames: int


class ProjectStartInputsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left: ProjectStartInputSideModel
    right: ProjectStartInputSideModel


class ProjectStartGeometryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_path: str


class ProjectStartTimingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_mode: str
    allow_frame_reuse: bool
    reuse_max_age_ms: float
    reuse_max_consecutive: int
    match_max_delta_ms: float
    time_source: str
    manual_offset_ms: float
    auto_offset_window_sec: float
    auto_offset_max_search_ms: float
    recalibration_interval_sec: float
    recalibration_trigger_skew_ms: float
    recalibration_trigger_wait_ratio: float
    auto_offset_confidence_min: float


class ProjectStartOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: str
    target: str
    codec: str
    bitrate: str
    preset: str
    muxer: str
    width: int
    height: int
    fps: float
    debug_overlay: bool


class ProjectStartOutputsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probe: ProjectStartOutputModel
    transmit: ProjectStartOutputModel


class ProjectStartRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_runtime: str
    ffmpeg_bin: str
    gpu_mode: str
    gpu_device: int
    stitch_output_scale: float
    stitch_every_n: int
    benchmark_log_interval_sec: float
    headless_benchmark: bool


class ProjectStartRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_geometry: bool = False
    inputs: ProjectStartInputsModel | None = None
    geometry: ProjectStartGeometryModel | None = None
    timing: ProjectStartTimingModel | None = None
    outputs: ProjectStartOutputsModel | None = None
    runtime: ProjectStartRuntimeModel | None = None

    def to_request_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProjectStateModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lifecycle_state: str = "idle"
    phase: str = "idle"
    status_message: str = ""
    running: bool = False
    can_start: bool = False
    can_stop: bool = False
    blocker_reason: str = ""
    geometry: GeometryTruthModel
    runtime: RuntimeTruthModel
    output: OutputPathTruthModel
    zero_copy: ZeroCopyTruthModel
    recent_events: list[ProjectLogEntryModel] = Field(default_factory=list)
    debug: ProjectDebugModel = Field(default_factory=ProjectDebugModel)

    @classmethod
    def from_api_payload(cls, payload: dict[str, Any] | None) -> "ProjectStateModel":
        data = payload if isinstance(payload, dict) else {}
        geometry = data.get("geometry") if isinstance(data.get("geometry"), dict) else {}
        runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        zero_copy = data.get("zero_copy") if isinstance(data.get("zero_copy"), dict) else {}
        debug = data.get("debug") if isinstance(data.get("debug"), dict) else {}
        recent_events = data.get("recent_events") if isinstance(data.get("recent_events"), list) else (
            data.get("project_log") if isinstance(data.get("project_log"), list) else []
        )
        debug_steps = debug.get("steps") if isinstance(debug.get("steps"), list) else (
            data.get("debug_steps") if isinstance(data.get("debug_steps"), list) else []
        )

        return cls(
            lifecycle_state=str(data.get("lifecycle_state") or data.get("status") or "idle"),
            phase=str(data.get("phase") or data.get("start_phase") or data.get("status") or "idle"),
            status_message=str(data.get("status_message") or ""),
            running=bool(data.get("running")),
            can_start=bool(data.get("can_start")),
            can_stop=bool(data.get("can_stop")),
            blocker_reason=str(data.get("blocker_reason") or ""),
            geometry=GeometryTruthModel.model_validate(
                geometry
                or {
                    "model": data.get("runtime_active_model") or "",
                    "requested_residual_model": data.get("runtime_requested_residual_model") or "",
                    "residual_model": data.get("runtime_active_residual_model") or data.get("geometry_residual_model") or "",
                    "artifact_path": data.get("runtime_active_artifact_path") or "",
                    "artifact_checksum": data.get("runtime_artifact_checksum") or "",
                    "launch_ready": data.get("runtime_launch_ready") or False,
                    "launch_ready_reason": data.get("runtime_launch_ready_reason") or "",
                    "rollout_status": data.get("geometry_rollout_status") or "",
                    "fallback_used": data.get("fallback_used") or False,
                    "operator_visible": data.get("geometry_operator_visible") or False,
                }
            ),
            runtime=RuntimeTruthModel.model_validate(
                runtime
                or {
                    "status": data.get("status") or "idle",
                    "running": data.get("running") or False,
                    "pid": data.get("runtime_pid"),
                    "phase": data.get("start_phase") or data.get("phase") or "",
                    "active_model": data.get("runtime_active_model") or "",
                    "active_residual_model": data.get("runtime_active_residual_model") or "",
                    "gpu_path_mode": data.get("gpu_path_mode") or "unknown",
                    "gpu_path_ready": data.get("gpu_path_ready") or False,
                    "input_path_mode": data.get("input_path_mode") or "",
                    "output_path_mode": data.get("output_path_mode") or "",
                }
            ),
            output=OutputPathTruthModel.model_validate(
                output
                or {
                    "receive_uri": data.get("output_receive_uri") or "",
                    "target": data.get("production_output_target") or "",
                    "mode": data.get("output_path_mode") or "",
                    "direct": data.get("output_path_direct") or False,
                    "bridge": data.get("output_path_bridge") or False,
                    "bridge_reason": data.get("output_bridge_reason") or "",
                    "last_error": data.get("production_output_last_error") or "",
                }
            ),
            zero_copy=ZeroCopyTruthModel.model_validate(
                zero_copy
                or {
                    "ready": data.get("zero_copy_ready") or False,
                    "reason": data.get("zero_copy_reason") or "",
                    "blockers": data.get("zero_copy_blockers") or [],
                    "status": "ready"
                    if bool(data.get("zero_copy_ready"))
                    else "blocked"
                    if list(data.get("zero_copy_blockers") or [])
                    else "pending",
                }
            ),
            recent_events=[
                ProjectLogEntryModel.model_validate(item if isinstance(item, dict) else {})
                for item in recent_events
            ],
            debug=ProjectDebugModel(
                enabled=bool(debug.get("enabled")) if debug else bool(data.get("debug_mode")),
                current_stage=str(debug.get("current_stage") or data.get("debug_current_stage") or ""),
                steps=[
                    ProjectDebugStepModel.model_validate(item if isinstance(item, dict) else {})
                    for item in debug_steps
                ],
            ),
        )

    def to_api_dict(self, *, include_legacy: bool = False) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        if not include_legacy:
            return payload

        payload.update(
            {
                "status": self.lifecycle_state,
                "start_phase": self.phase,
                "output_receive_uri": self.output.receive_uri,
                "production_output_target": self.output.target,
                "production_output_last_error": self.output.last_error,
                "output_bridge_reason": self.output.bridge_reason,
                "runtime_active_model": self.runtime.active_model,
                "runtime_active_residual_model": self.runtime.active_residual_model,
                "runtime_active_artifact_path": self.geometry.artifact_path,
                "runtime_artifact_checksum": self.geometry.artifact_checksum,
                "runtime_launch_ready": self.geometry.launch_ready,
                "runtime_launch_ready_reason": self.geometry.launch_ready_reason,
                "fallback_used": self.geometry.fallback_used,
                "gpu_path_mode": self.runtime.gpu_path_mode,
                "gpu_path_ready": self.runtime.gpu_path_ready,
                "input_path_mode": self.runtime.input_path_mode,
                "output_path_mode": self.runtime.output_path_mode,
                "output_path_direct": self.output.direct,
                "output_path_bridge": self.output.bridge,
                "zero_copy_ready": self.zero_copy.ready,
                "zero_copy_reason": self.zero_copy.reason,
                "zero_copy_blockers": list(self.zero_copy.blockers),
                "project_log": [entry.model_dump(mode="json") for entry in self.recent_events],
                "debug_mode": self.debug.enabled,
                "debug_current_stage": self.debug.current_stage,
                "debug_steps": [step.model_dump(mode="json") for step in self.debug.steps],
            }
        )
        return payload


class ProjectActionResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    message: str = ""
    state: ProjectStateModel | None = None
    detail: str = ""

    def to_api_dict(self, *, include_legacy: bool = False) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        if include_legacy and self.state is not None:
            payload["state"] = self.state.to_api_dict(include_legacy=True)
        return payload
