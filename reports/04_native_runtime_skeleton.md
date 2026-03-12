# Native Runtime 진행 현황과 현재 구조

> 주의: 이 문서는 native runtime 1차 skeleton 시점 기준 문서다.
> 최신 구현 상태와 현재 리스크는 `05_native_runtime_progress_and_finish_plan.md`를 우선 본다.

## 목적

현재 프로젝트의 목표는 Python이 더 이상 full-rate 영상 프레임의 hot path를 소유하지 않게 만드는 것이다.

최종 목표 구조는 다음과 같다.

```text
native runtime:
입력/디코드 -> pair/sync -> stitch -> encode -> final output stream

Python:
제어/UI/로그/최종 스트림 viewer 실행 보조
```

즉 Python은 운영 제어와 관찰 역할만 맡고, 실제 영상 처리 본체는 `native_runtime/` 아래의 C++ 런타임이 맡는다.

## 현재까지 구현된 것

### 1. 네이티브 프로젝트 골격

추가된 프로젝트:

- `native_runtime/CMakeLists.txt`
- `native_runtime/CMakePresets.json`
- `native_runtime/include/engine/*`
- `native_runtime/src/*`

빌드 결과:

- `native_runtime/build/windows-release/Release/stitch_runtime.exe`

즉, VS Code + CMake + MSVC/CUDA 도구체인으로 바로 빌드 가능한 독립 네이티브 프로젝트가 만들어져 있다.

### 2. 입력/디코드가 Python 밖으로 이동

현재 런타임은 RTSP를 Python `VideoCapture`가 아니라 네이티브 쪽에서 직접 읽는다.

- 입력 reader:
  - `native_runtime/src/input/ffmpeg_rtsp_reader.cpp`
  - `native_runtime/src/input/ffmpeg_rtsp_reader.h`
- Windows 프로세스/파이프 래퍼:
  - `native_runtime/src/platform/win_process_pipe.cpp`
  - `native_runtime/src/platform/win_process_pipe.h`

현재 구조:

```text
RTSP
-> ffmpeg subprocess
-> native reader
-> native frame queue
-> native pair/sync
```

즉 이전처럼 Python `numpy.ndarray`가 먼저 생기고, Python이 버퍼링과 동기화를 하는 구조가 아니다.

### 3. pair/sync/metrics가 네이티브로 이동

현재 네이티브 런타임이 계산하는 메트릭:

- `left_fps`
- `right_fps`
- `stitch_fps`
- `worker_fps`
- `pair_skew_ms_mean`
- `stitched_count`
- `left_stale_drops`
- `right_stale_drops`
- `gpu_warp_count`
- `cpu_warp_count`
- `gpu_blend_count`
- `cpu_blend_count`
- `output_active`
- `output_frames_written`

즉, 입력 상태와 엔진 상태는 이미 Python 밖에서 계산되고 있다.

### 4. 최소 stitch 경로가 이미 있음

현재 엔진 파일:

- `native_runtime/src/engine/stitch_engine.cpp`

현재 구현된 stitch 경로:

- 고정 homography JSON 로드
- homography가 없으면 identity fallback
- GPU warp 시도
- feather blend
- 필요 시 CPU blend fallback

현재 로그에서 실제로 확인된 상태:

- `gpu_warp_count > 0`
- `cpu_blend_count > 0`
- `gpu_blend_count = 0`

즉, 현재 병목은 입력이 아니라 **엔진 내부의 CPU feather blend** 쪽이다.

### 5. 출력 스트림 writer도 들어가 있음

출력 관련 파일:

- `native_runtime/src/output/ffmpeg_output_writer.cpp`
- `native_runtime/src/output/ffmpeg_output_writer.h`
- `native_runtime/src/platform/win_process_sink.cpp`
- `native_runtime/src/platform/win_process_sink.h`

현재 구조:

```text
stitch 결과
-> native output writer
-> ffmpeg stdin
-> encoded output stream
```

즉 native runtime이 이미 최종 출력 스트림의 소유권을 갖도록 가는 구조가 시작되어 있다.

### 6. Python 제어/뷰어 helper

Python 쪽 추가 파일:

- `stitching/runtime_launcher.py`
- `stitching/runtime_client.py`
- `stitching/native_runtime_cli.py`
- `stitching/final_stream_viewer.py`

의미:

- Python은 native runtime을 실행할 수 있다
- JSON line metrics를 읽을 수 있다
- `ffplay`로 최종 스트림을 외부 클라이언트처럼 볼 수 있다

## 현재 구조 요약

현재 실제 구조는 다음에 가깝다.

```text
native runtime:
RTSP 입력
-> ffmpeg decode
-> native pair/sync
-> fixed-H 기반 최소 stitch
-> encode/output stream

Python:
runtime launch
-> metrics/log 출력
-> final stream viewer 실행 보조
```

즉 "입력만 native" 단계는 이미 지난 상태다.

## 확인된 현재 병목

실제 런타임 로그 기준:

- `left_fps ≈ 30`
- `right_fps ≈ 30`
- `output_active=True`
- `output_frames_written` 정상 증가
- `stitch_fps ≈ 9~12`
- `gpu_warp_count` 증가
- `cpu_blend_count` 증가
- `gpu_blend_count = 0`

해석:

1. 입력/디코드는 정상
2. 출력 writer도 정상
3. 현재 병목은 native runtime 내부 stitch 경로
4. 특히 CPU feather blend가 1순위 병목

## 현재 사용자가 할 수 있는 것

### 로그 + 최종 영상 확인

`cmd`:

```cmd
scripts\run_native_runtime.cmd
```

### 로그만 확인

`cmd`:

```cmd
scripts\run_native_runtime.cmd --no-viewer
```

이 스크립트는:

1. native runtime을 실행
2. 현재 터미널에 metrics/log를 출력
3. `ffplay`로 final output stream viewer를 띄움

로그 전용 스크립트는 viewer 없이 metrics/log만 출력한다.

### 고정 homography 생성

현재 CLI에 `native-calibrate`가 추가되어 있다.

예시:

```powershell
.\.venv312\Scripts\python.exe -m stitching.cli native-calibrate --left-rtsp "rtsp://..." --right-rtsp "rtsp://..." --out "output/native/runtime_homography.json"
```

이 명령은:

1. RTSP에서 대표 프레임 한 쌍을 잡고
2. Python reference pipeline으로 homography를 계산하고
3. native runtime이 읽을 수 있는 homography JSON을 저장한다.

## 다음 단계

우선순위는 다음과 같다.

1. CPU feather blend -> GPU blend
2. 생성된 homography JSON을 넣고 실제 stitch 품질 확인
3. Python UI를 control/log/viewer 전용으로 정리
4. 이후 필요 시 manual calibration / recalibration command 추가

## 현재 판단

현재 native runtime 전환은 "설계만 있는 상태"가 아니다.

이미:

- 입력
- pair/sync
- 최소 stitch
- output writer
- final stream viewer helper

까지 들어간 상태이며, 지금부터는 **엔진 최적화와 품질 고정 단계**로 보는 것이 맞다.
