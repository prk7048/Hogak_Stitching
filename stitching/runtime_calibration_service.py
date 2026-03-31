from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import threading
from typing import Any

import cv2
import numpy as np

from stitching.native_runtime_cli import add_native_runtime_args
from stitching.output_presets import OUTPUT_PRESETS
from stitching.project_defaults import DEFAULT_NATIVE_HOMOGRAPHY_PATH, default_output_standard
from stitching.runtime_gradio_ui import RuntimeUiSession


def _build_runtime_ui_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    add_native_runtime_args(parser)
    return parser.parse_args([])


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("x/y must be numeric preview coordinates") from exc


def _receiver_uri_from_target(target: str) -> str:
    text = str(target or "").strip()
    if not text or not text.startswith("udp://"):
        return text
    endpoint = text.split("?", 1)[0][len("udp://") :]
    host_port = endpoint[1:] if endpoint.startswith("@") else endpoint
    separator = host_port.rfind(":")
    if separator < 0:
        return text
    port = host_port[separator + 1 :].strip()
    return f"udp://@:{port}" if port else text


def _target_is_loopback_only(target: str) -> bool:
    text = str(target or "").strip()
    if not text.startswith("udp://"):
        return False
    endpoint = text.split("?", 1)[0][len("udp://") :]
    host_port = endpoint[1:] if endpoint.startswith("@") else endpoint
    separator = host_port.rfind(":")
    host = (host_port[:separator] if separator >= 0 else host_port).strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _route_for_step(step: str) -> str:
    mapping = {
        "start": "/calibration/start",
        "assisted-calibration": "/calibration/assisted",
        "calibration-review": "/calibration/review",
        "stitch-review": "/calibration/stitch-review",
    }
    return mapping.get(str(step), "/calibration/start")


@dataclass(slots=True)
class _CachedImage:
    version: int = 0
    jpeg_bytes: bytes | None = None


class CalibrationService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session = RuntimeUiSession(_build_runtime_ui_args())
        self._images: dict[str, _CachedImage] = {
            "left-preview": _CachedImage(),
            "right-preview": _CachedImage(),
            "review-preview": _CachedImage(),
            "review-inliers": _CachedImage(),
            "stitch-preview": _CachedImage(),
        }

    def _output_standard(self, requested: Any) -> str:
        text = str(requested or "").strip()
        return text if text in OUTPUT_PRESETS else default_output_standard()

    def _action_settings(self, body: dict[str, Any] | None = None) -> tuple[str, bool, bool]:
        body = body or {}
        return (
            self._output_standard(body.get("output_standard")),
            _bool_value(body.get("run_calibration_first"), True),
            _bool_value(body.get("open_vlc_low_latency"), False),
        )

    def _encode_rgb_frame(self, frame: np.ndarray | None) -> bytes | None:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size <= 0:
            return None
        if frame.ndim == 3 and frame.shape[2] == 3:
            encoded_input = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        else:
            encoded_input = frame
        ok, encoded = cv2.imencode(".jpg", encoded_input, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            return None
        return encoded.tobytes()

    def _set_image(self, name: str, frame: np.ndarray | None) -> None:
        cached = self._images.setdefault(name, _CachedImage())
        payload = self._encode_rgb_frame(frame)
        cached.version += 1
        cached.jpeg_bytes = payload

    def _image_url(self, name: str) -> str:
        cached = self._images.get(name)
        if cached is None or cached.jpeg_bytes is None:
            return ""
        return f"/api/calibration/images/{name}?v={cached.version}"

    def image(self, name: str) -> bytes | None:
        with self._lock:
            cached = self._images.get(name)
            return None if cached is None else cached.jpeg_bytes

    def _pair_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, (left_point, right_point) in enumerate(zip(self._session.left_manual_points, self._session.right_manual_points)):
            records.append(
                {
                    "index": index,
                    "label": f"{index + 1}: L({int(round(left_point[0]))},{int(round(left_point[1]))}) <-> R({int(round(right_point[0]))},{int(round(right_point[1]))})",
                    "left": [float(left_point[0]), float(left_point[1])],
                    "right": [float(right_point[0]), float(right_point[1])],
                    "selected": self._session.calibration_selected_pair_index == index,
                }
            )
        return records

    def _candidate_summary(self) -> dict[str, Any] | None:
        result = self._session.calibration_candidate_result
        if not isinstance(result, dict):
            return None
        output_resolution = result.get("output_resolution")
        return {
            "manual_points_count": int(result.get("manual_points_count") or 0),
            "inliers_count": int(result.get("inliers_count") or 0),
            "inlier_ratio": float(result.get("inlier_ratio") or 0.0),
            "mean_reprojection_error": float(result.get("mean_reprojection_error") or 0.0),
            "output_resolution": [int(value) for value in output_resolution] if isinstance(output_resolution, (list, tuple)) else [],
            "homography_reference": str(result.get("distortion_reference") or "raw"),
        }

    def _ensure_assisted_previews(self) -> None:
        left_preview, right_preview, _, _, _ = self._session.render_assisted_calibration_state()
        self._set_image("left-preview", left_preview)
        self._set_image("right-preview", right_preview)

    def _ensure_review_previews(self) -> None:
        stitched_preview, inlier_preview, _ = self._session.render_calibration_review()
        self._set_image("review-preview", stitched_preview)
        self._set_image("review-inliers", inlier_preview)

    def _ensure_stitch_preview(self) -> None:
        preview, _ = self._session.prepare_stitch_review()
        self._set_image("stitch-preview", preview)

    def _snapshot_locked(self) -> dict[str, Any]:
        current_step = str(self._session.current_step or "start")
        if current_step == "assisted-calibration":
            self._ensure_assisted_previews()
        elif current_step == "calibration-review":
            self._ensure_review_previews()
        elif current_step == "stitch-review":
            self._ensure_stitch_preview()

        homography = self._session.active_homography_summary()
        pending_point = self._session.calibration_pending_point
        self._session._refresh_output_display_targets()
        return {
            "current_step": current_step,
            "route": _route_for_step(current_step),
            "workflow": {
                "current_step": current_step,
                "manual_pair_count": self._session.calibration_pair_count(),
                "homography_reference": str(self._session.homography_reference or homography.get("distortion_reference") or "raw"),
                "show_inliers": bool(self._session.show_calibration_inliers),
                "bridge_mode": "react-single-surface",
            },
            "output_standard_options": sorted(OUTPUT_PRESETS.keys()),
            "start": {
                "output_standard": str(self._session.output_standard),
                "run_calibration_first": bool(self._session.run_calibration_first),
                "open_vlc_low_latency": bool(self._session.open_vlc_low_latency),
                "use_current_homography_enabled": bool(self._session.use_current_homography_enabled()),
                "homography": homography,
            },
            "assisted": {
                "left_image_url": self._image_url("left-preview"),
                "right_image_url": self._image_url("right-preview"),
                "pair_count": self._session.calibration_pair_count(),
                "pending_side": str(self._session.calibration_pending_side),
                "pending_left_point": None if pending_point is None else [float(pending_point[0]), float(pending_point[1])],
                "selected_pair_index": self._session.calibration_selected_pair_index,
                "pairs": self._pair_records(),
                "compute_enabled": self._session.calibration_pair_count() >= 4,
            },
            "review": {
                "preview_image_url": self._image_url("review-preview"),
                "inlier_image_url": self._image_url("review-inliers"),
                "candidate": self._candidate_summary(),
            },
            "stitch_review": {
                "preview_image_url": self._image_url("stitch-preview"),
                "probe_sender_target": str(self._session.probe_target_for_viewer or ""),
                "transmit_sender_target": str(self._session.transmit_target_for_display or ""),
                "probe_receive_uri": _receiver_uri_from_target(str(self._session.probe_target_for_viewer or "")),
                "transmit_receive_uri": _receiver_uri_from_target(str(self._session.transmit_target_for_display or "")),
                "probe_loopback_only": _target_is_loopback_only(str(self._session.probe_target_for_viewer or "")),
                "transmit_loopback_only": _target_is_loopback_only(str(self._session.transmit_target_for_display or "")),
            },
            "recent_events": list(self._session.recent_events),
        }

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def start_session(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        output_standard, run_calibration_first, open_vlc_low_latency = self._action_settings(body)
        with self._lock:
            self._session.begin_assisted_calibration_start(output_standard, run_calibration_first, open_vlc_low_latency, False)
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def refresh_frames(self) -> dict[str, Any]:
        with self._lock:
            self._session.refresh_calibration_frames()
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def add_pair(self, body: dict[str, Any]) -> dict[str, Any]:
        slot = str(body.get("slot") or "").strip().lower()
        if slot not in {"left", "right"}:
            raise ValueError("slot must be 'left' or 'right'")
        x = _float_value(body.get("x"))
        y = _float_value(body.get("y"))
        with self._lock:
            self._session.add_calibration_click(slot, SimpleNamespace(index=(x, y)))
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def select_pair(self, body: dict[str, Any]) -> dict[str, Any]:
        raw_index = body.get("index")
        if raw_index is None:
            raise ValueError("index is required")
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise ValueError("index must be an integer") from exc
        with self._lock:
            if index < 0 or index >= self._session.calibration_pair_count():
                raise ValueError("selected pair index is out of range")
            self._session.select_calibration_pair(str(index))
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def undo_pair(self) -> dict[str, Any]:
        with self._lock:
            if self._session.calibration_pair_count() <= 0 and self._session.calibration_pending_point is None:
                raise ValueError("no calibration pair is available to undo")
            self._session.undo_last_calibration_pair()
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def delete_pair(self) -> dict[str, Any]:
        with self._lock:
            index = self._session.calibration_selected_pair_index
            if index is None or index < 0 or index >= self._session.calibration_pair_count():
                raise ValueError("select a calibration pair before deleting")
            self._session.delete_selected_calibration_pair()
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def clear_pairs(self) -> dict[str, Any]:
        with self._lock:
            if self._session.calibration_pair_count() <= 0 and self._session.calibration_pending_point is None:
                raise ValueError("there are no calibration pairs to clear")
            self._session.clear_calibration_pairs()
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def compute_candidate(self) -> dict[str, Any]:
        with self._lock:
            self._session.compute_assisted_calibration_candidate()
            self._ensure_review_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def review(self) -> dict[str, Any]:
        with self._lock:
            if self._session.calibration_candidate_result is None:
                raise ValueError("no calibration candidate is available for review")
            self._ensure_review_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def accept_review(self) -> dict[str, Any]:
        with self._lock:
            if self._session.calibration_candidate_result is None:
                raise ValueError("no calibration candidate is available to accept")
            self._session.accept_calibration_review()
            self._ensure_stitch_preview()
            return {"ok": True, "state": self._snapshot_locked()}

    def cancel_review(self) -> dict[str, Any]:
        with self._lock:
            if self._session.calibration_candidate_result is None:
                raise ValueError("no calibration candidate is available to cancel")
            self._session.cancel_calibration_review()
            self._ensure_assisted_previews()
            return {"ok": True, "state": self._snapshot_locked()}

    def stitch_review(self) -> dict[str, Any]:
        with self._lock:
            if self._session.left.frame is None or self._session.right.frame is None:
                raise ValueError("stitch review requires representative frames")
            self._ensure_stitch_preview()
            return {"ok": True, "state": self._snapshot_locked()}

    def use_current(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        output_standard, run_calibration_first, open_vlc_low_latency = self._action_settings(body)
        with self._lock:
            self._session.prepare_start(output_standard, run_calibration_first, open_vlc_low_latency, False)
            if not self._session.use_current_homography_enabled():
                raise ValueError("current homography is not launch-ready; run assisted calibration first")
            self._ensure_stitch_preview()
            return {"ok": True, "state": self._snapshot_locked()}

    def artifact_paths(self) -> dict[str, str]:
        with self._lock:
            homography_file = Path(str(getattr(self._session.args, "homography_file", DEFAULT_NATIVE_HOMOGRAPHY_PATH) or DEFAULT_NATIVE_HOMOGRAPHY_PATH))
            return {
                "homography_file": str(homography_file.expanduser()),
            }
