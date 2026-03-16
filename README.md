# Hogak Stitching

두 개의 RTSP 카메라 입력을 받아 하나의 stitched stream으로 송출하는 프로젝트다.

현재 메인 구조는 `Python control plane + C++ native runtime`이다.

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

## Repository Layout

- [config](/c:/Users/Pixellot/Hogak_Stitching/config): site config와 profile override
- [data](/c:/Users/Pixellot/Hogak_Stitching/data): runtime homography 같은 보존 데이터
- [stitching](/c:/Users/Pixellot/Hogak_Stitching/stitching): Python control plane
- [native_runtime](/c:/Users/Pixellot/Hogak_Stitching/native_runtime): C++ native runtime
- [output](/c:/Users/Pixellot/Hogak_Stitching/output): 재생성 가능한 실행 산출물
- [reports](/c:/Users/Pixellot/Hogak_Stitching/reports): 판단 기록과 상태 문서

## Build

```cmd
python -m pip install -r requirements.txt
copy native_runtime\CMakeUserPresets.example.json native_runtime\CMakeUserPresets.json
cmake --preset windows-release
cmake --build --preset build-windows-release
```

다른 Windows 머신에서는 `native_runtime\CMakeUserPresets.json`에 로컬 값을 채운다.

- `OpenCV_DIR`
- `HOGAK_FFMPEG_DEV_ROOT`
- 필요하면 `CUDAToolkit_ROOT`

상세 빌드 메모는 [native_runtime/README.md](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/README.md)를 본다.

## Config

기본 설정은 [config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)에서 읽는다.

- `left_rtsp`, `right_rtsp`
- homography 경로
- probe/transmit target
- cadence, transport, input buffer

중요:

- repo에 있는 `config/runtime.json`의 RTSP 값은 placeholder다
- 실제 현장 값은 `config/runtime.local.json`에 두는 것을 권장한다
- `runtime.local.json`은 git에 올리지 않는다

profile override:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
set HOGAK_RUNTIME_PROFILE=prod
python -m stitching.cli native-runtime
```

적용 순서와 profile 구조는 [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)를 본다.

## Calibration

가장 간단한 실행:

```cmd
python -m stitching.cli native-calibrate
```

기본 흐름:

1. 좌/우 대표 프레임 확인
2. 필요하면 대응점 선택
3. `COMPLETE`
4. preview 검토
5. `CONFIRM`
6. homography 저장
7. runtime 자동 실행

결과 homography는 [data/runtime_homography.json](/c:/Users/Pixellot/Hogak_Stitching/data/runtime_homography.json)에 저장한다.

## Runtime

기본 실행:

```cmd
python -m stitching.cli native-runtime
```

viewer 없이:

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer
```

25fps 카메라 profile:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
```

고부하 preset:

```cmd
python -m stitching.cli native-runtime --output-standard realtime_gpu_1080p
```

지원 preset:

- `realtime_hq_1080p`
- `realtime_gpu_1080p`

## Runtime Model

현재 기본 흐름은 아래와 같다.

```text
RTSP -> ffmpeg reader -> pair/sync -> stitch -> encode -> output
```

출력 역할은 둘로 나눈다.

- `probe`: viewer가 켜져 있을 때만 쓰는 local debug stream
- `transmit`: 실제 외부 송출 경로

viewer on:

```text
stitched frame -> probe encode -> local receive -> viewer
               -> transmit encode -> external target
```

viewer off:

```text
stitched frame -> transmit encode -> external target
```

## Current Baseline

- input runtime: `ffmpeg-cuda`
- input pipe format: `nv12`
- pair mode: `service`
- transmit runtime: `gpu-direct`
- output codec baseline: `h264_nvenc`
- output size baseline: stitched size 그대로

## What To Watch

monitor에서 먼저 볼 값:

- `stitch_actual_fps`
- `probe_fps`
- `transmit_fps`
- `left_age_ms`, `right_age_ms`
- `pair_skew_ms`
- `read_fail`, `restart`, `gpu_errors`

간단 해석:

- `stitch_actual_fps`: 실제 fresh stitched frame 속도
- `transmit_fps`: 실제 송출 cadence
- `age_ms`: 입력 지연
- `pair_skew_ms`: 좌우 시간 차이

## Deployment

현재는 direct Python 실행 기준으로 운영한다.

배포 시 같이 봐야 하는 경로:

- [config](/c:/Users/Pixellot/Hogak_Stitching/config)
- [data](/c:/Users/Pixellot/Hogak_Stitching/data)
- [stitching](/c:/Users/Pixellot/Hogak_Stitching/stitching)
- `native_runtime/build/windows-release/Release`

현장 장비에서는 `config/runtime.local.json`만 바꾸는 방식이 기본이다.

## More Docs

- 아키텍처/상태: [reports/README.md](/c:/Users/Pixellot/Hogak_Stitching/reports/README.md)
- native runtime 상세: [native_runtime/README.md](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/README.md)
- 현재 상태: [reports/03_native_runtime_current_status.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_native_runtime_current_status.md)
- 다음 계획: [reports/04_next_steps_and_release_plan.md](/c:/Users/Pixellot/Hogak_Stitching/reports/04_next_steps_and_release_plan.md)
