from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def runtime_config_path() -> Path:
    override = os.environ.get("HOGAK_RUNTIME_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return repo_root() / "config" / "runtime.json"


def runtime_profile_name() -> str:
    return os.environ.get("HOGAK_RUNTIME_PROFILE", "").strip()


def runtime_profile_path(profile_name: str | None = None) -> Path | None:
    profile = (profile_name if profile_name is not None else runtime_profile_name()).strip()
    if not profile:
        return None
    return repo_root() / "config" / "profiles" / f"{profile}.json"


def _load_json_dict(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
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
def _load_runtime_site_config_cached(config_path: str, profile_path: str) -> dict[str, Any]:
    payload = _load_json_dict(Path(config_path))
    if profile_path:
        payload = _deep_merge_dict(payload, _load_json_dict(Path(profile_path)))
    return payload


def load_runtime_site_config() -> dict[str, Any]:
    config_path = str(runtime_config_path())
    profile = runtime_profile_path()
    profile_path = str(profile) if profile is not None else ""
    return _load_runtime_site_config_cached(config_path, profile_path)


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
