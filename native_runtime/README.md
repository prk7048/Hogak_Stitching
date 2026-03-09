# native_runtime

이 디렉터리는 Python UI/제어 계층과 분리된 C++ 네이티브 런타임입니다.

현재 목표:

1. `stitch_engine_core` 라이브러리 경계 고정
2. `stitch_runtime` 실행 파일 추가
3. Python에서 별도 프로세스로 런타임을 띄우고 제어할 수 있는 구조 마련
4. RTSP 입력과 pair/sync 메트릭을 native runtime에서 처리

현재 범위:

- RTSP 입력은 `ffmpeg` subprocess reader로 동작합니다.
- 현재는 입력 fps, stale drop, pair skew, worker loop 상태를 native runtime에서 만들 수 있습니다.
- stitch/encode/output stream은 아직 구현 전입니다.
- `stdin/stdout` JSON Lines 기반 제어 채널이 동작합니다.

의도:

- 지금 단계에서 중요한 것은 **Python frame ownership 제거**와 **경계 분리**입니다.
- Python은 제어/로그/UI를 담당하고, 네이티브 런타임은 이후 hot path를 소유하게 됩니다.
- 이후 `stitch_engine_core`를 유지한 채
  - 별도 exe wrapper
  - Python binding
  - GStreamer/FFmpeg wrapper
  로 확장할 수 있게 설계합니다.

빌드 예시:

```cmd
cmake --preset windows-debug
cmake --build --preset build-windows-debug
```

실행 예시:

```cmd
native_runtime\build\windows-debug\Debug\stitch_runtime.exe --emit-hello --heartbeat-ms 500 --left-url "rtsp://..." --right-url "rtsp://..." --input-runtime ffmpeg-cuda
```
