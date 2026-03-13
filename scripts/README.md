# Scripts

현재 `scripts/`는 운영, 빌드, 핵심 검증에 필요한 파일만 남긴 상태다.

## Runtime

운영 스크립트다. 실제 calibration/runtime 실행에 사용한다.

- `run_native_calibrate.cmd`
- `run_native_runtime.cmd`
- `run_native_runtime_realtime.cmd`
- `run_native_runtime_strict.cmd`
- `run_native_runtime_soak.cmd`
- `run_native_runtime_common.cmd`
- `open_vlc_low_latency.cmd`

## Build / Setup

환경 준비와 빌드에만 사용한다.

- `build_native_runtime_release.cmd`
- `setup_cuda_opencv.ps1`
- `setup_ffmpeg_dev.py`
- `setup_ffmpeg_portable.ps1`
- `check_gpu_direct_dependencies.py`

## Core Validation

검증 전용 스크립트다. 운영 경로를 실행하는 대신 현재 baseline과 회귀를 측정할 때 쓴다.

- `diagnose_dual_udp_streams.py`
- `compare_pair_modes.py`
- `smoke_gpu_direct_output.py`
- `native_runtime_soak.py`
- `verify_runtime_udp_output.py`
- `clean_generated_artifacts.cmd`

내부 공통 helper:

- `validation_support.py`

`clean_generated_artifacts.cmd`는 아래를 정리한다.
- `output/debug`
- `scripts/`, `stitching/` 아래 `__pycache__`
- `output/native`의 재생성 가능한 산출물

보존 대상:
- `output/native/runtime_homography.json`

정리 기준:

- 일회성 진단이나 과거 회귀 재현 전용 스크립트는 제거했다.
- 현재 남은 스크립트는 "실행", "환경 준비", "핵심 검증" 중 하나의 역할이 분명한 것만 유지한다.
