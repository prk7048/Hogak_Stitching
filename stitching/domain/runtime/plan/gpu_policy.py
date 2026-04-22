from __future__ import annotations

from typing import Any

import cv2

from stitching.domain.runtime.service.launcher import query_gpu_direct_status


def gpu_only_blockers_for_plan(plan: Any) -> list[str]:
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
