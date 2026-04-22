from pydantic import BaseModel, ConfigDict, Field


class RuntimeTruthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "idle"
    running: bool = False
    pid: int | None = None
    phase: str = ""
    active_model: str = ""
    active_residual_model: str = ""
    gpu_path_mode: str = "unknown"
    gpu_path_ready: bool = False
    input_path_mode: str = ""
    output_path_mode: str = ""


class OutputPathTruthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receive_uri: str = ""
    target: str = ""
    mode: str = ""
    direct: bool = False
    bridge: bool = False
    bridge_reason: str = ""
    last_error: str = ""


class ZeroCopyTruthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool = False
    reason: str = ""
    blockers: list[str] = Field(default_factory=list)
    status: str = "unknown"
