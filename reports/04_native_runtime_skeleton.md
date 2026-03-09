# Native Runtime 최소 골격과 입력 파이프라인 1차 이전

## 목적

이번 단계의 목적은 두 가지다.

- Python: 제어/UI/로그
- Native runtime: 실제 엔진 hot path

즉 `decode -> buffer/sync -> stitch -> encode/send`의 소유권을 장기적으로 네이티브 엔진으로 옮기기 위한 최소 골격을 추가하고, 그중 첫 단계로 **RTSP 입력과 pair/sync 메트릭을 Python 밖으로 옮겼다.**

## 추가한 것

### 1. `native_runtime/`

- `CMakeLists.txt`
- `CMakePresets.json`
- `include/engine/*`
- `src/engine/*`
- `src/control/*`
- `src/app/runtime_main.cpp`

초기에는 완전한 스텁이었지만, 현재는 입력 파이프라인 1차가 들어갔다.

- `stitch_engine_core` 라이브러리
- `stitch_runtime` 실행 파일
- `stdin/stdout` JSON Lines 제어 채널
- `hello`, `metrics`, `stopped` 이벤트 송신
- Windows 전용 `ffmpeg` subprocess reader
- RTSP 두 개를 native runtime에서 직접 읽는 루프
- native pair/sync 메트릭 계산

## 설계 원칙

1. 코어 엔진과 wrapper 분리
2. Python이 raw frame 소유권을 가지지 않게 설계
3. 이후
   - 별도 exe
   - Python binding
   - GStreamer/FFmpeg wrapper
   로 확장 가능한 구조 유지

## Python 쪽 추가

### `stitching/runtime_launcher.py`

- native runtime 바이너리 경로 탐색
- 실행 명령 생성
- `subprocess.Popen`으로 런타임 시작

### `stitching/runtime_client.py`

- JSON Lines 기반 제어/이벤트 클라이언트
- `hello` 수신
- `metrics` 요청
- `shutdown` 명령 전송

### 현재 실제 동작

- Python launcher는 `stitch_runtime.exe`를 실행한다
- C++ runtime은 `ffmpeg`를 직접 띄워 RTSP를 읽는다
- 프레임은 Python `numpy.ndarray`가 아니라 **native reader 내부**에서 처리된다
- 현재는 아직 stitch 결과를 만들지 않고, 입력 fps / stale drop / pair skew / worker loop 상태만 만든다

즉 지금 단계는:

```text
RTSP -> ffmpeg subprocess -> native frame read -> native pair/sync -> metrics
```

까지 옮긴 상태다.

## 현재 제한

아직 다음은 구현되지 않았다.

- 실제 warp/blend 기반 stitch
- GPU stitching
- encode/send
- final output stream
- 실제 manual calibration command 처리

즉 이번 단계는 **구조 뼈대 + 입력 파이프라인 1차 이전**까지다.

## 다음 단계

1. native runtime에 고정 H 기반 최소 stitch 경로 추가
2. final output stream 소유권을 native runtime으로 이동
3. Python은 control + final stream viewer 역할만 하도록 연결
