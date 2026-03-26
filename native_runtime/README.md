# native_runtime

이 디렉터리는 실시간 stitch를 실제로 수행하는 C++ 네이티브 런타임이다.

상위 구조는 아래처럼 나뉜다.

```text
Python:
calibration / config / launch / monitor

C++ native runtime:
RTSP ingest -> pair/sync -> stitch -> encode -> output
```

현재 stitch 앞단에는 camera-slot별 distortion correction이 들어간다.

```text
RTSP ingest -> pair/sync -> undistort/remap -> homography warp -> feather blend -> encode -> output
```

## Scope

현재 런타임이 맡는 일:

- RTSP 입력 수신
- arrival/source dual timestamp 수집
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
  - libav 기반 RTSP ingest/decode reader와 arrival/source timestamp 수집
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

strict fresh baseline 검증:

```cmd
python -m stitching.cli native-validate --duration-sec 600
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

현재 sync 관련 핵심 키:

- `sync_time_source`
- `sync_manual_offset_ms`
- `sync_auto_offset_window_sec`
- `sync_auto_offset_max_search_ms`
- `sync_recalibration_interval_sec`
- `sync_recalibration_trigger_skew_ms`
- `sync_recalibration_trigger_wait_ratio`
- `sync_auto_offset_confidence_min`
- `distortion_mode`
- `use_saved_distortion`
- `distortion_auto_save`
- `left_distortion_file`
- `right_distortion_file`

기본값은 `sync_time_source=pts-offset-auto`다.
기본 distortion 모드는 `runtime-lines`다.

현재 auto sync는 `0ms prior + strong-evidence correction` 방식이다.

- source PTS가 유효하면 기본 pair 시간축은 `stream_pts_offset`
- offset 기본 가설은 `0ms`
- motion correlation이 충분히 강할 때만 offset을 조정
- 재보정은 작은 step으로만 반영
- `wallclock`은 자동 운영 기준이 아니라 explicit 진단 모드

## What To Watch

운영 중 먼저 볼 값:

- `stitch_actual_fps`
- `probe_fps`
- `transmit_fps`
- `left_age_ms`, `right_age_ms`
- `left_source_age_ms`, `right_source_age_ms`
- `pair_skew_ms`
- `pair_source_skew_ms_mean`
- `source_time_mode`
- `sync_effective_offset_ms`
- `sync_offset_source`
- `sync_offset_confidence`
- `sync_recalibration_count`
- `read_fail`, `restart`, `gpu_errors`

간단 해석:

- `stitch_actual_fps`: 실제 fresh stitched frame 속도
- `transmit_fps`: 실제 송출 cadence
- `age_ms`: arrival 기준 입력 지연
- `source_age_ms`: explicit `wallclock` 진단 모드에서만 의미 있는 source age
- `pair_skew_ms`: arrival 기준 좌우 시간 차이
- `pair_source_skew_ms_mean`: `stream_pts_offset` 또는 `wallclock` 기준 좌우 시간 차이
- `source_time_mode`: `stream_pts_offset`, `wallclock`, `fallback-arrival`
- `sync_effective_offset_ms`: 현재 pair selection에 실제 적용 중인 right-stream offset
- `sync_offset_source`: `auto`, `manual`, `recalibration`, `arrival-fallback`, `wallclock`
- `sync_offset_confidence`: auto/recalibration offset 신뢰도
- `sync_recalibration_count`: runtime 중 offset 재보정 횟수
- `sync_estimate_pairs`: 최근 auto estimate가 실제로 매칭한 pair 수
- `sync_estimate_avg_gap_ms`: auto estimate가 선택한 후보들의 평균 gap
- `sync_estimate_score`: auto estimate selection score
- `distortion_enabled_left/right`: runtime에서 실제 distortion remap이 켜졌는지
- `distortion_source_left/right`: `manual-lines`, `saved`, `off`
- `distortion_confidence_left/right`: 선택된 distortion profile confidence
- `distortion_model`: 현재는 `opencv_pinhole`

distortion 관련 중요한 안전 규칙:

- interactive `native-runtime` 시작 UI는 기본으로 좌/우 manual line selection을 수행한다
- `Reuse saved distortion calibration`을 체크하면 saved distortion을 그대로 재사용한다
- headless runtime과 `native-calibrate`는 saved distortion file이 있을 때만 distortion을 사용한다
- distortion correction은 **undistorted 기준으로 만든 homography** 와 같이 써야 한다
- homography file의 `distortion_reference`가 `undistorted`가 아니면 runtime은 distortion을 실제 적용하지 않는다
- 즉 saved distortion file이 있어도 old raw homography와 자동으로 섞어 쓰지 않는다

운영 권장:

- 기본은 `pts-offset-auto`
- 현장에 고정 offset이 있으면 `pts-offset-manual`
- auto 실패 시 manual까지 같이 준비하려면 `pts-offset-hybrid`
- `wallclock`은 기본 운영이 아니라 비교/진단용

현재 기본 튜닝값:

- `sync_auto_offset_window_sec=4.0`
- `sync_auto_offset_max_search_ms=500.0`
- `sync_recalibration_interval_sec=60.0`
- `sync_recalibration_trigger_skew_ms=45.0`
- `sync_recalibration_trigger_wait_ratio=0.50`
- `sync_auto_offset_confidence_min=0.85`

## Notes

- operator-facing 이름은 `probe`, `transmit`을 쓴다
- engine 내부 메트릭 필드는 일부 `output_*`, `production_output_*` 이름을 아직 유지한다
- calibration 결과 homography는 [data/runtime_homography.json](/c:/Users/Pixellot/Hogak_Stitching/data/runtime_homography.json)을 기본으로 쓴다

## Related Docs

- [README.md](/c:/Users/Pixellot/Hogak_Stitching/README.md)
- [config/README.md](/c:/Users/Pixellot/Hogak_Stitching/config/README.md)
- [03_current_status_and_roadmap.md](/c:/Users/Pixellot/Hogak_Stitching/reports/03_current_status_and_roadmap.md)
- [09_baseline_acceptance_and_source_timing.md](/c:/Users/Pixellot/Hogak_Stitching/reports/09_baseline_acceptance_and_source_timing.md)
- [08_runtime_architecture_diagrams.md](/c:/Users/Pixellot/Hogak_Stitching/reports/08_runtime_architecture_diagrams.md)
