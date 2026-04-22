from dataclasses import asdict, dataclass
from typing import Any

from stitching.domain.geometry.artifact import (
    runtime_geometry_effective_residual_model,
    runtime_geometry_fixed_crop_ready,
    runtime_geometry_model,
    runtime_geometry_requested_residual_model,
)


DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL = "virtual-center-rectilinear"
DEFAULT_OPERATOR_GEOMETRY_MODEL = "virtual-center-rectilinear-rigid"
SECONDARY_RUNTIME_GEOMETRY_MODELS: tuple[str, ...] = ()
SUPPORTED_RUNTIME_GEOMETRY_MODELS = (
    DEFAULT_OPERATOR_GEOMETRY_MODEL,
    *SECONDARY_RUNTIME_GEOMETRY_MODELS,
)


@dataclass(slots=True)
class GeometryRolloutEvaluation:
    geometry_model: str
    geometry_requested_residual_model: str
    geometry_residual_model: str
    geometry_rollout_status: str
    geometry_operator_visible: bool
    geometry_fallback_only: bool
    geometry_mesh_contract_ready: bool
    geometry_mesh_fallback_used: bool
    geometry_crop_ready: bool
    launch_ready: bool
    launch_ready_reason: str

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self))


@dataclass(slots=True)
class _GeometryPolicyInputs:
    model: str = ""
    requested_residual: str = ""
    effective_residual: str = ""
    crop_ready: bool = False
    quality_block_reason: str = ""


def rigid_geometry_quality_reason(metrics: dict[str, Any] | None) -> str:
    if not isinstance(metrics, dict):
        return ""
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

    if crop_ratio > 0.0 and crop_ratio < 0.50:
        return "rigid geometry crop ratio is too low; recompute geometry with a better-aligned scene"
    if scale_drift > 0.0 and abs(scale_drift - 1.0) > 0.22:
        return "rigid geometry shows excessive right-edge scale drift; recompute geometry"
    if tilt_deg > 6.0:
        return "rigid geometry is excessively tilted; recompute geometry"
    return ""


def _normalize_policy_model(model: Any) -> str:
    text = str(model or "").strip().lower().replace("_", "-")
    if text in {
        "",
        "virtual-center-rectilinear",
        "virtual-center-rectilinear-rigid",
        "virtual-center-rectilinear-mesh",
    }:
        return DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL if text else ""
    return text


def _artifact_policy_inputs(artifact: dict[str, Any]) -> _GeometryPolicyInputs:
    requested_residual = runtime_geometry_requested_residual_model(artifact)
    effective_residual = runtime_geometry_effective_residual_model(artifact)
    calibration = artifact.get("calibration", {})
    metrics = calibration.get("metrics", {}) if isinstance(calibration, dict) else {}
    quality_block_reason = ""
    if requested_residual == "rigid" and effective_residual == "rigid":
        quality_block_reason = rigid_geometry_quality_reason(metrics)

    return _GeometryPolicyInputs(
        model=_normalize_policy_model(runtime_geometry_model(artifact)),
        requested_residual=requested_residual,
        effective_residual=effective_residual,
        crop_ready=runtime_geometry_fixed_crop_ready(artifact),
        quality_block_reason=quality_block_reason,
    )


def _direct_policy_inputs(geometry_model: Any, residual_model: Any | None = None) -> _GeometryPolicyInputs:
    raw_model = str(geometry_model or "").strip()
    requested_residual = "" if residual_model is None else str(residual_model).strip().lower().replace("_", "-")
    if not requested_residual:
        normalized_raw_model = raw_model.lower().replace("_", "-")
        if normalized_raw_model.endswith("-rigid"):
            requested_residual = "rigid"
        elif normalized_raw_model.endswith("-mesh"):
            requested_residual = "mesh"
    return _GeometryPolicyInputs(
        model=_normalize_policy_model(raw_model),
        requested_residual=requested_residual,
        effective_residual=requested_residual,
    )


def _policy_inputs(geometry_model: Any, residual_model: Any | None = None) -> _GeometryPolicyInputs:
    if isinstance(geometry_model, dict):
        return _artifact_policy_inputs(geometry_model)
    return _direct_policy_inputs(geometry_model, residual_model)


def evaluate_geometry_rollout(geometry_model: Any, residual_model: Any | None = None) -> GeometryRolloutEvaluation:
    policy = _policy_inputs(geometry_model, residual_model)
    rigid_default = (
        policy.model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL
        and policy.requested_residual == "rigid"
        and policy.effective_residual == "rigid"
        and policy.crop_ready
        and not policy.quality_block_reason
    )
    rigid_missing_crop = (
        policy.model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL
        and policy.requested_residual == "rigid"
        and policy.effective_residual == "rigid"
        and not policy.crop_ready
    )
    rigid_quality_blocked = (
        policy.model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL
        and policy.requested_residual == "rigid"
        and policy.effective_residual == "rigid"
        and policy.crop_ready
        and bool(policy.quality_block_reason)
    )
    operator_visible = rigid_default or rigid_missing_crop or rigid_quality_blocked
    fallback_only = False

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
        launch_ready_reason = policy.quality_block_reason
    elif policy.model == DEFAULT_RUNTIME_BASE_GEOMETRY_MODEL and policy.requested_residual and policy.requested_residual != "rigid":
        public_model = DEFAULT_OPERATOR_GEOMETRY_MODEL
        rollout_status = "blocked"
        launch_ready = False
        launch_ready_reason = "unsupported runtime geometry residual model; regenerate the active rigid geometry artifact"
    elif policy.model:
        public_model = policy.model
        rollout_status = "unsupported"
        launch_ready = False
        launch_ready_reason = "unsupported runtime geometry model; only virtual-center-rectilinear-rigid is allowed on the product path"
    else:
        public_model = "-"
        rollout_status = "unknown"
        launch_ready = False
        launch_ready_reason = "geometry artifact model is missing"

    return GeometryRolloutEvaluation(
        geometry_model=public_model,
        geometry_requested_residual_model=policy.requested_residual or "-",
        geometry_residual_model=policy.effective_residual or "-",
        geometry_rollout_status=rollout_status,
        geometry_operator_visible=operator_visible,
        geometry_fallback_only=fallback_only,
        geometry_mesh_contract_ready=False,
        geometry_mesh_fallback_used=False,
        geometry_crop_ready=policy.crop_ready,
        launch_ready=launch_ready,
        launch_ready_reason=launch_ready_reason,
    )


def geometry_rollout_metadata(geometry_model: Any, residual_model: Any | None = None) -> dict[str, Any]:
    return evaluate_geometry_rollout(geometry_model, residual_model).to_dict()
