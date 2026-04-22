# native_runtime

이 디렉터리는 실시간 stitch를 실제로 수행하는 C++ 네이티브 런타임이다.

현재 유지하는 제품 경로는 `launch-ready rigid runtime artifact` 를 소비하는 구조다.

```text
RTSP ingest -> pair/sync -> rigid geometry remap and blend -> encode -> output
```

## Scope

현재 런타임이 맡는 일:

- RTSP 입력 수신
- arrival/source dual timestamp 수집
- left/right frame pair 선택과 sync 판단
- GPU stitch 및 blend
- probe/transmit 출력
- metrics / control channel

현재 메인 입력/출력 기준:

- input runtime: `ffmpeg-cuda`
- input pipe format: `nv12`
- pair mode baseline: `service`
- transmit runtime baseline: `gpu-direct`
- output codec baseline: `h264_nvenc`

## Key Files

- [operator_main.cpp](/C:/Users/Pixellot/Hogak_Stitching/native_runtime/src/app/operator_main.cpp)
  - native runtime 진입점
- [engine.cpp](/C:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/engine.cpp)
  - pair/sync, stitch, metrics 핵심
- [geometry_loader.cpp](/C:/Users/Pixellot/Hogak_Stitching/native_runtime/src/engine/geometry_loader.cpp)
  - launch-ready geometry artifact load
- [ffmpeg_rtsp_reader.cpp](/C:/Users/Pixellot/Hogak_Stitching/native_runtime/src/input/ffmpeg_rtsp_reader.cpp)
  - libav 기반 RTSP ingest/decode reader와 arrival/source timestamp 수집
- [gpu_direct_output_writer.cpp](/C:/Users/Pixellot/Hogak_Stitching/native_runtime/src/output/gpu_direct_output_writer.cpp)
  - libav/NVENC 기반 transmit writer
- [control_server.cpp](/C:/Users/Pixellot/Hogak_Stitching/native_runtime/src/control/control_server.cpp)
  - JSON Lines control / metrics channel

## Build

Windows 머신에서는 먼저 prerequisite check를 돌리는 쪽을 기준으로 삼는다:

```cmd
bootstrap_native_runtime.ps1
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
native_runtime\build\windows-release\Release\stitch_capture.exe
```

## Run Contract

native runtime은 제품 표면에서 직접 호출하지 않는다. Python control plane이 설정을 합치고 launch-ready rigid artifact를 확인한 뒤 native process를 띄운다.

```cmd
python -m stitching.cli operator-server
python -m stitching.cli mesh-refresh
```

`stitch_runtime.exe` 를 직접 실행하는 경로는 내부 계약이다. 운영 기준 surface는 `operator-server` 와 `mesh-refresh` 만 유지한다.

## Config Contract

실제 기본값은 [config/runtime.json](/C:/Users/Pixellot/Hogak_Stitching/config/runtime.json), `runtime.local.json`, profile override에서 온다.

중요한 입력:

- `left_rtsp`, `right_rtsp`
- `paths.homography_file`
- `runtime.probe.*`
- `runtime.transmit.*`
- `runtime.rtsp_transport`
- `runtime.sync_*`
- `runtime.input_buffer_frames`
- `runtime.gpu_mode`

repo의 `config/runtime.json` 은 placeholder RTSP만 유지한다. 실제 현장 값은 `config/runtime.local.json` 을 우선 사용한다.

## What To Watch

운영 중 먼저 볼 값:

- `stitch_actual_fps`
- `transmit_fps`
- `left_age_ms`, `right_age_ms`
- `pair_skew_ms`
- `pair_source_skew_ms_mean`
- `source_time_mode`
- `sync_effective_offset_ms`
- `sync_offset_source`
- `sync_offset_confidence`
- `sync_recalibration_count`
- `read_fail`, `restart`, `gpu_errors`

제품 경로에서 중요한 규칙:

- distortion 기능은 현재 제품 경로에서 비활성화 상태다
- runtime truth 는 rigid artifact 기준으로만 본다
- 웹 표면은 `Project state -> Start Project -> Stop Project` 흐름만 유지한다
- `mesh-refresh` 는 내부 준비 경로이며, 필요 시 `Start Project` 안에서 자동으로 호출될 수 있다

## Related Docs

- [README.md](/C:/Users/Pixellot/Hogak_Stitching/README.md)
- [config/README.md](/C:/Users/Pixellot/Hogak_Stitching/config/README.md)
- [docs/operator_acceptance.md](/C:/Users/Pixellot/Hogak_Stitching/docs/operator_acceptance.md)
- [docs/gpu_only_cleanup_ledger.md](/C:/Users/Pixellot/Hogak_Stitching/docs/gpu_only_cleanup_ledger.md)
