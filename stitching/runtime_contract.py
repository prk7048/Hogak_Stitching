from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import json
from typing import Any, Literal

from stitching.runtime_geometry_artifact import (
    runtime_geometry_fixed_crop_ready,
    runtime_geometry_effective_residual_model,
    runtime_geometry_mesh_contract_ready,
    runtime_geometry_mesh_fallback_used,
    load_runtime_geometry_artifact,
    runtime_geometry_model,
    runtime_geometry_requested_residual_model,
)


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
GpuMode = Literal["off", "auto", "on", "only"]
CommandType = Literal[
    "start",
    "stop",
    "shutdown",
    "reload_config",
    "set_manual_mode",
    "add_manual_point",
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

RUNTIME_SCHEMA_VERSION = 2
SCHEMA_V2_TOP_LEVEL_FIELDS = {
    "schema_version",
    "inputs",
    "geometry",
    "timing",
    "outputs",
    "runtime",
}

SCHEMA_V2_INPUT_SIDE_KEYS = (
    "url",
    "transport",
    "timeout_sec",
    "reconnect_cooldown_sec",
    "buffer_frames",
)
SCHEMA_V2_GEOMETRY_KEYS = ("artifact_path",)
DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL = "virtual-center-rectilinear"
DEFAULT_OPERATOR_GEOMETRY_MODEL = "virtual-center-rectilinear-rigid"
INTERNAL_FALLBACK_GEOMETRY_MODEL = "virtual-center-rectilinear-rigid"
INTERNAL_NON_PRODUCT_GEOMETRY_MODELS = ("virtual-center-rectilinear-mesh",)
LEGACY_FALLBACK_GEOMETRY_MODELS = ("cylindrical-affine",)
LEGACY_COMPAT_GEOMETRY_MODELS = ("planar-homography",)
SUPPORTED_RUNTIME_GEOMETRY_MODELS = (
    DEFAULT_OPERATOR_GEOMETRY_MODEL,
    INTERNAL_FALLBACK_GEOMETRY_MODEL,
    *INTERNAL_NON_PRODUCT_GEOMETRY_MODELS,
    *LEGACY_FALLBACK_GEOMETRY_MODELS,
    *LEGACY_COMPAT_GEOMETRY_MODELS,
)
SCHEMA_V2_TIMING_KEYS = (
    "pair_mode",
    "allow_frame_reuse",
    "reuse_max_age_ms",
    "reuse_max_consecutive",
    "match_max_delta_ms",
    "time_source",
    "manual_offset_ms",
    "auto_offset_window_sec",
    "auto_offset_max_search_ms",
    "recalibration_interval_sec",
    "recalibration_trigger_skew_ms",
    "recalibration_trigger_wait_ratio",
    "auto_offset_confidence_min",
)
SCHEMA_V2_OUTPUT_KEYS = (
    "runtime",
    "target",
    "codec",
    "bitrate",
    "preset",
    "muxer",
    "width",
    "height",
    "fps",
    "debug_overlay",
)
SCHEMA_V2_RUNTIME_KEYS = (
    "input_runtime",
    "ffmpeg_bin",
    "gpu_mode",
    "gpu_device",
    "stitch_output_scale",
    "stitch_every_n",
    "benchmark_log_interval_sec",
    "headless_benchmark",
)

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
    "probe_output_debug_overlay",
    "transmit_output_runtime",
    "transmit_output_target",
    "transmit_output_codec",
    "transmit_output_bitrate",
    "transmit_output_preset",
    "transmit_output_muxer",
    "transmit_output_width",
    "transmit_output_height",
    "transmit_output_fps",
    "transmit_output_debug_overlay",
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

def _as_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _copy_if_present(target: dict[str, Any], source: dict[str, Any], source_key: str, target_key: str) -> None:
    if source_key in source:
        target[target_key] = source[source_key]


def _require_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


def _require_exact_keys(value: dict[str, Any], *, allowed_keys: tuple[str, ...], field_name: str) -> None:
    unknown = sorted(set(value) - set(allowed_keys))
    if unknown:
        raise ValueError(f"unsupported {field_name} fields: {', '.join(unknown)}")


def _require_string(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _require_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _require_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    return float(value)


def _require_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be an integer")
    if int(value) != value:
        raise ValueError(f"{field_name} must be an integer")
    return int(value)


def geometry_rollout_metadata(geometry_model: Any, residual_model: Any | None = None) -> dict[str, Any]:
    requested_residual = ""
    effective_residual = ""
    mesh_requested = False
    mesh_contract_ready = False
    mesh_fallback_used = False
    crop_ready = False
    quality_block_reason = ""
    if isinstance(geometry_model, dict):
        artifact = geometry_model
        model = runtime_geometry_model(artifact)
        requested_residual = runtime_geometry_requested_residual_model(artifact)
        effective_residual = runtime_geometry_effective_residual_model(artifact)
        mesh_requested = requested_residual == "mesh"
        mesh_contract_ready = runtime_geometry_mesh_contract_ready(artifact)
        mesh_fallback_used = runtime_geometry_mesh_fallback_used(artifact)
        crop_ready = runtime_geometry_fixed_crop_ready(artifact)
        calibration = artifact.get("calibration", {})
        metrics = calibration.get("metrics", {}) if isinstance(calibration, dict) else {}
        if isinstance(metrics, dict):
            try:
                crop_ratio = float(metrics.get("virtual_center_crop_ratio") or 0.0)
            except (TypeError, ValueError):
                crop_ratio = 0.0
            try:
                scale_drift = float(metrics.get("virtual_center_right_edge_scale_drift") or 0.0)
            except (TypeError, ValueError):
                scale_drift = 0.0
            try:
                tilt_deg = float(metrics.get("virtual_center_mask_tilt_deg") or 0.0)
            except (TypeError, ValueError):
                tilt_deg = 0.0
            if requested_residual == "rigid" and effective_residual == "rigid":
                if crop_ratio > 0.0 and crop_ratio < 0.50:
                    quality_block_reason = "rigid geometry crop ratio is too low; recompute geometry with a better-aligned scene"
                elif scale_drift > 0.0 and abs(scale_drift - 1.0) > 0.22:
                    quality_block_reason = "rigid geometry shows excessive right-edge scale drift; recompute geometry"
                elif tilt_deg > 6.0:
                    quality_block_reason = "rigid geometry is excessively tilted; recompute geometry"
    else:
        model = "" if geometry_model is None else str(geometry_model).strip()
        requested_residual = "" if residual_model is None else str(residual_model).strip().lower().replace("_", "-")
        effective_residual = requested_residual
        mesh_requested = requested_residual == "mesh"
        mesh_contract_ready = mesh_requested
    mesh_non_product = model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL and mesh_requested
    rigid_default = (
        model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL
        and requested_residual == "rigid"
        and effective_residual == "rigid"
        and crop_ready
        and not quality_block_reason
    )
    rigid_missing_crop = (
        model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL
        and requested_residual == "rigid"
        and effective_residual == "rigid"
        and not crop_ready
    )
    rigid_quality_blocked = (
        model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL
        and requested_residual == "rigid"
        and effective_residual == "rigid"
        and crop_ready
        and bool(quality_block_reason)
    )
    operator_visible = rigid_default
    fallback_only = rigid_missing_crop or rigid_quality_blocked or mesh_non_product or model in LEGACY_FALLBACK_GEOMETRY_MODELS
    compat_only = model in LEGACY_COMPAT_GEOMETRY_MODELS

    if rigid_default:
        public_model = DEFAULT_OPERATOR_GEOMETRY_MODEL
        rollout_status = "default"
        launch_ready = True
        launch_ready_reason = "default launch-ready rigid geometry artifact"
    elif rigid_missing_crop:
        public_model = DEFAULT_OPERATOR_GEOMETRY_MODEL
        rollout_status = "blocked"
        launch_ready = False
        launch_ready_reason = "rigid geometry artifact is missing a fixed runtime crop; regenerate a valid rigid artifact before launch"
    elif rigid_quality_blocked:
        public_model = DEFAULT_OPERATOR_GEOMETRY_MODEL
        rollout_status = "blocked"
        launch_ready = False
        launch_ready_reason = quality_block_reason
    elif mesh_non_product:
        public_model = "virtual-center-rectilinear-mesh"
        rollout_status = "internal-only"
        launch_ready = False
        if mesh_fallback_used:
            launch_ready_reason = "mesh artifact degraded during solve; regenerate the rigid runtime artifact before launch"
        elif not crop_ready:
            launch_ready_reason = "mesh artifact is missing a fixed runtime crop; regenerate the rigid runtime artifact before launch"
        else:
            launch_ready_reason = "mesh artifacts are no longer product launch targets; regenerate the rigid runtime artifact before launch"
    elif model in LEGACY_FALLBACK_GEOMETRY_MODELS:
        public_model = model or "-"
        rollout_status = "fallback"
        launch_ready = True
        launch_ready_reason = "legacy fallback geometry artifact; use explicit geometry.artifact_path for rollback only"
    elif model in LEGACY_COMPAT_GEOMETRY_MODELS:
        public_model = model or "-"
        rollout_status = "legacy"
        launch_ready = True
        launch_ready_reason = "legacy compatibility geometry artifact; keep only for compatibility or emergency rollback"
    elif model:
        public_model = model
        rollout_status = "unsupported"
        launch_ready = False
        launch_ready_reason = "unsupported runtime geometry model"
    else:
        public_model = "-"
        rollout_status = "unknown"
        launch_ready = False
        launch_ready_reason = "geometry artifact model is missing"

    return {
        "geometry_model": public_model,
        "geometry_requested_residual_model": requested_residual or "-",
        "geometry_residual_model": effective_residual or "-",
        "geometry_rollout_status": rollout_status,
        "geometry_operator_visible": operator_visible,
        "geometry_fallback_only": fallback_only,
        "geometry_compat_only": compat_only,
        "geometry_mesh_contract_ready": mesh_contract_ready,
        "geometry_mesh_fallback_used": mesh_fallback_used,
        "geometry_crop_ready": crop_ready,
        "launch_ready": launch_ready,
        "launch_ready_reason": launch_ready_reason,
    }


def normalize_schema_v2_reload_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("reload_config payload must be a JSON object")

    unknown_top_level = sorted(set(payload) - {"inputs", "geometry", "timing", "outputs", "runtime"})
    if unknown_top_level:
        raise ValueError(f"unsupported schema v2 top-level fields: {', '.join(unknown_top_level)}")

    inputs = _require_object(payload.get("inputs"), field_name="inputs")
    geometry = _require_object(payload.get("geometry"), field_name="geometry")
    timing = _require_object(payload.get("timing"), field_name="timing")
    outputs = _require_object(payload.get("outputs"), field_name="outputs")
    runtime = _require_object(payload.get("runtime"), field_name="runtime")

    _require_exact_keys(inputs, allowed_keys=("left", "right"), field_name="inputs")
    _require_exact_keys(geometry, allowed_keys=SCHEMA_V2_GEOMETRY_KEYS, field_name="geometry")
    _require_exact_keys(timing, allowed_keys=SCHEMA_V2_TIMING_KEYS, field_name="timing")
    _require_exact_keys(outputs, allowed_keys=("probe", "transmit"), field_name="outputs")
    _require_exact_keys(runtime, allowed_keys=SCHEMA_V2_RUNTIME_KEYS, field_name="runtime")

    left = _require_object(inputs.get("left"), field_name="inputs.left")
    right = _require_object(inputs.get("right"), field_name="inputs.right")
    _require_exact_keys(left, allowed_keys=SCHEMA_V2_INPUT_SIDE_KEYS, field_name="inputs.left")
    _require_exact_keys(right, allowed_keys=SCHEMA_V2_INPUT_SIDE_KEYS, field_name="inputs.right")

    probe = _require_object(outputs.get("probe"), field_name="outputs.probe")
    transmit = _require_object(outputs.get("transmit"), field_name="outputs.transmit")
    _require_exact_keys(probe, allowed_keys=SCHEMA_V2_OUTPUT_KEYS, field_name="outputs.probe")
    _require_exact_keys(transmit, allowed_keys=SCHEMA_V2_OUTPUT_KEYS, field_name="outputs.transmit")

    normalized_left = {
        "url": _require_string(left.get("url"), field_name="inputs.left.url"),
        "transport": _require_string(left.get("transport"), field_name="inputs.left.transport"),
        "timeout_sec": _require_number(left.get("timeout_sec"), field_name="inputs.left.timeout_sec"),
        "reconnect_cooldown_sec": _require_number(
            left.get("reconnect_cooldown_sec"), field_name="inputs.left.reconnect_cooldown_sec"
        ),
        "buffer_frames": _require_int(left.get("buffer_frames"), field_name="inputs.left.buffer_frames"),
    }
    normalized_right = {
        "url": _require_string(right.get("url"), field_name="inputs.right.url"),
        "transport": _require_string(right.get("transport"), field_name="inputs.right.transport"),
        "timeout_sec": _require_number(right.get("timeout_sec"), field_name="inputs.right.timeout_sec"),
        "reconnect_cooldown_sec": _require_number(
            right.get("reconnect_cooldown_sec"), field_name="inputs.right.reconnect_cooldown_sec"
        ),
        "buffer_frames": _require_int(right.get("buffer_frames"), field_name="inputs.right.buffer_frames"),
    }
    for key in ("transport", "timeout_sec", "reconnect_cooldown_sec", "buffer_frames"):
        if normalized_left[key] != normalized_right[key]:
            raise ValueError(f"inputs.left.{key} must match inputs.right.{key}")

    normalized_geometry = {
        "artifact_path": _require_string(geometry.get("artifact_path"), field_name="geometry.artifact_path"),
    }

    normalized_timing = {
        "pair_mode": _require_string(timing.get("pair_mode"), field_name="timing.pair_mode"),
        "allow_frame_reuse": _require_bool(timing.get("allow_frame_reuse"), field_name="timing.allow_frame_reuse"),
        "reuse_max_age_ms": _require_number(timing.get("reuse_max_age_ms"), field_name="timing.reuse_max_age_ms"),
        "reuse_max_consecutive": _require_int(
            timing.get("reuse_max_consecutive"), field_name="timing.reuse_max_consecutive"
        ),
        "match_max_delta_ms": _require_number(timing.get("match_max_delta_ms"), field_name="timing.match_max_delta_ms"),
        "time_source": _require_string(timing.get("time_source"), field_name="timing.time_source"),
        "manual_offset_ms": _require_number(timing.get("manual_offset_ms"), field_name="timing.manual_offset_ms"),
        "auto_offset_window_sec": _require_number(
            timing.get("auto_offset_window_sec"), field_name="timing.auto_offset_window_sec"
        ),
        "auto_offset_max_search_ms": _require_number(
            timing.get("auto_offset_max_search_ms"), field_name="timing.auto_offset_max_search_ms"
        ),
        "recalibration_interval_sec": _require_number(
            timing.get("recalibration_interval_sec"), field_name="timing.recalibration_interval_sec"
        ),
        "recalibration_trigger_skew_ms": _require_number(
            timing.get("recalibration_trigger_skew_ms"), field_name="timing.recalibration_trigger_skew_ms"
        ),
        "recalibration_trigger_wait_ratio": _require_number(
            timing.get("recalibration_trigger_wait_ratio"), field_name="timing.recalibration_trigger_wait_ratio"
        ),
        "auto_offset_confidence_min": _require_number(
            timing.get("auto_offset_confidence_min"), field_name="timing.auto_offset_confidence_min"
        ),
    }

    normalized_probe_runtime = _require_string(probe.get("runtime"), field_name="outputs.probe.runtime")
    normalized_probe = {
        "runtime": normalized_probe_runtime,
        "target": _require_string(
            probe.get("target"),
            field_name="outputs.probe.target",
            allow_empty=normalized_probe_runtime == "none",
        ),
        "codec": _require_string(probe.get("codec"), field_name="outputs.probe.codec"),
        "bitrate": _require_string(probe.get("bitrate"), field_name="outputs.probe.bitrate"),
        "preset": _require_string(probe.get("preset"), field_name="outputs.probe.preset"),
        "muxer": _require_string(probe.get("muxer"), field_name="outputs.probe.muxer", allow_empty=True),
        "width": _require_int(probe.get("width"), field_name="outputs.probe.width"),
        "height": _require_int(probe.get("height"), field_name="outputs.probe.height"),
        "fps": _require_number(probe.get("fps"), field_name="outputs.probe.fps"),
        "debug_overlay": _require_bool(probe.get("debug_overlay"), field_name="outputs.probe.debug_overlay"),
    }
    normalized_transmit_runtime = _require_string(transmit.get("runtime"), field_name="outputs.transmit.runtime")
    normalized_transmit = {
        "runtime": normalized_transmit_runtime,
        "target": _require_string(
            transmit.get("target"),
            field_name="outputs.transmit.target",
            allow_empty=normalized_transmit_runtime == "none",
        ),
        "codec": _require_string(transmit.get("codec"), field_name="outputs.transmit.codec"),
        "bitrate": _require_string(transmit.get("bitrate"), field_name="outputs.transmit.bitrate"),
        "preset": _require_string(transmit.get("preset"), field_name="outputs.transmit.preset"),
        "muxer": _require_string(transmit.get("muxer"), field_name="outputs.transmit.muxer", allow_empty=True),
        "width": _require_int(transmit.get("width"), field_name="outputs.transmit.width"),
        "height": _require_int(transmit.get("height"), field_name="outputs.transmit.height"),
        "fps": _require_number(transmit.get("fps"), field_name="outputs.transmit.fps"),
        "debug_overlay": _require_bool(
            transmit.get("debug_overlay"), field_name="outputs.transmit.debug_overlay"
        ),
    }

    normalized_runtime = {
        "input_runtime": _require_string(runtime.get("input_runtime"), field_name="runtime.input_runtime"),
        "ffmpeg_bin": _require_string(runtime.get("ffmpeg_bin"), field_name="runtime.ffmpeg_bin", allow_empty=True),
        "gpu_mode": _require_string(runtime.get("gpu_mode"), field_name="runtime.gpu_mode"),
        "gpu_device": _require_int(runtime.get("gpu_device"), field_name="runtime.gpu_device"),
        "stitch_output_scale": _require_number(
            runtime.get("stitch_output_scale"), field_name="runtime.stitch_output_scale"
        ),
        "stitch_every_n": _require_int(runtime.get("stitch_every_n"), field_name="runtime.stitch_every_n"),
        "benchmark_log_interval_sec": _require_number(
            runtime.get("benchmark_log_interval_sec"), field_name="runtime.benchmark_log_interval_sec"
        ),
        "headless_benchmark": _require_bool(
            runtime.get("headless_benchmark"), field_name="runtime.headless_benchmark"
        ),
    }

    return {
        "inputs": {
            "left": normalized_left,
            "right": normalized_right,
        },
        "geometry": normalized_geometry,
        "timing": normalized_timing,
        "outputs": {
            "probe": normalized_probe,
            "transmit": normalized_transmit,
        },
        "runtime": normalized_runtime,
    }


def _resolve_legacy_homography_file_from_geometry(geometry: dict[str, Any]) -> str:
    explicit_homography = str(geometry.get("homography_file") or "").strip()
    if explicit_homography:
        return explicit_homography

    artifact_path = str(geometry.get("artifact_path") or "").strip()
    if not artifact_path:
        return ""

    artifact = load_runtime_geometry_artifact(artifact_path)
    source = artifact.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("runtime geometry artifact is missing a source object")
    homography_file = str(source.get("homography_file") or "").strip()
    if not homography_file:
        raise ValueError(
            "runtime geometry artifact does not expose source.homography_file for legacy runtime compatibility"
        )
    return homography_file


def flatten_schema_v2_reload_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("reload_config payload must be a JSON object")

    schema_version = payload.get("schema_version", RUNTIME_SCHEMA_VERSION)
    try:
        normalized_schema_version = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("schema_version must be an integer") from exc
    if normalized_schema_version != RUNTIME_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version={normalized_schema_version}; expected {RUNTIME_SCHEMA_VERSION}"
        )

    unknown_top_level = sorted(set(payload) - SCHEMA_V2_TOP_LEVEL_FIELDS)
    if unknown_top_level:
        raise ValueError(
            f"unsupported schema v2 top-level fields: {', '.join(unknown_top_level)}"
        )

    flat: dict[str, Any] = {}

    inputs = _as_dict(payload.get("inputs"), field_name="inputs")
    left = _as_dict(inputs.get("left"), field_name="inputs.left")
    right = _as_dict(inputs.get("right"), field_name="inputs.right")

    _copy_if_present(flat, left, "url", "left_rtsp")
    _copy_if_present(flat, right, "url", "right_rtsp")

    for shared_key, target_key in (
        ("transport", "rtsp_transport"),
        ("timeout_sec", "rtsp_timeout_sec"),
        ("reconnect_cooldown_sec", "reconnect_cooldown_sec"),
        ("buffer_frames", "input_buffer_frames"),
    ):
        left_value = left.get(shared_key)
        right_value = right.get(shared_key)
        if left_value is not None and right_value is not None and left_value != right_value:
            raise ValueError(f"schema v2 field inputs.left.{shared_key} must match inputs.right.{shared_key}")
        shared_value = left_value if left_value is not None else right_value
        if shared_value is not None:
            flat[target_key] = shared_value

    geometry = _as_dict(payload.get("geometry"), field_name="geometry")
    legacy_homography_file = _resolve_legacy_homography_file_from_geometry(geometry)
    if legacy_homography_file:
        flat["homography_file"] = legacy_homography_file
    lens_correction = _as_dict(geometry.get("lens_correction"), field_name="geometry.lens_correction")
    for source_key, target_key in (
        ("distortion_mode", "distortion_mode"),
        ("use_saved_distortion", "use_saved_distortion"),
        ("distortion_auto_save", "distortion_auto_save"),
        ("left_profile", "left_distortion_file"),
        ("right_profile", "right_distortion_file"),
        ("lens_model_hint", "distortion_lens_model_hint"),
        ("horizontal_fov_deg", "distortion_horizontal_fov_deg"),
        ("vertical_fov_deg", "distortion_vertical_fov_deg"),
        ("camera_model", "distortion_camera_model"),
    ):
        _copy_if_present(flat, lens_correction, source_key, target_key)

    timing = _as_dict(payload.get("timing"), field_name="timing")
    for source_key, target_key in (
        ("pair_mode", "sync_pair_mode"),
        ("allow_frame_reuse", "allow_frame_reuse"),
        ("reuse_max_age_ms", "pair_reuse_max_age_ms"),
        ("reuse_max_consecutive", "pair_reuse_max_consecutive"),
        ("match_max_delta_ms", "sync_match_max_delta_ms"),
        ("time_source", "sync_time_source"),
        ("manual_offset_ms", "sync_manual_offset_ms"),
        ("auto_offset_window_sec", "sync_auto_offset_window_sec"),
        ("auto_offset_max_search_ms", "sync_auto_offset_max_search_ms"),
        ("recalibration_interval_sec", "sync_recalibration_interval_sec"),
        ("recalibration_trigger_skew_ms", "sync_recalibration_trigger_skew_ms"),
        ("recalibration_trigger_wait_ratio", "sync_recalibration_trigger_wait_ratio"),
        ("auto_offset_confidence_min", "sync_auto_offset_confidence_min"),
    ):
        _copy_if_present(flat, timing, source_key, target_key)

    outputs = _as_dict(payload.get("outputs"), field_name="outputs")
    probe = _as_dict(outputs.get("probe"), field_name="outputs.probe")
    transmit = _as_dict(outputs.get("transmit"), field_name="outputs.transmit")
    for section, prefix in ((probe, "probe_output"), (transmit, "transmit_output")):
        for source_key, suffix in (
            ("runtime", "runtime"),
            ("target", "target"),
            ("codec", "codec"),
            ("bitrate", "bitrate"),
            ("preset", "preset"),
            ("muxer", "muxer"),
            ("width", "width"),
            ("height", "height"),
            ("fps", "fps"),
        ):
            _copy_if_present(flat, section, source_key, f"{prefix}_{suffix}")

    runtime = _as_dict(payload.get("runtime"), field_name="runtime")
    for source_key, target_key in (
        ("input_runtime", "input_runtime"),
        ("gpu_mode", "gpu_mode"),
        ("gpu_device", "gpu_device"),
        ("stitch_output_scale", "stitch_output_scale"),
        ("stitch_every_n", "stitch_every_n"),
        ("benchmark_log_interval_sec", "benchmark_log_interval_sec"),
        ("headless_benchmark", "headless_benchmark"),
    ):
        _copy_if_present(flat, runtime, source_key, target_key)

    unsupported = sorted(set(flat) - set(SUPPORTED_RELOAD_CONFIG_FIELDS))
    if unsupported:
        raise ValueError(f"unsupported flattened schema v2 fields: {', '.join(unsupported)}")
    return flat


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
    distortion_mode: DistortionMode = "off"
    use_saved_distortion: bool = False
    distortion_auto_save: bool = False
    left_distortion_file: str = "data/runtime_distortion_left.json"
    right_distortion_file: str = "data/runtime_distortion_right.json"
    distortion_lens_model_hint: str = "pinhole"
    distortion_horizontal_fov_deg: float = 0.0
    distortion_vertical_fov_deg: float = 0.0
    distortion_camera_model: str = "DH-IPC-HFW4841T-ZAS"
    process_scale: float = 1.0
    stitch_output_scale: float = 1.0
    stitch_every_n: int = 1
    min_matches: int = 20
    min_inliers: int = 8
    ratio_test: float = 0.82
    ransac_thresh: float = 6.0
    max_features: int = 2800
    manual_points: int = 4
    gpu_mode: GpuMode = "only"
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
            "schema_version": RUNTIME_SCHEMA_VERSION,
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
    geometry_mode: str = "-"
    alignment_mode: str = "-"
    seam_mode: str = "-"
    exposure_mode: str = "-"
    blend_mode: str = "-"
    geometry_artifact_path: str = ""
    geometry_artifact_model: str = "-"
    cylindrical_focal_px: float = 0.0
    cylindrical_center_x: float = 0.0
    cylindrical_center_y: float = 0.0
    residual_alignment_error_px: float = 0.0
    seam_path_jitter_px: float = 0.0
    exposure_gain: float = 1.0
    exposure_bias: float = 0.0
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
    output_runtime_mode: str = ""
    output_last_error: str = ""
    production_output_active: bool = False
    production_output_frames_written: int = 0
    production_output_frames_dropped: int = 0
    production_output_target: str = ""
    production_output_command_line: str = ""
    production_output_effective_codec: str = ""
    production_output_runtime_mode: str = ""
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
