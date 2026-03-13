from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import gradio as gr
import numpy as np

from stitching.live_stitching import LiveConfig, stitch_live_rtsp
from stitching.perf_profiles import resolve_perf_profile
from stitching.video_stitching import VideoConfig, stitch_videos


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_VIDEOS = REPO_ROOT / "output" / "videos"
OUTPUT_DEBUG = REPO_ROOT / "output" / "debug"
INPUT_VIDEOS = REPO_ROOT / "input" / "videos"


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _to_rgb(frame: np.ndarray | None) -> np.ndarray | None:
    if frame is None:
        return None
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def _bool_from_onoff(value: str) -> bool:
    return str(value).strip().lower() == "on"


def _resolve_pair_from_prefix(input_dir: Path, pair: str) -> tuple[Path, Path]:
    candidates = sorted(input_dir.glob(f"{pair}_left*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for left in candidates:
        right = left.with_name(left.name.replace("_left", "_right", 1))
        if right.exists():
            return left, right
    raise FileNotFoundError(f"pair '{pair}' left/right files not found: {input_dir}")


def _resolve_latest_pair(input_dir: Path) -> tuple[Path, Path]:
    left_candidates = sorted(input_dir.glob("*_left*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for left in left_candidates:
        right = left.with_name(left.name.replace("_left", "_right", 1))
        if right.exists():
            return left, right
    raise FileNotFoundError(f"cannot find matched *_left/*_right pair in: {input_dir}")


def _build_video_config(
    *,
    min_matches: int,
    min_inliers: int,
    ratio_test: float,
    ransac_thresh: float,
    max_duration_sec: float,
    calib_start_sec: float,
    calib_end_sec: float,
    calib_step_sec: float,
    perf_mode: str,
    process_scale: float | None,
    homography_mode: str,
    homography_file: str,
    adaptive_seam: str,
    seam_update_interval: int,
    seam_temporal_penalty: float,
    seam_motion_weight: float,
) -> VideoConfig:
    scale, max_features = resolve_perf_profile(perf_mode=perf_mode, process_scale=process_scale)
    h_file = Path(homography_file) if homography_file else None
    return VideoConfig(
        min_matches=int(min_matches),
        min_inliers=int(min_inliers),
        ratio_test=float(ratio_test),
        ransac_reproj_threshold=float(ransac_thresh),
        max_duration_sec=float(max_duration_sec),
        calib_start_sec=float(calib_start_sec),
        calib_end_sec=float(calib_end_sec),
        calib_step_sec=float(calib_step_sec),
        perf_mode=str(perf_mode),
        process_scale=float(scale),
        max_features=int(max_features),
        homography_mode=str(homography_mode),
        homography_file=h_file,
        adaptive_seam=_bool_from_onoff(adaptive_seam),
        seam_update_interval=max(1, int(seam_update_interval)),
        seam_temporal_penalty=max(0.0, float(seam_temporal_penalty)),
        seam_motion_weight=max(0.0, float(seam_motion_weight)),
    )


def run_offline_video(
    left: str,
    right: str,
    out: str,
    report: str,
    debug_dir: str,
    max_duration_sec: float,
    min_matches: int,
    min_inliers: int,
    ratio_test: float,
    ransac_thresh: float,
    calib_start_sec: float,
    calib_end_sec: float,
    calib_step_sec: float,
    perf_mode: str,
    process_scale: float | None,
    homography_mode: str,
    homography_file: str,
    adaptive_seam: str,
    seam_update_interval: int,
    seam_temporal_penalty: float,
    seam_motion_weight: float,
) -> tuple[str, str, str | None]:
    try:
        left_path = Path(left)
        right_path = Path(right)
        if not left_path.exists() or not right_path.exists():
            return "failed: check left/right paths", "", None

        out_path = Path(out)
        report_path = Path(report)
        debug_path = Path(debug_dir)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.mkdir(parents=True, exist_ok=True)

        config = _build_video_config(
            min_matches=min_matches,
            min_inliers=min_inliers,
            ratio_test=ratio_test,
            ransac_thresh=ransac_thresh,
            max_duration_sec=max_duration_sec,
            calib_start_sec=calib_start_sec,
            calib_end_sec=calib_end_sec,
            calib_step_sec=calib_step_sec,
            perf_mode=perf_mode,
            process_scale=process_scale,
            homography_mode=homography_mode,
            homography_file=homography_file,
            adaptive_seam=adaptive_seam,
            seam_update_interval=seam_update_interval,
            seam_temporal_penalty=seam_temporal_penalty,
            seam_motion_weight=seam_motion_weight,
        )

        stitch_videos(
            left_path=left_path,
            right_path=right_path,
            output_path=out_path,
            report_path=report_path,
            debug_dir=debug_path,
            config=config,
        )
        return f"done: {out_path}", _read_text(report_path), str(out_path)
    except Exception as exc:  # pragma: no cover - GUI guard
        return f"failed: {exc}", "", None


def run_preset_video(
    preset: str,
    pair: str,
    left: str,
    right: str,
    input_dir: str,
    output_dir: str,
    debug_root: str,
    min_matches: int,
    min_inliers: int,
    ratio_test: float,
    ransac_thresh: float,
    calib_start_sec: float,
    calib_end_sec: float,
    calib_step_sec: float,
    perf_mode: str,
    process_scale: float | None,
    homography_mode: str,
    homography_file: str,
    adaptive_seam: str,
    seam_update_interval: int,
    seam_temporal_penalty: float,
    seam_motion_weight: float,
) -> tuple[str, str, str | None]:
    try:
        input_root = Path(input_dir)
        output_root = Path(output_dir)
        debug_base = Path(debug_root)

        if left and right:
            left_path = Path(left)
            right_path = Path(right)
            pair_base = left_path.stem.replace("_left", "")
        elif pair:
            left_path, right_path = _resolve_pair_from_prefix(input_root, pair)
            pair_base = pair
        else:
            left_path, right_path = _resolve_latest_pair(input_root)
            pair_base = left_path.stem.replace("_left", "")

        if preset == "video-10s":
            suffix = "10s"
            max_duration = 10.0
        elif preset == "video-30s":
            suffix = "30s"
            max_duration = 30.0
        else:
            suffix = "full"
            max_duration = 0.0

        out_path = output_root / f"{pair_base}_{suffix}_stitched.mp4"
        report_path = output_root / f"{pair_base}_{suffix}_report.json"
        debug_dir = debug_base / f"{pair_base}_{suffix}"

        config = _build_video_config(
            min_matches=min_matches,
            min_inliers=min_inliers,
            ratio_test=ratio_test,
            ransac_thresh=ransac_thresh,
            max_duration_sec=max_duration,
            calib_start_sec=calib_start_sec,
            calib_end_sec=calib_end_sec,
            calib_step_sec=calib_step_sec,
            perf_mode=perf_mode,
            process_scale=process_scale,
            homography_mode=homography_mode,
            homography_file=homography_file,
            adaptive_seam=adaptive_seam,
            seam_update_interval=seam_update_interval,
            seam_temporal_penalty=seam_temporal_penalty,
            seam_motion_weight=seam_motion_weight,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        debug_dir.mkdir(parents=True, exist_ok=True)

        stitch_videos(
            left_path=left_path,
            right_path=right_path,
            output_path=out_path,
            report_path=report_path,
            debug_dir=debug_dir,
            config=config,
        )
        return f"done: {out_path}", _read_text(report_path), str(out_path)
    except Exception as exc:  # pragma: no cover - GUI guard
        return f"failed: {exc}", "", None


@dataclass
class LiveSnapshot:
    running: bool
    status: str
    out_path: str
    report_path: str
    left: np.ndarray | None
    right: np.ndarray | None
    stitched: np.ndarray | None
    report_text: str


class LiveSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._status = "idle"
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._out_path = ""
        self._report_path = ""
        self._latest_left: np.ndarray | None = None
        self._latest_right: np.ndarray | None = None
        self._latest_stitched: np.ndarray | None = None
        self._report_text = ""

    def start(
        self,
        left_rtsp: str,
        right_rtsp: str,
        out: str,
        report: str,
        debug_dir: str,
        max_duration_sec: float,
        output_fps: float,
        calib_max_attempts: int,
        max_read_failures: int,
        reconnect_cooldown_sec: float,
        rtsp_transport: str,
        rtsp_timeout_sec: float,
        sync_buffer_sec: float,
        sync_match_max_delta_ms: float,
        sync_manual_offset_ms: float,
        sync_no_pair_timeout_sec: float,
        sync_pair_mode: str,
        max_live_lag_sec: float,
        min_matches: int,
        min_inliers: int,
        ratio_test: float,
        ransac_thresh: float,
        perf_mode: str,
        process_scale: float | None,
        adaptive_seam: str,
        seam_update_interval: int,
        seam_temporal_penalty: float,
        seam_motion_weight: float,
    ) -> str:
        with self._lock:
            if self._running:
                return "already running"
            self._running = True
            self._status = "starting"
            self._stop_event.clear()
            self._latest_left = None
            self._latest_right = None
            self._latest_stitched = None
            self._report_text = '{"mode":"rtsp_preview","stitching":false}'
            self._out_path = str(Path(out)) if out else str(OUTPUT_VIDEOS / f"live_gui_{_now_tag()}.mp4")
            self._report_path = str(Path(report)) if report else str(OUTPUT_VIDEOS / f"live_gui_{_now_tag()}_report.json")

        reconnect_delay = max(0.2, float(reconnect_cooldown_sec))
        timeout_us = max(1, int(max(0.1, float(rtsp_timeout_sec)) * 1_000_000))
        transport = str(rtsp_transport).strip().lower()
        if transport not in {"tcp", "udp"}:
            transport = "tcp"

        def _open_capture(url: str) -> cv2.VideoCapture:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                f"rtsp_transport;{transport}|stimeout;{timeout_us}|fflags;nobuffer|flags;low_delay"
            )
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            cap.release()
            cap = cv2.VideoCapture(url)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            cap.release()
            raise RuntimeError(f"cannot open stream: {url}")

        def _reader_loop(slot: str, url: str) -> None:
            cap: cv2.VideoCapture | None = None
            try:
                while not self._stop_event.is_set():
                    try:
                        if cap is None:
                            cap = _open_capture(url)
                        ok = cap.grab()
                        if not ok:
                            cap.release()
                            cap = None
                            time.sleep(reconnect_delay)
                            continue
                        ok, frame = cap.retrieve()
                        if not ok or frame is None:
                            continue

                        rgb = _to_rgb(frame)
                        with self._lock:
                            if slot == "left":
                                self._latest_left = rgb
                            else:
                                self._latest_right = rgb
                    except Exception:
                        if cap is not None:
                            cap.release()
                            cap = None
                        time.sleep(reconnect_delay)
            finally:
                if cap is not None:
                    cap.release()

        def run() -> None:
            workers: list[threading.Thread] = []
            try:
                with self._lock:
                    self._status = "connecting"

                if left_rtsp.strip():
                    workers.append(threading.Thread(target=_reader_loop, args=("left", left_rtsp), daemon=True))
                if right_rtsp.strip():
                    workers.append(threading.Thread(target=_reader_loop, args=("right", right_rtsp), daemon=True))
                if not workers:
                    raise RuntimeError("left/right RTSP URL is empty")

                for worker in workers:
                    worker.start()

                while not self._stop_event.is_set():
                    with self._lock:
                        has_any_frame = (self._latest_left is not None) or (self._latest_right is not None)
                        self._status = "previewing" if has_any_frame else "connecting"
                    if not any(worker.is_alive() for worker in workers):
                        raise RuntimeError("all rtsp reader threads stopped")
                    time.sleep(0.01)
            except Exception as exc:  # pragma: no cover - GUI guard
                with self._lock:
                    self._status = f"failed: {exc}"
            finally:
                self._stop_event.set()
                for worker in workers:
                    worker.join(timeout=1.0)
                with self._lock:
                    self._running = False
                    if not self._status.startswith("failed"):
                        self._status = "stopped"

        thread = threading.Thread(target=run, daemon=True, name="live-gui-session")
        thread.start()
        with self._lock:
            self._thread = thread
        return f"live started: out={self._out_path}, report={self._report_path}"

    def stop(self) -> str:
        with self._lock:
            if not self._running:
                return "not running"
            self._stop_event.set()
            self._status = "stopping"
        return "stop requested"

    def snapshot(self) -> LiveSnapshot:
        with self._lock:
            report_text = self._report_text
            if (not report_text) and self._report_path:
                rp = Path(self._report_path)
                if rp.exists():
                    report_text = _read_text(rp)
            return LiveSnapshot(
                running=self._running,
                status=self._status,
                out_path=self._out_path,
                report_path=self._report_path,
                left=self._latest_left,
                right=self._latest_right,
                stitched=self._latest_stitched,
                report_text=report_text,
            )


class ServeSession:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self.host = "127.0.0.1"
        self.port = 8080
        self.storage = "storage"

    def start(self, host: str, port: int, storage_dir: str) -> str:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return "serve server already running"
            self.host = host
            self.port = int(port)
            self.storage = storage_dir
            cmd = [
                sys.executable,
                "-m",
                "stitching",
                "serve",
                "--host",
                host,
                "--port",
                str(port),
                "--storage-dir",
                storage_dir,
            ]
            self._proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
            return f"serve started: http://{host}:{port}"

    def stop(self) -> str:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                return "serve server not running"
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            return "serve stopped"

    def health(self) -> str:
        url = f"http://{self.host}:{self.port}/health"
        try:
            with urlopen(url, timeout=2) as resp:
                body = resp.read().decode("utf-8")
            return f"health OK: {body}"
        except Exception as exc:
            return f"health failed: {exc}"

    def submit_video_job(self, host: str, port: int, left_path: str, right_path: str, options_json: str) -> str:
        url = f"http://{host}:{port}/jobs/video-stitch"
        try:
            options = json.loads(options_json) if options_json.strip() else {}
            payload = {
                "left_path": left_path,
                "right_path": right_path,
                "options": options,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
            with urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
            return body
        except HTTPError as exc:
            return f"HTTPError {exc.code}: {exc.read().decode('utf-8', errors='ignore')}"
        except URLError as exc:
            return f"URLError: {exc}"
        except Exception as exc:
            return f"submit failed: {exc}"

    def get_job(self, host: str, port: int, job_id: str) -> str:
        url = f"http://{host}:{port}/jobs/{job_id}"
        try:
            with urlopen(url, timeout=5) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            return f"query failed: {exc}"

    def get_report(self, host: str, port: int, job_id: str) -> str:
        url = f"http://{host}:{port}/jobs/{job_id}/report"
        try:
            with urlopen(url, timeout=5) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            return f"report query failed: {exc}"

    def get_artifact(self, host: str, port: int, job_id: str) -> str:
        url = f"http://{host}:{port}/jobs/{job_id}/artifact"
        try:
            with urlopen(url, timeout=5) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:
            return f"artifact query failed: {exc}"


LIVE_SESSION = LiveSession()
SERVE_SESSION = ServeSession()


def _poll_live_view() -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None, str, str, str | None]:
    snap = LIVE_SESSION.snapshot()
    status = (
        f": {snap.status}\n"
        f": {snap.running}\n"
        f"output: {snap.out_path}\n"
        f"report: {snap.report_path}"
    )
    video_path = snap.out_path if snap.out_path and Path(snap.out_path).exists() else None
    return snap.left, snap.right, snap.stitched, status, snap.report_text, video_path


def build_gui() -> gr.Blocks:
    with gr.Blocks(title="Dual Smartphone Stitching GUI") as app:
        gr.Markdown("# Dual Smartphone Stitching GUI")
        gr.Markdown("Control offline/preset/live/serve features in one page.")

        with gr.Tab("Offline Video", visible=False):
            with gr.Row():
                left = gr.Textbox(label="Left Video", value=str(INPUT_VIDEOS / "Video10_left.mp4"))
                right = gr.Textbox(label="Right Video", value=str(INPUT_VIDEOS / "Video10_right.mp4"))
            with gr.Row():
                out = gr.Textbox(label="Output Video", value=str(OUTPUT_VIDEOS / "gui_video_stitched.mp4"))
                report = gr.Textbox(label="Report JSON", value=str(OUTPUT_VIDEOS / "gui_video_report.json"))
                debug_dir = gr.Textbox(label="Debug Dir", value=str(OUTPUT_DEBUG / "gui_video"))
            max_duration_sec = gr.Number(label="Max Duration (sec)", value=30.0)

            with gr.Accordion("Advanced Options", open=False):
                with gr.Row():
                    min_matches = gr.Number(label="min_matches", value=80)
                    min_inliers = gr.Number(label="min_inliers", value=30)
                    ratio_test = gr.Number(label="ratio_test", value=0.75)
                    ransac_thresh = gr.Number(label="ransac_thresh", value=5.0)
                with gr.Row():
                    calib_start_sec = gr.Number(label="calib_start_sec", value=0.0)
                    calib_end_sec = gr.Number(label="calib_end_sec", value=10.0)
                    calib_step_sec = gr.Number(label="calib_step_sec", value=1.0)
                with gr.Row():
                    perf_mode = gr.Dropdown(["quality", "balanced", "fast"], value="quality", label="perf_mode")
                    process_scale = gr.Number(label="process_scale (empty = use perf_mode)", value=None)
                    homography_mode = gr.Dropdown(["off", "auto", "reuse", "refresh"], value="off", label="homography_mode")
                    homography_file = gr.Textbox(label="homography_file", value="")
                with gr.Row():
                    adaptive_seam = gr.Dropdown(["off", "on"], value="off", label="adaptive_seam")
                    seam_update_interval = gr.Number(label="seam_update_interval", value=12)
                    seam_temporal_penalty = gr.Number(label="seam_temporal_penalty", value=1.5)
                    seam_motion_weight = gr.Number(label="seam_motion_weight", value=1.5)

            run_btn = gr.Button("Run Offline Stitch", variant="primary")
            run_status = gr.Textbox(label="Result")
            run_report = gr.Code(label="report.json", language="json")
            run_video = gr.Video(label="Output Video")

            run_btn.click(
                fn=run_offline_video,
                inputs=[
                    left,
                    right,
                    out,
                    report,
                    debug_dir,
                    max_duration_sec,
                    min_matches,
                    min_inliers,
                    ratio_test,
                    ransac_thresh,
                    calib_start_sec,
                    calib_end_sec,
                    calib_step_sec,
                    perf_mode,
                    process_scale,
                    homography_mode,
                    homography_file,
                    adaptive_seam,
                    seam_update_interval,
                    seam_temporal_penalty,
                    seam_motion_weight,
                ],
                outputs=[run_status, run_report, run_video],
            )

        with gr.Tab("Preset Video", visible=False):
            with gr.Row():
                preset = gr.Dropdown(["video-10s", "video-30s", "video-full"], value="video-10s", label="Preset")
                pair = gr.Textbox(label="pair()", value="video10")
                p_left = gr.Textbox(label="left()", value="")
                p_right = gr.Textbox(label="right()", value="")
            with gr.Row():
                input_dir = gr.Textbox(label="input_dir", value=str(INPUT_VIDEOS))
                output_dir = gr.Textbox(label="output_dir", value=str(OUTPUT_VIDEOS))
                debug_root = gr.Textbox(label="debug_root", value=str(OUTPUT_DEBUG))

            with gr.Accordion("Advanced Options", open=False):
                p_min_matches = gr.Number(label="min_matches", value=80)
                p_min_inliers = gr.Number(label="min_inliers", value=30)
                p_ratio_test = gr.Number(label="ratio_test", value=0.75)
                p_ransac_thresh = gr.Number(label="ransac_thresh", value=5.0)
                p_calib_start_sec = gr.Number(label="calib_start_sec", value=0.0)
                p_calib_end_sec = gr.Number(label="calib_end_sec", value=10.0)
                p_calib_step_sec = gr.Number(label="calib_step_sec", value=1.0)
                p_perf_mode = gr.Dropdown(["quality", "balanced", "fast"], value="quality", label="perf_mode")
                p_process_scale = gr.Number(label="process_scale", value=None)
                p_h_mode = gr.Dropdown(["off", "auto", "reuse", "refresh"], value="off", label="homography_mode")
                p_h_file = gr.Textbox(label="homography_file", value="")
                p_adaptive_seam = gr.Dropdown(["off", "on"], value="off", label="adaptive_seam")
                p_seam_update_interval = gr.Number(label="seam_update_interval", value=12)
                p_seam_temporal_penalty = gr.Number(label="seam_temporal_penalty", value=1.5)
                p_seam_motion_weight = gr.Number(label="seam_motion_weight", value=1.5)

            p_btn = gr.Button("Run Preset Stitch", variant="primary")
            p_status = gr.Textbox(label="Result")
            p_report = gr.Code(label="report.json", language="json")
            p_video = gr.Video(label="Output Video")

            p_btn.click(
                fn=run_preset_video,
                inputs=[
                    preset,
                    pair,
                    p_left,
                    p_right,
                    input_dir,
                    output_dir,
                    debug_root,
                    p_min_matches,
                    p_min_inliers,
                    p_ratio_test,
                    p_ransac_thresh,
                    p_calib_start_sec,
                    p_calib_end_sec,
                    p_calib_step_sec,
                    p_perf_mode,
                    p_process_scale,
                    p_h_mode,
                    p_h_file,
                    p_adaptive_seam,
                    p_seam_update_interval,
                    p_seam_temporal_penalty,
                    p_seam_motion_weight,
                ],
                outputs=[p_status, p_report, p_video],
            )

        with gr.Tab("Live RTSP"):
            with gr.Row():
                left_rtsp = gr.Textbox(label="Left RTSP", value="rtsp://admin:***@192.168.0.137:554/cam/realmonitor?channel=1&subtype=0")
                right_rtsp = gr.Textbox(label="Right RTSP", value="rtsp://admin:***@192.168.0.138:554/cam/realmonitor?channel=1&subtype=0")
            with gr.Row():
                live_out = gr.Textbox(label="Output Video", value=str(OUTPUT_VIDEOS / "live_gui_stitched.mp4"))
                live_report = gr.Textbox(label="Report JSON", value=str(OUTPUT_VIDEOS / "live_gui_report.json"))
                live_debug = gr.Textbox(label="Debug Dir", value=str(OUTPUT_DEBUG / "live_gui"))

            with gr.Accordion("Live Options", open=True):
                with gr.Row():
                    live_max_duration = gr.Number(label="max_duration_sec", value=30.0)
                    live_output_fps = gr.Number(label="output_fps (0=uncapped)", value=0.0)
                    live_perf_mode = gr.Dropdown(["quality", "balanced", "fast"], value="balanced", label="perf_mode")
                    live_process_scale = gr.Number(label="process_scale", value=None)
                with gr.Row():
                    live_min_matches = gr.Number(label="min_matches", value=80)
                    live_min_inliers = gr.Number(label="min_inliers", value=30)
                    live_ratio_test = gr.Number(label="ratio_test", value=0.75)
                    live_ransac_thresh = gr.Number(label="ransac_thresh", value=5.0)
                with gr.Row():
                    live_calib_max_attempts = gr.Number(label="calib_max_attempts", value=180)
                    live_max_read_failures = gr.Number(label="max_read_failures", value=45)
                    live_reconnect_cooldown = gr.Number(label="reconnect_cooldown_sec", value=1.0)
                    live_transport = gr.Dropdown(["tcp", "udp"], value="tcp", label="rtsp_transport")
                    live_timeout = gr.Number(label="rtsp_timeout_sec", value=10.0)
                with gr.Row():
                    live_sync_buffer = gr.Number(label="sync_buffer_sec", value=2.0)
                    live_sync_delta = gr.Number(label="sync_match_max_delta_ms", value=80.0)
                    live_sync_offset = gr.Number(label="sync_manual_offset_ms", value=0.0)
                    live_sync_timeout = gr.Number(label="sync_no_pair_timeout_sec", value=8.0)
                with gr.Row():
                    live_sync_mode = gr.Dropdown(["latest", "oldest", "service"], value="service", label="sync_pair_mode")
                    live_max_lag = gr.Number(label="max_live_lag_sec", value=1.0)
                    live_adaptive_seam = gr.Dropdown(["off", "on"], value="off", label="adaptive_seam")
                    live_seam_interval = gr.Number(label="seam_update_interval", value=12)
                    live_seam_temporal = gr.Number(label="seam_temporal_penalty", value=1.5)
                    live_seam_motion = gr.Number(label="seam_motion_weight", value=1.5)

            with gr.Row():
                live_start = gr.Button("Start Live", variant="primary")
                live_stop = gr.Button("Stop Live")
            live_status = gr.Textbox(label="Live Status")

            with gr.Row():
                view_left = gr.Image(label="Left Live", type="numpy")
                view_right = gr.Image(label="Right Live", type="numpy")
                view_stitched = gr.Image(label="Stitch Output (disabled)", type="numpy")
            view_report = gr.Code(label="live report.json", language="json")
            view_video = gr.Video(label="Saved Output Video")

            live_start.click(
                fn=LIVE_SESSION.start,
                inputs=[
                    left_rtsp,
                    right_rtsp,
                    live_out,
                    live_report,
                    live_debug,
                    live_max_duration,
                    live_output_fps,
                    live_calib_max_attempts,
                    live_max_read_failures,
                    live_reconnect_cooldown,
                    live_transport,
                    live_timeout,
                    live_sync_buffer,
                    live_sync_delta,
                    live_sync_offset,
                    live_sync_timeout,
                    live_sync_mode,
                    live_max_lag,
                    live_min_matches,
                    live_min_inliers,
                    live_ratio_test,
                    live_ransac_thresh,
                    live_perf_mode,
                    live_process_scale,
                    live_adaptive_seam,
                    live_seam_interval,
                    live_seam_temporal,
                    live_seam_motion,
                ],
                outputs=[live_status],
            )
            live_stop.click(fn=LIVE_SESSION.stop, outputs=[live_status])

            timer = gr.Timer(0.01)
            timer.tick(
                fn=_poll_live_view,
                outputs=[view_left, view_right, view_stitched, live_status, view_report, view_video],
            )

        with gr.Tab("(serve)"):
            with gr.Row():
                serve_host = gr.Textbox(label="host", value="127.0.0.1")
                serve_port = gr.Number(label="port", value=8080)
                serve_storage = gr.Textbox(label="storage_dir", value="storage")
            with gr.Row():
                serve_start = gr.Button("serve ", variant="primary")
                serve_stop = gr.Button("serve ")
                serve_health = gr.Button("health check")
            serve_status = gr.Textbox(label="serve ")
            serve_start.click(fn=SERVE_SESSION.start, inputs=[serve_host, serve_port, serve_storage], outputs=[serve_status])
            serve_stop.click(fn=SERVE_SESSION.stop, outputs=[serve_status])
            serve_health.click(fn=SERVE_SESSION.health, outputs=[serve_status])

            gr.Markdown("### API ")
            with gr.Row():
                api_host = gr.Textbox(label="API host", value="127.0.0.1")
                api_port = gr.Number(label="API port", value=8080)
            with gr.Row():
                api_left = gr.Textbox(label="left_path", value=str(INPUT_VIDEOS / "Video10_left.mp4"))
                api_right = gr.Textbox(label="right_path", value=str(INPUT_VIDEOS / "Video10_right.mp4"))
            api_options = gr.Code(
                label="options(json)",
                language="json",
                value='{"max_duration_sec": 10, "perf_mode": "balanced", "adaptive_seam": false}',
            )
            submit_job_btn = gr.Button("video-stitch  ")
            api_result = gr.Code(label="API ", language="json")
            submit_job_btn.click(
                fn=SERVE_SESSION.submit_video_job,
                inputs=[api_host, api_port, api_left, api_right, api_options],
                outputs=[api_result],
            )

            with gr.Row():
                query_job_id = gr.Textbox(label="job_id")
                get_job_btn = gr.Button("GET /jobs/{id}")
                get_report_btn = gr.Button("GET /jobs/{id}/report")
                get_artifact_btn = gr.Button("GET /jobs/{id}/artifact")
            get_job_btn.click(fn=SERVE_SESSION.get_job, inputs=[api_host, api_port, query_job_id], outputs=[api_result])
            get_report_btn.click(
                fn=SERVE_SESSION.get_report,
                inputs=[api_host, api_port, query_job_id],
                outputs=[api_result],
            )
            get_artifact_btn.click(
                fn=SERVE_SESSION.get_artifact,
                inputs=[api_host, api_port, query_job_id],
                outputs=[api_result],
            )

    return app


def run_gui(host: str = "127.0.0.1", port: int = 7860, share: bool = False) -> None:
    app = build_gui()
    app.queue(default_concurrency_limit=8)
    app.launch(server_name=host, server_port=port, share=share, inbrowser=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stitching GUI launcher")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--share", action="store_true")
    args = parser.parse_args()
    run_gui(host=args.host, port=args.port, share=args.share)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
