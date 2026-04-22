from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from stitching.domain.geometry.artifact import (
    load_runtime_geometry_artifact,
    runtime_geometry_model,
    runtime_geometry_requested_residual_model,
    runtime_geometry_residual_model,
)
from stitching.domain.geometry.policy import geometry_rollout_metadata
from stitching.domain.runtime.service.metrics import (
    _command_line_token,
    _is_pending_direct_fill_bridge_state,
)


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_receive_uri_from_target(target: Any) -> str:
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


def _merge_runtime_and_mesh_refresh_state(runtime_state: dict[str, Any], mesh_refresh_state: dict[str, Any]) -> dict[str, Any]:
    merged = dict(runtime_state or {})
    mesh_refresh = dict(mesh_refresh_state or {})
    runtime_active_artifact_path = str(
        merged.get("runtime_active_artifact_path")
        or merged.get("geometry_artifact_path")
        or mesh_refresh.get("runtime_active_artifact_path")
        or ""
    ).strip()
    runtime_active_model = str(
        merged.get("runtime_active_model")
        or merged.get("geometry_artifact_model")
        or merged.get("geometry_mode")
        or ""
    ).strip()
    runtime_active_residual_model = str(
        merged.get("runtime_active_residual_model")
        or merged.get("geometry_residual_model")
        or ""
    ).strip()
    runtime_requested_residual_model = str(
        merged.get("runtime_requested_residual_model")
        or merged.get("geometry_requested_residual_model")
        or ""
    ).strip()
    runtime_artifact_checksum = str(
        merged.get("runtime_artifact_checksum")
        or merged.get("geometry_artifact_checksum")
        or ""
    ).strip()
    artifact_rollout: dict[str, Any] | None = None

    if runtime_active_artifact_path:
        try:
            artifact_path = Path(runtime_active_artifact_path)
            artifact = load_runtime_geometry_artifact(artifact_path)
        except Exception:
            artifact = None
            artifact_path = None
        if isinstance(artifact, dict):
            artifact_rollout = geometry_rollout_metadata(artifact)
            runtime_active_model = runtime_active_model or str(
                artifact_rollout.get("geometry_model") or runtime_geometry_model(artifact)
            )
            runtime_requested_residual_model = runtime_requested_residual_model or str(
                artifact_rollout.get("geometry_requested_residual_model")
                or runtime_geometry_requested_residual_model(artifact)
            )
            runtime_active_residual_model = runtime_active_residual_model or str(
                artifact_rollout.get("geometry_residual_model") or runtime_geometry_residual_model(artifact)
            )
            if not runtime_artifact_checksum and artifact_path is not None and artifact_path.exists():
                runtime_artifact_checksum = _compute_sha256(artifact_path)
        elif not runtime_artifact_checksum:
            try:
                artifact_path = Path(runtime_active_artifact_path)
                if artifact_path.exists():
                    runtime_artifact_checksum = _compute_sha256(artifact_path)
            except Exception:
                pass

    prepared_plan = merged.get("prepared_plan") if isinstance(merged.get("prepared_plan"), dict) else {}
    production_output_runtime_mode = str(merged.get("production_output_runtime_mode") or "").strip()
    production_output_target = str(
        merged.get("production_output_target")
        or prepared_plan.get("transmit_target")
        or ""
    ).strip()
    input_runtime = str(merged.get("input_runtime") or prepared_plan.get("input_runtime") or "").strip().lower()
    input_path_mode = "unknown"
    if input_runtime == "ffmpeg-cuda":
        input_path_mode = "cuda-decode-cpu-staged"
    elif input_runtime:
        input_path_mode = f"{input_runtime}-cpu"

    output_path_mode = production_output_runtime_mode or "unknown"
    output_path_direct = output_path_mode == "native-nvenc-direct"
    output_path_bridge = output_path_mode == "native-nvenc-bridge"
    output_path_requested_direct = output_path_mode in {"gpu-direct", "gpu-direct-requested"}
    production_output_last_error = str(merged.get("production_output_last_error") or "").strip()
    production_output_command_line = str(merged.get("production_output_command_line") or "").strip()
    output_bridge_reason = _command_line_token(production_output_command_line, "bridge-reason")
    output_writer_mode = _command_line_token(production_output_command_line, "mode")
    output_bridge_pending = _is_pending_direct_fill_bridge_state(
        runtime_mode=output_path_mode,
        bridge_reason=output_bridge_reason,
        last_error=production_output_last_error,
    )
    zero_copy_blockers: list[str] = []
    zero_copy_truth_pending: list[str] = []
    if input_path_mode == "cuda-decode-cpu-staged":
        zero_copy_blockers.append("reader transfers decoded frames to CPU before stitch input")
    elif input_path_mode != "unknown":
        zero_copy_blockers.append(f"input path is {input_path_mode}, not zero-copy")
    else:
        zero_copy_truth_pending.append("input path truth will be resolved during prepare")
    if not output_path_direct and not output_path_requested_direct:
        if output_path_mode == "unknown":
            zero_copy_truth_pending.append("output path truth will be resolved during prepare")
        elif output_path_mode == "native-nvenc-unavailable":
            zero_copy_blockers.append("native NVENC output path is unavailable")
        elif output_path_mode == "native-nvenc-direct-blocked" and output_bridge_reason:
            zero_copy_blockers.append(f"gpu-direct direct-only requirement failed: {output_bridge_reason}")
        elif output_bridge_pending:
            zero_copy_truth_pending.append("gpu-direct bridge is priming the first direct-fill frame")
        elif production_output_last_error:
            zero_copy_blockers.append(production_output_last_error)
        elif output_path_mode == "native-nvenc-bridge" and output_bridge_reason:
            zero_copy_blockers.append(f"direct output unavailable: {output_bridge_reason}")
        else:
            zero_copy_blockers.append(f"output path is {output_path_mode}, not direct")
    zero_copy_ready = len(zero_copy_blockers) == 0
    if zero_copy_blockers:
        zero_copy_reason = "; ".join(zero_copy_blockers)
    elif zero_copy_truth_pending:
        zero_copy_reason = "; ".join(zero_copy_truth_pending)
    else:
        zero_copy_reason = "end-to-end zero-copy path is active"
    gpu_path_mode = output_path_mode
    gpu_path_ready = zero_copy_ready
    output_receive_uri = _project_receive_uri_from_target(production_output_target) or "udp://@:24000"

    runtime_launch_ready = False
    runtime_launch_ready_reason = ""
    mesh_refresh_artifact_path = str(mesh_refresh.get("runtime_active_artifact_path") or "").strip()
    if artifact_rollout is not None:
        runtime_launch_ready = bool(artifact_rollout.get("launch_ready"))
        runtime_launch_ready_reason = str(artifact_rollout.get("launch_ready_reason") or "").strip()
    elif runtime_active_artifact_path and mesh_refresh_artifact_path == runtime_active_artifact_path:
        runtime_launch_ready = bool(mesh_refresh.get("runtime_launch_ready"))
        runtime_launch_ready_reason = str(mesh_refresh.get("runtime_launch_ready_reason") or "").strip()
    elif merged.get("runtime_launch_ready") is not None:
        runtime_launch_ready = bool(merged.get("runtime_launch_ready"))
        runtime_launch_ready_reason = str(merged.get("runtime_launch_ready_reason") or "").strip()
    elif merged.get("launch_ready") is not None:
        runtime_launch_ready = bool(merged.get("launch_ready"))
        runtime_launch_ready_reason = str(merged.get("launch_ready_reason") or "").strip()

    fallback_used = bool(
        merged.get("fallback_used")
        or merged.get("geometry_fallback_only")
        or (artifact_rollout or {}).get("geometry_fallback_only")
    )
    gpu_only_blockers = [
        str(item).strip()
        for item in list(merged.get("gpu_only_blockers") or [])
        if str(item).strip()
    ]
    needs_mesh_refresh = (
        not runtime_active_artifact_path
        or str(runtime_active_residual_model or "").strip().lower() != "rigid"
        or fallback_used
        or not runtime_launch_ready
    )

    blocker_reasons: list[str] = []
    if gpu_only_blockers:
        blocker_reasons.extend(gpu_only_blockers)
    if zero_copy_blockers:
        blocker_reasons.extend(zero_copy_blockers)
    if not runtime_launch_ready and not needs_mesh_refresh:
        blocker_reasons.append(runtime_launch_ready_reason or "runtime geometry is not launch-ready")
    blocker_reason = " / ".join(item for item in blocker_reasons if str(item).strip())

    running = bool(merged.get("running"))
    status = str(merged.get("status") or "idle").strip() or "idle"
    if running:
        start_phase = "running"
        status_message = (
            f"Project is running. Open the external player at {output_receive_uri}."
            if output_receive_uri
            else "Project is running."
        )
    elif blocker_reason:
        start_phase = "blocked"
        status_message = blocker_reason
    elif needs_mesh_refresh:
        start_phase = "refreshing_mesh"
        status_message = "Start Project needs to regenerate the active rigid stitch geometry before launch."
    else:
        start_phase = "ready"
        status_message = "Project is ready to start."

    merged.update(
        {
            "status": status,
            "start_phase": start_phase,
            "status_message": status_message,
            "can_start": not running and not blocker_reason,
            "can_stop": running,
            "blocker_reason": blocker_reason,
            "output_receive_uri": output_receive_uri,
            "production_output_last_error": production_output_last_error,
            "production_output_command_line": production_output_command_line,
            "output_bridge_reason": output_bridge_reason,
            "output_writer_mode": output_writer_mode,
            "runtime_active_model": runtime_active_model or "",
            "runtime_requested_residual_model": runtime_requested_residual_model or "",
            "runtime_active_residual_model": runtime_active_residual_model or "",
            "geometry_residual_model": runtime_active_residual_model or "",
            "runtime_active_artifact_path": runtime_active_artifact_path,
            "runtime_artifact_checksum": runtime_artifact_checksum,
            "runtime_launch_ready": runtime_launch_ready,
            "runtime_launch_ready_reason": runtime_launch_ready_reason,
            "fallback_used": fallback_used,
            "input_path_mode": input_path_mode,
            "gpu_path_mode": gpu_path_mode,
            "gpu_path_ready": gpu_path_ready,
            "output_path_mode": output_path_mode,
            "output_path_direct": output_path_direct,
            "output_path_bridge": output_path_bridge,
            "zero_copy_ready": zero_copy_ready,
            "zero_copy_reason": zero_copy_reason,
            "zero_copy_blockers": zero_copy_blockers,
            "production_output_target": production_output_target,
        }
    )
    return merged


def _prepare_failure_needs_mesh_refresh(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "mesh-refresh",
            "launch-ready runtime geometry artifact",
            "launch-ready rigid runtime geometry artifact",
            "internal fallback model",
            "active rigid artifact",
            "rigid geometry artifact",
        )
    )


def _is_recoverable_missing_geometry_reason(message: Any) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "launch-ready runtime geometry artifact",
            "launch-ready rigid runtime geometry artifact",
            "run mesh-refresh first",
            "active rigid artifact",
            "internal fallback model",
            "rigid geometry artifact",
        )
    )


def _project_start_needs_mesh_refresh(state: dict[str, Any], exc: Exception | None = None) -> bool:
    if bool(state.get("running")):
        return False
    residual = str(state.get("geometry_residual_model") or "").strip().lower()
    if not str(state.get("runtime_active_artifact_path") or "").strip():
        return True
    if residual and residual != "rigid":
        return True
    if bool(state.get("fallback_used")):
        return True
    if not bool(state.get("runtime_launch_ready")):
        return True
    message = str(exc or "").strip().lower()
    return any(
        token in message
        for token in (
            "mesh-refresh",
            "mesh artifact",
            "rigid artifact",
            "launch-ready runtime geometry artifact",
            "active rigid artifact",
        )
    )
