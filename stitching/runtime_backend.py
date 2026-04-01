from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
from html import escape
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterable

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from stitching.project_defaults import (
    DEFAULT_NATIVE_HOMOGRAPHY_PATH,
)
from stitching.runtime_contract import (
    geometry_rollout_metadata,
    normalize_schema_v2_reload_payload,
)
from stitching.runtime_geometry_artifact import (
    load_runtime_geometry_artifact,
    runtime_geometry_artifact_path,
    runtime_geometry_model,
    runtime_geometry_requested_residual_model,
    runtime_geometry_residual_model,
)
from stitching.runtime_geometry_bakeoff import MeshRefreshService
from stitching.runtime_launcher import RuntimeLaunchSpec, query_gpu_direct_status
from stitching.runtime_site_config import load_runtime_site_config, repo_root, require_configured_rtsp_urls
from stitching.runtime_supervisor import RuntimeSupervisor


@dataclass(slots=True)
class RuntimePlan:
    geometry_artifact_path: Path
    homography_file: Path
    launch_spec: RuntimeLaunchSpec
    reload_payload: dict[str, Any]
    summary: dict[str, Any]


def _frontend_unavailable_html(frontend_path: Path) -> str:
    escaped_path = escape(str(frontend_path))
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Hogak 운영 화면 번들이 아직 준비되지 않았습니다</title>
    <style>
      :root {{
        color-scheme: light;
        font-family: "Aptos", "Segoe UI", sans-serif;
        background: linear-gradient(180deg, #f7fbf8 0%, #e8f0ee 100%);
        color: #1c2a2c;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
      }}
      main {{
        width: min(920px, 100%);
        border-radius: 24px;
        border: 1px solid rgba(28,42,44,0.08);
        background: rgba(255,255,255,0.92);
        box-shadow: 0 26px 90px rgba(61,84,80,0.12);
        padding: 28px;
      }}
      h1 {{
        margin: 0 0 10px;
        font-size: clamp(2rem, 4vw, 3rem);
      }}
      p, li {{
        color: #5f7474;
        line-height: 1.65;
      }}
      code, pre {{
        font-family: "Consolas", "Cascadia Code", monospace;
      }}
      pre {{
        margin: 14px 0;
        padding: 14px 16px;
        border-radius: 16px;
        background: rgba(28,42,44,0.06);
        border: 1px solid rgba(28,42,44,0.08);
        overflow-x: auto;
      }}
      .note {{
        margin-top: 18px;
        padding: 14px 16px;
        border-radius: 16px;
        background: rgba(89,157,255,0.12);
        border: 1px solid rgba(89,157,255,0.18);
      }}
      a {{
        color: #3069b1;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Hogak 운영 화면 번들이 아직 준비되지 않았습니다</h1>
      <p>
        <code>operator-server</code> 는 실행 중이지만, React 번들을
        <code>{escaped_path}</code> 에서 찾지 못했습니다.
      </p>
      <p>프런트엔드를 한 번 빌드한 뒤 서버를 다시 시작하세요.</p>
      <pre>cd frontend
npm install
npm run build</pre>
      <p>빌드가 끝나면 아래 명령으로 다시 실행하면 됩니다.</p>
      <pre>python -m stitching.cli operator-server</pre>
      <p><code>HOGAK_FRONTEND_DIST_DIR</code> 환경변수로 다른 빌드 결과물을 지정할 수도 있습니다.</p>
      <div class="note">
        <strong>현재 백엔드 상태</strong>
        <ul>
          <li>제품용 public API 는 <code>/api/project/state</code>, <code>/api/project/start</code>, <code>/api/project/stop</code> 만 유지됩니다.</li>
          <li>runtime debug, artifact admin, calibration 경로는 public surface에서 제거되었고 내부 경로로만 유지됩니다.</li>
          <li>이 브랜치의 기본 truth 는 <code>virtual-center-rectilinear-rigid</code> 이며, launch-ready 확인 전에는 시작이 차단됩니다.</li>
          <li>React 번들이 준비되면 단일 페이지에서 <code>Project state</code>, <code>Start Project</code>, <code>Stop Project</code> 흐름만 노출됩니다.</li>
        </ul>
      </div>
    </main>
  </body>
</html>"""


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    output_receive_uri = RuntimeService._receive_uri_from_target(production_output_target) or "udp://@:24000"

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
        or (artifact_rollout or {}).get("geometry_mesh_fallback_used")
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
        status_message = "Start Project will recompute rigid stitch geometry automatically."
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


def _command_line_token(command_line: Any, key: str) -> str:
    text = str(command_line or "").strip()
    key_text = str(key or "").strip()
    if not text or not key_text:
        return ""
    key_prefix = f"{key_text}="
    for part in text.split():
        if part.startswith(key_prefix):
            return part[len(key_prefix) :].strip()
    return ""


def _metric_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _metric_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _metrics_indicate_output_ready(metrics: dict[str, Any] | None) -> bool:
    if not isinstance(metrics, dict):
        return False
    runtime_mode = str(metrics.get("production_output_runtime_mode") or "").strip().lower()
    frames_written = _metric_int(metrics.get("production_output_frames_written"))
    output_active = _metric_bool(metrics.get("production_output_active"))
    return output_active and frames_written > 0 and runtime_mode == "native-nvenc-direct"


def _metrics_output_failure_reason(metrics: dict[str, Any] | None) -> str:
    if not isinstance(metrics, dict):
        return ""
    last_error = str(metrics.get("production_output_last_error") or "").strip()
    if last_error:
        return last_error
    runtime_mode = str(metrics.get("production_output_runtime_mode") or "").strip().lower()
    command_line = str(metrics.get("production_output_command_line") or "").strip()
    bridge_reason = _command_line_token(command_line, "bridge-reason")
    mode_token = _command_line_token(command_line, "mode")
    if mode_token == "direct-required-blocked" and bridge_reason:
        return f"gpu-direct direct-only requirement failed: {bridge_reason}"
    if runtime_mode == "native-nvenc-direct-blocked" and bridge_reason:
        return f"gpu-direct direct-only requirement failed: {bridge_reason}"
    if runtime_mode == "native-nvenc-bridge" and bridge_reason:
        return f"gpu-direct bridge active: {bridge_reason}"
    status = str(metrics.get("status") or "").strip().lower()
    if status in {"gpu_only_output_blocked", "reader_start_failed", "input decode failed", "stitch_failed"}:
        return status.replace("_", " ")
    return ""


def _project_log_entries(events: Iterable[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    recent_events = list(events)[-max(1, int(limit)) :]
    for event in recent_events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip().lower()
        payload = event.get("payload", {})
        payload = payload if isinstance(payload, dict) else {}
        phase = str(payload.get("phase") or payload.get("status") or "").strip().lower()
        timestamp_sec = float(event.get("timestamp_sec") or 0.0)
        level = "info"
        message = ""
        if event_type == "status":
            message = str(payload.get("message") or "").strip()
            if not message:
                status_name = str(payload.get("status") or "").strip().replace("_", " ")
                if status_name:
                    message = status_name.capitalize() + "."
            if phase in {"running", "prepared", "validated"}:
                level = "success"
            elif phase in {"blocked", "error"}:
                level = "error"
        elif event_type == "hello":
            phase = phase or "starting_runtime"
            level = "success"
            message = "Native runtime process started."
        elif event_type == "error":
            phase = phase or "error"
            level = "error"
            message = str(payload.get("message") or payload.get("code") or "Runtime error.").strip()
        elif event_type == "stopped":
            phase = phase or "idle"
            message = "Project stopped."
        elif event_type == "metrics":
            if _metrics_indicate_output_ready(payload):
                phase = "running"
                level = "success"
                message = "Live output was confirmed."
            else:
                failure_reason = _metrics_output_failure_reason(payload)
                if failure_reason:
                    phase = phase or "error"
                    level = "error"
                    message = failure_reason
        if not message:
            continue
        entry = {
            "id": int(event.get("id") or 0),
            "timestamp_sec": timestamp_sec,
            "phase": phase or event_type or "info",
            "level": level,
            "message": message,
        }
        if formatted and formatted[-1]["phase"] == entry["phase"] and formatted[-1]["message"] == entry["message"]:
            formatted[-1]["timestamp_sec"] = entry["timestamp_sec"]
            formatted[-1]["id"] = entry["id"]
            continue
        formatted.append(entry)
    return formatted[-max(1, int(limit)) :]


DEBUG_PROJECT_STAGE_ORDER = (
    "check_config",
    "connect_inputs",
    "capture_frames",
    "match_features",
    "solve_geometry",
    "build_artifact",
    "artifact_ready",
    "prepare_runtime",
    "launch_runtime",
    "confirm_output",
    "running",
)

DEBUG_PROJECT_STAGE_LABELS = {
    "check_config": "Check config",
    "connect_inputs": "Connect cameras",
    "capture_frames": "Capture frames",
    "match_features": "Match features",
    "solve_geometry": "Solve geometry",
    "build_artifact": "Build artifact",
    "artifact_ready": "Artifact ready",
    "prepare_runtime": "Prepare runtime",
    "launch_runtime": "Launch runtime",
    "confirm_output": "Confirm live output",
    "running": "Running",
}

PHASE_TO_DEBUG_STAGE = {
    "idle": "check_config",
    "checking_inputs": "check_config",
    "refreshing_mesh": "connect_inputs",
    "connect_inputs": "connect_inputs",
    "capture_frames": "capture_frames",
    "match_features": "match_features",
    "solve_geometry": "solve_geometry",
    "build_artifact": "build_artifact",
    "artifact_ready": "artifact_ready",
    "preparing_runtime": "prepare_runtime",
    "starting_runtime": "launch_runtime",
    "confirm_output": "confirm_output",
    "running": "running",
    "blocked": "confirm_output",
    "error": "confirm_output",
}


def _debug_stage_from_phase(phase: Any) -> str:
    normalized = str(phase or "").strip().lower()
    if not normalized:
        return "check_config"
    return PHASE_TO_DEBUG_STAGE.get(normalized, normalized if normalized in DEBUG_PROJECT_STAGE_ORDER else "check_config")


def _build_debug_steps(
    *,
    current_phase: Any,
    status: str,
    project_log: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_stage = _debug_stage_from_phase(current_phase)
    try:
        current_index = DEBUG_PROJECT_STAGE_ORDER.index(current_stage)
    except ValueError:
        current_index = 0

    latest_by_stage: dict[str, dict[str, Any]] = {}
    for entry in project_log:
        if not isinstance(entry, dict):
            continue
        stage = _debug_stage_from_phase(entry.get("phase"))
        latest_by_stage[stage] = entry

    debug_steps: list[dict[str, Any]] = []
    final_status = str(status or "").strip().lower()
    for index, stage in enumerate(DEBUG_PROJECT_STAGE_ORDER):
        state = "pending"
        if final_status == "running":
            state = "done" if stage != "running" else "current"
        elif final_status in {"blocked", "error"}:
            if index < current_index:
                state = "done"
            elif index == current_index:
                state = "failed"
        else:
            if index < current_index:
                state = "done"
            elif index == current_index and final_status == "starting":
                state = "current"
        entry = latest_by_stage.get(stage) or {}
        debug_steps.append(
            {
                "id": stage,
                "label": DEBUG_PROJECT_STAGE_LABELS.get(stage, stage.replace("_", " ").title()),
                "state": state,
                "message": str(entry.get("message") or "").strip(),
                "timestamp_sec": float(entry.get("timestamp_sec") or 0.0),
            }
        )
    return debug_steps


def _prepare_failure_needs_mesh_refresh(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "mesh-refresh",
            "launch-ready runtime geometry artifact",
            "internal fallback model",
            "active rigid artifact",
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
            "run mesh-refresh first",
            "active rigid artifact",
            "internal fallback model",
        )
    )


def _configured_rtsp_urls_for_request(request: dict[str, Any] | None = None) -> tuple[str, str]:
    site_config = load_runtime_site_config()
    cameras = site_config.get("cameras", {}) if isinstance(site_config.get("cameras"), dict) else {}
    request = request or {}
    left_inputs = request.get("inputs", {}).get("left", {}) if isinstance(request.get("inputs"), dict) else {}
    right_inputs = request.get("inputs", {}).get("right", {}) if isinstance(request.get("inputs"), dict) else {}
    left_rtsp = str(
        request.get("left_rtsp")
        or left_inputs.get("url")
        or cameras.get("left_rtsp")
        or ""
    ).strip()
    right_rtsp = str(
        request.get("right_rtsp")
        or right_inputs.get("url")
        or cameras.get("right_rtsp")
        or ""
    ).strip()
    return left_rtsp, right_rtsp


def _internal_mesh_refresh(
    mesh_refresh: MeshRefreshService,
    body: dict[str, Any] | None = None,
    *,
    progress: Any = None,
) -> dict[str, Any]:
    result = mesh_refresh.run_with_progress(body, progress=progress)
    if not isinstance(result, dict):
        raise ValueError("mesh-refresh did not return a JSON object")
    return result


def _project_state(runtime_state: dict[str, Any], mesh_refresh_state: dict[str, Any]) -> dict[str, Any]:
    merged = _merge_runtime_and_mesh_refresh_state(runtime_state, mesh_refresh_state)
    running = bool(merged.get("running"))
    last_error = str(merged.get("last_error") or "").strip()
    if last_error.lower().startswith("gpu-direct bridge active:"):
        last_error = ""
    config_blocker = ""
    try:
        left_rtsp, right_rtsp = _configured_rtsp_urls_for_request()
        require_configured_rtsp_urls(left_rtsp, right_rtsp, context="Start Project")
    except Exception as exc:
        config_blocker = str(exc)

    start_phase = str(merged.get("project_start_phase") or "").strip().lower()
    status_message = str(merged.get("project_status_message") or "").strip()
    merged_blocker = str(merged.get("blocker_reason") or "").strip()
    needs_mesh_refresh = _project_start_needs_mesh_refresh(merged)
    if needs_mesh_refresh:
        if _is_recoverable_missing_geometry_reason(last_error):
            last_error = ""
        if _is_recoverable_missing_geometry_reason(merged_blocker):
            merged_blocker = ""
        if _is_recoverable_missing_geometry_reason(merged.get("runtime_launch_ready_reason")):
            merged["runtime_launch_ready_reason"] = "Start Project will regenerate stitch geometry automatically."
    runtime_blocker = ""
    if not needs_mesh_refresh and not bool(merged.get("runtime_launch_ready")):
        runtime_blocker = str(merged.get("runtime_launch_ready_reason") or "").strip()
    blocker_reason = config_blocker or merged_blocker or runtime_blocker or last_error

    starting_phases = {
        "checking_inputs",
        "refreshing_mesh",
        "connect_inputs",
        "capture_frames",
        "match_features",
        "solve_geometry",
        "build_artifact",
        "artifact_ready",
        "preparing_runtime",
        "starting_runtime",
        "launch_runtime",
        "confirm_output",
    }

    if running:
        status = "running"
    elif start_phase in starting_phases:
        status = "starting"
    elif blocker_reason:
        status = "blocked"
    elif last_error:
        status = "error"
    else:
        status = "idle"

    if status == "running" and not status_message:
        status_message = "Project is running. Open the external player to confirm the stitched runtime output."
    elif status == "starting" and not status_message:
        status_message = "Start Project is preparing the stitched runtime."
    elif status == "blocked" and not status_message:
        status_message = blocker_reason or "Project start is blocked."
    elif status == "error" and not status_message:
        status_message = last_error or "Project start failed."
    elif not status_message:
        status_message = (
            "Start Project recalculates stitch geometry automatically."
            if needs_mesh_refresh
            else "Project is ready to start."
        )

    can_start = not running and status != "starting" and not blocker_reason
    can_stop = running
    output_target = str(merged.get("production_output_target") or "").strip()
    output_bridge_reason = str(merged.get("output_bridge_reason") or "").strip()
    production_output_last_error = str(merged.get("production_output_last_error") or "").strip()

    project_log = _project_log_entries(merged.get("recent_events") or [])
    debug_steps = _build_debug_steps(current_phase=start_phase, status=status, project_log=project_log)

    return {
        "status": status,
        "start_phase": start_phase or ("running" if running else "idle"),
        "status_message": status_message,
        "running": running,
        "can_start": can_start,
        "can_stop": can_stop,
        "blocker_reason": blocker_reason if status in {"blocked", "error"} else "",
        "output_receive_uri": _project_receive_uri_from_target(output_target) or "udp://@:24000",
        "production_output_target": output_target,
        "production_output_last_error": production_output_last_error,
        "output_bridge_reason": output_bridge_reason,
        "runtime_active_model": str(merged.get("runtime_active_model") or "").strip(),
        "runtime_active_residual_model": str(merged.get("runtime_active_residual_model") or "").strip(),
        "runtime_active_artifact_path": str(merged.get("runtime_active_artifact_path") or "").strip(),
        "runtime_artifact_checksum": str(merged.get("runtime_artifact_checksum") or "").strip(),
        "runtime_launch_ready": bool(merged.get("runtime_launch_ready")),
        "runtime_launch_ready_reason": str(merged.get("runtime_launch_ready_reason") or "").strip(),
        "geometry_residual_model": str(merged.get("runtime_active_residual_model") or "").strip(),
        "fallback_used": bool(merged.get("fallback_used")),
        "gpu_path_mode": str(merged.get("gpu_path_mode") or "unknown").strip() or "unknown",
        "gpu_path_ready": bool(merged.get("gpu_path_ready")),
        "input_path_mode": str(merged.get("input_path_mode") or "").strip(),
        "output_path_mode": str(merged.get("output_path_mode") or "").strip(),
        "output_path_direct": bool(merged.get("output_path_direct")),
        "output_path_bridge": bool(merged.get("output_path_bridge")),
        "zero_copy_ready": bool(merged.get("zero_copy_ready")),
        "zero_copy_reason": str(merged.get("zero_copy_reason") or "").strip(),
        "zero_copy_blockers": list(merged.get("zero_copy_blockers") or []),
        "project_log": project_log,
        "debug_mode": True,
        "debug_current_stage": _debug_stage_from_phase(start_phase),
        "debug_steps": debug_steps,
    }
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
            "rigid artifact",
            "launch-ready runtime geometry artifact",
            "internal fallback model",
        )
    )


def _project_start_response(
    backend: "RuntimeService",
    mesh_refresh: MeshRefreshService,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = body or {}
    initial_state = _project_state(backend.state(), mesh_refresh.state())
    if bool(initial_state.get("running")):
        return {
            "ok": True,
            "message": "Project is already running.",
            "state": initial_state,
        }

    backend.set_project_progress("check_config", "Checking runtime config and camera inputs.")
    left_rtsp, right_rtsp = _configured_rtsp_urls_for_request(request)
    require_configured_rtsp_urls(left_rtsp, right_rtsp, context="Start Project")

    explicit_artifact_path = backend._resolve_requested_artifact_path(request)
    mesh_refresh_triggered = False
    prepare_result: dict[str, Any] | None = None
    if explicit_artifact_path is None:
        backend.set_project_progress("connect_inputs", "Connecting to the camera streams.")
        _internal_mesh_refresh(mesh_refresh, request, progress=backend.set_project_progress)
        mesh_refresh_triggered = True
    try:
        backend.set_project_progress("preparing_runtime", "Preparing runtime.")
        prepare_result = backend.prepare(request)
    except Exception as exc:
        latest_state = _merge_runtime_and_mesh_refresh_state(backend.state(), mesh_refresh.state())
        if explicit_artifact_path is not None and _project_start_needs_mesh_refresh(latest_state, exc):
            backend.set_project_progress("connect_inputs", "Connecting to the camera streams.")
            _internal_mesh_refresh(mesh_refresh, request, progress=backend.set_project_progress)
            mesh_refresh_triggered = True
            backend.set_project_progress("preparing_runtime", "Preparing runtime.")
            prepare_result = backend.prepare(request)
        else:
            backend.set_project_progress("blocked", str(exc))
            raise

    prepared_project_state = _project_state(backend.state(), mesh_refresh.state())
    prepared_blocker = str(prepared_project_state.get("blocker_reason") or "").strip()
    if prepared_blocker:
        backend.set_project_progress("blocked", prepared_blocker)
        raise ValueError(prepared_blocker)
    if not bool(prepared_project_state.get("runtime_launch_ready")):
        reason = str(prepared_project_state.get("runtime_launch_ready_reason") or "Runtime launch is blocked.")
        backend.set_project_progress("blocked", reason)
        raise ValueError(reason)

    backend.set_project_progress("launch_runtime", "Launching the native runtime.")
    try:
        result = backend.start(request)
    except Exception as exc:
        backend.set_project_progress("error", str(exc))
        raise

    response = {
        "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
        "message": str(result.get("message") or "").strip() if isinstance(result, dict) else "",
        "state": _project_state(backend.state(), mesh_refresh.state()),
    }
    message = response["message"] or "Project started."
    if mesh_refresh_triggered:
        response["message"] = f"Stitch geometry was recalculated automatically. {message}".strip()
    elif prepare_result is not None and bool(prepare_result.get("auto_calibrated")):
        response["message"] = f"Stitch geometry was recalculated automatically. {message}".strip()
    else:
        response["message"] = message
    return response


class RuntimeService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._prepared_plan: RuntimePlan | None = None
        self._supervisor: RuntimeSupervisor | None = None
        self._event_pump_thread: threading.Thread | None = None
        self._event_pump_stop = threading.Event()
        self._latest_metrics: dict[str, Any] = {}
        self._latest_hello: dict[str, Any] = {}
        self._last_error = ""
        self._last_status = "idle"
        self._gpu_only_blockers: list[str] = []
        self._project_start_phase = "idle"
        self._project_status_message = "Start Project will recompute rigid stitch geometry automatically."

    def _record_event(self, event_type: str, payload: dict[str, Any] | None = None, *, seq: int = 0) -> dict[str, Any]:
        record = {
            "id": self._next_event_id,
            "seq": int(seq),
            "type": str(event_type),
            "timestamp_sec": time.time(),
            "payload": payload or {},
        }
        self._next_event_id += 1
        self._events.append(record)
        if len(self._events) > 200:
            del self._events[:-200]
        return record

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
        with self._lock:
            normalized_phase = str(phase or "idle").strip() or "idle"
            normalized_message = str(message or "").strip()
            changed = (
                normalized_phase != self._project_start_phase
                or normalized_message != self._project_status_message
            )
            self._project_start_phase = normalized_phase
            self._project_status_message = normalized_message
            if changed:
                self._record_event(
                    "status",
                    {
                        "status": normalized_phase,
                        "phase": normalized_phase,
                        "message": normalized_message,
                    },
                )

    def project_progress(self) -> tuple[str, str]:
        with self._lock:
            return self._project_start_phase, self._project_status_message

    def _ensure_default_geometry_artifact(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        request = request or {}
        explicit_artifact_path = self._resolve_requested_artifact_path(request)
        if explicit_artifact_path is not None:
            if not explicit_artifact_path.exists():
                raise ValueError(f"requested geometry artifact does not exist: {explicit_artifact_path}")
            artifact = load_runtime_geometry_artifact(explicit_artifact_path)
            rollout = geometry_rollout_metadata(artifact)
            return {
                "calibrated": False,
                "artifact_path": str(explicit_artifact_path),
                "geometry_model": rollout["geometry_model"],
                "launch_ready": bool(rollout["launch_ready"]),
                "message": "explicit geometry artifact selected",
            }

        site_config = load_runtime_site_config()
        paths = site_config.get("paths", {})
        homography_file = Path(
            str(request.get("homography_file") or paths.get("homography_file") or DEFAULT_NATIVE_HOMOGRAPHY_PATH)
        ).expanduser()
        geometry_artifact = runtime_geometry_artifact_path(homography_file)

        if geometry_artifact.exists():
            artifact = load_runtime_geometry_artifact(geometry_artifact)
            rollout = geometry_rollout_metadata(artifact)
            if bool(rollout["launch_ready"]) and bool(rollout["geometry_operator_visible"]):
                return {
                    "calibrated": False,
                    "artifact_path": str(geometry_artifact),
                    "geometry_model": rollout["geometry_model"],
                    "launch_ready": True,
                    "message": "existing launch-ready geometry artifact reused",
                }
            if bool(rollout["launch_ready"]) and not bool(rollout["geometry_operator_visible"]):
                raise ValueError(
                    "default runtime geometry artifact resolves to an internal fallback model. "
                    "Regenerate the launch-ready rigid geometry artifact first, or use an explicit geometry.artifact_path for internal rollback."
                )

        raise ValueError(
            "No launch-ready runtime geometry artifact is available. "
            "Run mesh-refresh first to regenerate the active rigid artifact."
        )
    def _build_plan(self, request: dict[str, Any] | None = None) -> RuntimePlan:
        site_config = load_runtime_site_config()
        request = request or {}
        cameras = site_config.get("cameras", {})
        paths = site_config.get("paths", {})
        runtime = site_config.get("runtime", {})
        canonical_request: dict[str, Any] | None = None
        has_full_schema_v2_payload = all(
            key in request for key in ("inputs", "geometry", "timing", "outputs", "runtime")
        )
        if has_full_schema_v2_payload:
            canonical_request = normalize_schema_v2_reload_payload(request)
        request_inputs = canonical_request["inputs"] if canonical_request is not None else (
            request.get("inputs") if isinstance(request.get("inputs"), dict) else {}
        )
        request_geometry = canonical_request["geometry"] if canonical_request is not None else (
            request.get("geometry") if isinstance(request.get("geometry"), dict) else {}
        )
        request_timing = canonical_request["timing"] if canonical_request is not None else (
            request.get("timing") if isinstance(request.get("timing"), dict) else {}
        )
        request_outputs = canonical_request["outputs"] if canonical_request is not None else (
            request.get("outputs") if isinstance(request.get("outputs"), dict) else {}
        )
        request_runtime = canonical_request["runtime"] if canonical_request is not None else (
            request.get("runtime") if isinstance(request.get("runtime"), dict) else {}
        )
        request_probe = request_outputs.get("probe") if isinstance(request_outputs.get("probe"), dict) else {}
        request_transmit = request_outputs.get("transmit") if isinstance(request_outputs.get("transmit"), dict) else {}

        left_rtsp = str(
            request.get("left_rtsp")
            or ((request_inputs.get("left") or {}).get("url") if isinstance(request_inputs.get("left"), dict) else "")
            or cameras.get("left_rtsp")
            or ""
        ).strip()
        right_rtsp = str(
            request.get("right_rtsp")
            or ((request_inputs.get("right") or {}).get("url") if isinstance(request_inputs.get("right"), dict) else "")
            or cameras.get("right_rtsp")
            or ""
        ).strip()
        if not left_rtsp or not right_rtsp:
            raise ValueError("left_rtsp and right_rtsp must be configured")
        require_configured_rtsp_urls(left_rtsp, right_rtsp, context="runtime backend")

        homography_file = Path(str(request.get("homography_file") or paths.get("homography_file") or "data/runtime_homography.json")).expanduser()
        geometry_artifact = runtime_geometry_artifact_path(homography_file)
        geometry_artifact_value = request.get("geometry_artifact_path") or request_geometry.get("artifact_path")
        if geometry_artifact_value:
            geometry_artifact = Path(str(geometry_artifact_value)).expanduser()

        resolved_homography = str(homography_file)
        artifact: dict[str, Any] | None = None
        if geometry_artifact.exists():
            artifact = load_runtime_geometry_artifact(geometry_artifact)
            source = artifact.get("source", {})
            if isinstance(source, dict):
                artifact_homography = str(source.get("homography_file") or "").strip()
                if artifact_homography:
                    resolved_homography = artifact_homography

        base_spec = RuntimeLaunchSpec()
        probe = runtime.get("probe", {}) if isinstance(runtime.get("probe"), dict) else {}
        transmit = runtime.get("transmit", {}) if isinstance(runtime.get("transmit"), dict) else {}

        probe_runtime = str(
            request_probe.get("runtime")
            or probe.get("runtime")
            or base_spec.output_runtime
            or "ffmpeg"
        ).strip()
        probe_target = str(request_probe.get("target") or probe.get("target") or "").strip()
        transmit_runtime = str(
            request_transmit.get("runtime")
            or transmit.get("runtime")
            or base_spec.production_output_runtime
            or "gpu-direct"
        ).strip()
        transmit_target = str(request_transmit.get("target") or transmit.get("target") or "").strip()
        cadence_fps = float(
            request_probe.get("fps")
            or request_transmit.get("fps")
            or runtime.get("output_cadence_fps")
            or 30.0
        )
        heartbeat_sec = float(
            request_runtime.get("benchmark_log_interval_sec")
            or runtime.get("benchmark_log_interval_sec")
            or runtime.get("status_interval_sec")
            or 5.0
        )

        launch_spec = RuntimeLaunchSpec(
            emit_hello=True,
            once=False,
            heartbeat_ms=max(250, int(round(heartbeat_sec * 1000.0))),
            left_rtsp=left_rtsp,
            right_rtsp=right_rtsp,
            input_runtime=str(request_runtime.get("input_runtime") or runtime.get("input_runtime") or base_spec.input_runtime),
            input_pipe_format=str(
                request_runtime.get("input_pipe_format")
                or runtime.get("input_pipe_format")
                or base_spec.input_pipe_format
            ),
            ffmpeg_bin=str(request_runtime.get("ffmpeg_bin") or runtime.get("ffmpeg_bin") or ""),
            homography_file=resolved_homography,
            transport=str(
                request_runtime.get("rtsp_transport")
                or runtime.get("rtsp_transport")
                or (request_inputs.get("left") or {}).get("transport")
                or base_spec.transport
            ),
            input_buffer_frames=int(
                request_runtime.get("input_buffer_frames")
                or runtime.get("input_buffer_frames")
                or (request_inputs.get("left") or {}).get("buffer_frames")
                or base_spec.input_buffer_frames
            ),
            timeout_sec=float(
                request_runtime.get("rtsp_timeout_sec")
                or runtime.get("rtsp_timeout_sec")
                or (request_inputs.get("left") or {}).get("timeout_sec")
                or base_spec.timeout_sec
            ),
            reconnect_cooldown_sec=float(
                request_runtime.get("reconnect_cooldown_sec")
                or runtime.get("reconnect_cooldown_sec")
                or (request_inputs.get("left") or {}).get("reconnect_cooldown_sec")
                or base_spec.reconnect_cooldown_sec
            ),
            output_runtime=probe_runtime,
            output_profile="inspection",
            output_target=probe_target,
            output_codec=str(request_probe.get("codec") or probe.get("codec") or base_spec.output_codec),
            output_bitrate=str(request_probe.get("bitrate") or probe.get("bitrate") or base_spec.output_bitrate),
            output_preset=str(request_probe.get("preset") or probe.get("preset") or base_spec.output_preset),
            output_muxer=str(request_probe.get("muxer") or probe.get("muxer") or ""),
            output_width=int(request_probe.get("width") or probe.get("width") or 0),
            output_height=int(request_probe.get("height") or probe.get("height") or 0),
            output_fps=float(request_probe.get("fps") or probe.get("fps") or cadence_fps),
            output_debug_overlay=bool(
                request_probe.get("debug_overlay")
                if "debug_overlay" in request_probe
                else (probe.get("debug_overlay") or False)
            ),
            production_output_runtime=transmit_runtime,
            production_output_profile="production-compatible",
            production_output_target=transmit_target,
            production_output_codec=str(
                request_transmit.get("codec") or transmit.get("codec") or base_spec.production_output_codec
            ),
            production_output_bitrate=str(
                request_transmit.get("bitrate") or transmit.get("bitrate") or base_spec.production_output_bitrate
            ),
            production_output_preset=str(
                request_transmit.get("preset") or transmit.get("preset") or base_spec.production_output_preset
            ),
            production_output_muxer=str(request_transmit.get("muxer") or transmit.get("muxer") or ""),
            production_output_width=int(
                request_transmit.get("width") or transmit.get("width") or base_spec.production_output_width
            ),
            production_output_height=int(
                request_transmit.get("height") or transmit.get("height") or base_spec.production_output_height
            ),
            production_output_fps=float(request_transmit.get("fps") or transmit.get("fps") or cadence_fps),
            production_output_debug_overlay=bool(
                request_transmit.get("debug_overlay")
                if "debug_overlay" in request_transmit
                else (transmit.get("debug_overlay") if "debug_overlay" in transmit else base_spec.production_output_debug_overlay)
            ),
            sync_pair_mode=str(request_timing.get("pair_mode") or runtime.get("sync_pair_mode") or "service"),
            allow_frame_reuse=bool(
                request_timing.get("allow_frame_reuse")
                if "allow_frame_reuse" in request_timing
                else runtime.get("allow_frame_reuse")
                if "allow_frame_reuse" in runtime
                else False
            ),
            pair_reuse_max_age_ms=float(
                request_timing.get("reuse_max_age_ms") or runtime.get("pair_reuse_max_age_ms") or 90.0
            ),
            pair_reuse_max_consecutive=int(
                request_timing.get("reuse_max_consecutive") or runtime.get("pair_reuse_max_consecutive") or 2
            ),
            sync_match_max_delta_ms=float(
                request_timing.get("match_max_delta_ms") or runtime.get("sync_match_max_delta_ms") or 35.0
            ),
            sync_time_source=str(request_timing.get("time_source") or runtime.get("sync_time_source") or "pts-offset-auto"),
            sync_manual_offset_ms=float(
                request_timing.get("manual_offset_ms") or runtime.get("sync_manual_offset_ms") or 0.0
            ),
            sync_auto_offset_window_sec=float(
                request_timing.get("auto_offset_window_sec") or runtime.get("sync_auto_offset_window_sec") or 4.0
            ),
            sync_auto_offset_max_search_ms=float(
                request_timing.get("auto_offset_max_search_ms") or runtime.get("sync_auto_offset_max_search_ms") or 500.0
            ),
            sync_recalibration_interval_sec=float(
                request_timing.get("recalibration_interval_sec") or runtime.get("sync_recalibration_interval_sec") or 60.0
            ),
            sync_recalibration_trigger_skew_ms=float(
                request_timing.get("recalibration_trigger_skew_ms") or runtime.get("sync_recalibration_trigger_skew_ms") or 45.0
            ),
            sync_recalibration_trigger_wait_ratio=float(
                request_timing.get("recalibration_trigger_wait_ratio")
                or runtime.get("sync_recalibration_trigger_wait_ratio")
                or 0.5
            ),
            sync_auto_offset_confidence_min=float(
                request_timing.get("auto_offset_confidence_min") or runtime.get("sync_auto_offset_confidence_min") or 0.85
            ),
            stitch_output_scale=float(
                request_runtime.get("stitch_output_scale") or runtime.get("stitch_output_scale") or 1.0
            ),
            stitch_every_n=int(request_runtime.get("stitch_every_n") or runtime.get("stitch_every_n") or 1),
            gpu_mode=str(request_runtime.get("gpu_mode") or runtime.get("gpu_mode") or "on"),
            gpu_device=int(request_runtime.get("gpu_device") or runtime.get("gpu_device") or 0),
            headless_benchmark=bool(
                request_runtime.get("headless_benchmark")
                if "headless_benchmark" in request_runtime
                else runtime.get("headless_benchmark")
                if "headless_benchmark" in runtime
                else False
            ),
        )

        reload_payload = normalize_schema_v2_reload_payload(
            {
                "inputs": {
                    "left": {
                        "url": left_rtsp,
                        "transport": str(
                            request_runtime.get("rtsp_transport")
                            or runtime.get("rtsp_transport")
                            or (request_inputs.get("left") or {}).get("transport")
                            or base_spec.transport
                        ),
                        "timeout_sec": float(
                            request_runtime.get("rtsp_timeout_sec")
                            or runtime.get("rtsp_timeout_sec")
                            or (request_inputs.get("left") or {}).get("timeout_sec")
                            or base_spec.timeout_sec
                        ),
                        "reconnect_cooldown_sec": float(
                            request_runtime.get("reconnect_cooldown_sec")
                            or runtime.get("reconnect_cooldown_sec")
                            or (request_inputs.get("left") or {}).get("reconnect_cooldown_sec")
                            or base_spec.reconnect_cooldown_sec
                        ),
                        "buffer_frames": int(
                            request_runtime.get("input_buffer_frames")
                            or runtime.get("input_buffer_frames")
                            or (request_inputs.get("left") or {}).get("buffer_frames")
                            or base_spec.input_buffer_frames
                        ),
                    },
                    "right": {
                        "url": right_rtsp,
                        "transport": str(
                            request_runtime.get("rtsp_transport")
                            or runtime.get("rtsp_transport")
                            or (request_inputs.get("right") or {}).get("transport")
                            or base_spec.transport
                        ),
                        "timeout_sec": float(
                            request_runtime.get("rtsp_timeout_sec")
                            or runtime.get("rtsp_timeout_sec")
                            or (request_inputs.get("right") or {}).get("timeout_sec")
                            or base_spec.timeout_sec
                        ),
                        "reconnect_cooldown_sec": float(
                            request_runtime.get("reconnect_cooldown_sec")
                            or runtime.get("reconnect_cooldown_sec")
                            or (request_inputs.get("right") or {}).get("reconnect_cooldown_sec")
                            or base_spec.reconnect_cooldown_sec
                        ),
                        "buffer_frames": int(
                            request_runtime.get("input_buffer_frames")
                            or runtime.get("input_buffer_frames")
                            or (request_inputs.get("right") or {}).get("buffer_frames")
                            or base_spec.input_buffer_frames
                        ),
                    },
                },
                "geometry": {
                    "artifact_path": str(geometry_artifact),
                },
                "timing": {
                    "pair_mode": str(request_timing.get("pair_mode") or runtime.get("sync_pair_mode") or "service"),
                    "allow_frame_reuse": bool(
                        request_timing.get("allow_frame_reuse")
                        if "allow_frame_reuse" in request_timing
                        else runtime.get("allow_frame_reuse")
                        if "allow_frame_reuse" in runtime
                        else False
                    ),
                    "reuse_max_age_ms": float(
                        request_timing.get("reuse_max_age_ms") or runtime.get("pair_reuse_max_age_ms") or 90.0
                    ),
                    "reuse_max_consecutive": int(
                        request_timing.get("reuse_max_consecutive") or runtime.get("pair_reuse_max_consecutive") or 2
                    ),
                    "match_max_delta_ms": float(
                        request_timing.get("match_max_delta_ms") or runtime.get("sync_match_max_delta_ms") or 35.0
                    ),
                    "time_source": str(request_timing.get("time_source") or runtime.get("sync_time_source") or "pts-offset-auto"),
                    "manual_offset_ms": float(
                        request_timing.get("manual_offset_ms") or runtime.get("sync_manual_offset_ms") or 0.0
                    ),
                    "auto_offset_window_sec": float(
                        request_timing.get("auto_offset_window_sec") or runtime.get("sync_auto_offset_window_sec") or 4.0
                    ),
                    "auto_offset_max_search_ms": float(
                        request_timing.get("auto_offset_max_search_ms") or runtime.get("sync_auto_offset_max_search_ms") or 500.0
                    ),
                    "recalibration_interval_sec": float(
                        request_timing.get("recalibration_interval_sec")
                        or runtime.get("sync_recalibration_interval_sec")
                        or 60.0
                    ),
                    "recalibration_trigger_skew_ms": float(
                        request_timing.get("recalibration_trigger_skew_ms")
                        or runtime.get("sync_recalibration_trigger_skew_ms")
                        or 45.0
                    ),
                    "recalibration_trigger_wait_ratio": float(
                        request_timing.get("recalibration_trigger_wait_ratio")
                        or runtime.get("sync_recalibration_trigger_wait_ratio")
                        or 0.5
                    ),
                    "auto_offset_confidence_min": float(
                        request_timing.get("auto_offset_confidence_min")
                        or runtime.get("sync_auto_offset_confidence_min")
                        or 0.85
                    ),
                },
                "outputs": {
                    "probe": {
                        "runtime": probe_runtime,
                        "target": probe_target,
                        "codec": str(request_probe.get("codec") or probe.get("codec") or base_spec.output_codec),
                        "bitrate": str(request_probe.get("bitrate") or probe.get("bitrate") or base_spec.output_bitrate),
                        "preset": str(request_probe.get("preset") or probe.get("preset") or base_spec.output_preset),
                        "muxer": str(request_probe.get("muxer") or probe.get("muxer") or ""),
                        "width": int(request_probe.get("width") or probe.get("width") or 0),
                        "height": int(request_probe.get("height") or probe.get("height") or 0),
                        "fps": float(request_probe.get("fps") or probe.get("fps") or cadence_fps),
                        "debug_overlay": bool(
                            request_probe.get("debug_overlay")
                            if "debug_overlay" in request_probe
                            else (probe.get("debug_overlay") or False)
                        ),
                    },
                    "transmit": {
                        "runtime": transmit_runtime,
                        "target": transmit_target,
                        "codec": str(
                            request_transmit.get("codec") or transmit.get("codec") or base_spec.production_output_codec
                        ),
                        "bitrate": str(
                            request_transmit.get("bitrate")
                            or transmit.get("bitrate")
                            or base_spec.production_output_bitrate
                        ),
                        "preset": str(
                            request_transmit.get("preset")
                            or transmit.get("preset")
                            or base_spec.production_output_preset
                        ),
                        "muxer": str(request_transmit.get("muxer") or transmit.get("muxer") or ""),
                        "width": int(
                            request_transmit.get("width") or transmit.get("width") or base_spec.production_output_width
                        ),
                        "height": int(
                            request_transmit.get("height") or transmit.get("height") or base_spec.production_output_height
                        ),
                        "fps": float(request_transmit.get("fps") or transmit.get("fps") or cadence_fps),
                        "debug_overlay": bool(
                            request_transmit.get("debug_overlay")
                            if "debug_overlay" in request_transmit
                            else (
                                transmit.get("debug_overlay")
                                if "debug_overlay" in transmit
                                else base_spec.production_output_debug_overlay
                            )
                        ),
                    },
                },
                "runtime": {
                    "input_runtime": str(request_runtime.get("input_runtime") or runtime.get("input_runtime") or base_spec.input_runtime),
                    "ffmpeg_bin": str(request_runtime.get("ffmpeg_bin") or runtime.get("ffmpeg_bin") or ""),
                    "gpu_mode": str(request_runtime.get("gpu_mode") or runtime.get("gpu_mode") or "on"),
                    "gpu_device": int(request_runtime.get("gpu_device") or runtime.get("gpu_device") or 0),
                    "stitch_output_scale": float(
                        request_runtime.get("stitch_output_scale") or runtime.get("stitch_output_scale") or 1.0
                    ),
                    "stitch_every_n": int(request_runtime.get("stitch_every_n") or runtime.get("stitch_every_n") or 1),
                    "benchmark_log_interval_sec": float(
                        request_runtime.get("benchmark_log_interval_sec")
                        or runtime.get("benchmark_log_interval_sec")
                        or runtime.get("status_interval_sec")
                        or 5.0
                    ),
                    "headless_benchmark": bool(
                        request_runtime.get("headless_benchmark")
                        if "headless_benchmark" in request_runtime
                        else runtime.get("headless_benchmark")
                        if "headless_benchmark" in runtime
                        else False
                    ),
                },
            }
        )
        rollout = geometry_rollout_metadata(artifact or {})
        summary = {
            "geometry_artifact_path": str(geometry_artifact),
            "homography_file": str(homography_file),
            "left_rtsp": left_rtsp,
            "right_rtsp": right_rtsp,
            "probe_target": probe_target,
            "transmit_target": transmit_target,
            "output_runtime_mode": probe_runtime,
            "production_output_runtime_mode": transmit_runtime,
            "sync_pair_mode": str(launch_spec.sync_pair_mode),
            "runtime_schema_version": 2,
            "gpu_only_mode": str(launch_spec.gpu_mode).strip().lower() == "only",
            "geometry_artifact_model": rollout["geometry_model"],
            "geometry_residual_model": rollout["geometry_residual_model"],
            "geometry_rollout_status": rollout["geometry_rollout_status"],
            "geometry_operator_visible": bool(rollout["geometry_operator_visible"]),
            "geometry_fallback_only": bool(rollout["geometry_fallback_only"]),
            "geometry_compat_only": bool(rollout["geometry_compat_only"]),
            "launch_ready": bool(rollout["launch_ready"]),
            "launch_ready_reason": str(rollout["launch_ready_reason"]),
        }
        return RuntimePlan(
            geometry_artifact_path=geometry_artifact,
            homography_file=homography_file,
            launch_spec=launch_spec,
            reload_payload=reload_payload,
            summary=summary,
        )

    @staticmethod
    def _resolve_requested_artifact_path(request: dict[str, Any] | None = None) -> Path | None:
        request = request or {}
        nested_geometry = request.get("geometry") if isinstance(request.get("geometry"), dict) else {}
        artifact_value = request.get("geometry_artifact_path") or nested_geometry.get("artifact_path")
        if not artifact_value:
            return None
        return Path(str(artifact_value)).expanduser()

    def _snapshot_locked(self) -> dict[str, Any]:
        supervisor = self._supervisor
        process = supervisor.process if supervisor is not None else None
        running = process is not None and process.poll() is None
        snapshot = {
            "running": running,
            "prepared": self._prepared_plan is not None,
            "runtime_pid": None if process is None else process.pid,
            "runtime_returncode": None if process is None else process.returncode,
            "status": self._last_status,
            "last_error": self._last_error,
            "prepared_plan": None if self._prepared_plan is None else self._prepared_plan.summary,
            "latest_hello": self._latest_hello,
            "latest_metrics": self._latest_metrics,
            "event_count": len(self._events),
            "recent_events": [dict(event) for event in self._events[-30:]],
            "gpu_only_mode": True,
            "gpu_only_ready": len(self._gpu_only_blockers) == 0,
            "gpu_only_blockers": list(self._gpu_only_blockers),
            "project_start_phase": self._project_start_phase,
            "project_status_message": self._project_status_message,
        }
        snapshot.update(self._flatten_truth_metrics(self._latest_metrics))
        if self._prepared_plan is not None:
            summary = self._prepared_plan.summary
            for key in (
                "geometry_artifact_path",
                "geometry_artifact_model",
                "geometry_residual_model",
                "geometry_rollout_status",
                "geometry_operator_visible",
                "geometry_fallback_only",
                "geometry_compat_only",
                "output_runtime_mode",
                "production_output_runtime_mode",
                "launch_ready",
                "launch_ready_reason",
            ):
                if not snapshot.get(key):
                    snapshot[key] = summary.get(key, "")
            snapshot["gpu_only_mode"] = bool(summary.get("gpu_only_mode", True))
            snapshot["gpu_only_ready"] = len(self._gpu_only_blockers) == 0
            snapshot["gpu_only_blockers"] = list(self._gpu_only_blockers)
            snapshot.setdefault("validation_mode", "read-only")
            snapshot.setdefault("strict_fresh", summary.get("sync_pair_mode", "") == "service")
            try:
                artifact = load_runtime_geometry_artifact(self._prepared_plan.geometry_artifact_path)
            except Exception:
                artifact = None
            if isinstance(artifact, dict) and not running:
                for key, value in self._flatten_artifact_truth(artifact).items():
                    current = snapshot.get(key)
                    if current in (None, "", "-", 0, 0.0, False):
                        snapshot[key] = value
        return snapshot

    @staticmethod
    def _flatten_truth_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
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
            "geometry_compat_only": _bool("geometry_compat_only"),
            "cylindrical_focal_px": _float("cylindrical_focal_px"),
            "cylindrical_center_x": _float("cylindrical_center_x"),
            "cylindrical_center_y": _float("cylindrical_center_y"),
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
            "production_output_frames_written": _int("production_output_frames_written"),
            "production_output_frames_dropped": _int("production_output_frames_dropped"),
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

    @staticmethod
    def _gpu_only_blockers_for_plan(plan: RuntimePlan) -> list[str]:
        spec = plan.launch_spec
        blockers: list[str] = []
        if str(spec.gpu_mode).strip().lower() != "only":
            blockers.append("GPU-only 브랜치에서는 runtime.gpu_mode 가 only 여야 합니다.")
        if str(spec.input_runtime).strip().lower() != "ffmpeg-cuda":
            blockers.append("GPU-only 모드에서는 입력 런타임이 ffmpeg-cuda 여야 합니다.")
        if str(spec.input_pipe_format).strip().lower() != "nv12":
            blockers.append("GPU-only 모드에서는 입력 파이프 포맷이 nv12 여야 합니다.")
        if str(spec.output_runtime).strip().lower() != "none":
            blockers.append("GPU-only 모드에서는 Probe 출력이 비활성화되어야 합니다.")
        if str(spec.output_target).strip():
            blockers.append("GPU-only 모드에서는 Probe target 이 비어 있어야 합니다.")
        if bool(spec.output_debug_overlay):
            blockers.append("GPU-only 모드에서는 Probe debug overlay 를 사용할 수 없습니다.")
        if str(spec.production_output_runtime).strip().lower() != "gpu-direct":
            blockers.append("GPU-only 모드에서는 Transmit runtime 이 gpu-direct 여야 합니다.")
        if not str(spec.production_output_target).strip():
            blockers.append("GPU-only 모드에서는 Transmit target 이 필요합니다.")
        if bool(spec.production_output_debug_overlay):
            blockers.append("GPU-only 모드에서는 Transmit debug overlay 를 사용할 수 없습니다.")
        try:
            gpu_count = int(cv2.cuda.getCudaEnabledDeviceCount())
        except Exception as exc:
            blockers.append(f"CUDA 장치 확인에 실패했습니다: {exc}")
        else:
            if gpu_count <= int(spec.gpu_device):
                blockers.append(
                    f"CUDA 장치 {int(spec.gpu_device)} 를 사용할 수 없습니다. 감지된 장치 수={gpu_count}."
                )
        gpu_direct_status = query_gpu_direct_status()
        if not bool(gpu_direct_status.get("dependency_ready")):
            status_text = str(
                gpu_direct_status.get("status")
                or gpu_direct_status.get("stderr")
                or gpu_direct_status.get("raw")
                or "gpu-direct dependency not ready"
            ).strip()
            blockers.append(f"gpu-direct 준비 상태가 아닙니다: {status_text}")
        return blockers

    @staticmethod
    def _flatten_artifact_truth(artifact: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(artifact, dict):
            return {}

        geometry = artifact.get("geometry", {}) if isinstance(artifact.get("geometry"), dict) else {}
        alignment = artifact.get("alignment", {}) if isinstance(artifact.get("alignment"), dict) else {}
        seam = artifact.get("seam", {}) if isinstance(artifact.get("seam"), dict) else {}
        exposure = artifact.get("exposure", {}) if isinstance(artifact.get("exposure"), dict) else {}
        projection = artifact.get("projection", {}) if isinstance(artifact.get("projection"), dict) else {}

        def _string(section: dict[str, Any], name: str, default: str = "") -> str:
            value = section.get(name, default)
            return "" if value is None else str(value)

        def _float(section: dict[str, Any], name: str, default: float = 0.0) -> float:
            value = section.get(name, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(default)

        geometry_mode = _string(geometry, "model", "-")
        seam_mode = _string(seam, "mode", "-")
        exposure_enabled = exposure.get("enabled")
        exposure_mode = "gain-bias" if bool(exposure_enabled) else "off"
        if not geometry_mode:
            geometry_mode = "-"
        rollout = geometry_rollout_metadata(artifact)

        left_projection = projection.get("left", {}) if isinstance(projection.get("left"), dict) else {}
        should_expose_cylindrical_projection = geometry_mode == "cylindrical-affine"
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
            "geometry_compat_only": bool(rollout["geometry_compat_only"]),
            "fallback_used": bool(rollout["geometry_fallback_only"] or rollout.get("geometry_mesh_fallback_used")),
            "launch_ready": bool(rollout["launch_ready"]),
            "launch_ready_reason": str(rollout["launch_ready_reason"]),
            "cylindrical_focal_px": _float(left_projection, "focal_px") if should_expose_cylindrical_projection else 0.0,
            "cylindrical_center_x": float(left_projection.get("center", [0.0, 0.0])[0]) if should_expose_cylindrical_projection and isinstance(left_projection.get("center"), list) and left_projection.get("center") else 0.0,
            "cylindrical_center_y": float(left_projection.get("center", [0.0, 0.0])[1]) if should_expose_cylindrical_projection and isinstance(left_projection.get("center"), list) and len(left_projection.get("center")) > 1 else 0.0,
        }

    def prepare(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                explicit_artifact_path = self._resolve_requested_artifact_path(request)
                if explicit_artifact_path is not None:
                    auto_geometry = self._ensure_default_geometry_artifact(request)
                else:
                    auto_geometry = {
                        "calibrated": False,
                        "artifact_path": "",
                        "geometry_model": "",
                        "launch_ready": False,
                        "message": "Start Project will regenerate stitch geometry automatically.",
                    }
                plan = self._build_plan(request)
                blockers = self._gpu_only_blockers_for_plan(plan)
                self._gpu_only_blockers = blockers
                if blockers:
                    raise ValueError(" / ".join(blockers))
            except Exception as exc:
                self._last_error = f"prepare failed: {exc}"
                self._record_event("error", {"code": "prepare_failed", "message": str(exc)})
                raise
            self._prepared_plan = plan
            self._last_status = "prepared"
            self._last_error = ""
            self._project_start_phase = "idle"
            self._project_status_message = "Project is ready to start."
            self._record_event("status", {"status": "prepared", "geometry_artifact_path": str(plan.geometry_artifact_path)})
            return {
                "ok": True,
                "prepared": plan.summary,
                "auto_calibrated": bool(auto_geometry.get("calibrated")),
                "message": str(auto_geometry.get("message") or "runtime prepared"),
                "state": self._snapshot_locked(),
            }

    def _ingest_runtime_event_locked(self, event: Any) -> None:
        if event is None:
            return
        if event.type == "metrics":
            self._latest_metrics = event.payload
        elif event.type == "hello":
            self._latest_hello = event.payload
        self._record_event(event.type, event.payload, seq=event.seq)

    def _wait_for_output_ready_locked(self, *, timeout_sec: float = 10.0) -> dict[str, Any]:
        if self._supervisor is None:
            raise RuntimeError("runtime supervisor is not available")
        deadline = time.time() + max(1.0, float(timeout_sec))
        last_metrics_request_sec = 0.0
        while time.time() < deadline:
            if self._supervisor.process.poll() is not None:
                stderr_tail = self._supervisor.get_stderr_tail().strip()
                detail = "runtime exited before live output was ready"
                if stderr_tail:
                    last_line = stderr_tail.splitlines()[-1].strip()
                    if last_line:
                        detail = f"{detail}: {last_line}"
                raise RuntimeError(detail)

            now = time.time()
            if now - last_metrics_request_sec >= 1.0:
                self._supervisor.request_metrics()
                last_metrics_request_sec = now

            event = self._supervisor.read_event(timeout_sec=min(0.25, max(0.05, deadline - now)))
            if event is not None:
                self._ingest_runtime_event_locked(event)

            failure_reason = _metrics_output_failure_reason(self._latest_metrics)
            if failure_reason:
                raise RuntimeError(f"runtime output failed before the first frame: {failure_reason}")
            if _metrics_indicate_output_ready(self._latest_metrics):
                return dict(self._latest_metrics)

        frames_written = _metric_int(self._latest_metrics.get("production_output_frames_written"))
        runtime_mode = str(self._latest_metrics.get("production_output_runtime_mode") or "").strip() or "unknown"
        failure_reason = _metrics_output_failure_reason(self._latest_metrics)
        raise RuntimeError(
            f"runtime did not confirm live output within {timeout_sec:.1f}s "
            f"(frames_written={frames_written}, output_path={runtime_mode}"
            f"{', reason=' + failure_reason if failure_reason else ''})"
        )

    def _start_event_pump(self) -> None:
        if self._event_pump_thread is not None:
            return
        self._event_pump_stop.clear()
        self._event_pump_thread = threading.Thread(target=self._pump_runtime_events, name="hogak-runtime-events", daemon=True)
        self._event_pump_thread.start()

    def _pump_runtime_events(self) -> None:
        while not self._event_pump_stop.is_set():
            supervisor = self._supervisor
            if supervisor is None:
                break
            event = supervisor.read_event(timeout_sec=0.25)
            if event is None:
                if supervisor.process.poll() is not None:
                    break
                continue
            with self._lock:
                self._ingest_runtime_event_locked(event)
        with self._lock:
            self._event_pump_thread = None

    def start(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._supervisor is not None and self._supervisor.process.poll() is None:
                self._last_status = "already_running"
                self._project_start_phase = "running"
                self._project_status_message = "Project is already running."
                return {"ok": True, "state": self._snapshot_locked()}
            auto_prepare_result: dict[str, Any] | None = None
            if self._prepared_plan is None:
                auto_prepare_result = self.prepare(request)
            plan = self._prepared_plan
            if plan is None:
                raise ValueError("runtime plan is unavailable after automatic prepare")
            blockers = self._gpu_only_blockers_for_plan(plan)
            self._gpu_only_blockers = blockers
            if blockers:
                self._last_error = f"start failed: {' / '.join(blockers)}"
                self._record_event("error", {"code": "gpu_only_blocked", "message": self._last_error})
                raise ValueError(" / ".join(blockers))
            self._last_status = "starting"
            self._last_error = ""
            self._supervisor = RuntimeSupervisor.launch(plan.launch_spec)
            try:
                hello = self._supervisor.wait_for_hello(timeout_sec=5.0)
                self._latest_hello = hello.payload
                self._record_event("hello", hello.payload, seq=hello.seq)
                self._supervisor.client.reload_config(plan.reload_payload)
                self._supervisor.request_metrics()
                self._record_event("status", {"status": "reload_sent", "geometry_artifact_path": str(plan.geometry_artifact_path)})
                self.set_project_progress("confirm_output", "Waiting for the first live output frame.")
                self._wait_for_output_ready_locked(timeout_sec=10.0)
            except Exception as exc:
                detail = str(exc).strip() or "runtime launch handshake failed"
                self._event_pump_stop.set()
                supervisor = self._supervisor
                self._supervisor = None
                try:
                    if supervisor is not None:
                        supervisor.close()
                except Exception:
                    pass
                self._last_status = "error"
                self._last_error = detail
                self._project_start_phase = "error"
                self._project_status_message = detail
                self._record_event("error", {"code": "start_failed", "message": detail})
                raise ValueError(detail)
            self._start_event_pump()
            self._last_status = "running"
            self._last_error = ""
            self._project_start_phase = "running"
            transmit_receive_uri = self._receive_uri_from_target(plan.summary.get("transmit_target", ""))
            start_message = (
                f"런타임을 시작했습니다. 외부 플레이어에서 {transmit_receive_uri or plan.summary.get('transmit_target', '')} 를 여세요."
            )
            receive_uri = transmit_receive_uri or str(plan.summary.get("transmit_target", "")).strip()
            start_message = f"런타임을 시작했습니다. 외부 플레이어에서 {receive_uri} 를 여세요."
            self._project_status_message = start_message
            self._record_event("status", {"status": "running", "runtime_pid": self._supervisor.process.pid, "message": start_message})
            if auto_prepare_result is not None:
                prepare_message = str(auto_prepare_result.get("message") or "").strip()
                if prepare_message:
                    start_message = f"{prepare_message} / {start_message}"
            return {
                "ok": True,
                "auto_prepared": auto_prepare_result is not None,
                "auto_calibrated": bool(auto_prepare_result and auto_prepare_result.get("auto_calibrated")),
                "message": start_message,
                "state": self._snapshot_locked(),
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._event_pump_stop.set()
            if self._supervisor is not None:
                try:
                    self._supervisor.shutdown()
                except Exception:
                    pass
                try:
                    self._supervisor.close()
                except Exception:
                    pass
                self._supervisor = None
            if self._event_pump_thread is not None:
                self._event_pump_thread.join(timeout=2.5)
                self._event_pump_thread = None
            self._last_status = "stopped"
            self._last_error = ""
            self._project_start_phase = "idle"
            self._project_status_message = "Project is stopped."
            self._record_event("stopped", {"status": "stopped"})
            return {"ok": True, "message": "Runtime stopped.", "state": self._snapshot_locked()}

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()


def create_app(
    *,
    service: RuntimeService | None = None,
    mesh_refresh_service: MeshRefreshService | None = None,
    frontend_dist_dir: str | Path | None = None,
) -> FastAPI:
    backend = service or RuntimeService()
    mesh_refresh = mesh_refresh_service or MeshRefreshService()
    app = FastAPI(
        title="Hogak Runtime API",
        version="2",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.runtime_service = backend
    app.state.mesh_refresh_service = mesh_refresh

    @app.get("/api/project/state")
    def project_state():
        return _project_state(backend.state(), mesh_refresh.state())

    @app.post("/api/project/start")
    def project_start(body: dict[str, Any] | None = None):
        try:
            return _project_start_response(backend, mesh_refresh, body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/project/stop")
    def project_stop():
        current_state = _project_state(backend.state(), mesh_refresh.state())
        if not bool(current_state.get("running")):
            return {
                "ok": True,
                "message": "Project is already stopped.",
                "state": current_state,
            }
        result = backend.stop()
        return {
            "ok": bool(result.get("ok", True)) if isinstance(result, dict) else True,
            "message": str(result.get("message") or "Project stopped."),
            "state": _project_state(backend.state(), mesh_refresh.state()),
        }

    if frontend_dist_dir is None:
        frontend_env = os.environ.get("HOGAK_FRONTEND_DIST_DIR", "").strip()
        frontend_path = Path(frontend_env).expanduser() if frontend_env else repo_root() / "frontend" / "dist"
    else:
        frontend_path = Path(frontend_dist_dir).expanduser()
    if frontend_path.is_dir():
        app.state.frontend_dist_dir = str(frontend_path)
        frontend_root = frontend_path.resolve()
        assets_dir = frontend_root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend_entrypoint(full_path: str):
            normalized = str(full_path or "").lstrip("/")
            if normalized.startswith("api/") or normalized.startswith("_internal/") or normalized.startswith("legacy/"):
                raise HTTPException(status_code=404, detail="not found")

            candidate = (frontend_root / normalized).resolve()
            try:
                candidate.relative_to(frontend_root)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="not found") from exc

            if candidate.is_file():
                return FileResponse(candidate)

            index_path = frontend_root / "index.html"
            if index_path.is_file():
                return FileResponse(index_path)
            raise HTTPException(status_code=404, detail="frontend unavailable")
    else:
        app.state.frontend_dist_dir = ""
        app.state.frontend_dist_missing = True
        fallback_html = _frontend_unavailable_html(frontend_path.resolve())
        print(f"[operator-server] React bundle not found at {frontend_path}; serving backend-only fallback page.")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend_unavailable(full_path: str):
            normalized = str(full_path or "").lstrip("/")
            if normalized.startswith("api/") or normalized.startswith("_internal/") or normalized.startswith("legacy/"):
                raise HTTPException(status_code=404, detail="not found")
            return HTMLResponse(content=fallback_html)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        try:
            backend.stop()
        except Exception:
            pass

    return app


app = create_app()


def main() -> int:
    try:
        import uvicorn
    except Exception as exc:
        raise RuntimeError("uvicorn is required to run the runtime backend") from exc

    host = os.environ.get("HOGAK_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("HOGAK_BACKEND_PORT", "8088"))
    uvicorn.run("stitching.runtime_backend:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
