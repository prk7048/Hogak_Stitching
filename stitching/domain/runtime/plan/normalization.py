from __future__ import annotations

from typing import Any

from stitching.domain.runtime.contract import normalize_schema_v2_reload_payload


def _request_section(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    value = (payload or {}).get(key)
    return value if isinstance(value, dict) else {}


def normalize_runtime_plan_request(
    request: dict[str, Any] | None = None,
    *,
    site_config: dict[str, Any],
) -> dict[str, Any]:
    request = request or {}
    cameras = site_config.get("cameras", {}) if isinstance(site_config.get("cameras"), dict) else {}
    paths = site_config.get("paths", {}) if isinstance(site_config.get("paths"), dict) else {}
    runtime = site_config.get("runtime", {}) if isinstance(site_config.get("runtime"), dict) else {}

    canonical_request: dict[str, Any] | None = None
    has_full_schema_v2_payload = all(
        key in request for key in ("inputs", "geometry", "timing", "outputs", "runtime")
    )
    if has_full_schema_v2_payload:
        canonical_request = normalize_schema_v2_reload_payload(request)

    request_inputs = _request_section(canonical_request, "inputs") if canonical_request is not None else _request_section(request, "inputs")
    request_geometry = _request_section(canonical_request, "geometry") if canonical_request is not None else _request_section(request, "geometry")
    request_timing = _request_section(canonical_request, "timing") if canonical_request is not None else _request_section(request, "timing")
    request_outputs = _request_section(canonical_request, "outputs") if canonical_request is not None else _request_section(request, "outputs")
    request_runtime = _request_section(canonical_request, "runtime") if canonical_request is not None else _request_section(request, "runtime")
    request_probe = _request_section(request_outputs, "probe")
    request_transmit = _request_section(request_outputs, "transmit")

    return {
        "request": request,
        "canonical_request": canonical_request,
        "cameras": cameras,
        "paths": paths,
        "runtime": runtime,
        "request_inputs": request_inputs,
        "request_geometry": request_geometry,
        "request_timing": request_timing,
        "request_outputs": request_outputs,
        "request_runtime": request_runtime,
        "request_probe": request_probe,
        "request_transmit": request_transmit,
    }


def configured_rtsp_urls(plan_request: dict[str, Any]) -> tuple[str, str]:
    request = plan_request.get("request") if isinstance(plan_request.get("request"), dict) else {}
    request_inputs = plan_request.get("request_inputs") if isinstance(plan_request.get("request_inputs"), dict) else {}
    cameras = plan_request.get("cameras") if isinstance(plan_request.get("cameras"), dict) else {}

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
    return left_rtsp, right_rtsp
