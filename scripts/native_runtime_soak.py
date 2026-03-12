from __future__ import annotations

import json
import os
import sys
import time
import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from stitching.runtime_client import RuntimeClient
from stitching.runtime_launcher import RuntimeLaunchSpec


DEFAULT_LEFT_RTSP = "rtsp://admin:admin123@192.168.0.137:554/cam/realmonitor?channel=1&subtype=0"
DEFAULT_RIGHT_RTSP = "rtsp://admin:admin123@192.168.0.138:554/cam/realmonitor?channel=1&subtype=0"
DEFAULT_UDP_TARGET = "udp://127.0.0.1:23000?pkt_size=1316"
REPORT_DIR = REPO_ROOT / "output" / "native" / "soak"
MIN_OUTPUT_FRAMES = 30
MAX_ALLOWED_PAIR_SKEW_MS = 250.0
MAX_ALLOWED_INPUT_AGE_MS = 3000.0


@dataclass(slots=True)
class SoakCase:
    name: str
    duration_sec: float
    homography_file: str
    output_target: str
    output_codec: str
    output_muxer: str = ""
    recalibrate_after_sec: float = 0.0
    recalibrate_homography_file: str = ""


def _env(name: str, fallback: str) -> str:
    return os.environ.get(name, "").strip() or fallback


def _default_cases(*, small_duration_sec: float, large_duration_sec: float) -> list[SoakCase]:
    homography_path = REPO_ROOT / "output" / "native" / "runtime_homography.json"
    homography = str(homography_path) if homography_path.exists() else ""
    return [
        SoakCase(
            name="small_udp_h264_nvenc",
            duration_sec=small_duration_sec,
            homography_file="",
            output_target=DEFAULT_UDP_TARGET,
            output_codec="h264_nvenc",
        ),
        SoakCase(
            name="large_udp_auto_hevc",
            duration_sec=large_duration_sec,
            homography_file=homography,
            output_target=DEFAULT_UDP_TARGET,
            output_codec="h264_nvenc",
        ),
        SoakCase(
            name="large_udp_recalibration",
            duration_sec=max(large_duration_sec, 30.0),
            homography_file=homography,
            output_target=DEFAULT_UDP_TARGET,
            output_codec="h264_nvenc",
            recalibrate_after_sec=max(5.0, min(15.0, large_duration_sec / 2.0 if large_duration_sec > 0 else 10.0)),
            recalibrate_homography_file=homography,
        ),
    ]


def _evaluate_result(result: dict) -> dict:
    metrics = result.get("metrics", {}) if isinstance(result.get("metrics"), dict) else {}
    reasons: list[str] = []
    if str(result.get("failure_message") or "").strip():
        reasons.append("failure_message")
    if int(result.get("returncode") or 0) != 0:
        reasons.append(f"returncode={result.get('returncode')}")
    if bool(result.get("shutdown_forced")):
        reasons.append("shutdown_forced")
    if str(metrics.get("status") or "").endswith("failed"):
        reasons.append(f"status={metrics.get('status')}")
    if not bool(metrics.get("output_active")):
        reasons.append("output_inactive")
    if int(metrics.get("output_frames_written") or 0) < MIN_OUTPUT_FRAMES:
        reasons.append(f"output_frames_written<{MIN_OUTPUT_FRAMES}")
    if str(metrics.get("output_last_error") or "").strip():
        reasons.append("output_last_error")
    if str(metrics.get("left_last_error") or "").strip():
        reasons.append("left_last_error")
    if str(metrics.get("right_last_error") or "").strip():
        reasons.append("right_last_error")
    if float(metrics.get("left_age_ms") or 0.0) > MAX_ALLOWED_INPUT_AGE_MS:
        reasons.append(f"left_age_ms>{MAX_ALLOWED_INPUT_AGE_MS}")
    if float(metrics.get("right_age_ms") or 0.0) > MAX_ALLOWED_INPUT_AGE_MS:
        reasons.append(f"right_age_ms>{MAX_ALLOWED_INPUT_AGE_MS}")

    pair_skew_ms = float(metrics.get("pair_skew_ms_mean") or 0.0)
    if pair_skew_ms > MAX_ALLOWED_PAIR_SKEW_MS:
        reasons.append(f"pair_skew_ms_mean>{MAX_ALLOWED_PAIR_SKEW_MS}")

    return {
        "passed": len(reasons) == 0,
        "reasons": reasons,
        "criteria": {
            "returncode": 0,
            "shutdown_forced": False,
            "output_active": True,
            "min_output_frames_written": MIN_OUTPUT_FRAMES,
            "output_last_error": "",
            "left_last_error": "",
            "right_last_error": "",
            "max_input_age_ms": MAX_ALLOWED_INPUT_AGE_MS,
            "max_pair_skew_ms_mean": MAX_ALLOWED_PAIR_SKEW_MS,
        },
    }


def _collect_case(case: SoakCase, *, left_rtsp: str, right_rtsp: str) -> dict:
    spec = RuntimeLaunchSpec(
        emit_hello=True,
        heartbeat_ms=1000,
        left_rtsp=left_rtsp,
        right_rtsp=right_rtsp,
        input_runtime="ffmpeg-cuda",
        homography_file=case.homography_file,
        transport="tcp",
        timeout_sec=10.0,
        reconnect_cooldown_sec=1.0,
        output_runtime="ffmpeg",
        output_target=case.output_target,
        output_codec=case.output_codec,
        output_bitrate="6M",
        output_preset="p1",
        output_muxer=case.output_muxer,
        output_width=1920,
        output_height=1080,
        sync_pair_mode="latest",
        sync_match_max_delta_ms=60.0,
        sync_manual_offset_ms=0.0,
        stitch_output_scale=0.25,
    )
    client = RuntimeClient.launch(spec)
    last_metrics: dict[str, object] = {}
    shutdown_forced = False
    recalibration_sent = False
    started_at = time.time()
    hello: dict[str, object] = {}
    failure_message = ""
    try:
        hello = client.wait_for_hello(timeout_sec=5.0).raw
        deadline = time.time() + case.duration_sec
        while time.time() < deadline:
            elapsed_sec = time.time() - started_at
            if (
                not recalibration_sent
                and case.recalibrate_after_sec > 0.0
                and elapsed_sec >= case.recalibrate_after_sec
            ):
                client.reload_homography(case.recalibrate_homography_file or case.homography_file)
                recalibration_sent = True
            event = client.read_event(timeout_sec=1.5)
            if event is None:
                if client.process.poll() is not None:
                    break
                continue
            if event.type == "metrics":
                last_metrics = dict(event.payload)
    except Exception as exc:
        failure_message = str(exc)
    finally:
        try:
            client.shutdown()
        except Exception:
            pass
        try:
            client.process.wait(timeout=15)
        except Exception:
            shutdown_forced = True
            client.process.kill()
            try:
                client.process.wait(timeout=3)
            except Exception:
                pass

    interesting_keys = (
        "status",
        "calibrated",
        "output_width",
        "output_height",
        "output_active",
        "output_frames_written",
        "output_frames_dropped",
        "output_effective_codec",
        "output_last_error",
        "stitch_fps",
        "worker_fps",
        "pair_skew_ms_mean",
        "left_age_ms",
        "right_age_ms",
        "left_motion_mean",
        "right_motion_mean",
        "stitched_motion_mean",
        "left_content_frozen",
        "right_content_frozen",
        "left_freeze_restarts",
        "right_freeze_restarts",
        "gpu_warp_count",
        "gpu_blend_count",
        "cpu_blend_count",
    )
    metrics = {key: last_metrics.get(key) for key in interesting_keys}
    result = {
        "case": asdict(case),
        "hello": hello,
        "elapsed_sec": round(time.time() - started_at, 3),
        "returncode": client.process.returncode,
        "shutdown_forced": shutdown_forced,
        "recalibration_sent": recalibration_sent,
        "failure_message": failure_message,
        "runtime_stderr_tail": client.get_stderr_tail(),
        "metrics": metrics,
    }
    result["evaluation"] = _evaluate_result(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run soak tests against the native runtime")
    parser.add_argument("--duration-sec", type=float, default=20.0, help="Default duration for each case")
    parser.add_argument("--small-duration-sec", type=float, default=0.0, help="Override small case duration")
    parser.add_argument("--large-duration-sec", type=float, default=0.0, help="Override large case duration")
    parser.add_argument(
        "--case",
        dest="case_name",
        choices=["all", "small", "large", "recalibration"],
        default="all",
        help="Run all cases or a single case",
    )
    args = parser.parse_args()

    left_rtsp = _env("HOGAK_LEFT_RTSP", DEFAULT_LEFT_RTSP)
    right_rtsp = _env("HOGAK_RIGHT_RTSP", DEFAULT_RIGHT_RTSP)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    small_duration_sec = float(args.small_duration_sec) if float(args.small_duration_sec) > 0.0 else float(args.duration_sec)
    large_duration_sec = float(args.large_duration_sec) if float(args.large_duration_sec) > 0.0 else float(args.duration_sec)

    cases = _default_cases(
        small_duration_sec=small_duration_sec,
        large_duration_sec=large_duration_sec,
    )
    if args.case_name == "small":
        cases = [case for case in cases if case.name.startswith("small_")]
    elif args.case_name == "large":
        cases = [case for case in cases if case.name.startswith("large_") and "recalibration" not in case.name]
    elif args.case_name == "recalibration":
        cases = [case for case in cases if "recalibration" in case.name]

    results = [_collect_case(case, left_rtsp=left_rtsp, right_rtsp=right_rtsp) for case in cases]
    summary = {
        "passed": sum(1 for result in results if result.get("evaluation", {}).get("passed")),
        "failed": sum(1 for result in results if not result.get("evaluation", {}).get("passed")),
    }
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"native_runtime_soak_{timestamp}.json"
    payload = {"summary": summary, "results": results}
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), **payload}, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
