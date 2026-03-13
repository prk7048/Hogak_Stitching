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
- `scripts\run_native_runtime_soak.cmd` 20초 재검증에서 small/large UDP 모두 `returncode=0`, `shutdown_forced=false`로 종료됐다.
- 즉 immediate writer crash는 현재 smoke/short soak 기준 재현되지 않고, 다음 1순위는 **longer soak test와 graceful shutdown 기준 정리**다.

## 빌드

```cmd
cmake --preset windows-release
cmake --build --preset build-windows-release
```

산출물 예시:

```text
native_runtime\build\windows-release\Release\stitch_runtime.exe
```

## 실행

### 기본 실행

```cmd
scripts\run_native_runtime_realtime.cmd
```

strict pair 기준으로 보고 싶으면:

```cmd
scripts\run_native_runtime_strict.cmd
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
.venv312\Scripts\python.exe -m stitching.cli native-runtime --left-rtsp "rtsp://..." --right-rtsp "rtsp://..." --monitor-mode compact
```

### viewer 없이 실행

```cmd
scripts\run_native_runtime.cmd --no-viewer
```

Python 시작 UI를 쓰는 경우 첫 화면에서 `Open VLC low-latency transmit`를 체크하면 probe viewer는 그대로 두고, transmit를 보는 VLC 저지연 창을 추가로 연다.

외부 VLC로 수신할 때는 기본 VLC 열기보다 저지연 옵션 스크립트를 쓰는 편이 낫다.

```cmd
scripts\open_vlc_low_latency.cmd transmit
scripts\open_vlc_low_latency.cmd probe
```

기본 포트는 `transmit=24000`, `probe=23000`이고, 두 번째 인자로 캐시 ms를 줄 수 있다.

`run_native_runtime.cmd`는 기본적으로 `output/native/runtime_homography.json`을 자동 사용한다.

### short soak test

```cmd
scripts\run_native_runtime_soak.cmd
```

### 직접 실행

```cmd
.venv312\Scripts\python.exe -m stitching.cli native-runtime --left-rtsp "rtsp://..." --right-rtsp "rtsp://..." --homography-file "output/native/runtime_homography.json" --viewer
```

기본 콘솔 출력은 상태 변화나 5초 주기 요약만 남긴다.
raw event가 필요할 때만 `--verbose-events`를 붙인다.

### homography 생성

가장 간단한 실행:

```cmd
scripts\run_native_calibrate.cmd
```

CLI도 기본 project camera 주소와 `output/native/runtime_homography.json`을 사용하므로 바로 실행 가능하다.

```cmd
.venv312\Scripts\python.exe -m stitching.cli native-calibrate
```

```cmd
.venv312\Scripts\python.exe -m stitching.cli native-calibrate --left-rtsp "rtsp://..." --right-rtsp "rtsp://..." --out "output/native/runtime_homography.json"
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

`scripts\native_runtime_soak.py`는 이제 단순 실행이 아니라 pass/fail을 함께 남긴다.

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
