from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np


RUNTIME_GEOMETRY_ARTIFACT_TYPE = "runtime_geometry"
RUNTIME_GEOMETRY_SCHEMA_VERSION = 2


def runtime_geometry_artifact_path(source_path: Path | str) -> Path:
    path = Path(source_path)
    name = path.name
    if "homography" in name:
        name = name.replace("homography", "geometry", 1)
    elif path.suffix:
        name = f"{path.stem}.geometry{path.suffix}"
    else:
        name = "runtime_geometry.json"
    return path.with_name(name)


def _pair(value: tuple[float, float] | list[float] | None, fallback: tuple[float, float]) -> list[float]:
    if value is None:
        value = fallback
    if len(value) < 2:
        return [float(fallback[0]), float(fallback[1])]
    return [float(value[0]), float(value[1])]


def _matrix_3x3(value: np.ndarray | list[list[float]] | None) -> list[list[float]]:
    if value is None:
        return np.eye(3, dtype=np.float64).tolist()
    return np.asarray(value, dtype=np.float64).reshape(3, 3).tolist()


def _matrix_2x3(value: np.ndarray | list[list[float]] | None) -> list[list[float]]:
    if value is None:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    array = np.asarray(value, dtype=np.float64)
    if array.size == 9:
        array = array.reshape(3, 3)[:2, :]
    return array.reshape(2, 3).tolist()


def _default_projection_focal_px(resolution: tuple[int, int]) -> float:
    width = max(1, int(resolution[0]))
    height = max(1, int(resolution[1]))
    return float(max(width, height) * 0.90)


def _default_projection_center(resolution: tuple[int, int]) -> list[float]:
    width = max(1, int(resolution[0]))
    height = max(1, int(resolution[1]))
    return [float(width) / 2.0, float(height) / 2.0]


def _input_space_projection_defaults(
    input_resolution: tuple[int, int] | list[int] | None,
    fallback_resolution: tuple[int, int] | list[int] | None = None,
) -> tuple[float, list[float], list[int]]:
    source = input_resolution if input_resolution is not None else fallback_resolution
    if not isinstance(source, (tuple, list)) or len(source) < 2:
        source = (1920, 1080)
    width = max(1, int(source[0]))
    height = max(1, int(source[1]))
    resolution = [width, height]
    return (
        _default_projection_focal_px((width, height)),
        _default_projection_center((width, height)),
        resolution,
    )


def _sanitize_projection_side(
    side_projection: dict[str, Any] | None,
    *,
    fallback_input_resolution: tuple[int, int] | list[int] | None,
    output_resolution: tuple[int, int] | list[int] | None,
) -> dict[str, Any]:
    side_projection = dict(side_projection or {})
    default_focal_px, default_center, input_resolution = _input_space_projection_defaults(
        side_projection.get("input_resolution"),
        fallback_input_resolution or output_resolution,
    )
    raw_center = side_projection.get("center")
    parsed_center = _pair(raw_center, (default_center[0], default_center[1])) if raw_center is not None else default_center
    resolution_width = max(1, int(input_resolution[0]))
    resolution_height = max(1, int(input_resolution[1]))
    output_width = 0
    output_height = 0
    if isinstance(output_resolution, (tuple, list)) and len(output_resolution) >= 2:
        output_width = max(0, int(output_resolution[0]))
        output_height = max(0, int(output_resolution[1]))
    center_out_of_bounds = (
        parsed_center[0] <= 0.0
        or parsed_center[0] >= float(resolution_width)
        or parsed_center[1] <= 0.0
        or parsed_center[1] >= float(resolution_height)
    )
    center_matches_output_canvas = (
        output_width > 0
        and output_height > 0
        and (output_width != resolution_width or output_height != resolution_height)
        and abs(parsed_center[0] - (float(output_width) * 0.5)) <= 1.0
        and abs(parsed_center[1] - (float(output_height) * 0.5)) <= 1.0
    )
    should_reset_center = center_out_of_bounds or center_matches_output_canvas
    sanitized_center = default_center if should_reset_center else parsed_center
    raw_focal_px = side_projection.get("focal_px")
    try:
        sanitized_focal_px = float(raw_focal_px) if raw_focal_px is not None else default_focal_px
    except (TypeError, ValueError):
        sanitized_focal_px = default_focal_px
    output_default_focal_px = (
        _default_projection_focal_px((output_width, output_height))
        if output_width > 0 and output_height > 0
        else default_focal_px
    )
    focal_matches_output_canvas = (
        should_reset_center
        and output_width > 0
        and output_height > 0
        and abs(sanitized_focal_px - output_default_focal_px) <= 1.0
    )
    if sanitized_focal_px <= 0.0 or focal_matches_output_canvas or (center_out_of_bounds and sanitized_focal_px > float(max(resolution_width, resolution_height)) * 1.25):
        sanitized_focal_px = default_focal_px
    side_projection["focal_px"] = float(sanitized_focal_px)
    side_projection["center"] = [float(sanitized_center[0]), float(sanitized_center[1])]
    side_projection["input_resolution"] = [resolution_width, resolution_height]
    if "output_resolution" not in side_projection and isinstance(output_resolution, (tuple, list)) and len(output_resolution) >= 2:
        side_projection["output_resolution"] = [int(output_resolution[0]), int(output_resolution[1])]
    return side_projection


def _legacy_geometry_model(geometry: dict[str, Any]) -> str:
    model = str(geometry.get("model") or geometry.get("warp_model") or "planar-homography")
    if model in {"planar_homography", "planar-homography"}:
        return "planar-homography"
    if model in {"cylindrical_affine", "cylindrical-affine"}:
        return "cylindrical-affine"
    return model


def _ensure_v2_shape(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("runtime geometry artifact must be a JSON object")

    schema_version = payload.get("schema_version", payload.get("version"))
    if schema_version is None:
        raise ValueError("runtime geometry artifact is missing schema_version")
    try:
        payload["schema_version"] = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime geometry artifact schema_version is invalid") from exc

    if payload["schema_version"] == 1:
        geometry = payload.get("geometry", {})
        if not isinstance(geometry, dict):
            geometry = {}
        calibration = payload.get("calibration", {})
        if not isinstance(calibration, dict):
            calibration = {}
        output_resolution = geometry.get("output_resolution") or calibration.get("output_resolution") or [0, 0]
        output_resolution = [int(output_resolution[0] or 0), int(output_resolution[1] or 0)] if len(output_resolution) >= 2 else [0, 0]
        homography = geometry.get("homography")
        left_resolution = calibration.get("left_resolution") or [0, 0]
        right_resolution = calibration.get("right_resolution") or [0, 0]
        left_focal_px, left_center, left_resolution = _input_space_projection_defaults(left_resolution, output_resolution)
        right_focal_px, right_center, right_resolution = _input_space_projection_defaults(right_resolution, output_resolution)
        alignment_model = "affine" if _legacy_geometry_model(geometry) == "cylindrical-affine" else "homography"
        alignment = {
            "model": alignment_model,
            "matrix": _matrix_2x3(homography) if alignment_model == "affine" else _matrix_3x3(homography),
        }
        payload = {
            "artifact_type": payload.get("artifact_type", RUNTIME_GEOMETRY_ARTIFACT_TYPE),
            "schema_version": RUNTIME_GEOMETRY_SCHEMA_VERSION,
            "saved_at_epoch_sec": payload.get("saved_at_epoch_sec", int(time.time())),
            "source": payload.get("source", {}),
            "geometry": {
                "model": _legacy_geometry_model(geometry),
                "warp_model": str(geometry.get("warp_model") or "warpPerspective"),
                "homography": _matrix_3x3(homography),
                "output_resolution": output_resolution,
                "legacy_model": "planar-homography",
            },
            "lens_correction": {
                "left": {
                    "enabled": False,
                    "source": "off",
                    "model": "opencv_pinhole",
                },
                "right": {
                    "enabled": False,
                    "source": "off",
                    "model": "opencv_pinhole",
                },
            },
            "projection": {
                "left": {
                    "model": "cylindrical",
                    "focal_px": left_focal_px,
                    "center": left_center,
                    "input_resolution": left_resolution,
                    "output_resolution": output_resolution,
                },
                "right": {
                    "model": "cylindrical",
                    "focal_px": right_focal_px,
                    "center": right_center,
                    "input_resolution": right_resolution,
                    "output_resolution": output_resolution,
                },
            },
            "alignment": alignment,
            "canvas": {
                "width": int(output_resolution[0]),
                "height": int(output_resolution[1]),
            },
            "seam": {
                "mode": "dynamic-path" if _legacy_geometry_model(geometry) == "cylindrical-affine" else "feather",
                "transition_px": 64,
                "smoothness_penalty": 4.0,
                "temporal_penalty": 2.0,
            },
            "exposure": {
                "enabled": True,
                "gain_min": 0.7,
                "gain_max": 1.4,
                "bias_abs_max": 35.0,
            },
            "calibration": {
                "distortion_reference": str(calibration.get("distortion_reference") or "raw"),
                "left_resolution": left_resolution,
                "right_resolution": right_resolution,
                "inliers_count": int(calibration.get("inliers_count") or 0),
                "inlier_ratio": float(calibration.get("inlier_ratio") or 0.0),
                "left_inlier_points": calibration.get("left_inlier_points") or [],
                "right_inlier_points": calibration.get("right_inlier_points") or [],
                "metadata": dict(calibration.get("metadata") or {}),
            },
        }
        payload["source"] = payload["source"] if isinstance(payload["source"], dict) else {}
        return payload

    payload.setdefault("artifact_type", RUNTIME_GEOMETRY_ARTIFACT_TYPE)
    payload.setdefault("source", {})
    payload.setdefault("geometry", {})
    payload.setdefault("lens_correction", {})
    payload.setdefault("projection", {})
    payload.setdefault("alignment", {})
    payload.setdefault("canvas", {})
    payload.setdefault("seam", {})
    payload.setdefault("exposure", {})
    payload.setdefault("calibration", {})
    projection = payload.get("projection")
    if isinstance(projection, dict):
        geometry = payload.get("geometry", {}) if isinstance(payload.get("geometry"), dict) else {}
        calibration = payload.get("calibration", {}) if isinstance(payload.get("calibration"), dict) else {}
        output_resolution = geometry.get("output_resolution") or calibration.get("output_resolution") or [0, 0]
        left_resolution = calibration.get("left_resolution") or [0, 0]
        right_resolution = calibration.get("right_resolution") or [0, 0]
        projection["left"] = _sanitize_projection_side(
            projection.get("left") if isinstance(projection.get("left"), dict) else {},
            fallback_input_resolution=left_resolution,
            output_resolution=output_resolution,
        )
        projection["right"] = _sanitize_projection_side(
            projection.get("right") if isinstance(projection.get("right"), dict) else {},
            fallback_input_resolution=right_resolution,
            output_resolution=output_resolution,
        )
    return payload


def build_runtime_geometry_artifact(
    *,
    source_homography_file: Path | str,
    geometry_file: Path | str,
    homography: np.ndarray,
    metadata: dict[str, Any],
    distortion_reference: str,
    left_resolution: tuple[int, int],
    right_resolution: tuple[int, int],
    output_resolution: tuple[int, int],
    inliers_count: int,
    inlier_ratio: float,
    left_inlier_points: list[list[float]],
    right_inlier_points: list[list[float]],
    geometry_model: str = "cylindrical-affine",
    warp_model: str = "warpPerspective",
    alignment_model: str = "affine",
    alignment_matrix: np.ndarray | list[list[float]] | None = None,
    projection_focal_px: float | None = None,
    projection_center: tuple[float, float] | list[float] | None = None,
    projection_left_focal_px: float | None = None,
    projection_left_center: tuple[float, float] | list[float] | None = None,
    projection_right_focal_px: float | None = None,
    projection_right_center: tuple[float, float] | list[float] | None = None,
    seam_transition_px: int = 64,
    seam_smoothness_penalty: float = 4.0,
    seam_temporal_penalty: float = 2.0,
    exposure_enabled: bool = True,
    exposure_gain_min: float = 0.7,
    exposure_gain_max: float = 1.4,
    exposure_bias_abs_max: float = 35.0,
) -> dict[str, Any]:
    left_resolution = (int(left_resolution[0]), int(left_resolution[1]))
    right_resolution = (int(right_resolution[0]), int(right_resolution[1]))
    output_resolution = (int(output_resolution[0]), int(output_resolution[1]))
    shared_focal_px = float(projection_focal_px) if projection_focal_px is not None else None
    shared_center = tuple(projection_center) if projection_center is not None else None
    left_focal_px = float(projection_left_focal_px) if projection_left_focal_px is not None else (
        shared_focal_px if shared_focal_px is not None else _default_projection_focal_px(left_resolution)
    )
    left_center = _pair(
        tuple(projection_left_center) if projection_left_center is not None else shared_center,
        (left_resolution[0] / 2.0, left_resolution[1] / 2.0),
    )
    right_focal_px = float(projection_right_focal_px) if projection_right_focal_px is not None else (
        shared_focal_px if shared_focal_px is not None else _default_projection_focal_px(right_resolution)
    )
    right_center = _pair(
        tuple(projection_right_center) if projection_right_center is not None else shared_center,
        (right_resolution[0] / 2.0, right_resolution[1] / 2.0),
    )
    alignment = _matrix_2x3(alignment_matrix if alignment_matrix is not None else homography)
    geometry_model = str(geometry_model or "cylindrical-affine")
    seam_mode = "dynamic-path" if geometry_model == "cylindrical-affine" else "feather"
    return {
        "artifact_type": RUNTIME_GEOMETRY_ARTIFACT_TYPE,
        "schema_version": RUNTIME_GEOMETRY_SCHEMA_VERSION,
        "saved_at_epoch_sec": int(time.time()),
        "source": {
            "component": "native_runtime_calibration",
            "homography_file": str(source_homography_file),
            "geometry_file": str(geometry_file),
        },
        "geometry": {
            "model": geometry_model,
            "warp_model": str(warp_model),
            "homography": _matrix_3x3(homography),
            "output_resolution": [output_resolution[0], output_resolution[1]],
            "legacy_model": "planar-homography",
        },
        "lens_correction": {
            "left": {
                "enabled": False,
                "source": "off",
                "model": "opencv_pinhole",
            },
            "right": {
                "enabled": False,
                "source": "off",
                "model": "opencv_pinhole",
            },
        },
        "projection": {
            "left": {
                "model": "cylindrical",
                "focal_px": left_focal_px,
                "center": left_center,
                "input_resolution": [int(left_resolution[0]), int(left_resolution[1])],
                "output_resolution": [output_resolution[0], output_resolution[1]],
            },
            "right": {
                "model": "cylindrical",
                "focal_px": right_focal_px,
                "center": right_center,
                "input_resolution": [int(right_resolution[0]), int(right_resolution[1])],
                "output_resolution": [output_resolution[0], output_resolution[1]],
            },
        },
        "alignment": {
            "model": str(alignment_model),
            "matrix": alignment,
        },
        "canvas": {
            "width": output_resolution[0],
            "height": output_resolution[1],
        },
        "seam": {
            "mode": seam_mode,
            "transition_px": int(seam_transition_px),
            "smoothness_penalty": float(seam_smoothness_penalty),
            "temporal_penalty": float(seam_temporal_penalty),
        },
        "exposure": {
            "enabled": bool(exposure_enabled),
            "gain_min": float(exposure_gain_min),
            "gain_max": float(exposure_gain_max),
            "bias_abs_max": float(exposure_bias_abs_max),
        },
        "calibration": {
            "distortion_reference": str(distortion_reference or "raw"),
            "left_resolution": [int(left_resolution[0]), int(left_resolution[1])],
            "right_resolution": [int(right_resolution[0]), int(right_resolution[1])],
            "inliers_count": int(inliers_count),
            "inlier_ratio": float(inlier_ratio),
            "left_inlier_points": [[float(x), float(y)] for x, y in left_inlier_points],
            "right_inlier_points": [[float(x), float(y)] for x, y in right_inlier_points],
            "metrics": dict(metadata),
        },
    }


def save_runtime_geometry_artifact(path: Path | str, artifact: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")


def load_runtime_geometry_artifact(path: Path | str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime geometry artifact must be a JSON object")
    return _ensure_v2_shape(payload)


def runtime_geometry_model(artifact: dict[str, Any]) -> str:
    geometry = artifact.get("geometry", {})
    if not isinstance(geometry, dict):
        return "planar-homography"
    model = str(geometry.get("model") or "planar-homography")
    if model in {"planar_homography", "planar-homography"}:
        return "planar-homography"
    if model in {"cylindrical_affine", "cylindrical-affine"}:
        return "cylindrical-affine"
    return model


def runtime_geometry_alignment_matrix(artifact: dict[str, Any]) -> np.ndarray:
    alignment = artifact.get("alignment", {})
    if isinstance(alignment, dict):
        matrix = alignment.get("matrix")
        if matrix is not None:
            array = np.asarray(matrix, dtype=np.float64)
            if array.size == 6:
                affine = array.reshape(2, 3)
                homogeneous = np.eye(3, dtype=np.float64)
                homogeneous[:2, :] = affine
                return homogeneous
            return array.reshape(3, 3)
    geometry = artifact.get("geometry", {})
    if isinstance(geometry, dict):
        homography = geometry.get("homography")
        if homography is not None:
            return np.asarray(homography, dtype=np.float64).reshape(3, 3)
    return np.eye(3, dtype=np.float64)


def runtime_geometry_projection_focal_px(artifact: dict[str, Any]) -> float:
    projection = artifact.get("projection", {})
    if isinstance(projection, dict):
        for side in ("left", "right"):
            side_projection = projection.get(side, {})
            if isinstance(side_projection, dict):
                focal = side_projection.get("focal_px")
                if focal is not None:
                    try:
                        return float(focal)
                    except (TypeError, ValueError):
                        pass
                input_resolution = side_projection.get("input_resolution")
                if isinstance(input_resolution, (list, tuple)) and len(input_resolution) >= 2:
                    return _default_projection_focal_px((int(input_resolution[0]), int(input_resolution[1])))
    geometry = artifact.get("geometry", {})
    if isinstance(geometry, dict):
        resolution = geometry.get("output_resolution")
        if isinstance(resolution, (list, tuple)) and len(resolution) >= 2:
            return _default_projection_focal_px((int(resolution[0]), int(resolution[1])))
    return _default_projection_focal_px((1920, 1080))
