# native_runtime

이 디렉터리는 Python UI/제어 계층과 분리된 C++ 네이티브 런타임이다.

현재 목표 구조:

```text
native runtime:
RTSP 입력/디코드 -> pair/sync -> stitch -> encode -> final output stream

Python:
runtime launch / metrics / calibration / encoded probe viewer
```

post-stitch 출력 역할은 두 개로 본다.

- `probe`: transmit가 켜져 있으면 encoded transmit를 local UDP로 다시 받는 debug receive 경로다. transmit가 없을 때만 standalone local encode로 fallback한다.
- `transmit`: 실제 외부 송출 출력. 필요할 때만 켠다.

`mp4`/`file` 출력은 최종 제품 목표가 아니라, writer/codec 회귀 검증과 결과 샘플 보존을 위한 보조 경로다.

## 현재 구현 범위

이미 들어간 것:

- RTSP 입력: `ffmpeg` subprocess reader
- pair/sync 및 메트릭 계산
- 고정 homography JSON 로드
- GPU warp + GPU feather blend
- ffmpeg output writer
- `stdin/stdout` JSON Lines 제어/메트릭 채널
- `reload_config` subset
  - output target / codec / bitrate / preset / muxer
  - input runtime / sync / gpu 관련 일부 설정
- Python launcher/client/mirrored-probe viewer helper

핵심 파일:

- `src/app/runtime_main.cpp`
- `src/engine/stitch_engine.cpp`
- `src/input/ffmpeg_rtsp_reader.cpp`
- `src/output/ffmpeg_output_writer.cpp`
- `src/control/control_server.cpp`

## 2026-03-10 재검증 결과

확인된 것:

- `no-homography 1920x1080` UDP + `h264_nvenc`는 `output_active=True` 상태로 유지됐다.
- current source rebuild 후 `4710x2215` 큰 출력도 UDP/file + `hevc_nvenc` 15초 smoke test에서 유지됐다.
- `libx264`는 odd height에서 죽는 문제가 있었고, writer 단계 even-dimension pad를 넣은 뒤 file 출력이 유지됐다.
- `python -m stitching.cli native-runtime --no-output-ui --no-viewer --duration-sec 20` 재검증에서 small/large UDP 모두 `returncode=0`, `shutdown_forced=false`로 종료됐다.
- 즉 immediate writer crash는 현재 smoke/short soak 기준 재현되지 않고, 다음 1순위는 **longer soak test와 graceful shutdown 기준 정리**다.

## 빌드

```cmd
copy CMakeUserPresets.example.json CMakeUserPresets.json
cmake --preset windows-release
cmake --build --preset build-windows-release
```

공용 preset은 이제 로컬 경로를 직접 들고 있지 않는다.
다른 Windows 머신에서는 `CMakeUserPresets.json` 또는 환경변수로 아래 값을 넣으면 된다.

- `OpenCV_DIR`
- `HOGAK_FFMPEG_DEV_ROOT`
- 필요하면 `CUDAToolkit_ROOT`

repo 안 `.third_party` 경로는 fallback일 뿐이고, 다른 PC에서 같은 폴더 구조를 강제하지 않는다.

예시:

```json
{
  "version": 6,
  "configurePresets": [
    {
      "name": "windows-release-local",
      "inherits": "windows-release",
      "cacheVariables": {
        "OpenCV_DIR": "C:/path/to/opencv/install/x64/vc17/staticlib",
        "HOGAK_FFMPEG_DEV_ROOT": "C:/path/to/ffmpeg-dev/current",
        "CUDAToolkit_ROOT": "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.1"
      }
    }
  ],
  "buildPresets": [
    {
      "name": "build-windows-release-local",
      "configurePreset": "windows-release-local",
      "configuration": "Release"
    }
  ]
}
```

빌드는 고정 MSBuild 경로 대신 `cmake --preset` / `cmake --build --preset`를 사용한다.
기본은 `windows-release` / `build-windows-release`이고, 다른 이름을 쓰고 싶으면 `HOGAK_CMAKE_CONFIGURE_PRESET`, `HOGAK_CMAKE_BUILD_PRESET`를 지정하면 된다.

산출물 예시:

```text
native_runtime\build\windows-release\Release\stitch_runtime.exe
```

## 실행

### 기본 실행

```cmd
python -m stitching.cli native-runtime --output-standard realtime_gpu_1080p
```

strict pair 기준으로 보고 싶으면:

```cmd
python -m stitching.cli native-runtime --output-standard realtime_gpu_1080p --sync-pair-mode service --no-allow-frame-reuse
```

기본 monitor mode는 `dashboard`다.

- 현재 status / calibrated / viewer 상태
- left/right fps, pair skew
- input age / motion / frozen suspicion
- internal/worker/output fps, gpu/cpu blend 카운터
- output 상태, codec, dropped/written frame
- 최근 status/warning/error event

runtime viewer는 raw snapshot preview가 아니라 local probe를 다시 받아 보여주는 post-encode viewer다.
transmit가 켜져 있으면 기본 viewer는 별도 encode가 아니라 mirrored transmit receive 결과를 본다.
즉 operator가 보는 기본 viewer는 stitch 직후 raw frame이 아니라 송출 후 local receive 결과다.
현재 기본 runtime 스크립트는 `transmit` 출력 위에 debug overlay를 넣는다. `frame`, `seq`, `reuse`, `pair_age`가 보여서 외부 VLC에서 보이는 정지가 실제 송출 정지인지 반복 프레임인지 구분할 수 있다.

를 터미널 한 화면으로 보여준다.

출력 모드 변경 예시:

```cmd
python -m stitching.cli native-runtime --left-rtsp "rtsp://..." --right-rtsp "rtsp://..." --monitor-mode compact
```

### viewer 없이 실행

```cmd
python -m stitching.cli native-runtime --no-viewer
```

`python -m stitching.cli native-runtime`와 Python UI는 기본값을 [config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json)에서 읽는다.
즉 카메라 RTSP 주소, homography 경로, probe/transmit target은 코드가 아니라 설정 파일에서 바꾼다.
운영 차이는 [config/profiles](/c:/Users/Pixellot/Hogak_Stitching/config/profiles) override로 분리할 수 있다.

예:

```cmd
python -m stitching.cli --runtime-profile camera25 native-runtime
```

```cmd
set HOGAK_RUNTIME_PROFILE=prod
python -m stitching.cli native-runtime --no-viewer
```

### short soak test

```cmd
python -m stitching.cli native-runtime --no-output-ui --no-viewer --duration-sec 20 --monitor-mode compact
```

### 직접 실행

```cmd
python -m stitching.cli native-runtime --left-rtsp "rtsp://..." --right-rtsp "rtsp://..." --homography-file "data/runtime_homography.json" --viewer
```

기본 콘솔 출력은 상태 변화나 5초 주기 요약만 남긴다.
raw event가 필요할 때만 `--verbose-events`를 붙인다.

### homography 생성

가장 간단한 실행:

```cmd
python -m stitching.cli native-calibrate
```

CLI는 기본 site config([config/runtime.json](/c:/Users/Pixellot/Hogak_Stitching/config/runtime.json))를 사용하므로 바로 실행 가능하다.

```cmd
python -m stitching.cli native-calibrate
```

```cmd
python -m stitching.cli native-calibrate --left-rtsp "rtsp://..." --right-rtsp "rtsp://..." --out "data/runtime_homography.json"
```

기본 calibration mode는 `assisted`다.

- 좌/우 대표 프레임을 한 OpenCV 창에서 보여준다
- 사용자가 같은 지점을 원하는 만큼 찍는다
- `COMPLETE`를 누르면 즉시 homography를 계산한다
- 점을 하나도 안 찍으면 자동 보정으로 fallback한다
- 점을 하나라도 찍으면 그 점들을 seed로 유지한 채 추가 매칭을 보강한다
- seed 1개는 translation, 2~3개는 affine, 4개 이상은 homography 가이드로 사용한다
- `manual`도 direct homography solve가 아니라 seed-guided matching 경로로 처리한다

필요하면 `--calibration-mode assisted|manual|auto`, `--match-backend auto|classic|deep`로 명시할 수 있다.

## 현재 남은 핵심 작업

1. longer soak test와 종료 시 return code/flush 동작 기준 정리
2. manual calibration, recalibration 같은 나머지 제어 명령 구현
3. control plane 명령 집합 정리

## 현재 soak 판정 기준

별도 soak 보조 스크립트는 유지하지 않고, `python -m stitching.cli native-runtime --duration-sec ...` 기반 smoke/soak만 남긴다.

현재 기본 통과 기준:

- `returncode == 0`
- `shutdown_forced == false`
- compact monitor 기준 `probe_active == true`
- compact monitor 기준 `probe_written >= 30`
- `output_last_error`, `left_last_error`, `right_last_error`가 비어 있음
- `left_age_ms`, `right_age_ms <= 3000`
- `pair_skew_ms_mean <= 250`

기본 케이스:

- `small_udp_h264_nvenc`
- `large_udp_auto_hevc`
- `large_udp_recalibration`

## 현재 reload_config 지원 필드

현재 공식 지원 subset은 아래다.

- `left_rtsp`, `right_rtsp`
- `input_runtime`, `ffmpeg_bin`, `homography_file`
- `output_runtime`, `output_target`, `output_codec`, `output_bitrate`, `output_preset`, `output_muxer`
- Python CLI alias: `probe_output_*`, `transmit_output_*`, `probe_source`
- `rtsp_transport`, `rtsp_timeout_sec`, `reconnect_cooldown_sec`
- `sync_pair_mode`, `sync_match_max_delta_ms`, `sync_manual_offset_ms`
- `process_scale`, `stitch_output_scale`, `stitch_every_n`
- `gpu_mode`, `gpu_device`
- `benchmark_log_interval_sec`, `headless_benchmark`

주의:

- 현재 제어 채널은 전체 명세 대비 일부만 구현되어 있다.
- raw JSONL metrics/config 이름은 아직 `output_*`, `production_output_*`를 유지한다. operator-facing monitor/CLI는 `probe`, `transmit` 이름을 쓴다.
- 최신 전체 상태는 [reports/05_native_runtime_progress_and_finish_plan.md](c:/Users/Pixellot/Hogak_Stitching/reports/05_native_runtime_progress_and_finish_plan.md)를 같이 보는 편이 맞다.
