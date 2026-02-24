from __future__ import annotations

import json
import time
from contextlib import ContextDecorator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stitching.errors import ErrorCode


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class StageTimer(ContextDecorator):
    """Measure elapsed seconds for each pipeline stage."""

    def __init__(self, stage_times: dict[str, float], stage_name: str) -> None:
        self.stage_times = stage_times
        self.stage_name = stage_name
        self._start: float | None = None

    def __enter__(self) -> "StageTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        end = time.perf_counter()
        if self._start is not None:
            self.stage_times[self.stage_name] = round(end - self._start, 4)


def base_report(
    pipeline: str, inputs: dict[str, str], job_id: str | None = None
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "pipeline": pipeline,
        "created_at": utc_now_iso(),
        "status": "failed",
        "error_code": ErrorCode.NONE.value,
        "reason_detail": "",
        "inputs": inputs,
        "warnings": [],
        "metrics": {
            "matches_count": 0,
            "inliers_count": 0,
            "processing_time_sec": {},
            "output_resolution": None,
            "estimated_sync_offset_ms": None,
            "processed_frames": None,
        },
    }


def finalize_total_time(report: dict[str, Any], started_at: float) -> None:
    total = round(time.perf_counter() - started_at, 4)
    report["metrics"]["processing_time_sec"]["total"] = total


def mark_failed(report: dict[str, Any], error_code: ErrorCode, detail: str) -> None:
    report["status"] = "failed"
    report["error_code"] = error_code.value
    report["reason_detail"] = detail


def mark_succeeded(report: dict[str, Any]) -> None:
    report["status"] = "succeeded"
    report["error_code"] = ErrorCode.NONE.value
    report["reason_detail"] = ""


def write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

