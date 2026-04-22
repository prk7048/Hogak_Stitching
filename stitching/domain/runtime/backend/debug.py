from __future__ import annotations

from typing import Any, Iterable

from stitching.domain.runtime.service.metrics import (
    _metrics_indicate_output_ready,
    metrics_output_failure_reason,
)


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
                failure_reason = metrics_output_failure_reason(payload)
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
