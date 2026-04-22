from pydantic import BaseModel, ConfigDict


class GeometryTruthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = ""
    requested_residual_model: str = ""
    residual_model: str = ""
    artifact_path: str = ""
    artifact_checksum: str = ""
    launch_ready: bool = False
    launch_ready_reason: str = ""
    rollout_status: str = ""
    fallback_used: bool = False
    operator_visible: bool = False
