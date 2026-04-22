from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


class RuntimeSiteConfigError(RuntimeError):
    pass


def repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "config" / "runtime.json").exists() and (candidate / "stitching").exists():
            return candidate
    return current.parents[3]


def runtime_config_path() -> Path:
    override = os.environ.get("HOGAK_RUNTIME_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return repo_root() / "config" / "runtime.json"


def runtime_local_config_path() -> Path | None:
    override = os.environ.get("HOGAK_RUNTIME_LOCAL_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    candidate = repo_root() / "config" / "runtime.local.json"
    return candidate if candidate.exists() else None


def runtime_profile_name() -> str:
    return os.environ.get("HOGAK_RUNTIME_PROFILE", "").strip()


def runtime_profile_path(profile_name: str | None = None) -> Path | None:
    profile = (profile_name if profile_name is not None else runtime_profile_name()).strip()
    if not profile:
        return None
    return repo_root() / "config" / "profiles" / f"{profile}.json"


def _load_json_dict(path: Path | None, *, label: str, required: bool) -> dict[str, Any]:
    if path is None:
        if required:
            raise RuntimeSiteConfigError(f"{label} path is not set")
        return {}
    if not path.exists():
        if required:
            raise RuntimeSiteConfigError(f"{label} not found: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeSiteConfigError(f"failed to read {label}: {path} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeSiteConfigError(f"invalid JSON in {label}: {path} ({exc})") from exc
    if not isinstance(payload, dict):
        raise RuntimeSiteConfigError(f"{label} must contain a JSON object: {path}")
    return payload


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(base_value, value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=8)
def _load_runtime_site_config_cached(config_path: str, local_config_path: str, profile_path: str) -> dict[str, Any]:
    payload = _load_json_dict(Path(config_path), label="runtime config", required=True)
    if local_config_path:
        payload = _deep_merge_dict(
            payload,
            _load_json_dict(Path(local_config_path), label="runtime local config", required=False),
        )
    if profile_path:
        payload = _deep_merge_dict(
            payload,
            _load_json_dict(Path(profile_path), label="runtime profile", required=True),
        )
    return payload


def load_runtime_site_config() -> dict[str, Any]:
    config_path = str(runtime_config_path())
    local_config = runtime_local_config_path()
    local_config_path = str(local_config) if local_config is not None else ""
    profile = runtime_profile_path()
    profile_path = str(profile) if profile is not None else ""
    return _load_runtime_site_config_cached(config_path, local_config_path, profile_path)


def site_config_value(path: str, default: Any) -> Any:
    current: Any = load_runtime_site_config()
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def site_config_str(path: str, default: str) -> str:
    value = site_config_value(path, default)
    return value if isinstance(value, str) and value.strip() else default


def site_config_int(path: str, default: int) -> int:
    value = site_config_value(path, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def site_config_float(path: str, default: float) -> float:
    value = site_config_value(path, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def site_config_bool(path: str, default: bool) -> bool:
    value = site_config_value(path, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def rtsp_url_looks_configured(url: str) -> bool:
    value = (url or "").strip()
    if not value:
        return False
    if ".example.invalid" in value:
        return False
    if "password@" in value:
        return False
    return value.lower().startswith(("rtsp://", "rtsps://"))


def require_configured_rtsp_urls(left_rtsp: str, right_rtsp: str, *, context: str) -> None:
    missing: list[str] = []
    if not rtsp_url_looks_configured(left_rtsp):
        missing.append("left_rtsp")
    if not rtsp_url_looks_configured(right_rtsp):
        missing.append("right_rtsp")
    if missing:
        raise RuntimeSiteConfigError(
            f"{context} requires configured RTSP URLs. Put site-specific values in config/runtime.local.json (preferred) or set "
            f"HOGAK_LEFT_RTSP and HOGAK_RIGHT_RTSP. The repo config/runtime.json keeps placeholder values. Missing/placeholder fields: {', '.join(missing)}"
        )
