from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from stitching.domain.calibration.native.calibration import NativeCalibrationConfig
from stitching.domain.runtime.defaults import (
    DEFAULT_CALIBRATION_DEBUG_DIR,
    DEFAULT_CALIBRATION_INLIERS_FILE,
    DEFAULT_HOMOGRAPHY_PATH,
    DEFAULT_RTSP_TIMEOUT_SEC,
    DEFAULT_RTSP_TRANSPORT,
)
from stitching.domain.geometry.artifact import runtime_geometry_artifact_path
from stitching.domain.runtime.site_config import load_runtime_site_config


DEFAULT_MESH_REFRESH_ROOT = Path("data/mesh_refresh")
DEFAULT_CLIP_FRAMES = 8
DEFAULT_MESH_REFRESH_WARMUP_FRAMES = 12
INTERNAL_MESH_REFRESH_MODEL = "virtual-center-rectilinear-rigid"
ProgressCallback = Callable[[str, str], None]


def _session_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def _resolve_active_runtime_paths() -> tuple[Path, Path]:
    site_config = load_runtime_site_config()
    paths = site_config.get("paths", {}) if isinstance(site_config.get("paths"), dict) else {}
    homography_file = Path(str(paths.get("homography_file") or DEFAULT_HOMOGRAPHY_PATH)).expanduser()
    geometry_file = runtime_geometry_artifact_path(homography_file)
    return homography_file, geometry_file


def _mesh_refresh_session_dir(root: Path | None = None) -> Path:
    return Path(root or DEFAULT_MESH_REFRESH_ROOT).expanduser() / _session_id()


def _resolve_mesh_refresh_dir(body: dict[str, Any] | None = None) -> Path | None:
    payload = body or {}
    value = payload.get("refresh_dir") or payload.get("bundle_dir")
    if not value:
        return None
    return Path(str(value)).expanduser()


def _validate_mesh_refresh_request(body: dict[str, Any] | None = None) -> None:
    payload = body or {}
    requested_model = str(payload.get("model") or "").strip()
    if requested_model and requested_model != INTERNAL_MESH_REFRESH_MODEL:
        raise ValueError(
            f"mesh-refresh only supports {INTERNAL_MESH_REFRESH_MODEL}; comparison candidate selection is no longer exposed here"
        )
    legacy_only_fields = [name for name in ("video_duration_sec", "video_fps") if name in payload]
    if legacy_only_fields:
        raise ValueError(
            f"mesh-refresh does not generate public comparison videos; unsupported fields: {', '.join(legacy_only_fields)}"
        )


def _load_mesh_refresh_manifest(session_dir: Path) -> dict[str, Any]:
    payload = json.loads((session_dir / "mesh_refresh.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("mesh refresh manifest must be a JSON object")
    return payload


def latest_mesh_refresh(root: Path | None = None) -> dict[str, Any] | None:
    refresh_root = Path(root or DEFAULT_MESH_REFRESH_ROOT).expanduser()
    if not refresh_root.exists():
        return None
    sessions = sorted(
        [path for path in refresh_root.iterdir() if path.is_dir() and (path / "mesh_refresh.json").exists()],
        key=lambda item: item.name,
        reverse=True,
    )
    if not sessions:
        return None
    return _load_mesh_refresh_manifest(sessions[0])


def _empty_mesh_refresh_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "session_id": "",
        "refresh_dir": "",
        "runtime_active_artifact_path": "",
        "mesh_refresh_model": "",
        "geometry_artifact_model": "",
        "geometry_residual_model": "",
        "requested_residual_model": "",
        "effective_residual_model": "",
        "degraded_to_rigid": False,
        "fallback_used": False,
        "status_detail": "",
        "geometry_rollout_status": "",
        "runtime_launch_ready": False,
        "runtime_launch_ready_reason": "",
        "launch_compatible": False,
        "launch_compatibility_reason": "",
    }


def _mesh_refresh_config_from_body(body: dict[str, Any] | None = None) -> NativeCalibrationConfig:
    body = body or {}
    _validate_mesh_refresh_request(body)
    site_config = load_runtime_site_config()
    cameras = site_config.get("cameras", {}) if isinstance(site_config.get("cameras"), dict) else {}
    return NativeCalibrationConfig(
        left_rtsp=str(body.get("left_rtsp") or cameras.get("left_rtsp") or "").strip(),
        right_rtsp=str(body.get("right_rtsp") or cameras.get("right_rtsp") or "").strip(),
        output_path=Path(str(body.get("out") or DEFAULT_HOMOGRAPHY_PATH)).expanduser(),
        inliers_output_path=Path(
            str(body.get("inliers_out") or DEFAULT_CALIBRATION_INLIERS_FILE)
        ).expanduser(),
        debug_dir=Path(str(body.get("debug_dir") or DEFAULT_CALIBRATION_DEBUG_DIR)).expanduser(),
        rtsp_transport=str(body.get("rtsp_transport") or DEFAULT_RTSP_TRANSPORT).strip(),
        rtsp_timeout_sec=max(1.0, float(body.get("rtsp_timeout_sec") or DEFAULT_RTSP_TIMEOUT_SEC)),
        warmup_frames=max(
            1,
            int(body.get("warmup_frames") or DEFAULT_MESH_REFRESH_WARMUP_FRAMES),
        ),
        process_scale=max(0.1, float(body.get("process_scale") or 1.0)),
        calibration_mode="auto",
        assisted_reproj_threshold=max(1.0, float(body.get("assisted_reproj_threshold") or 12.0)),
        assisted_max_auto_matches=max(0, int(body.get("assisted_max_auto_matches") or 600)),
        match_backend="classic",
        review_required=False,
        min_matches=max(8, int(body.get("min_matches") or 40)),
        min_inliers=max(6, int(body.get("min_inliers") or 20)),
        ratio_test=float(body.get("ratio_test") or 0.75),
        ransac_reproj_threshold=float(body.get("ransac_thresh") or 5.0),
        max_features=max(500, int(body.get("max_features") or 4000)),
    )


def _run_mesh_refresh_impl() -> Callable[..., dict[str, Any]]:
    from stitching.domain.geometry.refresh_runner import run_mesh_refresh

    return run_mesh_refresh


def run_mesh_refresh_from_args(args: argparse.Namespace) -> int:
    run_mesh_refresh = _run_mesh_refresh_impl()
    config = NativeCalibrationConfig(
        left_rtsp=str(args.left_rtsp),
        right_rtsp=str(args.right_rtsp),
        output_path=Path(args.out),
        inliers_output_path=Path(args.inliers_out),
        debug_dir=Path(args.debug_dir),
        rtsp_transport=str(args.rtsp_transport),
        rtsp_timeout_sec=max(1.0, float(args.rtsp_timeout_sec)),
        warmup_frames=max(1, int(args.warmup_frames)),
        process_scale=max(0.1, float(args.process_scale)),
        calibration_mode="auto",
        assisted_reproj_threshold=max(1.0, float(args.assisted_reproj_threshold)),
        assisted_max_auto_matches=max(0, int(args.assisted_max_auto_matches)),
        match_backend="classic",
        review_required=False,
        min_matches=max(8, int(getattr(args, "min_matches", 40))),
        min_inliers=max(6, int(getattr(args, "min_inliers", 20))),
        ratio_test=float(getattr(args, "ratio_test", 0.75)),
        ransac_reproj_threshold=float(getattr(args, "ransac_thresh", 5.0)),
        max_features=max(500, int(getattr(args, "max_features", 4000))),
    )
    result = run_mesh_refresh(
        config,
        session_dir=Path(str(args.refresh_dir)).expanduser() if getattr(args, "refresh_dir", None) else None,
        clip_frames=max(3, int(getattr(args, "clip_frames", DEFAULT_CLIP_FRAMES))),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


class MeshRefreshService:
    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root or DEFAULT_MESH_REFRESH_ROOT)

    def state(self) -> dict[str, Any]:
        manifest = latest_mesh_refresh(self._root)
        if manifest is None:
            return _empty_mesh_refresh_state()
        return manifest

    def run(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.run_with_progress(body)

    def run_with_progress(
        self,
        body: dict[str, Any] | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        run_mesh_refresh = _run_mesh_refresh_impl()
        config = _mesh_refresh_config_from_body(body)
        refresh_dir = _resolve_mesh_refresh_dir(body)
        clip_frames = max(3, int((body or {}).get("clip_frames") or DEFAULT_CLIP_FRAMES))
        return run_mesh_refresh(config, session_dir=refresh_dir, clip_frames=clip_frames, progress=progress)
