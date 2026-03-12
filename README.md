# Hogak Stitching

두 개의 RTSP 카메라 입력을 받아 하나의 stitched stream으로 실시간 송출하는 프로젝트다.

현재 구조는 `Python control plane + C++ native runtime`이다.

- Python:
  - calibration UI
  - runtime launch/control
  - monitor/dashboard
  - viewer launch
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

realtime 우선 프리셋:

```cmd
scripts\run_native_runtime_realtime.cmd
```

strict pair 우선 프리셋:

```cmd
scripts\run_native_runtime_strict.cmd
```

현재 기본 운영값 방향:

- output: `1920x1080`
- codec: `h264_nvenc`
- pair mode: realtime 쪽이 기본

## What To Watch

monitor에서 우선 볼 값:

- `output_fps`
- `left_fps`, `right_fps`
- `left_age_ms`, `right_age_ms`
- `left_motion_mean`, `right_motion_mean`
- `output active`
- `viewer`

해석:

- `output_fps`가 유지되면 실제 송출이 유지되는 상태다.
- `age_ms`가 커지면 해당 입력이 멈추거나 지연된 상태다.
- `motion_mean`이 낮고 age는 낮다면, 프레임은 오지만 내용이 얼어 있는 상태일 수 있다.

## Calibration Notes

현재 calibration 원칙:

- 사용자는 대응점을 `0..n`개까지 줄 수 있다
- auto baseline은 항상 먼저 계산한다
- 수동 점은 seed로만 사용한다
- assisted 결과가 baseline보다 나쁘면 버린다
- 최종 저장은 더 좋은 candidate만 사용한다

즉 수동 입력은 “강제 정답”이 아니라 “더 좋은 매칭을 돕는 힌트”다.

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
