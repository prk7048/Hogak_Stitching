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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse

from stitching.runtime_calibration_service import CalibrationService
from stitching.runtime_contract import normalize_schema_v2_reload_payload
from stitching.runtime_geometry_artifact import load_runtime_geometry_artifact, runtime_geometry_artifact_path
from stitching.runtime_launcher import RuntimeLaunchSpec
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
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Hogak Operator UI Not Built</title>
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
      <h1>Hogak Operator UI is not built yet</h1>
      <p>
        <code>operator-server</code> is running, but the React bundle was not found at
        <code>{escaped_path}</code>.
      </p>
      <p>Build the frontend once, then restart the server:</p>
      <pre>cd frontend
npm install
npm run build</pre>
      <p>After the build completes, restart:</p>
      <pre>python -m stitching.cli operator-server</pre>
      <p>You can also point the backend at a different built frontend with <code>HOGAK_FRONTEND_DIST_DIR</code>.</p>
      <div class="note">
        <strong>Current backend status</strong>
        <ul>
          <li>Runtime API is still available at <code>/api/runtime/*</code>.</li>
          <li>Calibration routes will appear in the React UI after the build.</li>
          <li>Legacy bridge was removed; <code>/legacy/calibration</code> now redirects to <code>/calibration/start</code>.</li>
          <li>Even after the UI appears, stitched output only starts after <code>Prepare</code> then <code>Start</code>.</li>
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


class PreviewWorker:
    def __init__(self, target: str) -> None:
        self._target = target.strip()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._latest_jpeg: bytes | None = None
        self._status = "idle"

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    @property
    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._latest_jpeg

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="hogak-preview-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.5)
            self._thread = None

    def _store_frame(self, frame: np.ndarray) -> None:
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return
        with self._lock:
            self._latest_frame = frame.copy()
            self._latest_jpeg = encoded.tobytes()
            self._status = "streaming"

    def _run(self) -> None:
        if not self._target:
            with self._lock:
                self._status = "missing target"
            return
        with self._lock:
            self._status = "connecting"
        while not self._stop_event.is_set():
            capture = cv2.VideoCapture(self._target, cv2.CAP_FFMPEG)
            if not capture.isOpened():
                with self._lock:
                    self._status = "connect failed"
                time.sleep(0.75)
                continue
            with self._lock:
                self._status = "connected"
            try:
                while not self._stop_event.is_set():
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        with self._lock:
                            self._status = "waiting"
                        time.sleep(0.1)
                        break
                    self._store_frame(frame)
            finally:
                capture.release()
        with self._lock:
            self._status = "stopped"


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
        self._preview_worker: PreviewWorker | None = None
        self._latest_metrics: dict[str, Any] = {}
        self._latest_hello: dict[str, Any] = {}
        self._latest_validation: dict[str, Any] = {}
        self._last_error = ""
        self._last_status = "idle"

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
            "preview_status": None if self._preview_worker is None else self._preview_worker.status,
            "event_count": len(self._events),
        }
        snapshot.update(self._flatten_truth_metrics(self._latest_metrics))
        if self._latest_validation:
            snapshot.update(self._latest_validation)
        if self._prepared_plan is not None:
            summary = self._prepared_plan.summary
            for key in ("geometry_artifact_path", "output_runtime_mode", "production_output_runtime_mode"):
                if not snapshot.get(key):
                    snapshot[key] = summary.get(key, "")
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
            "gpu_feature_enabled": _bool("gpu_feature_enabled"),
            "gpu_warp_count": _int("gpu_warp_count"),
            "cpu_warp_count": _int("cpu_warp_count"),
            "gpu_blend_count": _int("gpu_blend_count"),
            "cpu_blend_count": _int("cpu_blend_count"),
        }

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

        left_projection = projection.get("left", {}) if isinstance(projection.get("left"), dict) else {}
        return {
            "geometry_mode": geometry_mode,
            "alignment_mode": _string(alignment, "model", "-"),
            "seam_mode": seam_mode,
            "exposure_mode": exposure_mode,
            "blend_mode": seam_mode or _string(geometry, "warp_model", "-"),
            "geometry_artifact_model": geometry_mode,
            "cylindrical_focal_px": _float(left_projection, "focal_px"),
            "cylindrical_center_x": float(left_projection.get("center", [0.0, 0.0])[0]) if isinstance(left_projection.get("center"), list) and left_projection.get("center") else 0.0,
            "cylindrical_center_y": float(left_projection.get("center", [0.0, 0.0])[1]) if isinstance(left_projection.get("center"), list) and len(left_projection.get("center")) > 1 else 0.0,
        }

    def prepare(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            try:
                plan = self._build_plan(request)
            except Exception as exc:
                self._last_error = f"prepare failed: {exc}"
                self._record_event("error", {"code": "prepare_failed", "message": str(exc)})
                raise
            self._prepared_plan = plan
            self._last_status = "prepared"
            self._last_error = ""
            self._record_event("status", {"status": "prepared", "geometry_artifact_path": str(plan.geometry_artifact_path)})
            return {"ok": True, "prepared": plan.summary, "state": self._snapshot_locked()}

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
            if self._prepared_plan is None:
                self._prepared_plan = self._build_plan(request)
            plan = self._prepared_plan
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
            probe_target = plan.summary.get("probe_target", "")
            if isinstance(probe_target, str) and probe_target.strip():
                self._preview_worker = PreviewWorker(probe_target)
                self._preview_worker.start()
            self._start_event_pump()
            self._last_status = "running"
            self._last_error = ""
            self._record_event("status", {"status": "running", "runtime_pid": self._supervisor.process.pid})
            return {"ok": True, "state": self._snapshot_locked()}

    def reload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_schema_v2_reload_payload(payload)
        with self._lock:
            if self._supervisor is None or self._supervisor.process.poll() is not None:
                raise RuntimeError("runtime is not running")
            self._supervisor.client.reload_config(normalized)
            self._prepared_plan = self._build_plan(normalized)
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
            if self._preview_worker is not None:
                self._preview_worker.stop()
                self._preview_worker = None
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
            return {"ok": True, "state": self._snapshot_locked()}

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
        geometry_model = str(artifact.get("geometry", {}).get("model", ""))
        artifact_unchanged = checksum_before == checksum_after
        result = {
            "ok": True,
            "validation_mode": "read-only",
            "plan": plan_summary,
            "artifact_type": artifact.get("artifact_type", ""),
            "schema_version": artifact.get("schema_version", 0),
            "geometry_model": geometry_model,
            "geometry_artifact_checksum": checksum_before,
            "artifact_unchanged": artifact_unchanged,
            "launch_ready": bool(artifact_unchanged and geometry_model in {"planar-homography", "cylindrical-affine"}),
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

    def preview_jpeg(self) -> bytes | None:
        with self._lock:
            if self._preview_worker is None:
                return None
            return self._preview_worker.latest_jpeg

    def list_geometry_artifacts(self) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        data_dir = repo_root() / "data"
        if not data_dir.exists():
            return artifacts
        for path in sorted(data_dir.glob("*.json")):
            try:
                artifact = load_runtime_geometry_artifact(path)
            except Exception:
                continue
            artifacts.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "artifact_type": artifact.get("artifact_type", ""),
                    "schema_version": artifact.get("schema_version", 0),
                    "saved_at_epoch_sec": artifact.get("saved_at_epoch_sec", 0),
                    "geometry_model": artifact.get("geometry", {}).get("model", ""),
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
    calibration_service: CalibrationService | None = None,
    frontend_dist_dir: str | Path | None = None,
) -> FastAPI:
    backend = service or RuntimeService()
    calibration = calibration_service or CalibrationService()
    app = FastAPI(title="Hogak Runtime API", version="2")
    app.state.runtime_service = backend
    app.state.calibration_service = calibration

    @app.post("/api/runtime/prepare")
    def prepare_runtime(body: dict[str, Any] | None = None):
        try:
            return backend.prepare(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runtime/start")
    def start_runtime(body: dict[str, Any] | None = None):
        try:
            return backend.start(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runtime/stop")
    def stop_runtime():
        return backend.stop()

    @app.post("/api/runtime/validate")
    def validate_runtime(body: dict[str, Any] | None = None):
        try:
            return backend.validate(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runtime/reload")
    def reload_runtime(body: dict[str, Any]):
        try:
            return backend.reload(body)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runtime/state")
    def runtime_state():
        return backend.state()

    @app.get("/api/runtime/events")
    def runtime_events(last_event_id: int = 0):
        return StreamingResponse(
            backend.stream_events(last_event_id=last_event_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/runtime/preview.jpg")
    def runtime_preview():
        jpeg = backend.preview_jpeg()
        if jpeg is None:
            raise HTTPException(status_code=503, detail="preview frame unavailable")
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

    @app.post("/api/calibration/session/start")
    def start_calibration_session(body: dict[str, Any] | None = None):
        try:
            return calibration.start_session(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/calibration/session/state")
    def get_calibration_session_state():
        return calibration.state()

    @app.post("/api/calibration/frames/refresh")
    def refresh_calibration_frames():
        try:
            return calibration.refresh_frames()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/pairs")
    def add_calibration_pair(body: dict[str, Any]):
        try:
            return calibration.add_pair(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/pairs/select")
    def select_calibration_pair(body: dict[str, Any]):
        try:
            return calibration.select_pair(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/pairs/undo")
    def undo_calibration_pair():
        try:
            return calibration.undo_pair()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/pairs/delete")
    def delete_calibration_pair():
        try:
            return calibration.delete_pair()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/pairs/clear")
    def clear_calibration_pairs():
        try:
            return calibration.clear_pairs()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/candidate/compute")
    def compute_calibration_candidate():
        try:
            return calibration.compute_candidate()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/calibration/review")
    def get_calibration_review():
        try:
            return calibration.review()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/review/accept")
    def accept_calibration_review():
        try:
            return calibration.accept_review()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/review/cancel")
    def cancel_calibration_review():
        try:
            return calibration.cancel_review()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/calibration/stitch-review")
    def get_stitch_review():
        try:
            return calibration.stitch_review()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/calibration/use-current")
    def use_current_homography(body: dict[str, Any] | None = None):
        try:
            return calibration.use_current(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/calibration/images/{name}")
    def get_calibration_image(name: str):
        jpeg = calibration.image(name)
        if jpeg is None:
            raise HTTPException(status_code=404, detail="calibration preview unavailable")
        return Response(content=jpeg, media_type="image/jpeg")

    @app.get("/legacy/calibration", include_in_schema=False)
    @app.get("/legacy/calibration/", include_in_schema=False)
    def legacy_calibration_redirect():
        return RedirectResponse(url="/calibration/start")

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
            if normalized.startswith("api/") or normalized.startswith("legacy/"):
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
            if normalized.startswith("api/") or normalized.startswith("legacy/"):
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
