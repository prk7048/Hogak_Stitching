# native_runtime

이 디렉터리는 실시간 stitch를 실제로 수행하는 C++ 네이티브 런타임이다.

상위 구조는 아래처럼 나뉜다.

```text
Python:
calibration / config / launch / monitor

C++ native runtime:
RTSP ingest -> pair/sync -> stitch -> encode -> output
```

## Scope

현재 런타임이 맡는 일:

- RTSP 입력 수신
- left/right frame pair 선택과 sync 판단
- homography 기반 stitch
- GPU warp / GPU feather blend
- encoded probe/transmit 출력
- metrics / control channel

현재 메인 입력/출력 기준:

- input runtime: `ffmpeg-cuda`
- input pipe format: `nv12`
- pair mode baseline: `service`
- transmit runtime baseline: `gpu-direct`
- output codec baseline: `h264_nvenc`

## Key Files

- [runtime_main.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/app/runtime_main.cpp)
  - native runtime 진입점
- [stitch_engine.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/stitch_engine.cpp)
  - pair/sync, stitch, metrics 핵심
- [ffmpeg_rtsp_reader.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)
  - RTSP 입력과 rawvideo pipe reader
- [ffmpeg_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/ffmpeg_output_writer.cpp)
  - ffmpeg 기반 encoded output writer
- [gpu_direct_output_writer.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)
  - libav/NVENC 기반 transmit writer
- [control_server.cpp](/c:/Users/Pixellot/Hogak_Stitching/native_runtime/src/control/control_server.cpp)
  - JSON Lines control / metrics channel

## Build

```cmd
copy CMakeUserPresets.example.json CMakeUserPresets.json
cmake --preset windows-release
cmake --build --preset build-windows-release
```

다른 Windows 머신에서는 `CMakeUserPresets.json` 또는 환경변수로 아래 값을 채운다.

- `OpenCV_DIR`
- `HOGAK_FFMPEG_DEV_ROOT`
- 필요하면 `CUDAToolkit_ROOT`

기본 산출물:

```text
native_runtime\build\windows-release\Release\stitch_runtime.exe
```

## Run

런타임은 보통 Python entrypoint를 통해 실행한다.

```cmd
python -m stitching.cli native-runtime
```

viewer 없이:

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer
```

25fps profile:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
```

고부하 preset:

```cmd
python -m stitching.cli native-runtime --output-standard realtime_gpu_1080p
```

## Output Model

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

## Config Contract

native runtime이 기대하는 주요 입력:

- `left_rtsp`, `right_rtsp`
- `homography_file`
- `probe_output_*`
- `transmit_output_*`
- `rtsp_transport`
- `sync_pair_mode`
- `input_buffer_frames`
- `gpu_mode`

실제 기본값은 [config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)과 profile override에서 온다.

## What To Watch

운영 중 먼저 볼 값:

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

## Notes

- operator-facing 이름은 `probe`, `transmit`을 쓴다
- engine 내부 메트릭 필드는 일부 `output_*`, `production_output_*` 이름을 아직 유지한다
- calibration 결과 homography는 [data/runtime_homography.json](/c:/Users/Pixellot/Hogak_Stitching/data/runtime_homography.json)을 기본으로 쓴다

## Related Docs

- [README.md](/c:/Users/Pixellot/Hogak_Stitching/README.md)
- [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)
- [03_native_runtime_current_status.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_native_runtime_current_status.md)
- [04_next_steps_and_release_plan.md](/c:/Users/Pixellot/Hogak_Stitching/reports/04_next_steps_and_release_plan.md)
