from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
from html import escape
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Iterable

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from stitching.project_defaults import (
    DEFAULT_NATIVE_HOMOGRAPHY_PATH,
)
from stitching.runtime_contract import (
    geometry_rollout_metadata,
    normalize_schema_v2_reload_payload,
    public_runtime_state_surface,
)
from stitching.runtime_geometry_artifact import (
    load_runtime_geometry_artifact,
    runtime_geometry_artifact_path,
    runtime_geometry_model,
    runtime_geometry_residual_model,
)
from stitching.runtime_geometry_bakeoff import MeshRefreshService
from stitching.runtime_launcher import RuntimeLaunchSpec, query_gpu_direct_status
from stitching.runtime_site_config import load_runtime_site_config, repo_root, require_configured_rtsp_urls
from stitching.runtime_supervisor import RuntimeSupervisor


@dataclass(slots=True)
class RuntimePlan:
    geometry_artifact_path: Path
    homography_file: Path
    launch_spec: RuntimeLaunchSpec
    reload_payload: dict[str, Any]
    summary: dict[str, Any]


def _frontend_unavailable_html(frontend_path: Path) -> str:
    escaped_path = escape(str(frontend_path))
    return f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Hogak 운영 화면 번들이 아직 준비되지 않았습니다</title>
    <style>
      :root {{
        color-scheme: dark;
        font-family: "Aptos", "Segoe UI", sans-serif;
        background: linear-gradient(180deg, #0d131d 0%, #090c12 100%);
        color: #f4f7ff;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
      }}
      main {{
        width: min(920px, 100%);
        border-radius: 24px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(12,18,28,0.88);
        box-shadow: 0 26px 90px rgba(0,0,0,0.36);
        padding: 28px;
      }}
      h1 {{
        margin: 0 0 10px;
        font-size: clamp(2rem, 4vw, 3rem);
      }}
      p, li {{
        color: #c8d4ea;
        line-height: 1.65;
      }}
      code, pre {{
        font-family: "Consolas", "Cascadia Code", monospace;
      }}
      pre {{
        margin: 14px 0;
        padding: 14px 16px;
        border-radius: 16px;
        background: rgba(255,255,255,0.05);
        border: 1px solid rgba(255,255,255,0.06);
        overflow-x: auto;
      }}
      .note {{
        margin-top: 18px;
        padding: 14px 16px;
        border-radius: 16px;
        background: rgba(89,157,255,0.12);
        border: 1px solid rgba(89,157,255,0.18);
      }}
      a {{
        color: #a9c8ff;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Hogak 운영 화면 번들이 아직 준비되지 않았습니다</h1>
      <p>
        <code>operator-server</code> 는 실행 중이지만, React 번들을
        <code>{escaped_path}</code> 에서 찾지 못했습니다.
      </p>
      <p>프런트엔드를 한 번 빌드한 뒤 서버를 다시 시작하세요.</p>
      <pre>cd frontend
npm install
npm run build</pre>
      <p>빌드가 끝나면 아래 명령으로 다시 실행하면 됩니다.</p>
      <pre>python -m stitching.cli operator-server</pre>
      <p><code>HOGAK_FRONTEND_DIST_DIR</code> 환경변수로 다른 빌드 결과물을 지정할 수도 있습니다.</p>
      <div class="note">
        <strong>현재 백엔드 상태</strong>
        <ul>
          <li>제품용 public API 는 <code>/api/runtime/*</code> 와 <code>/api/artifacts/geometry*</code> 만 유지됩니다.</li>
          <li>Bakeoff, calibration, debug 경로는 public surface에서 제거되었고 내부 경로로만 유지됩니다.</li>
          <li>이 브랜치의 기본 truth 는 <code>virtual-center-rectilinear-mesh</code> 이며, launch-ready 확인 전에는 시작이 차단됩니다.</li>
          <li>React 번들이 준비되면 대시보드에서 <code>정렬 미리보기</code>, <code>시작</code>, <code>검증</code> 흐름만 노출됩니다.</li>
        </ul>
      </div>
    </main>
  </body>
</html>"""


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _merge_runtime_and_mesh_refresh_state(runtime_state: dict[str, Any], mesh_refresh_state: dict[str, Any]) -> dict[str, Any]:
    merged = dict(runtime_state or {})
    mesh_refresh = dict(mesh_refresh_state or {})
    runtime_active_artifact_path = str(
        merged.get("runtime_active_artifact_path")
        or merged.get("geometry_artifact_path")
        or mesh_refresh.get("runtime_active_artifact_path")
        or ""
    ).strip()
    runtime_active_model = str(
        merged.get("runtime_active_model")
        or merged.get("geometry_artifact_model")
        or merged.get("geometry_mode")
        or ""
    ).strip()
    runtime_active_residual_model = str(
        merged.get("runtime_active_residual_model")
        or merged.get("geometry_residual_model")
        or ""
    ).strip()

    if runtime_active_artifact_path and (not runtime_active_model or not runtime_active_residual_model):
        try:
            artifact = load_runtime_geometry_artifact(Path(runtime_active_artifact_path))
        except Exception:
            artifact = None
        if isinstance(artifact, dict):
            rollout = geometry_rollout_metadata(artifact)
            if not runtime_active_model:
                runtime_active_model = str(rollout.get("geometry_model") or runtime_geometry_model(artifact))
            if not runtime_active_residual_model:
                runtime_active_residual_model = str(
                    rollout.get("geometry_residual_model") or runtime_geometry_residual_model(artifact)
                )

    production_output_runtime_mode = str(merged.get("production_output_runtime_mode") or "").strip()
    input_runtime = str(
        merged.get("input_runtime")
        or merged.get("prepared_plan", {}).get("input_runtime")
        or ""
    ).strip().lower() if isinstance(merged.get("prepared_plan"), dict) else str(merged.get("input_runtime") or "").strip().lower()
    input_pipe_format = str(
        merged.get("input_pipe_format")
        or merged.get("prepared_plan", {}).get("input_pipe_format")
        or ""
    ).strip().lower() if isinstance(merged.get("prepared_plan"), dict) else str(merged.get("input_pipe_format") or "").strip().lower()
    input_path_mode = "unknown"
    if input_runtime == "ffmpeg-cuda":
        input_path_mode = "cuda-decode-cpu-staged"
    elif input_runtime:
        input_path_mode = f"{input_runtime}-cpu"

    output_path_mode = production_output_runtime_mode or "unknown"
    output_path_direct = output_path_mode == "native-nvenc-direct"
    output_path_bridge = output_path_mode in {"native-nvenc-bridge", "gpu-direct"}
    zero_copy_blockers: list[str] = []
    if input_path_mode == "cuda-decode-cpu-staged":
        zero_copy_blockers.append("reader transfers decoded frames to CPU before stitch input")
    elif input_path_mode != "unknown":
        zero_copy_blockers.append(f"input path is {input_path_mode}, not zero-copy")
    else:
        zero_copy_blockers.append("input path truth is unavailable")
    if not output_path_direct:
        if output_path_mode == "unknown":
            zero_copy_blockers.append("output path truth is unavailable")
        elif output_path_mode == "native-nvenc-unavailable":
            zero_copy_blockers.append("native nvenc output path is unavailable")
        else:
            zero_copy_blockers.append(f"output path is {output_path_mode}, not direct")
    zero_copy_ready = len(zero_copy_blockers) == 0
    zero_copy_reason = (
        "end-to-end zero-copy path is active"
        if zero_copy_ready
        else "; ".join(zero_copy_blockers)
    )
    gpu_path_mode = output_path_mode
    gpu_path_ready = zero_copy_ready
    preview_left_url = str(
        merged.get("preview_left_url")
        or merged.get("alignment_preview_left_url")
        or merged.get("start_preview_left_url")
        or ""
    ).strip()
    preview_right_url = str(
        merged.get("preview_right_url")
        or merged.get("alignment_preview_right_url")
        or merged.get("start_preview_right_url")
        or ""
    ).strip()
    preview_stitched_url = str(
        merged.get("preview_stitched_url")
        or merged.get("alignment_preview_stitched_url")
        or merged.get("start_preview_stitched_url")
        or ""
    ).strip()
    preview_ready = any((preview_left_url, preview_right_url, preview_stitched_url)) or bool(
        merged.get("alignment_preview_ready") or merged.get("start_preview_ready")
    )

    merged.update(
        {
            "runtime_active_model": runtime_active_model or "",
            "runtime_active_residual_model": runtime_active_residual_model or "",
            "runtime_active_artifact_path": runtime_active_artifact_path,
            "runtime_artifact_checksum": str(merged.get("geometry_artifact_checksum") or "").strip(),
            "runtime_launch_ready": bool(merged.get("launch_ready")),
            "runtime_launch_ready_reason": str(merged.get("launch_ready_reason") or "").strip(),
            "fallback_used": bool(merged.get("geometry_fallback_only")),
            "input_path_mode": input_path_mode,
            "gpu_path_mode": gpu_path_mode,
            "gpu_path_ready": gpu_path_ready,
            "output_path_mode": output_path_mode,
            "output_path_direct": output_path_direct,
            "output_path_bridge": output_path_bridge,
            "zero_copy_ready": zero_copy_ready,
            "zero_copy_reason": zero_copy_reason,
            "zero_copy_blockers": zero_copy_blockers,
            "preview_ready": preview_ready,
            "preview_left_url": preview_left_url,
            "preview_right_url": preview_right_url,
            "preview_stitched_url": preview_stitched_url,
        }
    )
    return merged


def _public_runtime_state(runtime_state: dict[str, Any], mesh_refresh_state: dict[str, Any]) -> dict[str, Any]:
    return public_runtime_state_surface(_merge_runtime_and_mesh_refresh_state(runtime_state, mesh_refresh_state))


def _public_runtime_response(payload: dict[str, Any], mesh_refresh_state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": True, "state": _public_runtime_state({}, mesh_refresh_state)}
    result = dict(payload)
    if isinstance(result.get("state"), dict):
        result["state"] = _public_runtime_state(result["state"], mesh_refresh_state)
    return result


def _internal_mesh_refresh(mesh_refresh: MeshRefreshService, body: dict[str, Any] | None = None) -> dict[str, Any]:
    result = mesh_refresh.run(body)
    if not isinstance(result, dict):
        raise ValueError("mesh-refresh did not return a JSON object")
    return result


class _CaptureOptionsEnv:
    def __init__(self, *, transport: str, timeout_sec: float) -> None:
        self._transport = str(transport or "tcp").strip() or "tcp"
        self._timeout_sec = max(1.0, float(timeout_sec))
        self._previous = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")

    def __enter__(self) -> None:
        timeout_us = max(100_000, int(self._timeout_sec * 1_000_000.0))
        capture_options = [f"rtsp_transport;{self._transport}", f"timeout;{timeout_us}"]
        if self._transport.lower() == "udp":
            capture_options.extend(["fifo_size;8388608", "overrun_nonfatal;1"])
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "|".join(capture_options)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous is None:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._previous


def _encode_jpeg(frame: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise ValueError("failed to encode jpeg preview")
    return encoded.tobytes()


def _capture_rtsp_frame(url: str, *, transport: str, timeout_sec: float, warmup_frames: int = 18) -> np.ndarray:
    if not str(url or "").strip():
        raise ValueError("rtsp url is empty")
    with _CaptureOptionsEnv(transport=transport, timeout_sec=timeout_sec):
        capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise ValueError(f"cannot open rtsp stream: {url}")
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    latest_frame: np.ndarray | None = None
    deadline = time.time() + max(2.0, float(timeout_sec))
    try:
        while time.time() < deadline and latest_frame is None:
            ok, frame = capture.read()
            if ok and frame is not None:
                latest_frame = frame
        while time.time() < deadline and latest_frame is not None and warmup_frames > 1:
            ok, frame = capture.read()
            if ok and frame is not None:
                latest_frame = frame
            warmup_frames -= 1
    finally:
        capture.release()
    if latest_frame is None:
        raise ValueError(f"failed to capture a frame from {url}")
    return latest_frame


def _artifact_canvas_size(artifact: dict[str, Any]) -> tuple[int, int]:
    canvas = artifact.get("canvas", {}) if isinstance(artifact.get("canvas"), dict) else {}
    width = int(canvas.get("width") or 0)
    height = int(canvas.get("height") or 0)
    if width > 0 and height > 0:
        return width, height
    geometry = artifact.get("geometry", {}) if isinstance(artifact.get("geometry"), dict) else {}
    output_resolution = geometry.get("output_resolution")
    if isinstance(output_resolution, (list, tuple)) and len(output_resolution) >= 2:
        return max(1, int(output_resolution[0])), max(1, int(output_resolution[1]))
    return 1920, 1080


def _build_rectilinear_remap(side_projection: dict[str, Any], *, output_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    output_width = max(1, int(output_size[0]))
    output_height = max(1, int(output_size[1]))
    src_focal = float(side_projection.get("focal_px") or 1.0)
    src_center = side_projection.get("center") if isinstance(side_projection.get("center"), (list, tuple)) else [0.0, 0.0]
    virtual_focal = float(side_projection.get("virtual_focal_px") or side_projection.get("focal_px") or 1.0)
    virtual_center = side_projection.get("virtual_center") if isinstance(side_projection.get("virtual_center"), (list, tuple)) else [output_width / 2.0, output_height / 2.0]
    rotation_raw = side_projection.get("virtual_to_source_rotation")
    rotation = np.eye(3, dtype=np.float64)
    if isinstance(rotation_raw, (list, tuple, np.ndarray)):
        try:
            rotation = np.asarray(rotation_raw, dtype=np.float64).reshape(3, 3)
        except Exception:
            rotation = np.eye(3, dtype=np.float64)

    grid_x, grid_y = np.meshgrid(
        np.arange(output_width, dtype=np.float64),
        np.arange(output_height, dtype=np.float64),
    )
    virtual_dirs = np.stack(
        [
            (grid_x - float(virtual_center[0])) / max(1.0, float(virtual_focal)),
            (grid_y - float(virtual_center[1])) / max(1.0, float(virtual_focal)),
            np.ones((output_height, output_width), dtype=np.float64),
        ],
        axis=-1,
    )
    source_dirs = np.einsum("ij,hwj->hwi", rotation, virtual_dirs)
    z = source_dirs[..., 2]
    valid = np.isfinite(z) & (z > 1e-6)
    map_x = np.full((output_height, output_width), -1.0, dtype=np.float32)
    map_y = np.full((output_height, output_width), -1.0, dtype=np.float32)
    valid_x = (float(src_focal) * source_dirs[..., 0] / z) + float(src_center[0])
    valid_y = (float(src_focal) * source_dirs[..., 1] / z) + float(src_center[1])
    map_x[valid] = valid_x[valid].astype(np.float32)
    map_y[valid] = valid_y[valid].astype(np.float32)
    return map_x, map_y


def _apply_alignment_transform(frame: np.ndarray, alignment: dict[str, Any]) -> np.ndarray:
    matrix = alignment.get("matrix")
    if matrix is None:
        return frame
    try:
        array = np.asarray(matrix, dtype=np.float64)
    except Exception:
        return frame
    output_size = (int(frame.shape[1]), int(frame.shape[0]))
    if array.size == 6:
        affine = array.reshape(2, 3)
        return cv2.warpAffine(
            frame,
            affine.astype(np.float32),
            output_size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
    if array.size == 9:
        homography = array.reshape(3, 3)
        return cv2.warpPerspective(
            frame,
            homography.astype(np.float32),
            output_size,
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )
    return frame


def _compose_feather_preview(left_frame: np.ndarray, right_frame: np.ndarray) -> np.ndarray:
    left = np.asarray(left_frame, dtype=np.float32)
    right = np.asarray(right_frame, dtype=np.float32)
    output = np.zeros_like(left)
    left_mask = np.any(left_frame > 0, axis=2)
    right_mask = np.any(right_frame > 0, axis=2)
    overlap = left_mask & right_mask

    output[left_mask & ~right_mask] = left[left_mask & ~right_mask]
    output[right_mask & ~left_mask] = right[right_mask & ~left_mask]

    if np.any(overlap):
        overlap_columns = np.where(np.any(overlap, axis=0))[0]
        if overlap_columns.size >= 2:
            start = int(overlap_columns[0])
            end = int(overlap_columns[-1])
            width = max(1, end - start)
            weights = np.zeros(left.shape[:2], dtype=np.float32)
            band = np.clip((np.arange(left.shape[1], dtype=np.float32) - start) / float(width), 0.0, 1.0)
            weights[:, :] = band[None, :]
            weights = np.where(overlap, weights, 0.0)
        else:
            weights = np.where(overlap, 0.5, 0.0).astype(np.float32)
        output[overlap] = (
            left[overlap] * (1.0 - weights[overlap, None]) + right[overlap] * weights[overlap, None]
        )
    return np.clip(output, 0.0, 255.0).astype(np.uint8)


def _render_virtual_alignment_previews(
    artifact: dict[str, Any],
    *,
    left_frame: np.ndarray,
    right_frame: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if runtime_geometry_model(artifact) != "virtual-center-rectilinear":
        raise ValueError("start preview requires a virtual-center-rectilinear geometry artifact")

    projection = artifact.get("projection", {}) if isinstance(artifact.get("projection"), dict) else {}
    left_projection = projection.get("left", {}) if isinstance(projection.get("left"), dict) else {}
    right_projection = projection.get("right", {}) if isinstance(projection.get("right"), dict) else {}
    alignment = artifact.get("alignment", {}) if isinstance(artifact.get("alignment"), dict) else {}
    output_size = _artifact_canvas_size(artifact)

    expected_left_resolution = left_projection.get("input_resolution")
    if isinstance(expected_left_resolution, (list, tuple)) and len(expected_left_resolution) >= 2:
        target_left = (max(1, int(expected_left_resolution[0])), max(1, int(expected_left_resolution[1])))
        if (left_frame.shape[1], left_frame.shape[0]) != target_left:
            left_frame = cv2.resize(left_frame, target_left, interpolation=cv2.INTER_LINEAR)

    expected_right_resolution = right_projection.get("input_resolution")
    if isinstance(expected_right_resolution, (list, tuple)) and len(expected_right_resolution) >= 2:
        target_right = (max(1, int(expected_right_resolution[0])), max(1, int(expected_right_resolution[1])))
        if (right_frame.shape[1], right_frame.shape[0]) != target_right:
            right_frame = cv2.resize(right_frame, target_right, interpolation=cv2.INTER_LINEAR)

    left_map_x, left_map_y = _build_rectilinear_remap(left_projection, output_size=output_size)
    right_map_x, right_map_y = _build_rectilinear_remap(right_projection, output_size=output_size)

    left_projected = cv2.remap(
        left_frame,
        left_map_x,
        left_map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    right_projected = cv2.remap(
        right_frame,
        right_map_x,
        right_map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    right_projected = _apply_alignment_transform(right_projected, alignment)
    stitched = _compose_feather_preview(left_projected, right_projected)
    return left_projected, right_projected, stitched


class RuntimeService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._event_condition = threading.Condition(self._lock)
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._prepared_plan: RuntimePlan | None = None
        self._supervisor: RuntimeSupervisor | None = None
        self._event_pump_thread: threading.Thread | None = None
        self._event_pump_stop = threading.Event()
        self._latest_metrics: dict[str, Any] = {}
        self._latest_hello: dict[str, Any] = {}
        self._latest_validation: dict[str, Any] = {}
        self._last_error = ""
        self._last_status = "idle"
        self._gpu_only_blockers: list[str] = []
        self._start_preview_pending_confirmation = False
        self._start_preview_left_jpeg: bytes | None = None
        self._start_preview_right_jpeg: bytes | None = None
        self._start_preview_stitched_jpeg: bytes | None = None

    def _record_event(self, event_type: str, payload: dict[str, Any] | None = None, *, seq: int = 0) -> dict[str, Any]:
        record = {
            "id": self._next_event_id,
            "seq": int(seq),
            "type": str(event_type),
            "timestamp_sec": time.time(),
            "payload": payload or {},
        }
        self._next_event_id += 1
        self._events.append(record)
        self._event_condition.notify_all()
        return record

    @staticmethod
    def _receive_uri_from_target(target: str) -> str:
        text = str(target or "").strip()
        if not text.startswith("udp://"):
            return text
        endpoint = text.split("?", 1)[0][len("udp://") :]
        host_port = endpoint[1:] if endpoint.startswith("@") else endpoint
        separator = host_port.rfind(":")
        if separator < 0:
            return text
        port = host_port[separator + 1 :].strip()
        if not port:
            return text
        return f"udp://@:{port}"

    def _clear_start_preview_locked(self) -> None:
        self._start_preview_pending_confirmation = False
        self._start_preview_left_jpeg = None
        self._start_preview_right_jpeg = None
        self._start_preview_stitched_jpeg = None

    def _start_preview_url(self, name: str) -> str:
        return f"/api/runtime/preview-align/assets/{name}.jpg"

    def _render_start_preview_locked(self, plan: RuntimePlan) -> None:
        artifact = load_runtime_geometry_artifact(plan.geometry_artifact_path)
        left_frame = _capture_rtsp_frame(
            plan.launch_spec.left_rtsp,
            transport=plan.launch_spec.transport,
            timeout_sec=plan.launch_spec.timeout_sec,
        )
        right_frame = _capture_rtsp_frame(
            plan.launch_spec.right_rtsp,
            transport=plan.launch_spec.transport,
            timeout_sec=plan.launch_spec.timeout_sec,
        )
        left_preview, right_preview, stitched_preview = _render_virtual_alignment_previews(
            artifact,
            left_frame=left_frame,
            right_frame=right_frame,
        )
        self._start_preview_left_jpeg = _encode_jpeg(left_preview)
        self._start_preview_right_jpeg = _encode_jpeg(right_preview)
        self._start_preview_stitched_jpeg = _encode_jpeg(stitched_preview)
        self._start_preview_pending_confirmation = False
        self._record_event(
            "status",
            {
                "status": "preview_ready",
                "geometry_artifact_path": str(plan.geometry_artifact_path),
            },
        )

    def _ensure_default_geometry_artifact(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        request = request or {}
        explicit_artifact_path = self._resolve_requested_artifact_path(request)
        if explicit_artifact_path is not None:
            if not explicit_artifact_path.exists():
                raise ValueError(f"requested geometry artifact does not exist: {explicit_artifact_path}")
            artifact = load_runtime_geometry_artifact(explicit_artifact_path)
            rollout = geometry_rollout_metadata(artifact)
            return {
                "calibrated": False,
                "artifact_path": str(explicit_artifact_path),
                "geometry_model": rollout["geometry_model"],
                "launch_ready": bool(rollout["launch_ready"]),
                "message": "explicit geometry artifact selected",
            }

        site_config = load_runtime_site_config()
        paths = site_config.get("paths", {})
        homography_file = Path(
            str(request.get("homography_file") or paths.get("homography_file") or DEFAULT_NATIVE_HOMOGRAPHY_PATH)
        ).expanduser()
        geometry_artifact = runtime_geometry_artifact_path(homography_file)

        if geometry_artifact.exists():
            artifact = load_runtime_geometry_artifact(geometry_artifact)
            rollout = geometry_rollout_metadata(artifact)
            if bool(rollout["launch_ready"]) and bool(rollout["geometry_operator_visible"]):
                return {
                    "calibrated": False,
                    "artifact_path": str(geometry_artifact),
                    "geometry_model": rollout["geometry_model"],
                    "launch_ready": True,
                    "message": "existing launch-ready geometry artifact reused",
                }
            if bool(rollout["launch_ready"]) and not bool(rollout["geometry_operator_visible"]):
                raise ValueError(
                    "default runtime geometry artifact resolves to an internal fallback model. "
                    "Promote a product mesh artifact first, or use an explicit geometry.artifact_path for internal rollback."
                )

        raise ValueError(
            "launch-ready runtime geometry artifact가 없습니다. "
            "내부 mesh-refresh를 먼저 실행해 active mesh artifact를 다시 생성해 주세요."
        )

    def _build_plan(self, request: dict[str, Any] | None = None) -> RuntimePlan:
        site_config = load_runtime_site_config()
        request = request or {}
        cameras = site_config.get("cameras", {})
        paths = site_config.get("paths", {})
        runtime = site_config.get("runtime", {})
        canonical_request: dict[str, Any] | None = None
        has_full_schema_v2_payload = all(
            key in request for key in ("inputs", "geometry", "timing", "outputs", "runtime")
        )
        if has_full_schema_v2_payload:
            canonical_request = normalize_schema_v2_reload_payload(request)
        request_inputs = canonical_request["inputs"] if canonical_request is not None else (
            request.get("inputs") if isinstance(request.get("inputs"), dict) else {}
        )
        request_geometry = canonical_request["geometry"] if canonical_request is not None else (
            request.get("geometry") if isinstance(request.get("geometry"), dict) else {}
        )
        request_timing = canonical_request["timing"] if canonical_request is not None else (
            request.get("timing") if isinstance(request.get("timing"), dict) else {}
        )
        request_outputs = canonical_request["outputs"] if canonical_request is not None else (
            request.get("outputs") if isinstance(request.get("outputs"), dict) else {}
        )
        request_runtime = canonical_request["runtime"] if canonical_request is not None else (
            request.get("runtime") if isinstance(request.get("runtime"), dict) else {}
        )
        request_probe = request_outputs.get("probe") if isinstance(request_outputs.get("probe"), dict) else {}
        request_transmit = request_outputs.get("transmit") if isinstance(request_outputs.get("transmit"), dict) else {}

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
        if not left_rtsp or not right_rtsp:
            raise ValueError("left_rtsp and right_rtsp must be configured")
        require_configured_rtsp_urls(left_rtsp, right_rtsp, context="runtime backend")

        homography_file = Path(str(request.get("homography_file") or paths.get("homography_file") or "data/runtime_homography.json")).expanduser()
        geometry_artifact = runtime_geometry_artifact_path(homography_file)
        geometry_artifact_value = request.get("geometry_artifact_path") or request_geometry.get("artifact_path")
        if geometry_artifact_value:
            geometry_artifact = Path(str(geometry_artifact_value)).expanduser()

        resolved_homography = str(homography_file)
        artifact: dict[str, Any] | None = None
        if geometry_artifact.exists():
            artifact = load_runtime_geometry_artifact(geometry_artifact)
            source = artifact.get("source", {})
            if isinstance(source, dict):
                artifact_homography = str(source.get("homography_file") or "").strip()
                if artifact_homography:
                    resolved_homography = artifact_homography

        base_spec = RuntimeLaunchSpec()
        probe = runtime.get("probe", {}) if isinstance(runtime.get("probe"), dict) else {}
        transmit = runtime.get("transmit", {}) if isinstance(runtime.get("transmit"), dict) else {}

        probe_runtime = str(
            request_probe.get("runtime")
            or probe.get("runtime")
            or base_spec.output_runtime
            or "ffmpeg"
        ).strip()
        probe_target = str(request_probe.get("target") or probe.get("target") or "").strip()
        transmit_runtime = str(
            request_transmit.get("runtime")
            or transmit.get("runtime")
            or base_spec.production_output_runtime
            or "gpu-direct"
        ).strip()
        transmit_target = str(request_transmit.get("target") or transmit.get("target") or "").strip()
        cadence_fps = float(
            request_probe.get("fps")
            or request_transmit.get("fps")
            or runtime.get("output_cadence_fps")
            or 30.0
        )
        heartbeat_sec = float(
            request_runtime.get("benchmark_log_interval_sec")
            or runtime.get("benchmark_log_interval_sec")
            or runtime.get("status_interval_sec")
            or 5.0
        )

        launch_spec = RuntimeLaunchSpec(
            emit_hello=True,
            once=False,
            heartbeat_ms=max(250, int(round(heartbeat_sec * 1000.0))),
            left_rtsp=left_rtsp,
            right_rtsp=right_rtsp,
            input_runtime=str(request_runtime.get("input_runtime") or runtime.get("input_runtime") or base_spec.input_runtime),
            input_pipe_format=str(
                request_runtime.get("input_pipe_format")
                or runtime.get("input_pipe_format")
                or base_spec.input_pipe_format
            ),
            ffmpeg_bin=str(request_runtime.get("ffmpeg_bin") or runtime.get("ffmpeg_bin") or ""),
            homography_file=resolved_homography,
            transport=str(
                request_runtime.get("rtsp_transport")
                or runtime.get("rtsp_transport")
                or (request_inputs.get("left") or {}).get("transport")
                or base_spec.transport
            ),
            input_buffer_frames=int(
                request_runtime.get("input_buffer_frames")
                or runtime.get("input_buffer_frames")
                or (request_inputs.get("left") or {}).get("buffer_frames")
                or base_spec.input_buffer_frames
            ),
            timeout_sec=float(
                request_runtime.get("rtsp_timeout_sec")
                or runtime.get("rtsp_timeout_sec")
                or (request_inputs.get("left") or {}).get("timeout_sec")
                or base_spec.timeout_sec
            ),
            reconnect_cooldown_sec=float(
                request_runtime.get("reconnect_cooldown_sec")
                or runtime.get("reconnect_cooldown_sec")
                or (request_inputs.get("left") or {}).get("reconnect_cooldown_sec")
                or base_spec.reconnect_cooldown_sec
            ),
            output_runtime=probe_runtime,
            output_profile="inspection",
            output_target=probe_target,
            output_codec=str(request_probe.get("codec") or probe.get("codec") or base_spec.output_codec),
            output_bitrate=str(request_probe.get("bitrate") or probe.get("bitrate") or base_spec.output_bitrate),
            output_preset=str(request_probe.get("preset") or probe.get("preset") or base_spec.output_preset),
            output_muxer=str(request_probe.get("muxer") or probe.get("muxer") or ""),
            output_width=int(request_probe.get("width") or probe.get("width") or 0),
            output_height=int(request_probe.get("height") or probe.get("height") or 0),
            output_fps=float(request_probe.get("fps") or probe.get("fps") or cadence_fps),
            output_debug_overlay=bool(
                request_probe.get("debug_overlay")
                if "debug_overlay" in request_probe
                else (probe.get("debug_overlay") or False)
            ),
            production_output_runtime=transmit_runtime,
            production_output_profile="production-compatible",
            production_output_target=transmit_target,
            production_output_codec=str(
                request_transmit.get("codec") or transmit.get("codec") or base_spec.production_output_codec
            ),
            production_output_bitrate=str(
                request_transmit.get("bitrate") or transmit.get("bitrate") or base_spec.production_output_bitrate
            ),
            production_output_preset=str(
                request_transmit.get("preset") or transmit.get("preset") or base_spec.production_output_preset
            ),
            production_output_muxer=str(request_transmit.get("muxer") or transmit.get("muxer") or ""),
            production_output_width=int(
                request_transmit.get("width") or transmit.get("width") or base_spec.production_output_width
            ),
            production_output_height=int(
                request_transmit.get("height") or transmit.get("height") or base_spec.production_output_height
            ),
            production_output_fps=float(request_transmit.get("fps") or transmit.get("fps") or cadence_fps),
            production_output_debug_overlay=bool(
                request_transmit.get("debug_overlay")
                if "debug_overlay" in request_transmit
                else (transmit.get("debug_overlay") if "debug_overlay" in transmit else base_spec.production_output_debug_overlay)
            ),
            sync_pair_mode=str(request_timing.get("pair_mode") or runtime.get("sync_pair_mode") or "service"),
            allow_frame_reuse=bool(
                request_timing.get("allow_frame_reuse")
                if "allow_frame_reuse" in request_timing
                else runtime.get("allow_frame_reuse")
                if "allow_frame_reuse" in runtime
                else False
            ),
            pair_reuse_max_age_ms=float(
                request_timing.get("reuse_max_age_ms") or runtime.get("pair_reuse_max_age_ms") or 90.0
            ),
            pair_reuse_max_consecutive=int(
                request_timing.get("reuse_max_consecutive") or runtime.get("pair_reuse_max_consecutive") or 2
            ),
            sync_match_max_delta_ms=float(
                request_timing.get("match_max_delta_ms") or runtime.get("sync_match_max_delta_ms") or 35.0
            ),
            sync_time_source=str(request_timing.get("time_source") or runtime.get("sync_time_source") or "pts-offset-auto"),
            sync_manual_offset_ms=float(
                request_timing.get("manual_offset_ms") or runtime.get("sync_manual_offset_ms") or 0.0
            ),
            sync_auto_offset_window_sec=float(
                request_timing.get("auto_offset_window_sec") or runtime.get("sync_auto_offset_window_sec") or 4.0
            ),
            sync_auto_offset_max_search_ms=float(
                request_timing.get("auto_offset_max_search_ms") or runtime.get("sync_auto_offset_max_search_ms") or 500.0
            ),
            sync_recalibration_interval_sec=float(
                request_timing.get("recalibration_interval_sec") or runtime.get("sync_recalibration_interval_sec") or 60.0
            ),
            sync_recalibration_trigger_skew_ms=float(
                request_timing.get("recalibration_trigger_skew_ms") or runtime.get("sync_recalibration_trigger_skew_ms") or 45.0
            ),
            sync_recalibration_trigger_wait_ratio=float(
                request_timing.get("recalibration_trigger_wait_ratio")
                or runtime.get("sync_recalibration_trigger_wait_ratio")
                or 0.5
            ),
            sync_auto_offset_confidence_min=float(
                request_timing.get("auto_offset_confidence_min") or runtime.get("sync_auto_offset_confidence_min") or 0.85
            ),
            stitch_output_scale=float(
                request_runtime.get("stitch_output_scale") or runtime.get("stitch_output_scale") or 1.0
            ),
            stitch_every_n=int(request_runtime.get("stitch_every_n") or runtime.get("stitch_every_n") or 1),
            gpu_mode=str(request_runtime.get("gpu_mode") or runtime.get("gpu_mode") or "on"),
            gpu_device=int(request_runtime.get("gpu_device") or runtime.get("gpu_device") or 0),
            headless_benchmark=bool(
                request_runtime.get("headless_benchmark")
                if "headless_benchmark" in request_runtime
                else runtime.get("headless_benchmark")
                if "headless_benchmark" in runtime
                else False
            ),
        )

        reload_payload = normalize_schema_v2_reload_payload(
            {
                "inputs": {
                    "left": {
                        "url": left_rtsp,
                        "transport": str(
                            request_runtime.get("rtsp_transport")
                            or runtime.get("rtsp_transport")
                            or (request_inputs.get("left") or {}).get("transport")
                            or base_spec.transport
                        ),
                        "timeout_sec": float(
                            request_runtime.get("rtsp_timeout_sec")
                            or runtime.get("rtsp_timeout_sec")
                            or (request_inputs.get("left") or {}).get("timeout_sec")
                            or base_spec.timeout_sec
                        ),
                        "reconnect_cooldown_sec": float(
                            request_runtime.get("reconnect_cooldown_sec")
                            or runtime.get("reconnect_cooldown_sec")
                            or (request_inputs.get("left") or {}).get("reconnect_cooldown_sec")
                            or base_spec.reconnect_cooldown_sec
                        ),
                        "buffer_frames": int(
                            request_runtime.get("input_buffer_frames")
                            or runtime.get("input_buffer_frames")
                            or (request_inputs.get("left") or {}).get("buffer_frames")
                            or base_spec.input_buffer_frames
                        ),
                    },
                    "right": {
                        "url": right_rtsp,
                        "transport": str(
                            request_runtime.get("rtsp_transport")
                            or runtime.get("rtsp_transport")
                            or (request_inputs.get("right") or {}).get("transport")
                            or base_spec.transport
                        ),
                        "timeout_sec": float(
                            request_runtime.get("rtsp_timeout_sec")
                            or runtime.get("rtsp_timeout_sec")
                            or (request_inputs.get("right") or {}).get("timeout_sec")
                            or base_spec.timeout_sec
                        ),
                        "reconnect_cooldown_sec": float(
                            request_runtime.get("reconnect_cooldown_sec")
                            or runtime.get("reconnect_cooldown_sec")
                            or (request_inputs.get("right") or {}).get("reconnect_cooldown_sec")
                            or base_spec.reconnect_cooldown_sec
                        ),
                        "buffer_frames": int(
                            request_runtime.get("input_buffer_frames")
                            or runtime.get("input_buffer_frames")
                            or (request_inputs.get("right") or {}).get("buffer_frames")
                            or base_spec.input_buffer_frames
                        ),
                    },
                },
                "geometry": {
                    "artifact_path": str(geometry_artifact),
                },
                "timing": {
                    "pair_mode": str(request_timing.get("pair_mode") or runtime.get("sync_pair_mode") or "service"),
                    "allow_frame_reuse": bool(
                        request_timing.get("allow_frame_reuse")
                        if "allow_frame_reuse" in request_timing
                        else runtime.get("allow_frame_reuse")
                        if "allow_frame_reuse" in runtime
                        else False
                    ),
                    "reuse_max_age_ms": float(
                        request_timing.get("reuse_max_age_ms") or runtime.get("pair_reuse_max_age_ms") or 90.0
                    ),
                    "reuse_max_consecutive": int(
                        request_timing.get("reuse_max_consecutive") or runtime.get("pair_reuse_max_consecutive") or 2
                    ),
                    "match_max_delta_ms": float(
                        request_timing.get("match_max_delta_ms") or runtime.get("sync_match_max_delta_ms") or 35.0
                    ),
                    "time_source": str(request_timing.get("time_source") or runtime.get("sync_time_source") or "pts-offset-auto"),
                    "manual_offset_ms": float(
                        request_timing.get("manual_offset_ms") or runtime.get("sync_manual_offset_ms") or 0.0
                    ),
                    "auto_offset_window_sec": float(
                        request_timing.get("auto_offset_window_sec") or runtime.get("sync_auto_offset_window_sec") or 4.0
                    ),
                    "auto_offset_max_search_ms": float(
                        request_timing.get("auto_offset_max_search_ms") or runtime.get("sync_auto_offset_max_search_ms") or 500.0
                    ),
                    "recalibration_interval_sec": float(
                        request_timing.get("recalibration_interval_sec")
                        or runtime.get("sync_recalibration_interval_sec")
                        or 60.0
                    ),
                    "recalibration_trigger_skew_ms": float(
                        request_timing.get("recalibration_trigger_skew_ms")
                        or runtime.get("sync_recalibration_trigger_skew_ms")
                        or 45.0
                    ),
                    "recalibration_trigger_wait_ratio": float(
                        request_timing.get("recalibration_trigger_wait_ratio")
                        or runtime.get("sync_recalibration_trigger_wait_ratio")
                        or 0.5
                    ),
                    "auto_offset_confidence_min": float(
                        request_timing.get("auto_offset_confidence_min")
                        or runtime.get("sync_auto_offset_confidence_min")
                        or 0.85
                    ),
                },
                "outputs": {
                    "probe": {
                        "runtime": probe_runtime,
                        "target": probe_target,
                        "codec": str(request_probe.get("codec") or probe.get("codec") or base_spec.output_codec),
                        "bitrate": str(request_probe.get("bitrate") or probe.get("bitrate") or base_spec.output_bitrate),
                        "preset": str(request_probe.get("preset") or probe.get("preset") or base_spec.output_preset),
                        "muxer": str(request_probe.get("muxer") or probe.get("muxer") or ""),
                        "width": int(request_probe.get("width") or probe.get("width") or 0),
                        "height": int(request_probe.get("height") or probe.get("height") or 0),
                        "fps": float(request_probe.get("fps") or probe.get("fps") or cadence_fps),
                        "debug_overlay": bool(
                            request_probe.get("debug_overlay")
                            if "debug_overlay" in request_probe
                            else (probe.get("debug_overlay") or False)
                        ),
                    },
                    "transmit": {
                        "runtime": transmit_runtime,
                        "target": transmit_target,
                        "codec": str(
                            request_transmit.get("codec") or transmit.get("codec") or base_spec.production_output_codec
                        ),
                        "bitrate": str(
                            request_transmit.get("bitrate")
                            or transmit.get("bitrate")
                            or base_spec.production_output_bitrate
                        ),
                        "preset": str(
                            request_transmit.get("preset")
                            or transmit.get("preset")
                            or base_spec.production_output_preset
                        ),
                        "muxer": str(request_transmit.get("muxer") or transmit.get("muxer") or ""),
                        "width": int(
                            request_transmit.get("width") or transmit.get("width") or base_spec.production_output_width
                        ),
                        "height": int(
                            request_transmit.get("height") or transmit.get("height") or base_spec.production_output_height
                        ),
                        "fps": float(request_transmit.get("fps") or transmit.get("fps") or cadence_fps),
                        "debug_overlay": bool(
                            request_transmit.get("debug_overlay")
                            if "debug_overlay" in request_transmit
                            else (
                                transmit.get("debug_overlay")
                                if "debug_overlay" in transmit
                                else base_spec.production_output_debug_overlay
                            )
                        ),
                    },
                },
                "runtime": {
                    "input_runtime": str(request_runtime.get("input_runtime") or runtime.get("input_runtime") or base_spec.input_runtime),
                    "ffmpeg_bin": str(request_runtime.get("ffmpeg_bin") or runtime.get("ffmpeg_bin") or ""),
                    "gpu_mode": str(request_runtime.get("gpu_mode") or runtime.get("gpu_mode") or "on"),
                    "gpu_device": int(request_runtime.get("gpu_device") or runtime.get("gpu_device") or 0),
                    "stitch_output_scale": float(
                        request_runtime.get("stitch_output_scale") or runtime.get("stitch_output_scale") or 1.0
                    ),
                    "stitch_every_n": int(request_runtime.get("stitch_every_n") or runtime.get("stitch_every_n") or 1),
                    "benchmark_log_interval_sec": float(
                        request_runtime.get("benchmark_log_interval_sec")
                        or runtime.get("benchmark_log_interval_sec")
                        or runtime.get("status_interval_sec")
                        or 5.0
                    ),
                    "headless_benchmark": bool(
                        request_runtime.get("headless_benchmark")
                        if "headless_benchmark" in request_runtime
                        else runtime.get("headless_benchmark")
                        if "headless_benchmark" in runtime
                        else False
                    ),
                },
            }
        )
        rollout = geometry_rollout_metadata(artifact or {})
        summary = {
            "geometry_artifact_path": str(geometry_artifact),
            "homography_file": str(homography_file),
            "left_rtsp": left_rtsp,
            "right_rtsp": right_rtsp,
            "probe_target": probe_target,
            "transmit_target": transmit_target,
            "output_runtime_mode": probe_runtime,
            "production_output_runtime_mode": transmit_runtime,
            "sync_pair_mode": str(launch_spec.sync_pair_mode),
            "runtime_schema_version": 2,
            "gpu_only_mode": str(launch_spec.gpu_mode).strip().lower() == "only",
            "geometry_artifact_model": rollout["geometry_model"],
            "geometry_residual_model": rollout["geometry_residual_model"],
            "geometry_rollout_status": rollout["geometry_rollout_status"],
            "geometry_operator_visible": bool(rollout["geometry_operator_visible"]),
            "geometry_fallback_only": bool(rollout["geometry_fallback_only"]),
            "geometry_compat_only": bool(rollout["geometry_compat_only"]),
            "launch_ready": bool(rollout["launch_ready"]),
            "launch_ready_reason": str(rollout["launch_ready_reason"]),
        }
        return RuntimePlan(
            geometry_artifact_path=geometry_artifact,
            homography_file=homography_file,
            launch_spec=launch_spec,
            reload_payload=reload_payload,
            summary=summary,
        )

    @staticmethod
    def _resolve_requested_artifact_path(request: dict[str, Any] | None = None) -> Path | None:
        request = request or {}
        nested_geometry = request.get("geometry") if isinstance(request.get("geometry"), dict) else {}
        artifact_value = request.get("geometry_artifact_path") or nested_geometry.get("artifact_path")
        if not artifact_value:
            return None
        return Path(str(artifact_value)).expanduser()

    def _snapshot_locked(self) -> dict[str, Any]:
        supervisor = self._supervisor
        process = supervisor.process if supervisor is not None else None
        running = process is not None and process.poll() is None
        snapshot = {
            "running": running,
            "prepared": self._prepared_plan is not None,
            "runtime_pid": None if process is None else process.pid,
            "runtime_returncode": None if process is None else process.returncode,
            "status": self._last_status,
            "last_error": self._last_error,
            "prepared_plan": None if self._prepared_plan is None else self._prepared_plan.summary,
            "latest_hello": self._latest_hello,
            "latest_metrics": self._latest_metrics,
            "latest_validation": self._latest_validation,
            "event_count": len(self._events),
            "gpu_only_mode": True,
            "gpu_only_ready": len(self._gpu_only_blockers) == 0,
            "gpu_only_blockers": list(self._gpu_only_blockers),
            "start_preview_ready": any(
                item is not None
                for item in (
                    self._start_preview_left_jpeg,
                    self._start_preview_right_jpeg,
                    self._start_preview_stitched_jpeg,
                )
            ),
            "start_preview_pending_confirmation": self._start_preview_pending_confirmation,
            "start_preview_left_url": self._start_preview_url("left") if self._start_preview_left_jpeg is not None else "",
            "start_preview_right_url": self._start_preview_url("right") if self._start_preview_right_jpeg is not None else "",
            "start_preview_stitched_url": self._start_preview_url("stitched") if self._start_preview_stitched_jpeg is not None else "",
            "alignment_preview_ready": any(
                item is not None
                for item in (
                    self._start_preview_left_jpeg,
                    self._start_preview_right_jpeg,
                    self._start_preview_stitched_jpeg,
                )
            ),
            "alignment_preview_left_url": self._start_preview_url("left") if self._start_preview_left_jpeg is not None else "",
            "alignment_preview_right_url": self._start_preview_url("right") if self._start_preview_right_jpeg is not None else "",
            "alignment_preview_stitched_url": self._start_preview_url("stitched") if self._start_preview_stitched_jpeg is not None else "",
        }
        snapshot.update(self._flatten_truth_metrics(self._latest_metrics))
        if self._latest_validation:
            snapshot.update(self._latest_validation)
        if self._prepared_plan is not None:
            summary = self._prepared_plan.summary
            for key in (
                "geometry_artifact_path",
                "geometry_artifact_model",
                "geometry_residual_model",
                "geometry_rollout_status",
                "geometry_operator_visible",
                "geometry_fallback_only",
                "geometry_compat_only",
                "output_runtime_mode",
                "production_output_runtime_mode",
                "launch_ready",
                "launch_ready_reason",
            ):
                if not snapshot.get(key):
                    snapshot[key] = summary.get(key, "")
            snapshot["gpu_only_mode"] = bool(summary.get("gpu_only_mode", True))
            snapshot["gpu_only_ready"] = len(self._gpu_only_blockers) == 0
            snapshot["gpu_only_blockers"] = list(self._gpu_only_blockers)
            snapshot.setdefault("validation_mode", "read-only")
            snapshot.setdefault("strict_fresh", summary.get("sync_pair_mode", "") == "service")
            try:
                artifact = load_runtime_geometry_artifact(self._prepared_plan.geometry_artifact_path)
            except Exception:
                artifact = None
            if isinstance(artifact, dict) and not running:
                for key, value in self._flatten_artifact_truth(artifact).items():
                    current = snapshot.get(key)
                    if current in (None, "", "-", 0, 0.0, False):
                        snapshot[key] = value
        return snapshot

    @staticmethod
    def _flatten_truth_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metrics, dict):
            metrics = {}

        def _string(name: str, default: str = "") -> str:
            value = metrics.get(name, default)
            return "" if value is None else str(value)

        def _float(name: str, default: float = 0.0) -> float:
            value = metrics.get(name, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(default)

        def _int(name: str, default: int = 0) -> int:
            value = metrics.get(name, default)
            try:
                return int(value)
            except (TypeError, ValueError):
                return int(default)

        def _bool(name: str, default: bool = False) -> bool:
            value = metrics.get(name, default)
            if isinstance(value, bool):
                return value
            return bool(value)

        return {
            "geometry_mode": _string("geometry_mode", "-"),
            "alignment_mode": _string("alignment_mode", "-"),
            "seam_mode": _string("seam_mode", "-"),
            "exposure_mode": _string("exposure_mode", "-"),
            "blend_mode": _string("blend_mode", "-"),
            "geometry_artifact_path": _string("geometry_artifact_path"),
            "geometry_artifact_model": _string("geometry_artifact_model", "-"),
            "geometry_rollout_status": _string("geometry_rollout_status"),
            "geometry_operator_visible": _bool("geometry_operator_visible"),
            "geometry_fallback_only": _bool("geometry_fallback_only"),
            "geometry_compat_only": _bool("geometry_compat_only"),
            "cylindrical_focal_px": _float("cylindrical_focal_px"),
            "cylindrical_center_x": _float("cylindrical_center_x"),
            "cylindrical_center_y": _float("cylindrical_center_y"),
            "residual_alignment_error_px": _float("residual_alignment_error_px"),
            "seam_path_jitter_px": _float("seam_path_jitter_px"),
            "exposure_gain": _float("exposure_gain", 1.0),
            "exposure_bias": _float("exposure_bias"),
            "overlap_diff_mean": _float("overlap_diff_mean"),
            "stitched_mean_luma": _float("stitched_mean_luma"),
            "left_mean_luma": _float("left_mean_luma"),
            "right_mean_luma": _float("right_mean_luma"),
            "warped_mean_luma": _float("warped_mean_luma"),
            "only_left_pixels": _int("only_left_pixels"),
            "only_right_pixels": _int("only_right_pixels"),
            "overlap_pixels": _int("overlap_pixels"),
            "output_runtime_mode": _string("output_runtime_mode"),
            "production_output_runtime_mode": _string("production_output_runtime_mode"),
            "output_target": _string("output_target"),
            "production_output_target": _string("production_output_target"),
            "output_effective_codec": _string("output_effective_codec"),
            "production_output_effective_codec": _string("production_output_effective_codec"),
            "output_frames_written": _int("output_frames_written"),
            "output_frames_dropped": _int("output_frames_dropped"),
            "production_output_frames_written": _int("production_output_frames_written"),
            "production_output_frames_dropped": _int("production_output_frames_dropped"),
            "output_written_fps": _float("output_written_fps"),
            "production_output_written_fps": _float("production_output_written_fps"),
            "reused_count": _int("reused_count"),
            "wait_sync_pair_count": _int("wait_sync_pair_count"),
            "wait_paired_fresh_count": _int("wait_paired_fresh_count"),
            "pair_source_skew_ms_mean": _float("pair_source_skew_ms_mean"),
            "stitch_actual_fps": _float("stitch_actual_fps"),
            "stitch_fps": _float("stitch_fps"),
            "worker_fps": _float("worker_fps"),
            "gpu_enabled": _bool("gpu_enabled"),
            "gpu_reason": _string("gpu_reason"),
            "gpu_feature_enabled": _bool("gpu_feature_enabled"),
            "gpu_feature_reason": _string("gpu_feature_reason"),
            "gpu_warp_count": _int("gpu_warp_count"),
            "cpu_warp_count": _int("cpu_warp_count"),
            "gpu_blend_count": _int("gpu_blend_count"),
            "cpu_blend_count": _int("cpu_blend_count"),
            "launch_ready": _bool("launch_ready"),
            "launch_ready_reason": _string("launch_ready_reason"),
        }

    @staticmethod
    def _gpu_only_blockers_for_plan(plan: RuntimePlan) -> list[str]:
        spec = plan.launch_spec
        blockers: list[str] = []
        if str(spec.gpu_mode).strip().lower() != "only":
            blockers.append("GPU-only 브랜치에서는 runtime.gpu_mode 가 only 여야 합니다.")
        if str(spec.input_runtime).strip().lower() != "ffmpeg-cuda":
            blockers.append("GPU-only 모드에서는 입력 런타임이 ffmpeg-cuda 여야 합니다.")
        if str(spec.input_pipe_format).strip().lower() != "nv12":
            blockers.append("GPU-only 모드에서는 입력 파이프 포맷이 nv12 여야 합니다.")
        if str(spec.output_runtime).strip().lower() != "none":
            blockers.append("GPU-only 모드에서는 Probe 출력이 비활성화되어야 합니다.")
        if str(spec.output_target).strip():
            blockers.append("GPU-only 모드에서는 Probe target 이 비어 있어야 합니다.")
        if bool(spec.output_debug_overlay):
            blockers.append("GPU-only 모드에서는 Probe debug overlay 를 사용할 수 없습니다.")
        if str(spec.production_output_runtime).strip().lower() != "gpu-direct":
            blockers.append("GPU-only 모드에서는 Transmit runtime 이 gpu-direct 여야 합니다.")
        if not str(spec.production_output_target).strip():
            blockers.append("GPU-only 모드에서는 Transmit target 이 필요합니다.")
        if bool(spec.production_output_debug_overlay):
            blockers.append("GPU-only 모드에서는 Transmit debug overlay 를 사용할 수 없습니다.")
        try:
            gpu_count = int(cv2.cuda.getCudaEnabledDeviceCount())
        except Exception as exc:
            blockers.append(f"CUDA 장치 확인에 실패했습니다: {exc}")
        else:
            if gpu_count <= int(spec.gpu_device):
                blockers.append(
                    f"CUDA 장치 {int(spec.gpu_device)} 를 사용할 수 없습니다. 감지된 장치 수={gpu_count}."
                )
        gpu_direct_status = query_gpu_direct_status()
        if not bool(gpu_direct_status.get("dependency_ready")):
            status_text = str(
                gpu_direct_status.get("status")
                or gpu_direct_status.get("stderr")
                or gpu_direct_status.get("raw")
                or "gpu-direct dependency not ready"
            ).strip()
            blockers.append(f"gpu-direct 준비 상태가 아닙니다: {status_text}")
        return blockers

    @staticmethod
    def _flatten_artifact_truth(artifact: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(artifact, dict):
            return {}

        geometry = artifact.get("geometry", {}) if isinstance(artifact.get("geometry"), dict) else {}
        alignment = artifact.get("alignment", {}) if isinstance(artifact.get("alignment"), dict) else {}
        seam = artifact.get("seam", {}) if isinstance(artifact.get("seam"), dict) else {}
        exposure = artifact.get("exposure", {}) if isinstance(artifact.get("exposure"), dict) else {}
        projection = artifact.get("projection", {}) if isinstance(artifact.get("projection"), dict) else {}

        def _string(section: dict[str, Any], name: str, default: str = "") -> str:
            value = section.get(name, default)
            return "" if value is None else str(value)

        def _float(section: dict[str, Any], name: str, default: float = 0.0) -> float:
            value = section.get(name, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return float(default)

        geometry_mode = _string(geometry, "model", "-")
        seam_mode = _string(seam, "mode", "-")
        exposure_enabled = exposure.get("enabled")
        exposure_mode = "gain-bias" if bool(exposure_enabled) else "off"
        if not geometry_mode:
            geometry_mode = "-"
        rollout = geometry_rollout_metadata(artifact)

        left_projection = projection.get("left", {}) if isinstance(projection.get("left"), dict) else {}
        should_expose_cylindrical_projection = geometry_mode == "cylindrical-affine"
        return {
            "geometry_mode": str(rollout["geometry_model"] or geometry_mode),
            "geometry_residual_model": rollout["geometry_residual_model"],
            "alignment_mode": _string(alignment, "model", "-"),
            "seam_mode": seam_mode,
            "exposure_mode": exposure_mode,
            "blend_mode": seam_mode or _string(geometry, "warp_model", "-"),
            "geometry_artifact_model": rollout["geometry_model"],
            "geometry_rollout_status": rollout["geometry_rollout_status"],
            "geometry_operator_visible": bool(rollout["geometry_operator_visible"]),
            "geometry_fallback_only": bool(rollout["geometry_fallback_only"]),
            "geometry_compat_only": bool(rollout["geometry_compat_only"]),
            "fallback_used": bool(rollout["geometry_fallback_only"]),
            "launch_ready": bool(rollout["launch_ready"]),
            "launch_ready_reason": str(rollout["launch_ready_reason"]),
            "cylindrical_focal_px": _float(left_projection, "focal_px") if should_expose_cylindrical_projection else 0.0,
            "cylindrical_center_x": float(left_projection.get("center", [0.0, 0.0])[0]) if should_expose_cylindrical_projection and isinstance(left_projection.get("center"), list) and left_projection.get("center") else 0.0,
            "cylindrical_center_y": float(left_projection.get("center", [0.0, 0.0])[1]) if should_expose_cylindrical_projection and isinstance(left_projection.get("center"), list) and len(left_projection.get("center")) > 1 else 0.0,
        }

    def prepare(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                self._clear_start_preview_locked()
                auto_geometry = self._ensure_default_geometry_artifact(request)
                plan = self._build_plan(request)
                blockers = self._gpu_only_blockers_for_plan(plan)
                self._gpu_only_blockers = blockers
                if blockers:
                    raise ValueError(" / ".join(blockers))
            except Exception as exc:
                self._last_error = f"prepare failed: {exc}"
                self._record_event("error", {"code": "prepare_failed", "message": str(exc)})
                raise
            self._prepared_plan = plan
            self._last_status = "prepared"
            self._last_error = ""
            self._record_event("status", {"status": "prepared", "geometry_artifact_path": str(plan.geometry_artifact_path)})
            return {
                "ok": True,
                "prepared": plan.summary,
                "auto_calibrated": bool(auto_geometry.get("calibrated")),
                "message": str(auto_geometry.get("message") or "runtime prepared"),
                "state": self._snapshot_locked(),
            }

    def preview_align(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._supervisor is not None and self._supervisor.process.poll() is None:
                self._last_status = "already_running"
                return {"ok": True, "message": "runtime already running", "state": self._snapshot_locked()}
            auto_prepare_result: dict[str, Any] | None = None
            if self._prepared_plan is None:
                auto_prepare_result = self.prepare(request)
            plan = self._prepared_plan
            if plan is None:
                raise ValueError("runtime plan is unavailable after automatic prepare")
            blockers = self._gpu_only_blockers_for_plan(plan)
            self._gpu_only_blockers = blockers
            if blockers:
                self._last_error = f"preview failed: {' / '.join(blockers)}"
                self._record_event("error", {"code": "gpu_only_blocked", "message": self._last_error})
                raise ValueError(" / ".join(blockers))
            self._render_start_preview_locked(plan)
            self._last_status = "preview_ready"
            self._last_error = ""
            preview_message = "Virtual camera alignment preview is ready."
            if auto_prepare_result is not None:
                prepare_message = str(auto_prepare_result.get("message") or "").strip()
                if prepare_message:
                    preview_message = f"{prepare_message} / {preview_message}"
            return {
                "ok": True,
                "preview_ready": True,
                "auto_prepared": auto_prepare_result is not None,
                "auto_calibrated": bool(auto_prepare_result and auto_prepare_result.get("auto_calibrated")),
                "message": preview_message,
                "state": self._snapshot_locked(),
            }

    def _start_event_pump(self) -> None:
        if self._event_pump_thread is not None:
            return
        self._event_pump_stop.clear()
        self._event_pump_thread = threading.Thread(target=self._pump_runtime_events, name="hogak-runtime-events", daemon=True)
        self._event_pump_thread.start()

    def _pump_runtime_events(self) -> None:
        while not self._event_pump_stop.is_set():
            supervisor = self._supervisor
            if supervisor is None:
                break
            event = supervisor.read_event(timeout_sec=0.25)
            if event is None:
                if supervisor.process.poll() is not None:
                    break
                continue
            with self._lock:
                if event.type == "metrics":
                    self._latest_metrics = event.payload
                elif event.type == "hello":
                    self._latest_hello = event.payload
                self._record_event(event.type, event.payload, seq=event.seq)
        with self._lock:
            self._event_pump_thread = None

    def start(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if self._supervisor is not None and self._supervisor.process.poll() is None:
                self._last_status = "already_running"
                return {"ok": True, "state": self._snapshot_locked()}
            auto_prepare_result: dict[str, Any] | None = None
            if self._prepared_plan is None:
                auto_prepare_result = self.prepare(request)
            plan = self._prepared_plan
            if plan is None:
                raise ValueError("runtime plan is unavailable after automatic prepare")
            blockers = self._gpu_only_blockers_for_plan(plan)
            self._gpu_only_blockers = blockers
            if blockers:
                self._last_error = f"start failed: {' / '.join(blockers)}"
                self._record_event("error", {"code": "gpu_only_blocked", "message": self._last_error})
                raise ValueError(" / ".join(blockers))
            self._supervisor = RuntimeSupervisor.launch(plan.launch_spec)
            try:
                hello = self._supervisor.wait_for_hello(timeout_sec=5.0)
                self._latest_hello = hello.payload
                self._record_event("hello", hello.payload, seq=hello.seq)
                self._supervisor.client.reload_config(plan.reload_payload)
                self._supervisor.request_metrics()
                self._record_event("status", {"status": "reload_sent", "geometry_artifact_path": str(plan.geometry_artifact_path)})
            except Exception:
                self._last_error = "runtime launch handshake failed"
                self.stop()
                raise
            self._start_event_pump()
            self._last_status = "running"
            self._last_error = ""
            self._record_event("status", {"status": "running", "runtime_pid": self._supervisor.process.pid})
            transmit_receive_uri = self._receive_uri_from_target(plan.summary.get("transmit_target", ""))
            start_message = (
                f"런타임을 시작했습니다. 외부 플레이어에서 {transmit_receive_uri or plan.summary.get('transmit_target', '')} 를 여세요."
            )
            receive_uri = transmit_receive_uri or str(plan.summary.get("transmit_target", "")).strip()
            start_message = f"런타임을 시작했습니다. 외부 플레이어에서 {receive_uri} 를 여세요."
            if auto_prepare_result is not None:
                prepare_message = str(auto_prepare_result.get("message") or "").strip()
                if prepare_message:
                    start_message = f"{prepare_message} / {start_message}"
            return {
                "ok": True,
                "auto_prepared": auto_prepare_result is not None,
                "auto_calibrated": bool(auto_prepare_result and auto_prepare_result.get("auto_calibrated")),
                "message": start_message,
                "state": self._snapshot_locked(),
            }

    def reload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_schema_v2_reload_payload(payload)
        with self._lock:
            if self._supervisor is None or self._supervisor.process.poll() is not None:
                raise RuntimeError("runtime is not running")
            self._supervisor.client.reload_config(normalized)
            self._prepared_plan = self._build_plan(normalized)
            self._clear_start_preview_locked()
            self._last_status = "reloaded"
            self._record_event(
                "status",
                {
                    "status": "reloaded",
                    "geometry_artifact_path": self._prepared_plan.summary.get("geometry_artifact_path", ""),
                },
            )
            return {"ok": True, "state": self._snapshot_locked()}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._event_pump_stop.set()
            self._clear_start_preview_locked()
            if self._supervisor is not None:
                try:
                    self._supervisor.shutdown()
                except Exception:
                    pass
                try:
                    self._supervisor.close()
                except Exception:
                    pass
                self._supervisor = None
            if self._event_pump_thread is not None:
                self._event_pump_thread.join(timeout=2.5)
                self._event_pump_thread = None
            self._last_status = "stopped"
            self._record_event("stopped", {"status": "stopped"})
            return {"ok": True, "message": "Runtime stopped.", "state": self._snapshot_locked()}

    def validate(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        strict_fresh = False
        try:
            plan = self._build_plan(request)
            artifact_path = plan.geometry_artifact_path
            plan_summary = plan.summary
            strict_fresh = str(plan.launch_spec.sync_pair_mode).strip().lower() == "service"
        except Exception:
            artifact_path = self._resolve_requested_artifact_path(request)
            if artifact_path is None:
                raise
            plan_summary = {
                "geometry_artifact_path": str(artifact_path),
                "runtime_schema_version": 2,
            }

        checksum_before = _compute_sha256(artifact_path)
        artifact = load_runtime_geometry_artifact(artifact_path)
        checksum_after = _compute_sha256(artifact_path)
        geometry_model = runtime_geometry_model(artifact)
        artifact_unchanged = checksum_before == checksum_after
        rollout = geometry_rollout_metadata(artifact)
        launch_ready = bool(artifact_unchanged and rollout["launch_ready"])
        launch_ready_reason = (
            "geometry artifact changed during read-only validation"
            if not artifact_unchanged
            else str(rollout["launch_ready_reason"])
        )
        result = {
            "ok": True,
            "validation_mode": "read-only",
            "plan": plan_summary,
            "artifact_type": artifact.get("artifact_type", ""),
            "schema_version": artifact.get("schema_version", 0),
            "geometry_model": rollout["geometry_model"],
            "geometry_artifact_model": rollout["geometry_model"],
            "geometry_residual_model": rollout["geometry_residual_model"],
            "geometry_rollout_status": rollout["geometry_rollout_status"],
            "geometry_operator_visible": bool(rollout["geometry_operator_visible"]),
            "geometry_fallback_only": bool(rollout["geometry_fallback_only"]),
            "geometry_compat_only": bool(rollout["geometry_compat_only"]),
            "geometry_artifact_checksum": checksum_before,
            "artifact_unchanged": artifact_unchanged,
            "launch_ready": launch_ready,
            "launch_ready_reason": launch_ready_reason,
            "strict_fresh": strict_fresh,
        }
        with self._lock:
            self._latest_validation = result.copy()
            self._record_event(
                "status",
                {"status": "validated", "geometry_artifact_path": str(artifact_path)},
            )
        return result

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def start_preview_jpeg(self, name: str) -> bytes | None:
        with self._lock:
            normalized = str(name or "").strip().lower()
            if normalized == "left":
                return self._start_preview_left_jpeg
            if normalized == "right":
                return self._start_preview_right_jpeg
            if normalized == "stitched":
                return self._start_preview_stitched_jpeg
            return None

    def list_geometry_artifacts(self) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        data_dir = repo_root() / "data"
        if not data_dir.exists():
            return artifacts
        active_artifact = ""
        if self._prepared_plan is not None:
            active_artifact = str(self._prepared_plan.geometry_artifact_path)
        for path in sorted(data_dir.glob("*.json")):
            try:
                artifact = load_runtime_geometry_artifact(path)
            except Exception:
                continue
            rollout = geometry_rollout_metadata(artifact)
            if not bool(rollout["geometry_operator_visible"]) and str(path) != active_artifact:
                continue
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "artifact_type": artifact.get("artifact_type", ""),
                    "schema_version": artifact.get("schema_version", 0),
                    "saved_at_epoch_sec": artifact.get("saved_at_epoch_sec", 0),
                    "geometry_model": rollout["geometry_model"],
                    "geometry_residual_model": rollout["geometry_residual_model"],
                    "geometry_rollout_status": rollout["geometry_rollout_status"],
                    "operator_visible": bool(rollout["geometry_operator_visible"]),
                    "fallback_only": bool(rollout["geometry_fallback_only"]),
                    "compat_only": bool(rollout["geometry_compat_only"]),
                    "launch_ready": bool(rollout["launch_ready"]),
                    "launch_ready_reason": str(rollout["launch_ready_reason"]),
                }
            )
        return artifacts

    def get_geometry_artifact(self, name: str) -> dict[str, Any]:
        candidate = (repo_root() / "data" / name).resolve()
        data_root = (repo_root() / "data").resolve()
        if data_root not in candidate.parents and candidate != data_root:
            raise FileNotFoundError("invalid artifact path")
        artifact = load_runtime_geometry_artifact(candidate)
        artifact["path"] = str(candidate)
        return artifact

    def stream_events(self, last_event_id: int = 0) -> Iterable[str]:
        with self._lock:
            start_index = 0
            for index, event in enumerate(self._events):
                if int(event.get("id", 0)) > int(last_event_id):
                    start_index = index
                    break
            else:
                start_index = len(self._events)

        def _generator() -> Iterable[str]:
            nonlocal start_index
            while True:
                with self._event_condition:
                    while start_index >= len(self._events):
                        self._event_condition.wait(timeout=1.0)
                    pending = self._events[start_index:]
                    start_index = len(self._events)
                for event in pending:
                    yield f"id: {event['id']}\n"
                    yield f"event: {event['type']}\n"
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return _generator()


def create_app(
    *,
    service: RuntimeService | None = None,
    mesh_refresh_service: MeshRefreshService | None = None,
    frontend_dist_dir: str | Path | None = None,
) -> FastAPI:
    backend = service or RuntimeService()
    mesh_refresh = mesh_refresh_service or MeshRefreshService()
    app = FastAPI(title="Hogak Runtime API", version="2")
    app.state.runtime_service = backend
    app.state.mesh_refresh_service = mesh_refresh

    @app.post("/_internal/runtime/prepare", include_in_schema=False)
    def prepare_runtime(body: dict[str, Any] | None = None):
        try:
            return backend.prepare(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runtime/start")
    def start_runtime(body: dict[str, Any] | None = None):
        try:
            return _public_runtime_response(backend.start(body), mesh_refresh.state())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runtime/preview-align")
    def preview_runtime_alignment(body: dict[str, Any] | None = None):
        try:
            return _public_runtime_response(backend.preview_align(body), mesh_refresh.state())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runtime/stop")
    def stop_runtime():
        return _public_runtime_response(backend.stop(), mesh_refresh.state())

    @app.post("/api/runtime/validate")
    def validate_runtime(body: dict[str, Any] | None = None):
        try:
            return _public_runtime_response(backend.validate(body), mesh_refresh.state())
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/_internal/runtime/reload", include_in_schema=False)
    def reload_runtime(body: dict[str, Any]):
        try:
            return backend.reload(body)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runtime/state")
    def runtime_state():
        return _public_runtime_state(backend.state(), mesh_refresh.state())

    @app.get("/_internal/runtime/state", include_in_schema=False)
    def internal_runtime_state():
        return _merge_runtime_and_mesh_refresh_state(backend.state(), mesh_refresh.state())

    @app.get("/_internal/runtime/events", include_in_schema=False)
    def runtime_events(last_event_id: int = 0):
        return StreamingResponse(
            backend.stream_events(last_event_id=last_event_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/runtime/preview-align/assets/{name}.jpg")
    def runtime_preview_asset(name: str):
        jpeg = backend.start_preview_jpeg(name)
        if jpeg is None:
            raise HTTPException(status_code=503, detail="start preview frame unavailable")
        return Response(content=jpeg, media_type="image/jpeg")

    @app.get("/api/artifacts/geometry")
    def list_geometry_artifacts():
        return {"items": backend.list_geometry_artifacts()}

    @app.get("/api/artifacts/geometry/{name}")
    def get_geometry_artifact(name: str):
        try:
            return backend.get_geometry_artifact(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/_internal/runtime/mesh-refresh/state", include_in_schema=False)
    def mesh_refresh_state():
        return mesh_refresh.state()

    @app.post("/_internal/runtime/mesh-refresh", include_in_schema=False)
    def refresh_runtime_mesh_api(body: dict[str, Any] | None = None):
        try:
            return _internal_mesh_refresh(mesh_refresh, body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if frontend_dist_dir is None:
        frontend_env = os.environ.get("HOGAK_FRONTEND_DIST_DIR", "").strip()
        frontend_path = Path(frontend_env).expanduser() if frontend_env else repo_root() / "frontend" / "dist"
    else:
        frontend_path = Path(frontend_dist_dir).expanduser()
    if frontend_path.is_dir():
        app.state.frontend_dist_dir = str(frontend_path)
        frontend_root = frontend_path.resolve()
        assets_dir = frontend_root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend_entrypoint(full_path: str):
            normalized = str(full_path or "").lstrip("/")
            if normalized.startswith("api/") or normalized.startswith("_internal/") or normalized.startswith("legacy/"):
                raise HTTPException(status_code=404, detail="not found")

            candidate = (frontend_root / normalized).resolve()
            try:
                candidate.relative_to(frontend_root)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="not found") from exc

            if candidate.is_file():
                return FileResponse(candidate)

            index_path = frontend_root / "index.html"
            if index_path.is_file():
                return FileResponse(index_path)
            raise HTTPException(status_code=404, detail="frontend unavailable")
    else:
        app.state.frontend_dist_dir = ""
        app.state.frontend_dist_missing = True
        fallback_html = _frontend_unavailable_html(frontend_path.resolve())
        print(f"[operator-server] React bundle not found at {frontend_path}; serving backend-only fallback page.")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend_unavailable(full_path: str):
            normalized = str(full_path or "").lstrip("/")
            if normalized.startswith("api/") or normalized.startswith("_internal/") or normalized.startswith("legacy/"):
                raise HTTPException(status_code=404, detail="not found")
            return HTMLResponse(content=fallback_html)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        try:
            backend.stop()
        except Exception:
            pass

    return app


app = create_app()


def main() -> int:
    try:
        import uvicorn
    except Exception as exc:
        raise RuntimeError("uvicorn is required to run the runtime backend") from exc

    host = os.environ.get("HOGAK_BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("HOGAK_BACKEND_PORT", "8088"))
    uvicorn.run("stitching.runtime_backend:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
