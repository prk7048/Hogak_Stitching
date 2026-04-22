from __future__ import annotations

from typing import Any

from stitching.domain.project.state import (
    confirm_output_timeout_sec as project_output_ready_timeout_sec,
    metrics_output_failure_reason as project_output_failure_reason,
)


def _is_pending_direct_fill_bridge_reason(reason: Any) -> bool:
    text = str(reason or "").strip().lower()
    return text == "awaiting first direct-fill attempt"


def _is_pending_direct_fill_bridge_state(
    *,
    runtime_mode: Any,
    bridge_reason: Any = "",
    last_error: Any = "",
) -> bool:
    runtime_mode_text = str(runtime_mode or "").strip().lower()
    if runtime_mode_text != "native-nvenc-bridge":
        return False
    bridge_reason_text = str(bridge_reason or "").strip()
    if _is_pending_direct_fill_bridge_reason(bridge_reason_text):
        return True
    last_error_text = str(last_error or "").strip()
    if not last_error_text.lower().startswith("gpu-direct bridge active:"):
        return False
    return _is_pending_direct_fill_bridge_reason(last_error_text.split(":", 1)[1].strip())


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


def metrics_output_failure_reason(metrics: dict[str, Any] | None) -> str:
    return project_output_failure_reason(
        metrics,
        is_pending_direct_fill_bridge_state=_is_pending_direct_fill_bridge_state,
        command_line_token=_command_line_token,
    )


def confirm_output_timeout_sec(plan: Any) -> float:
    return project_output_ready_timeout_sec(plan)
