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


KEY_LABELS: dict[str, str] = {
    "job_id": "작업ID",
    "pipeline": "파이프라인",
    "created_at": "생성시각",
    "status": "상태",
    "error_code": "오류코드",
    "reason_detail": "상세사유",
    "inputs": "입력",
    "warnings": "경고",
    "metrics": "메트릭",
    "matches_count": "매칭개수",
    "inliers_count": "인라이어개수",
    "processing_time_sec": "처리시간_초",
    "output_resolution": "출력해상도",
    "processed_frames": "처리프레임수",
    "total": "전체",
    "probe": "입력확인",
    "homography": "호모그래피",
    "frame_loop": "프레임루프",
    "perf_mode": "성능모드",
    "processing_scale": "처리스케일",
    "adaptive_seam_enabled": "적응심활성화",
    "seam_update_interval": "심업데이트간격",
    "seam_temporal_penalty": "심시간패널티",
    "seam_motion_weight": "심움직임가중치",
    "seam_updates": "심업데이트횟수",
    "homography_mode_requested": "호모그래피모드요청",
    "homography_file": "호모그래피파일",
    "homography_source": "호모그래피소스",
    "calib_candidates": "캘리브후보",
    "calib_candidates_total": "캘리브후보전체",
    "calib_candidates_valid": "캘리브후보유효",
    "calib_used_time_sec": "캘리브사용시각_초",
    "calib_best_inliers": "캘리브최고인라이어",
    "calib_best_reproj_error": "캘리브최고재투영오차",
    "saved_h_reproj_error": "저장H재투영오차",
    "homography_saved": "호모그래피저장여부",
    "blend_mode": "블렌딩모드",
    "seam_x": "심X",
    "seam_x_initial": "초기심X",
    "seam_x_final": "최종심X",
    "seam_shift_abs_mean": "심이동절대평균",
    "overlap_diff_mean": "오버랩차이평균",
    "exposure_gain": "노출게인",
    "exposure_bias": "노출바이어스",
    "mode": "모드",
    "process_scale": "처리스케일",
    "output_fps": "출력FPS",
    "rtsp_transport": "RTSP전송방식",
    "rtsp_timeout_sec": "RTSP타임아웃_초",
    "sync_buffer_sec": "동기버퍼_초",
    "sync_match_max_delta_ms": "동기매칭최대오차_ms",
    "sync_manual_offset_ms": "동기수동오프셋_ms",
    "sync_no_pair_timeout_sec": "동기페어없음타임아웃_초",
    "sync_pair_mode": "동기페어모드",
    "max_live_lag_sec": "최대라이브지연_초",
    "target_output_frames": "목표출력프레임수",
    "stitched_frames": "스티칭프레임수",
    "catchup_frames": "따라잡기프레임수",
    "hold_frames": "유지프레임수",
    "dropped_pairs": "드롭된페어수",
    "unmatched_pairs": "미매칭페어수",
    "reconnect_left_count": "좌재연결횟수",
    "reconnect_right_count": "우재연결횟수",
    "left_read_failures": "좌읽기실패수",
    "right_read_failures": "우읽기실패수",
    "left_buffer_overflow_drops": "좌버퍼오버플로드롭수",
    "right_buffer_overflow_drops": "우버퍼오버플로드롭수",
    "left_stale_drops": "좌오래된프레임드롭수",
    "right_stale_drops": "우오래된프레임드롭수",
    "pair_skew_ms_mean": "페어시간차평균_ms",
    "pair_skew_ms_abs_p95": "페어시간차절대P95_ms",
    "pair_skew_ms_abs_max": "페어시간차절대최대_ms",
    "observed_output_fps": "관측출력FPS",
    "observed_stitch_fps": "관측스티칭FPS",
    "output_duration_sec": "출력길이_초",
    "stop_reason": "종료사유",
    "left_path": "좌영상경로",
    "right_path": "우영상경로",
    "left_rtsp": "좌RTSP주소",
    "right_rtsp": "우RTSP주소",
    "time_sec": "시각_초",
    "matches_count": "매칭개수",
    "inliers_count": "인라이어개수",
    "reproj_error": "재투영오차",
    "valid": "유효여부",
}


def _bilingual_key(key: str) -> str:
    label = KEY_LABELS.get(key, "항목")
    return f"{label}({key})"


def _localize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        localized: dict[str, Any] = {}
        for key, item in value.items():
            localized[_bilingual_key(str(key))] = _localize_keys(item)
        return localized
    if isinstance(value, list):
        return [_localize_keys(item) for item in value]
    return value


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
    # 모든 파이프라인이 공통으로 가지는 최소 리포트 골격.
    # 세부 메트릭은 각 파이프라인에서 추가한다.
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
    localized_report = _localize_keys(report)
    report_path.write_text(
        json.dumps(localized_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
