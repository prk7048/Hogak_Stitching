# Native Runtime Current Status

## Summary

현재 프로젝트는 구조 전환 단계는 대부분 끝났고, 운영 기준과 calibration 품질 UX를 마무리하는 단계에 들어와 있다.

핵심 상태를 한 줄로 요약하면 다음과 같다.

> 실시간 stitching main engine은 이미 native runtime으로 옮겨졌고, 남은 일은 운영 기준 확정과 calibration/operator 경험 정리다.

## What Is Implemented

### Native Runtime

- RTSP input reader
- ffmpeg decode path
- pair/sync handling
- GPU warp
- GPU blend
- ffmpeg output writer
- JSON line control/metrics channel

### Python Control Plane

- runtime launcher
- runtime client
- dashboard monitor
- viewer launch helper
- assisted-first calibration UI
- runtime launch after calibration confirmation

### Operating Presets

- realtime-oriented runtime scripts
- strict/runtime split direction
- `1920x1080` output baseline
- `h264_nvenc` default-friendly path

## What Has Been Confirmed

현재 코드 기준으로 확인된 동작:

- 좌/우 RTSP 입력 수신
- calibration homography load
- GPU warp/blend path 동작
- stitched output stream 송출
- monitor에서 output/input/system 상태 확인
- viewer launch path 연결
- calibration -> confirm -> runtime launch 운영 흐름

즉 end-to-end 구조 자체는 이미 살아 있다.

## Current Risks

아직 운영 관점에서 남아 있는 리스크는 다음이다.

1. 장시간 실행 안정성
2. 입력 freeze / content stall에 대한 운영 판정
3. calibration 품질이 장면에 따라 흔들리는 문제
4. deep matching backend가 아직 placeholder인 점
5. viewer/monitor 경험을 더 다듬을 필요

## Progress Estimate

실무적으로 보면 현재 진척은 이 정도로 보는 게 맞다.

- 구조 전환: `80~85%`
- 운영 준비도: `65~75%`

즉 “엔진을 새로 만드는 단계”는 거의 지났고,
지금은 “운영 가능한 기준으로 닫는 단계”라고 보면 된다.

## Current Best Interpretation

현재 프로젝트는 실패한 실험 단계가 아니다.
반대로 핵심 구조 결정은 이미 끝났고, 실제로 stitched stream이 나오는 상태까지 왔다.

남은 일은 새로운 대형 아키텍처 변경이 아니라 아래 성격에 가깝다.

- calibration 품질 정리
- 운영 기준 정의
- long-run validation
- 문서와 실행 흐름 마감

즉 현재 phase는 build보다 finish에 더 가깝다.
