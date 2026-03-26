# Hogak Stitching

두 개의 RTSP 카메라 입력을 받아 하나의 stitched stream으로 송출하는 프로젝트다.

현재 구조는 `Python control plane + C++ native runtime`이다.

- Python: calibration, config/profile loading, runtime launch, monitor UI
- C++: RTSP ingest, pair/sync, stitch, encode, output

## Requirements

- Windows
- NVIDIA GPU
- CUDA / NVENC 사용 가능 환경
- Python 3.12 근처 환경

## Main Entry Points

현재 운영 기준 진입점은 둘뿐이다.

```cmd
python -m stitching.cli native-calibrate
python -m stitching.cli native-runtime
```

strict fresh baseline 검증은 아래로 수행한다.

```cmd
python -m stitching.cli native-validate --duration-sec 600
```

## Quick Start

빌드:

```cmd
python -m pip install -r requirements.txt
copy native_runtime\CMakeUserPresets.example.json native_runtime\CMakeUserPresets.json
cmake --preset windows-release
cmake --build --preset build-windows-release
```

runtime 실행:

```cmd
python -m stitching.cli native-runtime
```

headless 실행:

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer
```

25fps profile:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
```

strict fresh 30 smoke/validation:

```cmd
python -m stitching.cli native-validate --duration-sec 10
python -m stitching.cli native-validate --duration-sec 600
```

검증 결과는 `output/debug/native_validate_*.json`으로 저장된다.

기본 sync 기준은 `pts-offset-auto`다. 즉 runtime은 카메라 wallclock을 기본으로 믿지 않고,
`stream_pts + auto-estimated offset`을 먼저 시도한다.

명시적으로 고정하고 싶으면 manual mode를 쓴다.

```cmd
python -m stitching.cli native-runtime --sync-time-source pts-offset-manual --sync-manual-offset-ms -64
```

## Config

기본 설정은 [runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)에서 읽는다.

중요한 운영 원칙:

- repo의 `config/runtime.json` RTSP 값은 placeholder다
- 실제 현장 값은 `config/runtime.local.json`에 둔다
- `runtime.local.json`은 git에 올리지 않는다

적용 순서와 profile 구조는 [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)를 본다.

## Repository Layout

- [config](/c:/Users/Pixellot/Hogak_Stitching/config): site config와 profile override
- [data](/c:/Users/Pixellot/Hogak_Stitching/data): runtime homography 같은 보존 데이터
- [stitching](/c:/Users/Pixellot/Hogak_Stitching/stitching): Python control plane
- [native_runtime](/c:/Users/Pixellot/Hogak_Stitching/native_runtime): C++ native runtime
- [output](/c:/Users/Pixellot/Hogak_Stitching/output): 재생성 가능한 실행 산출물
- [reports](/c:/Users/Pixellot/Hogak_Stitching/reports): 아키텍처, 상태, 배포, 온보딩 문서

## Runtime Summary

현재 기본 흐름은 아래와 같다.

```text
RTSP -> libav ingest/decode -> pair/sync -> stitch -> encode -> output
```

기본 pair/sync 시간축은 아래 순서로 본다.

- 기본: `pts-offset-auto`
- 수동 고정: `pts-offset-manual`
- 혼합: `pts-offset-hybrid`
- fallback: `arrival`
- 진단 전용: `wallclock`

출력 역할은 둘로 나눈다.

- `probe`: local debug/viewer용
- `transmit`: 실제 외부 송출 경로

현재 baseline 설명과 운영 상태는 [reports/03_current_status_and_roadmap.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_current_status_and_roadmap.md)를 본다.
strict fresh `30fps` acceptance 기준과 source timing 지표는 [reports/09_baseline_acceptance_and_source_timing.md](/c:/Users/Pixellot/Hogak_Stitching/reports/09_baseline_acceptance_and_source_timing.md)를 본다.

## Documentation Map

- 설정 구조: [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)
- native runtime 상세: [native_runtime/README.md](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/README.md)
- 문서 인덱스: [reports/README.md](/c:/Users/Pixellot/Hogak_Stitching/reports/README.md)
- 아키텍처 개요: [01_project_overview_and_architecture.md](/c:/Users/Pixellot/Hogak_Stitching/reports/01_project_overview_and_architecture.md)
- 현재 상태와 다음 단계: [03_current_status_and_roadmap.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_current_status_and_roadmap.md)
- baseline acceptance / source timing: [09_baseline_acceptance_and_source_timing.md](/c:/Users/Pixellot/Hogak_Stitching/reports/09_baseline_acceptance_and_source_timing.md)
- 배포/지원 환경: [06_deployment_and_support_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/06_deployment_and_support_guide.md)
- 신입 온보딩 문서: [07_new_hire_handoff_study_guide.md](/c:/Users/Pixellot/Hogak_Stitching/reports/07_new_hire_handoff_study_guide.md)
