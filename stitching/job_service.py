from __future__ import annotations

import json
import queue
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from stitching.errors import ErrorCode
from stitching.video_stitching import VideoConfig, stitch_videos


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class JobItem:
    """워커 큐에 들어가는 영상 스티칭 작업 단위."""

    job_id: str
    left_path: Path
    right_path: Path
    options: dict[str, Any]


class JobManager:
    """영상 스티칭 잡 생성/실행/상태조회 관리자."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = storage_dir
        self.raw_dir = storage_dir / "raw"
        self.debug_dir = storage_dir / "debug"
        self.out_dir = storage_dir / "out"
        self.report_dir = storage_dir / "report"
        self.jobs_dir = storage_dir / "jobs"
        for path in [self.raw_dir, self.debug_dir, self.out_dir, self.report_dir, self.jobs_dir]:
            path.mkdir(parents=True, exist_ok=True)

        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[JobItem | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="stitch-worker")

    def start(self) -> None:
        if not self._worker.is_alive():
            self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        self._worker.join(timeout=3)

    def submit_video_job(
        self,
        left_path: str,
        right_path: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """원본 파일을 storage/raw에 복사한 뒤 큐에 넣는다."""

        options = options or {}
        left_src = Path(left_path)
        right_src = Path(right_path)
        if not left_src.exists() or not right_src.exists():
            raise FileNotFoundError("left/right input path does not exist")

        job_id = str(uuid.uuid4())
        job_raw_dir = self.raw_dir / job_id
        job_raw_dir.mkdir(parents=True, exist_ok=True)
        left_dst = job_raw_dir / f"left{left_src.suffix.lower()}"
        right_dst = job_raw_dir / f"right{right_src.suffix.lower()}"
        shutil.copy2(left_src, left_dst)
        shutil.copy2(right_src, right_dst)

        created_at = _now_iso()
        job_state = {
            "job_id": job_id,
            "kind": "video",
            "status": "queued",
            "error_code": ErrorCode.NONE.value,
            "reason_detail": "",
            "artifact_path": None,
            "report_path": str(self.report_dir / f"{job_id}.json"),
            "created_at": created_at,
            "updated_at": created_at,
        }
        with self._lock:
            self._jobs[job_id] = job_state
        self._persist_job(job_id)

        self._queue.put(JobItem(job_id=job_id, left_path=left_dst, right_path=right_dst, options=options))
        return job_state

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return None
            return dict(state)

    def _update_job(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            state = self._jobs[job_id]
            state.update(updates)
            state["updated_at"] = _now_iso()
        self._persist_job(job_id)

    def _persist_job(self, job_id: str) -> None:
        with self._lock:
            payload = dict(self._jobs[job_id])
        path = self.jobs_dir / f"{job_id}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None:
                break
            self._process_job(item)

    def _process_job(self, item: JobItem) -> None:
        """영상 스티칭 워커 본체."""

        report_path = self.report_dir / f"{item.job_id}.json"
        debug_dir = self.debug_dir / item.job_id
        debug_dir.mkdir(parents=True, exist_ok=True)

        try:
            out_path = self.out_dir / f"{item.job_id}.mp4"
            config = VideoConfig(
                min_matches=int(item.options.get("min_matches", 80)),
                min_inliers=int(item.options.get("min_inliers", 30)),
                ratio_test=float(item.options.get("ratio_test", 0.75)),
                ransac_reproj_threshold=float(item.options.get("ransac_thresh", 5.0)),
                max_duration_sec=float(item.options.get("max_duration_sec", 30.0)),
                calib_start_sec=float(item.options.get("calib_start_sec", 0.0)),
                calib_end_sec=float(item.options.get("calib_end_sec", 10.0)),
                calib_step_sec=float(item.options.get("calib_step_sec", 1.0)),
            )

            hook = self._video_status_hook(item.job_id)
            report = stitch_videos(
                left_path=item.left_path,
                right_path=item.right_path,
                output_path=out_path,
                report_path=report_path,
                debug_dir=debug_dir,
                config=config,
                job_id=item.job_id,
                status_hook=hook,
            )

            if report["status"] == "succeeded":
                self._update_job(
                    item.job_id,
                    status="succeeded",
                    error_code=ErrorCode.NONE.value,
                    reason_detail="",
                    artifact_path=str(out_path),
                )
            else:
                self._update_job(
                    item.job_id,
                    status="failed",
                    error_code=report.get("error_code", ErrorCode.INTERNAL_ERROR.value),
                    reason_detail=report.get("reason_detail", ""),
                    artifact_path=None,
                )
        except Exception as exc:  # pragma: no cover - 워커 안전망
            self._update_job(
                item.job_id,
                status="failed",
                error_code=ErrorCode.INTERNAL_ERROR.value,
                reason_detail=f"worker failed: {exc}",
                artifact_path=None,
            )

    def _video_status_hook(self, job_id: str):
        """스테이지 문자열을 잡 상태값으로 반영한다."""

        def hook(stage: str) -> None:
            if stage == "probing":
                self._update_job(job_id, status="probing")
            else:
                self._update_job(job_id, status="stitching")

        return hook


def run_server(host: str, port: int, storage_dir: Path) -> None:
    manager = JobManager(storage_dir=storage_dir)
    manager.start()

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:
            path = self.path.strip("/")
            if path == "health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return

            parts = path.split("/")
            if len(parts) == 2 and parts[0] == "jobs":
                job = manager.get_job(parts[1])
                if job is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                    return
                self._send_json(HTTPStatus.OK, job)
                return

            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "report":
                job = manager.get_job(parts[1])
                if job is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                    return
                report_path = Path(job["report_path"])
                if not report_path.exists():
                    self._send_json(HTTPStatus.ACCEPTED, {"status": job["status"]})
                    return
                payload = json.loads(report_path.read_text(encoding="utf-8"))
                self._send_json(HTTPStatus.OK, payload)
                return

            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "artifact":
                job = manager.get_job(parts[1])
                if job is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                    return
                if job.get("artifact_path") is None:
                    self._send_json(HTTPStatus.CONFLICT, {"status": job["status"], "artifact_path": None})
                    return
                self._send_json(HTTPStatus.OK, {"artifact_path": job["artifact_path"]})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            path = self.path.strip("/")
            if path != "jobs/video-stitch":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return

            try:
                payload = self._read_json()
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
                return

            left_path = payload.get("left_path")
            right_path = payload.get("right_path")
            options = payload.get("options", {})
            if not isinstance(left_path, str) or not isinstance(right_path, str):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "left_path/right_path required"})
                return
            if not isinstance(options, dict):
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "options must be object"})
                return

            try:
                job = manager.submit_video_job(
                    left_path=left_path,
                    right_path=right_path,
                    options=options,
                )
            except FileNotFoundError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except Exception as exc:  # pragma: no cover - 방어 코드
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"submit failed: {exc}"})
                return

            self._send_json(HTTPStatus.ACCEPTED, job)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"stitching server listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.stop()
