# Project Overview And Architecture

## Goal

이 프로젝트의 목표는 두 개의 RTSP 카메라 입력을 받아 하나의 stitched stream으로 실시간 송출하는 것이다.
최종 산출물의 중심은 파일 생성이 아니라 실시간 output stream이다.

핵심 파이프라인:

```text
RTSP input -> decode -> pair/sync -> stitch -> encode -> output stream
```

## Current Direction

현재 구조는 `Python control plane + C++ native runtime`으로 정리되어 있다.

- Python:
  - calibration UI
  - runtime 실행/중지
  - monitor/dashboard
  - viewer launch
  - control command
- C++ native runtime:
  - RTSP input
  - ffmpeg decode
  - frame pairing and sync
  - GPU warp/blend
  - ffmpeg encode and output

즉 Python은 운영 도구이고, 실제 메인 엔진은 C++ runtime이다.

## Why The Split Exists

이전 Python/OpenCV 중심 구조는 빠른 실험에는 좋았지만, 실시간 고해상도 경로에는 한계가 분명했다.

핵심 문제:

- 큰 프레임이 Python 경계를 통과했다
- Python이 sync/buffer hot path에 남아 있었다
- GPU를 써도 host-to-device 경계가 계속 남았다
- final output까지 Python이 관여하면 성능과 안정성이 같이 흔들렸다

그래서 현재는 프레임 소유권과 실시간 path를 C++ 쪽으로 옮겼다.

## Operator Flow

현재 사용 흐름은 아래처럼 정리된다.

1. [`scripts/run_native_calibrate.cmd`](/c:/Users/Pixellot/Hogak_Stitching/scripts/run_native_calibrate.cmd) 실행
2. 좌/우 대표 프레임 확인
3. 필요하면 overlap guide를 참고해서 seed point 선택
4. `COMPLETE`
5. inlier match / stitched preview 검토
6. `CONFIRM`
7. homography 저장
8. main runtime 자동 실행
9. monitor + viewer로 stitched output 확인

즉 calibration과 runtime launch가 하나의 연속된 운영 흐름으로 묶이는 방향이다.

## Current Operating Baseline

현재 운영 기본값은 realtime 기준으로 정리되고 있다.

- output target: local UDP viewer path
- output resolution: `1920x1080`
- codec baseline: `h264_nvenc`
- runtime preset: realtime 중심
- calibration mode baseline: assisted-first

## What This Project Is Not

이 프로젝트는 더 이상 Python에서 직접 실시간 stitching을 처리하는 앱이 아니다.
Python은 calibration/control/operator tooling이고, stitched stream의 실제 생성과 송출은 native runtime이 담당한다.

이 문서를 한 줄로 요약하면 다음과 같다.

> 현재 Hogak Stitching은 Python UI 실험 프로젝트가 아니라, Python control plane 위에서 동작하는 native real-time stitching runtime 프로젝트다.
