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
        alignment_model = "affine" if _legacy_geometry_model(geometry) == "cylindrical-affine" else "homography"
        alignment = {
            "model": alignment_model,
            "matrix": _matrix_2x3(homography) if alignment_model == "affine" else _matrix_3x3(homography),
        }
        focal_px = _default_projection_focal_px(tuple(output_resolution))
        center = [float(max(1, output_resolution[0]) / 2.0), float(max(1, output_resolution[1]) / 2.0)]
        left_resolution = calibration.get("left_resolution") or [0, 0]
        right_resolution = calibration.get("right_resolution") or [0, 0]
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
                    "focal_px": focal_px,
                    "center": center,
                    "input_resolution": left_resolution,
                    "output_resolution": output_resolution,
                },
                "right": {
                    "model": "cylindrical",
                    "focal_px": focal_px,
                    "center": center,
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
    seam_transition_px: int = 64,
    seam_smoothness_penalty: float = 4.0,
    seam_temporal_penalty: float = 2.0,
    exposure_enabled: bool = True,
    exposure_gain_min: float = 0.7,
    exposure_gain_max: float = 1.4,
    exposure_bias_abs_max: float = 35.0,
) -> dict[str, Any]:
    output_resolution = (int(output_resolution[0]), int(output_resolution[1]))
    focal_px = float(projection_focal_px) if projection_focal_px is not None else _default_projection_focal_px(output_resolution)
    center = _pair(
        tuple(projection_center) if projection_center is not None else None,
        (output_resolution[0] / 2.0, output_resolution[1] / 2.0),
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
                "focal_px": focal_px,
                "center": center,
                "input_resolution": [int(left_resolution[0]), int(left_resolution[1])],
                "output_resolution": [output_resolution[0], output_resolution[1]],
            },
            "right": {
                "model": "cylindrical",
                "focal_px": focal_px,
                "center": center,
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
    geometry = artifact.get("geometry", {})
    if isinstance(geometry, dict):
        resolution = geometry.get("output_resolution")
        if isinstance(resolution, (list, tuple)) and len(resolution) >= 2:
            return _default_projection_focal_px((int(resolution[0]), int(resolution[1])))
    return _default_projection_focal_px((1920, 1080))
