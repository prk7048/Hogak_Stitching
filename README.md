# Dual Smartphone Video Stitching PoC

좌/우 2개 영상 또는 RTSP 스트림을 받아 하나의 파노라마로 스티칭하는 프로젝트다.

현재 기준으로는 Python/OpenCV 프로토타입을 유지하면서, 실시간 운영 경로를 `Python control + C++ native runtime` 구조로 옮기는 중이다.

## 현재 상태

- Python 오프라인/실험 경로는 계속 사용 가능하다.
- 현재 권장 실시간 경로는 `native-calibrate -> run_native_runtime.cmd`다.
- 원래 프로젝트 목표는 `mp4 파일 생성`이 아니라 `stitched output stream 송출`이다.
- `mp4`/`file` 출력은 디버그, 회귀 확인, 인코더 조합 검증용 보조 경로다.
- 2026-03-10 재검증 기준:
  - `no-homography 1920x1080` UDP + `h264_nvenc`는 안정적으로 유지됐다.
  - current source로 release rebuild 후 `4710x2215` 큰 출력도 UDP/file + `hevc_nvenc` 15초 smoke test에서 유지됐다.
  - `libx264`는 odd height에서 바로 죽는 문제가 있었고, writer 단계 even-dimension pad를 넣은 뒤 file 출력이 유지됐다.
  - `scripts\run_native_runtime_soak.cmd` 20초 재검증에서는 small/large UDP 둘 다 `returncode=0`, `shutdown_forced=false`로 종료됐다.
  - 현재 남은 핵심 이슈는 즉시 crash보다 **더 긴 soak test, graceful shutdown 기준 정리, control channel 확장** 쪽이다.

상세 설계와 진행 상태는 [reports/README.md](c:/Users/Pixellot/Hogak_Stitching/reports/README.md)를 먼저 보는 편이 맞다.

## 1) 설치

```powershell
python -m pip install -r requirements.txt
```

## 2) 권장 실행: Native Runtime

### 빌드

```cmd
cmake --preset windows-release
cmake --build --preset build-windows-release
```

### 고정 homography 생성

```powershell
python -m stitching native-calibrate `
  --left-rtsp 'rtsp://...' `
  --right-rtsp 'rtsp://...' `
  --out .\output\native\runtime_homography.json
```

### 기본 실행

실시간 30fps 우선 프리셋:

```cmd
scripts\run_native_runtime_realtime.cmd
```

strict pair 우선 프리셋:

```cmd
scripts\run_native_runtime_strict.cmd
```

`scripts\run_native_runtime.cmd`는 현재 realtime 프리셋과 같은 기본값으로 유지한다.

### viewer 없이 실행

```cmd
scripts\run_native_runtime.cmd --no-viewer
```

### soak test

```cmd
scripts\run_native_runtime_soak.cmd
```

### 직접 실행

```powershell
python -m stitching native-runtime `
  --left-rtsp 'rtsp://...' `
  --right-rtsp 'rtsp://...' `
  --homography-file .\output\native\runtime_homography.json `
  --viewer
```

주의:
- 운영 기준 실행 경로는 `native-calibrate -> run_native_runtime.cmd` 2단계만 본다.
- `native-runtime` 콘솔 출력은 기본적으로 상태 변화나 5초 주기 요약만 남긴다. raw event가 필요할 때만 `--verbose-events`를 쓴다.
- `run_native_runtime.cmd`는 기본적으로 `output/native/runtime_homography.json`을 자동 사용한다.
- `native-runtime` 기본 monitor mode는 `dashboard`다. 터미널에 현재 status/input/stitch/output/errors/recent events를 한 화면으로 표시한다.
- 현재 realtime 기본 운영값은 `1920x1080 output + h264_nvenc + latest pair + stitch_output_scale 0.25`다.
- 필요할 때는 `--monitor-mode compact` 또는 `--monitor-mode json`으로 바꿀 수 있다.
- `scripts\run_native_runtime_soak.cmd`는 이제 결과 JSON에 케이스별 `evaluation.passed`와 실패 이유를 같이 남긴다.
- soak 기본 케이스에는 `large_udp_recalibration`이 포함되며, 실행 중 homography reload control path도 같이 확인한다.

## 3) Python 참조/레거시 경로

아래 경로들은 여전히 동작하는 참조 구현이지만, 현재 주력 실시간 운영 경로는 아니다.

### 오프라인 비디오

```powershell
python -m stitching video-10s --pair video10
python -m stitching video-30s --pair video10
python -m stitching video-full --pair video10
```

### 데스크톱 RTSP 실험/벤치

```powershell
python -m stitching desktop --left-rtsp "rtsp://..." --right-rtsp "rtsp://..."
```

### GUI

```powershell
python -m stitching gui --host 127.0.0.1 --port 7860
```

주의:
- 현재 `gui`는 최신 native runtime 운영 경로를 대표하지 않는다.
- 실시간 운영 검증은 `native-runtime` 경로 기준으로 보는 편이 맞다.

## 4) 오프라인 수동 실행

```powershell
python -m stitching video `
  --left .\input\videos\video10_left.mp4 `
  --right .\input\videos\video10_right.mp4 `
  --out .\output\videos\video10_manual.mp4 `
  --report .\output\videos\video10_manual_report.json `
  --debug-dir .\output\debug\video10_manual `
  --max-duration-sec 30
```

## 5) API 서버(선택)

```powershell
python -m stitching serve --host 127.0.0.1 --port 8080 --storage-dir .\storage
```

작업 생성:

```powershell
curl -X POST http://127.0.0.1:8080/jobs/video-stitch `
  -H "Content-Type: application/json" `
  -d "{\"left_path\":\"C:/path/to/video_left.mp4\",\"right_path\":\"C:/path/to/video_right.mp4\",\"options\":{\"max_duration_sec\":30,\"perf_mode\":\"balanced\",\"homography_mode\":\"auto\"}}"
```

## 6) 에러 코드

- `PROBE_FAIL`
- `OVERLAP_LOW`
- `HOMOGRAPHY_FAIL`
- `ENCODE_FAIL`
- `INTERNAL_ERROR`

## 7) report.json 키 형식

- 저장 시 모든 키는 `한글(영어)` 형식으로 기록된다.
- 예: `상태(status)`, `오류코드(error_code)`, `메트릭(metrics)`.
