from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import json
from typing import Any, Literal


InputRuntime = Literal["ffmpeg-cpu", "ffmpeg-cuda", "opencv"]
OutputRuntime = Literal["none", "ffmpeg", "gpu-direct"]
Transport = Literal["tcp", "udp"]
SyncPairMode = Literal["none", "latest", "oldest", "service"]
SyncTimeSource = Literal[
    "pts-offset-auto",
    "pts-offset-manual",
    "pts-offset-hybrid",
    "arrival",
    "wallclock",
]
DistortionMode = Literal["off", "runtime-lines"]
GpuMode = Literal["off", "auto", "on"]
CommandType = Literal[
    "start",
    "stop",
    "shutdown",
    "reload_config",
    "reload_homography",
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
]

SUPPORTED_RELOAD_CONFIG_FIELDS = (
    "left_rtsp",
    "right_rtsp",
    "input_runtime",
    "ffmpeg_bin",
    "homography_file",
    "probe_output_runtime",
    "probe_output_target",
    "probe_output_codec",
    "probe_output_bitrate",
    "probe_output_preset",
    "probe_output_muxer",
    "probe_output_width",
    "probe_output_height",
    "probe_output_fps",
    "transmit_output_runtime",
    "transmit_output_target",
    "transmit_output_codec",
    "transmit_output_bitrate",
    "transmit_output_preset",
    "transmit_output_muxer",
    "transmit_output_width",
    "transmit_output_height",
    "transmit_output_fps",
    "rtsp_transport",
    "input_buffer_frames",
    "rtsp_timeout_sec",
    "reconnect_cooldown_sec",
    "sync_pair_mode",
    "allow_frame_reuse",
    "pair_reuse_max_age_ms",
    "pair_reuse_max_consecutive",
    "sync_match_max_delta_ms",
    "sync_time_source",
    "sync_manual_offset_ms",
    "sync_auto_offset_window_sec",
    "sync_auto_offset_max_search_ms",
    "sync_recalibration_interval_sec",
    "sync_recalibration_trigger_skew_ms",
    "sync_recalibration_trigger_wait_ratio",
    "sync_auto_offset_confidence_min",
    "distortion_mode",
    "use_saved_distortion",
    "distortion_auto_save",
    "left_distortion_file",
    "right_distortion_file",
    "distortion_lens_model_hint",
    "distortion_horizontal_fov_deg",
    "distortion_vertical_fov_deg",
    "distortion_camera_model",
    "process_scale",
    "stitch_output_scale",
    "stitch_every_n",
    "gpu_mode",
    "gpu_device",
    "benchmark_log_interval_sec",
    "headless_benchmark",
)


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
    max_buffered_frames: int = 8
    timeout_sec: float = 10.0
    reconnect_cooldown_sec: float = 1.0


@dataclass(slots=True)
class OutputSpec:
    runtime: OutputRuntime = "none"
    profile: str = "inspection"
    target: str = ""
    codec: str = "h264_nvenc"
    bitrate: str = "12M"
    preset: str = "p4"
    muxer: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0


@dataclass(slots=True)
class EngineConfig:
    left: StreamSpec | None = None
    right: StreamSpec | None = None
    input_runtime: InputRuntime = "ffmpeg-cuda"
    output: OutputSpec = field(default_factory=OutputSpec)
    production_output: OutputSpec = field(default_factory=OutputSpec)
    sync_pair_mode: SyncPairMode = "none"
    allow_frame_reuse: bool = False
    pair_reuse_max_age_ms: float = 90.0
    pair_reuse_max_consecutive: int = 2
    sync_match_max_delta_ms: float = 35.0
    sync_time_source: SyncTimeSource = "pts-offset-auto"
    sync_manual_offset_ms: float = 0.0
    sync_auto_offset_window_sec: float = 4.0
    sync_auto_offset_max_search_ms: float = 500.0
    sync_recalibration_interval_sec: float = 60.0
    sync_recalibration_trigger_skew_ms: float = 45.0
    sync_recalibration_trigger_wait_ratio: float = 0.50
    sync_auto_offset_confidence_min: float = 0.85
    distortion_mode: DistortionMode = "runtime-lines"
    use_saved_distortion: bool = True
    distortion_auto_save: bool = True
    left_distortion_file: str = "data/runtime_distortion_left.json"
    right_distortion_file: str = "data/runtime_distortion_right.json"
    distortion_lens_model_hint: str = "auto"
    distortion_horizontal_fov_deg: float = 0.0
    distortion_vertical_fov_deg: float = 0.0
    distortion_camera_model: str = ""
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
    sync_pair_mode: SyncPairMode = "none"
    frame_index: int = 0
    left_fps: float = 0.0
    right_fps: float = 0.0
    left_avg_frame_interval_ms: float = 0.0
    right_avg_frame_interval_ms: float = 0.0
    left_last_frame_interval_ms: float = 0.0
    right_last_frame_interval_ms: float = 0.0
    left_max_frame_interval_ms: float = 0.0
    right_max_frame_interval_ms: float = 0.0
    left_late_frame_intervals: int = 0
    right_late_frame_intervals: int = 0
    left_buffer_span_ms: float = 0.0
    right_buffer_span_ms: float = 0.0
    left_avg_read_ms: float = 0.0
    right_avg_read_ms: float = 0.0
    left_max_read_ms: float = 0.0
    right_max_read_ms: float = 0.0
    left_buffer_seq_span: int = 0
    right_buffer_seq_span: int = 0
    left_age_ms: float = 0.0
    right_age_ms: float = 0.0
    left_source_age_ms: float = 0.0
    right_source_age_ms: float = 0.0
    selected_left_lag_ms: float = 0.0
    selected_right_lag_ms: float = 0.0
    selected_left_lag_frames: int = 0
    selected_right_lag_frames: int = 0
    stitch_fps: float = 0.0
    stitch_actual_fps: float = 0.0
    worker_fps: float = 0.0
    output_written_fps: float = 0.0
    production_output_written_fps: float = 0.0
    pair_skew_ms_mean: float = 0.0
    pair_source_skew_ms_mean: float = 0.0
    source_time_valid_left: bool = False
    source_time_valid_right: bool = False
    source_time_mode: str = "fallback-arrival"
    sync_effective_offset_ms: float = 0.0
    sync_offset_source: str = "arrival-fallback"
    sync_offset_confidence: float = 0.0
    sync_recalibration_count: int = 0
    sync_estimate_pairs: int = 0
    sync_estimate_avg_gap_ms: float = 0.0
    sync_estimate_score: float = 0.0
    distortion_enabled_left: bool = False
    distortion_enabled_right: bool = False
    distortion_source_left: str = "off"
    distortion_source_right: str = "off"
    distortion_confidence_left: float = 0.0
    distortion_confidence_right: float = 0.0
    distortion_model: str = "opencv_pinhole"
    distortion_fit_score_left: float = 0.0
    distortion_fit_score_right: float = 0.0
    distortion_line_count_left: int = 0
    distortion_line_count_right: int = 0
    distortion_frame_count_left: int = 0
    distortion_frame_count_right: int = 0
    distortion_lens_model_left: str = "opencv_pinhole"
    distortion_lens_model_right: str = "opencv_pinhole"
    matches: int = 0
    inliers: int = 0
    stitched_count: int = 0
    reused_count: int = 0
    wait_both_streams_count: int = 0
    wait_sync_pair_count: int = 0
    wait_next_frame_count: int = 0
    wait_paired_fresh_count: int = 0
    wait_paired_fresh_left_count: int = 0
    wait_paired_fresh_right_count: int = 0
    wait_paired_fresh_both_count: int = 0
    wait_paired_fresh_left_age_ms_avg: float = 0.0
    wait_paired_fresh_right_age_ms_avg: float = 0.0
    realtime_fallback_pair_count: int = 0
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
    left_buffered_frames: int = 0
    right_buffered_frames: int = 0
    left_stale_drops: int = 0
    right_stale_drops: int = 0
    left_launch_failures: int = 0
    right_launch_failures: int = 0
    left_read_failures: int = 0
    right_read_failures: int = 0
    left_reader_restarts: int = 0
    right_reader_restarts: int = 0
    left_motion_mean: float = 0.0
    right_motion_mean: float = 0.0
    left_content_frozen: bool = False
    right_content_frozen: bool = False
    left_frozen_duration_sec: float = 0.0
    right_frozen_duration_sec: float = 0.0
    left_freeze_restarts: int = 0
    right_freeze_restarts: int = 0
    output_active: bool = False
    output_frames_written: int = 0
    output_frames_dropped: int = 0
    output_target: str = ""
    output_command_line: str = ""
    output_effective_codec: str = ""
    output_last_error: str = ""
    production_output_active: bool = False
    production_output_frames_written: int = 0
    production_output_frames_dropped: int = 0
    production_output_target: str = ""
    production_output_command_line: str = ""
    production_output_effective_codec: str = ""
    production_output_last_error: str = ""
    calibrated: bool = False
    output_width: int = 0
    output_height: int = 0
    production_output_width: int = 0
    production_output_height: int = 0
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
