from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import json
from typing import Any, Literal


InputRuntime = Literal["ffmpeg-cpu", "ffmpeg-cuda", "opencv"]
OutputRuntime = Literal["none", "ffmpeg"]
Transport = Literal["tcp", "udp"]
SyncPairMode = Literal["none", "latest", "oldest"]
GpuMode = Literal["off", "auto", "on"]
CommandType = Literal[
    "start",
    "stop",
    "shutdown",
    "reload_config",
    "set_manual_mode",
    "add_manual_point",
    "reset_auto_calibration",
    "request_snapshot",
]
EventType = Literal[
    "hello",
    "started",
    "stopped",
    "status",
    "metrics",
    "warning",
    "error",
    "manual_state",
    "snapshot_ready",
]


def _to_primitive(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _to_primitive(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_primitive(v) for v in value]
    return value


@dataclass(slots=True)
class StreamSpec:
    name: str
    url: str
    transport: Transport = "tcp"
    timeout_sec: float = 10.0
    reconnect_cooldown_sec: float = 1.0


@dataclass(slots=True)
class PreviewSpec:
    enabled: bool = False
    max_fps: float = 2.0
    max_width: int = 1280
    jpeg_quality: int = 80


@dataclass(slots=True)
class OutputSpec:
    runtime: OutputRuntime = "none"
    target: str = ""
    codec: str = "h264_nvenc"
    bitrate: str = "12M"
    preset: str = "p4"
    muxer: str = ""


@dataclass(slots=True)
class EngineConfig:
    left: StreamSpec | None = None
    right: StreamSpec | None = None
    input_runtime: InputRuntime = "ffmpeg-cuda"
    output: OutputSpec = field(default_factory=OutputSpec)
    preview: PreviewSpec = field(default_factory=PreviewSpec)
    sync_pair_mode: SyncPairMode = "none"
    sync_match_max_delta_ms: float = 35.0
    sync_manual_offset_ms: float = 0.0
    process_scale: float = 1.0
    stitch_output_scale: float = 1.0
    stitch_every_n: int = 1
    min_matches: int = 20
    min_inliers: int = 8
    ratio_test: float = 0.82
    ransac_thresh: float = 6.0
    max_features: int = 2800
    manual_points: int = 4
    gpu_mode: GpuMode = "on"
    gpu_device: int = 0
    cpu_threads: int = 0
    headless_benchmark: bool = False
    benchmark_log_interval_sec: float = 1.0

    def to_message(self) -> dict[str, Any]:
        return _to_primitive(self)


@dataclass(slots=True)
class ManualPoint:
    side: Literal["left", "right"]
    x: float
    y: float
    width: int
    height: int


@dataclass(slots=True)
class SnapshotRequest:
    kind: Literal["metrics", "preview"] = "metrics"


@dataclass(slots=True)
class EngineCommand:
    seq: int
    type: CommandType
    payload: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> dict[str, Any]:
        return {
            "seq": int(self.seq),
            "type": str(self.type),
            "payload": _to_primitive(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_message(), ensure_ascii=True)


@dataclass(slots=True)
class EngineMetrics:
    status: str = "idle"
    frame_index: int = 0
    left_fps: float = 0.0
    right_fps: float = 0.0
    stitch_fps: float = 0.0
    worker_fps: float = 0.0
    pair_skew_ms_mean: float = 0.0
    matches: int = 0
    inliers: int = 0
    stitched_count: int = 0
    reused_count: int = 0
    gpu_enabled: bool = False
    gpu_reason: str = "-"
    gpu_feature_enabled: bool = False
    gpu_feature_reason: str = "-"
    gpu_warp_count: int = 0
    cpu_warp_count: int = 0
    gpu_match_count: int = 0
    cpu_match_count: int = 0
    gpu_blend_count: int = 0
    cpu_blend_count: int = 0
    gpu_errors: int = 0
    gpu_feature_errors: int = 0
    blend_mode: str = "-"
    overlap_diff_mean: float = 0.0
    manual_mode: bool = False
    manual_left: int = 0
    manual_right: int = 0
    manual_target: int = 0
    left_frames_total: int = 0
    right_frames_total: int = 0
    left_stale_drops: int = 0
    right_stale_drops: int = 0
    left_last_error: str = ""
    right_last_error: str = ""

    def to_message(self) -> dict[str, Any]:
        return _to_primitive(self)


@dataclass(slots=True)
class ManualState:
    enabled: bool = False
    target_points: int = 4
    left_points: list[dict[str, float | int]] = field(default_factory=list)
    right_points: list[dict[str, float | int]] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        return _to_primitive(self)


@dataclass(slots=True)
class RuntimeEvent:
    seq: int
    type: EventType
    timestamp_sec: float
    payload: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> dict[str, Any]:
        return {
            "seq": int(self.seq),
            "type": str(self.type),
            "timestamp_sec": float(self.timestamp_sec),
            "payload": _to_primitive(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_message(), ensure_ascii=True)

