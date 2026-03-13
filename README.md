# Hogak Stitching

두 개의 RTSP 카메라 입력을 받아 하나의 stitched stream으로 실시간 송출하는 프로젝트다.

현재 구조는 `Python control plane + C++ native runtime`이다.

- Python:
  - calibration UI
  - runtime launch/control
  - monitor/dashboard
  - final encoded output probe viewer
- C++ native runtime:
  - RTSP input
  - pair/sync
  - GPU warp/blend
  - encode/output stream

현재 기준 메인 경로는 Python에서 직접 스티칭하는 방식이 아니라,
Python이 calibration과 운영 제어를 맡고 C++ runtime이 실제 stitched stream을 만드는 방식이다.

상세 판단 문서는 [`reports/README.md`](/c:/Users/Pixellot/Hogak_Stitching/reports/README.md)를 보면 된다.

## Quick Start

### 1. Install

```cmd
python -m pip install -r requirements.txt
```

### 2. Build Native Runtime

```cmd
cmake --preset windows-release
cmake --build --preset build-windows-release
```

### 3. Calibrate

가장 간단한 실행:

```cmd
scripts\run_native_calibrate.cmd
```

현재 calibration 기본 흐름:

1. 좌/우 대표 프레임 표시
2. 필요하면 overlap guide를 참고해 대응점 선택
3. `COMPLETE`
4. 실제 inlier match / stitched preview 검토
5. `CONFIRM`
6. homography 저장
7. main runtime 자동 실행

즉 calibration 성공 후 바로 runtime까지 이어진다.

보정만 하고 종료하려면:

```cmd
scripts\run_native_calibrate.cmd --calibration-only
```

CLI 직접 실행도 가능하다.

```cmd
python -m stitching.cli native-calibrate
```

## Runtime

기본 운영 경로:

```cmd
scripts\run_native_runtime.cmd
```

viewer 없이 monitor만 보려면:

```cmd
scripts\run_native_runtime.cmd --no-viewer
```

Python 시작 UI 첫 화면에서 `Open VLC low-latency transmit`를 체크하면 probe viewer는 그대로 두고, transmit를 보는 VLC 저지연 창을 추가로 연다.

외부 VLC로 본선 송출을 저지연으로 보려면:

```cmd
scripts\open_vlc_low_latency.cmd transmit
```

probe 미러를 VLC로 보려면:

```cmd
scripts\open_vlc_low_latency.cmd probe
```

두 번째 인자로 캐시 ms를 줄 수 있다. 예: `scripts\open_vlc_low_latency.cmd transmit 100`

post-stitch 기본 구조는 아래처럼 정리한다.

```text
stitched frame
-> transmit encode -> external target
                   -> local UDP debug receive

fallback without transmit:
stitched frame -> standalone probe encode -> local UDP probe -> viewer/debug receive
```

- `probe`: transmit가 켜져 있으면 그 송출 비트스트림을 로컬에서 다시 받는 debug receive 경로다. transmit가 없을 때만 standalone local encode로 fallback한다.
- `transmit`: 실제 외부 송출 경로
- viewer는 raw preview가 아니라 기본적으로 `probe`를 다시 받아 보여준다
- 기본 runtime 스크립트는 현재 `transmit`에 debug overlay를 넣는다. `frame`, `seq`, `reuse`, `pair_age`가 보여서 24000 화면이 진짜 멈춘 건지 반복 프레임인지 바로 구분할 수 있다.

realtime 우선 프리셋:

```cmd
scripts\run_native_runtime_realtime.cmd
```

strict pair 우선 프리셋:

```cmd
scripts\run_native_runtime_strict.cmd
```

현재 기본 운영값 방향:

- probe source: `auto`
- probe output: local UDP loopback
- transmit output: 필요할 때만 명시적으로 활성화
- output size baseline: `1920x1080`
- codec: `h264_nvenc`
- pair mode: realtime 쪽이 기본

PAL/NTSC 같은 출력 규격은 Python control plane에서 preset으로 고른다.

예:

```cmd
python -m stitching.cli native-runtime --left-rtsp "..." --right-rtsp "..." --output-standard ntsc_sd
python -m stitching.cli native-runtime --left-rtsp "..." --right-rtsp "..." --output-standard pal_hd
python -m stitching.cli native-runtime --left-rtsp "..." --right-rtsp "..." --probe-source transmit --transmit-output-runtime ffmpeg --transmit-output-target "udp://10.0.0.20:5000?pkt_size=1316"
```

현재 지원 preset:

- `realtime_1080p`
- `realtime_hq_1080p`
- `realtime_hq_1080p_strict`
- `ntsc_sd`
- `pal_sd`
- `ntsc_hd`
- `pal_hd`

## What To Watch

monitor에서 우선 볼 값:

- `probe_fps`, `transmit_fps`
- `left_fps`, `right_fps`
- `left_age_ms`, `right_age_ms`
- `left_motion_mean`, `right_motion_mean`
- `probe active`, `transmit active`
- `viewer`

해석:

- `probe_fps`는 local debug receive가 실제로 흘러가는지 보여준다. transmit가 켜져 있으면 기본적으로 mirrored transmit 기준이다.
- `transmit_fps`는 외부 송출 경로가 실제로 흘러가는지 보여준다.
- `age_ms`가 커지면 해당 입력이 멈추거나 지연된 상태다.
- `motion_mean`이 낮고 age는 낮다면, 프레임은 오지만 내용이 얼어 있는 상태일 수 있다.
- `viewer`는 raw snapshot이 아니라 post-encode local debug receive 기준이다.

## Calibration Notes

현재 calibration 원칙:

- 사용자는 대응점을 `0..n`개까지 줄 수 있다
- auto baseline은 항상 먼저 계산한다
- 수동 점은 seed로만 사용한다
- assisted 결과가 baseline보다 나쁘면 버린다
- 최종 저장은 더 좋은 candidate만 사용한다

즉 수동 입력은 “강제 정답”이 아니라 “더 좋은 매칭을 돕는 힌트”다.

딥러닝 backend도 calibration 단계에만 후보로 들어간다.

- `--match-backend deep`
- `--deep-backend auto|lightglue|loftr`

현재 동작:

- `auto`: deep backend가 없으면 classic으로 fallback
- `deep`: deep backend가 실제로 없으면 명시적으로 실패

즉 deep matcher는 baseline auto를 깨는 기본 경로가 아니라, 더 좋을 때만 채택되는 추가 후보다.

## Legacy Paths

아래 경로는 아직 남아 있지만 현재 주력 운영 경로는 아니다.

- `python -m stitching.cli desktop`
- `python -m stitching.cli gui`
- offline video stitching commands

현재 프로젝트 기준 main path는 아래 둘이다.

1. `scripts\run_native_calibrate.cmd`
2. `scripts\run_native_runtime.cmd`

## Common Errors

- `OVERLAP_LOW`: calibration match가 너무 적다
- `HOMOGRAPHY_FAIL`: homography 또는 geometry 품질이 부족하다
- `ENCODE_FAIL`: output encode path 실패
- `INTERNAL_ERROR`: 내부 처리 실패

## Status

현재 상태를 짧게 요약하면:

- native runtime main path는 이미 동작한다
- calibration UX와 operator flow가 정리되고 있다
- 남은 일은 장시간 안정성, calibration 품질, deep matching 확장이다

즉 지금 phase는 구조를 새로 만드는 단계보다 운영 baseline을 마감하는 단계에 가깝다.
